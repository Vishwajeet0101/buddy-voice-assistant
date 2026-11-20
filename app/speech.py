"""Speaking out loud.

Natural voices come from Microsoft Edge's free online text-to-speech (edge-tts, no key needed).
Without internet the built-in Windows voice is used instead.
Answers are spoken sentence by sentence, so Buddy starts talking before the AI has finished writing.
"""
import asyncio
import ctypes
import hashlib
import queue
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import config

log = config.log
CACHE_DIR = config.DATA_DIR / "voice_cache"
NO_WINDOW = 0x08000000
VOICES = {"male": ("en-IN-PrabhatNeural", "hi-IN-MadhurNeural"),
          "female": ("en-IN-NeerjaNeural", "hi-IN-SwaraNeural")}
SPOKEN_LIMIT = 420          # longer answers: the rest is shown on screen

_mci = ctypes.windll.winmm.mciSendStringW
user32 = ctypes.windll.user32


def esc_pressed():
    return bool(user32.GetAsyncKeyState(0x1B) & 0x8000)


def clean_for_speech(text):
    t = re.sub(r"https?://\S+", "the link", text)
    t = re.sub(r"[A-Za-z]:\\\S+", "the file", t)
    t = re.sub(r"```.*?```", " (code on screen) ", t, flags=re.S)
    t = re.sub(r"[*#`_>|~]", "", t)
    t = re.sub(r"^\s*[-•]\s*", "", t, flags=re.M)
    t = re.sub(r"\s*\n+\s*", ". ", t)
    t = re.sub(r":\s*\.", ":", t)
    t = re.sub(r"\.\s*\.", ".", t)
    return re.sub(r"\s+", " ", t).strip(" .") + ("." if t.strip() and t.strip()[-1] not in ".!?।" else "")


_BOUNDARY = re.compile(r"[.!?।][\"')\]]*\s+|\n+")


def split_ready(buf, final=False):
    """Takes complete sentences off the front of buf. Returns (sentences, rest)."""
    out = []
    while True:
        m = next((m for m in _BOUNDARY.finditer(buf) if m.end() >= 25), None)
        if not m:
            break
        out.append(buf[:m.end()])
        buf = buf[m.end():]
    if final and buf.strip():
        out.append(buf)
        buf = ""
    return out, buf


class Speaker:
    def __init__(self):
        self.stop_flag = threading.Event()
        self.lock = threading.Lock()
        self.speaking = False
        self._n = 0
        self._n_lock = threading.Lock()
        CACHE_DIR.mkdir(exist_ok=True)
        try:                                   # keep the cache small: forget the oldest replies
            files = sorted(CACHE_DIR.glob("*.mp3"), key=lambda p: p.stat().st_atime)
            for p in files[:-400]:
                p.unlink()
        except OSError:
            pass

    def _next(self):
        with self._n_lock:
            self._n += 1
            return self._n

    def voice_for(self, text):
        en, hi = VOICES.get(config.load().get("voice"), VOICES["male"])
        return hi if re.search(r"[ऀ-ॿ]", text) else en

    def synth(self, text, cache=False):
        """Text -> mp3 file (online neural voice). None when it fails (no internet)."""
        voice = self.voice_for(text)
        if cache:
            path = CACHE_DIR / (hashlib.sha1(f"{voice}|{text}".encode()).hexdigest()[:16] + ".mp3")
            if path.exists() and path.stat().st_size > 1000:
                return path
        else:
            path = Path(tempfile.gettempdir()) / f"buddy_voice_{self._next()}.mp3"
        try:
            import edge_tts

            async def run():
                await asyncio.wait_for(edge_tts.Communicate(text, voice, rate="+8%").save(str(path)), 12)
            t = time.time()
            asyncio.run(run())
            if path.exists() and path.stat().st_size > 500:
                log.info("tts %.2fs for %d chars", time.time() - t, len(text))
                return path
        except Exception as e:
            log.warning("online voice failed (%s) - using Windows voice", e)
        return None

    def _play(self, path):
        """Plays an mp3 and waits. False if it was stopped (Esc / click / stop())."""
        alias = f"buddy{self._next()}"
        err = _mci(f'open "{path}" type mpegvideo alias {alias}', None, 0, 0)
        if err:
            log.warning("could not open audio (mci error %s)", err)
            return None
        _mci(f"play {alias}", None, 0, 0)
        buf = ctypes.create_unicode_buffer(32)
        finished = True
        try:
            time.sleep(0.05)
            while True:
                _mci(f"status {alias} mode", buf, 31, 0)
                if buf.value != "playing":
                    break
                if self.stop_flag.is_set() or esc_pressed():
                    finished = False
                    break
                time.sleep(0.03)
        finally:
            _mci(f"close {alias}", None, 0, 0)
        return finished

    def _windows_voice(self, text):
        text = " ".join(text.split())[:500].replace("'", "''")
        script = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  f"$s.Rate = 1; $s.Speak('{text}')")
        p = subprocess.Popen(["powershell", "-NoProfile", "-Command", script], creationflags=NO_WINDOW)
        while p.poll() is None:
            if self.stop_flag.is_set() or esc_pressed():
                p.kill()
                return False
            time.sleep(0.05)
        return True

    def _say_item(self, item):
        if item[0] == "file":
            _, path, temp, text = item
            ok = self._play(path)
            if temp:
                try:
                    path.unlink()
                except OSError:
                    pass
            return ok if ok is not None else self._windows_voice(text)
        return self._windows_voice(item[1])

    def say(self, text, cache=False):
        """Speaks text and waits. Returns False if stopped. Short replies are cached so they play instantly next time."""
        if cache or len(clean_for_speech(text or "")) <= 45:
            text = clean_for_speech(text or "") if not cache else text
            text = (text or "").strip()
            if not text:
                return True
            with self.lock:
                self.stop_flag.clear()
                self.speaking = True
                try:
                    path = self.synth(text, cache=True)
                    ok = self._play(path) if path else None
                    return ok if ok is not None else self._windows_voice(text)
                finally:
                    self.speaking = False
        return self.say_stream([text])[1]

    def say_stream(self, chunks, on_text=None):
        """Speaks text that arrives in pieces (e.g. an AI answer streaming in).
        on_text(full_text_so_far) is called as it arrives. Returns (full_text, finished)."""
        with self.lock:
            self.stop_flag.clear()
            self.speaking = True
            q = queue.Queue()
            result = {"full": ""}

            def emit(sentence, state):
                spoken = clean_for_speech(sentence)
                if not spoken or state["cut"]:
                    return
                if state["n"] and state["n"] + len(spoken) > SPOKEN_LIMIT:
                    state["cut"] = True
                    spoken = "The rest is on your screen."
                state["n"] += len(spoken)
                path = self.synth(spoken)
                q.put(("file", path, True, spoken) if path else ("say", spoken, False))

            def produce():
                state = {"n": 0, "cut": False}
                buf = ""
                try:
                    for chunk in chunks:
                        if self.stop_flag.is_set():
                            break
                        if not chunk:
                            continue
                        result["full"] += chunk
                        buf += chunk
                        if on_text:
                            on_text(result["full"])
                        ready, buf = split_ready(buf)
                        for s in ready:
                            emit(s, state)
                    ready, _ = split_ready(buf, final=True)
                    for s in ready:
                        emit(s, state)
                except Exception as e:
                    msg = getattr(e, "friendly", None) or "Sorry, something went wrong."
                    log.warning("answer failed: %r", e)
                    result["full"] += ("\n" if result["full"] else "") + msg
                    if on_text:
                        on_text(result["full"])
                    emit(msg, {"n": 0, "cut": False})
                finally:
                    q.put(None)

            threading.Thread(target=produce, daemon=True).start()
            finished = True
            try:
                while True:
                    item = q.get()
                    if item is None:
                        break
                    if not finished or self.stop_flag.is_set():
                        if item[0] == "file":   # stopped: just delete the rest
                            try:
                                item[1].unlink()
                            except OSError:
                                pass
                        continue
                    if not self._say_item(item):
                        finished = False
                        self.stop_flag.set()
            finally:
                self.speaking = False
            return result["full"], finished

    def stop(self):
        self.stop_flag.set()


speaker = Speaker()
