"""The ears: waits for "Hey Buddy", listens to what you say, and runs one conversation at a time.

Offline: wake word + speech-to-text with Vosk (small Indian-English model, ~40 MB).
When the offline text looks unsure (Hinglish, names, songs...) the recording is re-checked online (Groq/Gemini).
"""
import array
import io
import itertools
import json
import random
import re
import shutil
import threading
import time
import urllib.request
import wave
import winreg
import winsound
import zipfile
from datetime import datetime

import brain as brain_mod
import config
import skills
from speech import esc_pressed, live_voice, speaker

log = config.log
RATE = 16000
BLOCK = RATE // 10                 # 0.1 s per read
STOP_WORDS = {"stop", "cancel", "nothing", "never mind", "nevermind", "bas", "rehne do", "chhodo", "no", "nope",
              "thank you", "thanks", "ok thanks", "okay thanks", "thank you so much", "shukriya", "that's all",
              "that's it", "bye", "goodbye", "go away", "shut up", "be quiet"}
# Said while Buddy is talking: stop talking (the whole heard phrase must be one of these)
BARGE_STOP = {"stop", "stop it", "stop talking", "ruko", "ruk jao", "bas", "bas karo", "chup", "chup karo", "be quiet",
              "shut up", "enough", "cancel", "okay stop", "ok stop", "wait", "buddy stop"}
# Short words get misheard over the sound of Buddy's own voice - accept these when said on their own.
BARGE_ALIKE = {"stock", "top", "stuff", "stopped", "stops", "shop", "stab", "stall", "bus", "buss", "bass", "boss",
               "roku", "rocco", "brokaw", "brook", "rook", "rookie", "choop", "chip", "chop", "enough", "wade", "weight"}


def cosine(a, b):
    num = sum(x * y for x, y in zip(a, b))
    den = (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5)
    return num / den if den else 0.0


def idle_seconds():
    """Seconds since the last key press / mouse move."""
    import ctypes

    class LII(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    lii = LII(ctypes.sizeof(LII), 0)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
    return (ctypes.windll.kernel32.GetTickCount() - lii.dwTime) / 1000


def rms(data):
    a = array.array("h", data)
    return (sum(s * s for s in a) / max(1, len(a))) ** 0.5


def amplify(data, gain):
    """Makes a quiet microphone louder (many laptop mics record very softly)."""
    if gain <= 1.2:
        return data
    a = array.array("h", data)
    g = gain
    for i, s in enumerate(a):
        v = int(s * g)
        a[i] = 32767 if v > 32767 else -32768 if v < -32768 else v
    return a.tobytes()


def to_wav(frames):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(frames))
    return buf.getvalue()


def mic_busy_elsewhere():
    """True while another app (Zoom, Meet in the browser, Discord, a dictation app...) is using the microphone."""
    base = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
    for sub in ("", r"\NonPackaged"):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base + sub) as k:
                i = 0
                while True:
                    try:
                        name = winreg.EnumKey(k, i)
                    except OSError:
                        break
                    i += 1
                    if "python" in name.lower() or name == "NonPackaged":
                        continue
                    try:
                        with winreg.OpenKey(k, name) as app:
                            start = winreg.QueryValueEx(app, "LastUsedTimeStart")[0]
                            stop = winreg.QueryValueEx(app, "LastUsedTimeStop")[0]
                        if start and not stop:
                            return True
                    except OSError:
                        continue
        except OSError:
            pass
    return False


def is_locked():
    u = __import__("ctypes").windll.user32
    h = u.OpenInputDesktop(0, False, 0x0100)
    if not h:
        return True
    try:
        return not u.SwitchDesktop(h)
    finally:
        u.CloseDesktop(h)


class Engine:
    def __init__(self, ui, orb_getter, notify):
        self.ui = ui                    # ui(fn): run fn on the window thread
        self.orb = orb_getter           # () -> Orb
        self.notify = notify            # notify(title, text): tray balloon
        self.brain = brain_mod.Brain()
        self.model = None
        self.ready = False
        self.status = "Starting..."
        self.trigger_event = threading.Event()
        self.cancel = threading.Event()
        self.in_session = False
        self.levels = []
        self.gain = 1.0
        self.name = "buddy"
        self.pending = None             # a skill asked a question; the next answer goes to it
        self.show_text = None
        self._after = []
        self.jobs_lock = threading.Lock()
        self.spk = None                 # voice fingerprint model (for "only my voice")
        self.last_spk = None
        self.busy_reason = None         # "training" / "announcing": ignore the wake word for a moment
        self._barge = None              # (recogniser, audio) of a sentence that interrupted Buddy
        self.quiet_start = False        # started with Windows: no "I'm ready" pop-up

    def start(self):
        threading.Thread(target=self._main, daemon=True, name="listener").start()
        threading.Thread(target=self._scheduler, daemon=True, name="scheduler").start()

    # ===================== setup =====================

    def _ensure_model(self):
        path = config.model_dir()
        if path:
            return path
        self.status = "Downloading the offline speech model (40 MB, first time only)..."
        self.notify("Buddy is getting ready", "Downloading the offline speech model (about 40 MB). One minute...")
        dest = config.DATA_DIR / "models"
        dest.mkdir(exist_ok=True)
        tmp = dest / "model.zip.part"
        with urllib.request.urlopen(config.MODEL_URL, timeout=60) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        with zipfile.ZipFile(tmp) as z:
            z.extractall(dest)
        tmp.unlink()
        return config.model_dir()

    def _main(self):
        while not self.ready:
            try:
                import vosk
                vosk.SetLogLevel(-1)
                self.model = vosk.Model(str(self._ensure_model()))
                self.ready = True
            except Exception as e:
                log.exception("speech model failed")
                self.status = f"Speech model problem: {e}. Retrying in 30 s."
                time.sleep(30)
        threading.Thread(target=self.warm_greetings, daemon=True).start()
        self.load_spk()
        name = config.load()["assistant_name"]
        if not self.quiet_start:
            self.notify(f"{name} is ready", f'Say "Hey {name}" anytime. Press Ctrl+Alt+B to talk without the name.')
        while True:
            try:
                self._loop()
            except Exception as e:
                log.exception("microphone loop")
                self.status = f"Microphone problem: {e}"
                time.sleep(5)

    def warm_greetings(self):
        for g in self.greetings():
            speaker.synth(g, cache=True)

    def greetings(self):
        raw = config.load().get("greetings") or config.DEFAULTS["greetings"]
        return [g.strip() for g in raw.split("|") if g.strip()] or ["Yes?"]

    # ===================== names =====================

    def known_word(self, word):
        if not self.model:
            return True
        import vosk
        return vosk._c.vosk_model_find_word(self.model._handle, word.encode()) >= 0

    def check_name(self, name):
        """A problem message, or None if the offline listener can hear this name."""
        words = re.findall(r"[a-z']+", name.lower())
        if not words:
            return "Please type a name."
        if len(words) > 2:
            return "Pick a short name (one or two words)."
        bad = [w for w in words if not self.known_word(w)]
        if bad:
            return (f"The offline listener doesn't know the word \"{bad[0]}\". Pick a common English name "
                    "like Buddy, Jarvis, Nova, Friday, Max, Leo or Alexa.")
        return None

    def _wake_name(self):
        name = (config.load().get("assistant_name") or "Buddy").strip().lower()
        return name if not self.check_name(name) else "buddy"

    # ===================== listening loop =====================

    def trigger(self):
        """Hotkey / tray: start listening right away. While talking: stop talking."""
        if self.in_session:
            self.stop_session()
            return
        self.trigger_event.set()

    def stop_session(self):
        speaker.stop()
        self.cancel.set()

    def pause_reason(self):
        if not config.load().get("wake_word", True):
            return "off"
        if is_locked():
            return "locked"
        if mic_busy_elsewhere():
            return "busy"
        return None

    def set_wake(self, on):
        config.update(wake_word=bool(on))
        name = config.load()["assistant_name"]
        return (f"Okay, I'm listening for hey {name} again." if on else
                f"Okay, I'll stop listening. Press Control Alt B or use the tray icon when you need me.")

    def _open_stream(self):
        import sounddevice as sd
        return sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK)

    def _loop(self):
        while True:
            reason = self.pause_reason()
            if reason:
                self.status = {"off": "Wake word is off (Ctrl+Alt+B still works).",
                               "locked": "Paused while the laptop is locked.",
                               "busy": "Paused - another app (a call?) is using the microphone."}[reason]
                if self.trigger_event.wait(2):
                    self.trigger_event.clear()
                    with self._open_stream() as stream:
                        self.session(stream, greet=False)
                continue
            name = config.load()["assistant_name"]
            self.status = f'Listening for "Hey {name}"'
            with self._open_stream() as stream:
                self._wake_loop(stream)

    def _recognizer(self, grammar=None, spk=False):
        import vosk
        rec = vosk.KaldiRecognizer(self.model, RATE, json.dumps(grammar)) if grammar else vosk.KaldiRecognizer(self.model, RATE)
        rec.SetWords(True)
        if spk and self.spk:
            rec.SetSpkModel(self.spk)
        return rec

    # ===================== only my voice =====================

    def load_spk(self, download=False):
        """Loads the small voice-fingerprint model (13 MB; downloaded the first time it's needed)."""
        if self.spk:
            return True
        try:
            import vosk
            path = config.spk_model_dir()
            if not path and download:
                dest = config.DATA_DIR / "models"
                dest.mkdir(exist_ok=True)
                tmp = dest / "spk.zip.part"
                with urllib.request.urlopen(config.SPK_MODEL_URL, timeout=60) as r, open(tmp, "wb") as f:
                    shutil.copyfileobj(r, f, 1 << 20)
                with zipfile.ZipFile(tmp) as z:
                    z.extractall(dest)
                tmp.unlink()
                path = config.spk_model_dir()
            if path:
                self.spk = vosk.SpkModel(str(path))
        except Exception:
            log.exception("voice fingerprint model")
        return bool(self.spk)

    def verify_on(self):
        s = config.load()
        return bool(s.get("only_my_voice") and s.get("voice_print") and self.spk)

    def is_owner(self, vec):
        """Does this voice fingerprint match the trained voice? (Unknown = yes, so Buddy never goes deaf.)"""
        if not self.verify_on() or not vec:
            return True
        s = config.load()
        sim = cosine(vec, s["voice_print"])
        ok = sim >= float(s.get("voice_match", 0.45))
        log.info("voice check: similarity %.2f -> %s", sim, "you" if ok else "someone else")
        return ok

    def train_voice(self, on_step):
        """Records the wake phrase 3 times and saves the user's voice fingerprint.
        on_step(message) shows progress. Returns (ok, message)."""
        if not self.ready:
            return False, "Still starting - try again in a few seconds."
        on_step("Getting the voice model ready (13 MB, first time only)...")
        if not self.load_spk(download=True):
            return False, "Couldn't load the voice model. Check your internet and try again."
        name = config.load()["assistant_name"]
        phrases = [f"Hey {name}", f"Hey {name}, what's the time", f"Hey {name}, open Chrome"]
        vecs = []
        self.busy_reason = "training"
        try:
            with self._open_stream() as stream:
                for i, phrase in enumerate(phrases):
                    for attempt in range(3):
                        on_step(f"Step {i + 1} of 3 - say:  “{phrase}”")
                        vec = self._record_fingerprint(stream)
                        if vec:
                            vecs.append(vec)
                            break
                        on_step("I didn't hear that - please say it again, a bit louder.")
                        time.sleep(1.2)
        finally:
            self.busy_reason = None
        if len(vecs) < 2:
            return False, "I couldn't hear you clearly. Try again in a quieter place."
        avg = [sum(v[k] for v in vecs) / len(vecs) for k in range(len(vecs[0]))]
        config.update(voice_print=avg, only_my_voice=True)
        return True, f"Done! Now only your voice can wake {name}."

    def _record_fingerprint(self, stream):
        rec = self._recognizer(spk=True)
        started, quiet, t0, vec = False, 0.0, time.time(), None
        self._flush(stream)
        while time.time() - t0 < 7:
            data = bytes(stream.read(BLOCK)[0])
            level = self._measure(data)
            if rec.AcceptWaveform(amplify(data, self.gain)):
                vec = json.loads(rec.Result()).get("spk") or vec
            if level > self.threshold():
                started, quiet = True, 0.0
            elif started:
                quiet += 0.1
                if quiet >= 0.9:
                    break
        r = json.loads(rec.FinalResult())
        return r.get("spk") or vec if started else None

    WAKE_PREFIX = {"hey", "hi", "ok", "okay", "hello", "hay", "he", "a", "yo"}
    SOUNDALIKE = {"buddy": {"body", "buddhi", "bodhi", "buddie"}}

    def wake_split(self, text):
        """If text contains the wake phrase ("hey buddy ..." or a sentence starting with "buddy"),
        returns what was said after it ('' if nothing yet). Otherwise None."""
        words = text.lower().split()
        name = self.name.split()
        first, rest = name[0], name[1:]
        alike = self.SOUNDALIKE.get(first, set())
        for i, w in enumerate(words):
            if w != first and w not in alike:
                continue
            if words[i + 1:i + 1 + len(rest)] != rest:
                continue
            greeted = i > 0 and words[i - 1] in self.WAKE_PREFIX
            if (w == first and (i == 0 or greeted)) or (w in alike and greeted):
                return " ".join(words[i + 1 + len(rest):])
        return None

    def _wake_loop(self, stream):
        """Runs the full offline recogniser on the mic and wakes up as soon as the name is heard."""
        self.name = self._wake_name()
        verify = self.verify_on()
        rec = self._recognizer(spk=verify)
        frames = []
        last_check = started = last_read = time.time()
        silent_for = 99.0                  # seconds since anything louder than the room was heard
        pre = []
        while True:
            if self.trigger_event.is_set():
                self.trigger_event.clear()
                self.session(stream, greet=False)
                return
            if time.time() - last_check > 2.5:
                last_check = time.time()
                if self.pause_reason() or self._wake_name() != self.name or self.verify_on() != verify:
                    return
            data = bytes(stream.read(BLOCK)[0])
            if time.time() - last_read > 3:            # the laptop was asleep (lid closed): reopen the microphone
                log.info("woke from sleep - reopening the microphone")
                return
            last_read = time.time()
            level = self._measure(data)
            data = amplify(data, self.gain)
            silent_for = 0.0 if level > self.gate() else silent_for + 0.1
            if silent_for > 2.5:           # quiet room: don't run the recogniser (saves battery)
                pre.append(data)
                del pre[:-5]
                continue
            for d in pre:                  # sound again: first feed the last half second, so "hey" isn't cut off
                rec.AcceptWaveform(d)
                frames.append(d)
            pre = []
            frames.append(data)
            del frames[:-150]
            final = rec.AcceptWaveform(data)
            r = json.loads(rec.Result() if final else rec.PartialResult())
            text = r.get("text", "") if final else r.get("partial", "")
            after = self.wake_split(text) if text and not self.busy_reason else None
            if after is None:
                if final:
                    frames = []
                    if time.time() - started > 120:      # fresh recogniser now and then
                        return
                continue
            log.info("wake word heard: %r", text)
            if final:
                if not self.is_owner(r.get("spk")):
                    frames = []
                    continue
                if after.strip():                         # whole command already said: "Hey Buddy what's the time"
                    words = r.get("result", [])[-len(after.split()):]
                    self.session(stream, greet=False, heard=(after, words, to_wav(frames)))
                else:
                    self.session(stream, greet=True)
            else:                                         # still talking - finish the sentence with this recogniser
                self.session(stream, greet=None, rec=rec, frames=frames, verify=verify)
            return

    # ----- loudness: everything is relative to this microphone's own background noise -----

    def _measure(self, data):
        level = rms(data)
        self.levels.append(level)
        del self.levels[:-300]                       # last 30 seconds
        self._n_measured = getattr(self, "_n_measured", 0) + 1
        # Automatic gain: follows loud sounds at once (no distortion), lets go over ~2 s (quiet voices get boosted).
        self.env = max(level, getattr(self, "env", 0.0) * 0.93)
        self.gain = max(1.0, min(15.0, 3000 / max(self.env, self.floor() * 3, 1.0)))
        if self._n_measured % 600 == 0:
            log.info("mic: floor %.1f, loud %.1f, gain x%.1f", self.floor(), sorted(self.levels)[-1], self.gain)
        return level

    def floor(self):
        if not self.levels:
            return 5.0
        return sorted(self.levels)[len(self.levels) // 5]

    def threshold(self):
        """Louder than this = someone is talking."""
        return max(self.floor() * 3.5, 12)

    def gate(self):
        """Louder than this = worth running the recogniser."""
        return max(self.floor() * 2.2, 6)

    # ===================== one conversation =====================

    def title(self, state="speaking"):
        n = (config.load().get("assistant_name") or "Buddy")
        return {"listening": f"{n}  ·  listening", "thinking": f"{n}  ·  thinking"}.get(state, n)

    def thinking(self):
        self.ui(lambda: self.orb().update("thinking", self.title("thinking")))

    def after_speech(self, fn):
        self._after.append(fn)

    def _flush(self, stream):
        try:
            n = stream.read_available
            if n:
                stream.read(n)
        except Exception:
            pass

    def session(self, stream, greet=True, heard=None, rec=None, frames=None, verify=False):
        """One conversation. greet=None: woke mid-sentence - finish it and greet only if nothing followed the name.
        verify: check it's the owner's voice (after the sentence ends) before showing anything."""
        self.in_session = True
        self.cancel.clear()
        self.pending = None
        try:
            live_voice.warm()                  # open the voice connection now, so the answer is spoken sooner
            if rec is not None and verify:
                got = self.listen(stream, 2, rec=rec, frames=frames, quiet_end=0.6)
                if not self.is_owner(self.last_spk):
                    return
                rec = None
                heard = self._after_wake(got)
                greet = heard is None
            threading.Thread(target=winsound.Beep, args=(880, 80), daemon=True).start()
            self.ui(lambda: self.orb().show("listening", self.title("listening"), "Listening..."))
            if rec is not None:
                got = self.listen(stream, 2, rec=rec, frames=frames, quiet_end=0.7)
                heard = self._after_wake(got)
                greet = heard is None
            if greet:
                g = random.choice(self.greetings())
                self.ui(lambda: self.orb().update("speaking", self.title(), g))
                speaker.say(g, cache=True)
                self._flush(stream)
                self.ui(lambda: self.orb().update("listening", self.title("listening"), "Listening..."))
            wait = 7
            while not self.cancel.is_set():
                if heard is None:
                    heard = self.listen(stream, wait)
                if not heard:
                    break
                text = self.understand(*heard)
                if text is None:
                    break
                outcome = self.respond(text, stream)
                heard = None
                if outcome == "wake":               # you said "Hey Buddy" while it was talking: listen again
                    self.ui(lambda: self.orb().update("listening", self.title("listening"), "Listening..."))
                    if self._barge:                 # you were still talking: finish that sentence
                        b_rec, b_frames = self._barge
                        self._barge = None
                        heard = self._after_wake(self.listen(stream, 2, rec=b_rec, frames=b_frames, quiet_end=0.7))
                    wait = 7
                    continue
                if outcome != "done" or self.cancel.is_set():
                    break
                if not (config.load().get("follow_up", True) or self.pending):
                    self.ui(lambda: self.orb().hide(4500))
                    return
                self._flush(stream)
                self.ui(lambda: self.orb().update("listening", self.title("listening")))
                wait = 8 if self.pending else 5
            self.ui(lambda: self.orb().hide(1500))
        except Exception as e:
            log.exception("session")
            self.ui(lambda: self.orb().show("speaking", self.title(), f"Something went wrong: {e}"))
            self.ui(lambda: self.orb().hide(5000))
        finally:
            self.pending = None
            self.in_session = False
            self._flush(stream)

    def _after_wake(self, got):
        """(text, words, wav) of a sentence that began with the wake phrase -> just the part after it (None if empty)."""
        if not got:
            return None
        text, words, wav = got
        rest = self.wake_split(text)
        rest = text if rest is None else rest
        if not rest.strip():
            return None
        return rest, words[-len(rest.split()):], wav

    def listen(self, stream, wait_seconds, rec=None, frames=None, quiet_end=0.8):
        """Records one sentence. Returns (offline_text, words, wav) or None if nothing was said.
        rec/frames: continue a sentence the wake-word listener already started hearing."""
        started = rec is not None
        rec = rec or self._recognizer()
        frames, texts, words = list(frames or []), [], []
        quiet = 0.0
        t0 = time.time()
        threshold = self.threshold()

        self.last_spk = None

        def feed(data):
            frames.append(data)
            if rec.AcceptWaveform(data):
                r = json.loads(rec.Result())
                if r.get("text"):
                    texts.append(r["text"])
                    words.extend(r.get("result", []))
                    self.last_spk = self.last_spk or r.get("spk")
                return None
            return json.loads(rec.PartialResult()).get("partial", "")

        while True:
            if self.cancel.is_set() or esc_pressed():
                self.cancel.set()
                return None
            data = bytes(stream.read(BLOCK)[0])
            level = self._measure(data)
            self.ui(lambda lv=min(1.0, level / (threshold * 4)): self.orb().set_level(lv))
            partial = feed(amplify(data, self.gain))
            if partial or (texts and partial is None):
                started = True
                live = " ".join(texts + ([partial] if partial else []))
                self.ui(lambda t=live: self.orb().update(text=t + " ..."))
            if level > threshold:
                if not started and time.time() - t0 > 0.3:
                    started = True
                quiet = 0.0
            elif started:
                quiet += 0.1
                if quiet >= quiet_end:
                    break
            if not started and time.time() - t0 > wait_seconds:
                return None
            if time.time() - t0 > 15:
                break
        r = json.loads(rec.FinalResult())
        if r.get("text"):
            texts.append(r["text"])
            words.extend(r.get("result", []))
            self.last_spk = self.last_spk or r.get("spk")
        text = " ".join(texts).strip()
        if not text and len(frames) < 8:
            return None
        return text, words, to_wav(frames)

    def understand(self, text, words, wav):
        """Final text of what was said (offline, or re-checked online when unsure). None = stop."""
        confs = [w["conf"] for w in words if w["word"] != "[unk]"]
        avg = sum(confs) / len(confs) if confs else 0
        k = skills.kind(text) if text else None
        question = bool(re.match(r"(?:what|who|whom|why|how|when|where|which|is|are|was|were|do|does|did|can|could|"
                                 r"should|will|would|tell me|explain|give me|define|meaning)\b", text or ""))
        # Not a known command and not clearly a question? It may be a misheard command - double-check online.
        sure = (text and k == "fast" and avg >= 0.5) or (
            text and k is None and question and avg >= 0.88 and min(confs) >= 0.6 and len(confs) >= 3)
        if self.pending and text and avg >= 0.8:
            sure = True
        log.info("heard offline: %r (avg conf %.2f, %s)", text, avg, k)
        if not text:
            # The offline recogniser heard no words at all (noise, a cough, silence). Online speech models
            # tend to invent text for such clips ("Thank you.", "See you guys."), so don't ask them.
            return None
        final = text
        if not sure and self.brain.has_ai():
            self.ui(lambda: self.orb().update("thinking", self.title("thinking"), (text or "...") + " ..."))
            try:
                online = self.brain.transcribe(wav)
                log.info("heard online: %r", online)
                junk = {"hinglish letters", "thank you", "thanks for watching", "see you guys", "bye", "you",
                        "subtitles by the amara.org community", "voice command to a laptop assistant"}
                if online and online.lower().strip(" .!?") not in junk:
                    final = online
            except Exception as e:
                log.warning("online speech-to-text failed: %r", e)
        final = re.sub(r"\s+", " ", (final or "")).strip()
        name = re.escape(self.name)       # online transcripts may still start with "Hey Buddy,"
        final = re.sub(rf"^(?:(?:hey|hi|ok|okay|hello)[\s,]+)?{name}\b[\s,.!]*", "", final, flags=re.I).strip()
        if not final:
            if text == "":
                return None
            self.ui(lambda: self.orb().update("speaking", self.title(), "Sorry, I didn't catch that."))
            speaker.say("Sorry, I didn't catch that.", cache=True)
            return ""
        plain = skills.clean(final)
        if plain in STOP_WORDS and not self.pending:
            if plain.startswith(("thank", "shukriya")):
                self.ui(lambda: self.orb().update("speaking", self.title(), "You're welcome!"))
                speaker.say(random.choice(["You're welcome!", "Anytime!", "Happy to help!"]), cache=True)
            return None
        return final

    def respond(self, text, stream=None):
        """Runs a command or asks the AI, and says the answer.
        Returns 'done', 'stopped' (Esc/click/"stop"), or 'wake' ("Hey Buddy" said while it was talking)."""
        if not text:
            return "done"
        self.show_text, self._after = None, []
        shown = text if len(text) < 120 else text[:117] + "..."
        self.ui(lambda: self.orb().update("thinking", self.title("thinking"), f"“{shown}”"))
        reply = skills.run(text, self)
        speak = config.load().get("speak_replies", True)
        on_text = lambda t: self.ui(lambda: self.orb().update("speaking", self.title(), t))
        if reply is None and not self.brain.has_ai():
            reply = ("I can do laptop tasks without the internet, but to answer questions I need a free AI key. "
                     "Right-click my tray icon and open Settings to add one.")
        if reply is None:
            # Ask the AI. If it decides this was really a laptop command (any wording, Hinglish, Hindi...),
            # it answers "CMD: <command>" and we run that command instead of speaking.
            stream_ = self.brain.ask_stream(text)
            try:
                head, rest = self._peek(stream_)
            except Exception as e:
                head, rest = getattr(e, "friendly", "Sorry, something went wrong."), iter(())
            if head.lstrip().upper().startswith("CMD:"):
                command = (head + "".join(rest)).split(":", 1)[1].strip().strip('"').splitlines()[0]
                log.info("AI turned %r into command %r", text, command)
                reply = skills.run(command, self) or "Sorry, I can't do that on the laptop yet."
                if self.brain.history and self.brain.history[-1]["role"] == "assistant":
                    self.brain.history[-1]["text"] = reply
            else:
                chunks = itertools.chain([head], rest)
                if not speak:
                    on_text("".join(chunks))
                    self._run_after()
                    return "done"

                def talk():
                    full, finished = speaker.say_stream(chunks, on_text=on_text)
                    log.info("AI answered: %r", full[:200])
                    return finished
                outcome = self._speak(stream, talk, "")
                self._run_after()
                return outcome
        shown = self.show_text or reply
        log.info("replied: %r", reply[:200])
        on_text(shown)
        outcome = self._speak(stream, lambda: speaker.say(reply), reply) if speak else "done"
        self._run_after()
        return outcome

    @staticmethod
    def _peek(gen):
        """Reads the first few characters of a stream (to spot 'CMD:'), and returns (head, rest_of_stream)."""
        head = ""
        for piece in gen:
            head += piece
            if len(head.strip()) >= 4:
                break
        return head, gen

    def _speak(self, stream, talk, spoken_text):
        """Runs talk() (which speaks and returns True if it finished) while still listening to the mic,
        so you can interrupt: say "stop" to make Buddy be quiet, or "Hey Buddy" to ask something new."""
        if stream is None:
            return "done" if talk() else "stopped"
        box = {}
        t = threading.Thread(target=lambda: box.setdefault("ok", talk()), daemon=True)
        t.start()
        rec = self._recognizer()
        frames = []
        allow_wake = self.name not in spoken_text.lower()      # Buddy saying its own name mustn't wake itself
        heard = None
        self._barge = None
        while t.is_alive():
            try:
                data = bytes(stream.read(BLOCK)[0])
            except Exception:
                break
            self._measure(data)
            data = amplify(data, self.gain)
            frames.append(data)
            final = rec.AcceptWaveform(data)
            said = (json.loads(rec.Result()).get("text", "") if final
                    else json.loads(rec.PartialResult()).get("partial", "")).strip()
            if not said:
                continue
            if final:
                log.debug("heard while talking: %r", said)
            after = self.wake_split(said)
            rest = (after or "").strip()
            if said in BARGE_STOP or rest in BARGE_STOP or (final and said in BARGE_ALIKE) \
                    or (after is not None and rest in BARGE_ALIKE):
                heard = "stopped"
            elif allow_wake and after is not None and (rest or final):
                heard = "wake"                     # "Hey Buddy ..." - keep listening to the rest of the sentence
                if not final:
                    self._barge = (rec, frames)
            if heard:
                log.info("interrupted by voice: %r", said)
                speaker.stop()
                break
            if final:
                frames = []
        t.join(5)
        if heard:
            return heard
        return "done" if box.get("ok", True) else "stopped"

    def _run_after(self):
        for fn in self._after:
            try:
                fn()
            except Exception:
                log.exception("after-speech action")
        self._after = []

    # ===================== timers, alarms, reminders =====================

    def _jobs(self):
        out = []
        for j in config.load().get("alarms", []):
            try:
                out.append({**j, "at": datetime.fromisoformat(j["at"])})
            except (KeyError, ValueError):
                pass
        return sorted(out, key=lambda j: j["at"])

    def _save_jobs(self, jobs):
        config.update(alarms=[{**j, "at": j["at"].isoformat(timespec="seconds")} for j in jobs])

    def schedule(self, when, kind, text):
        with self.jobs_lock:
            jobs = self._jobs()
            jobs.append({"at": when, "kind": kind, "text": text})
            self._save_jobs(jobs)
        log.info("scheduled %s at %s", kind, when)

    def list_jobs(self):
        return self._jobs()

    def cancel_jobs(self, kind):
        with self.jobs_lock:
            jobs = self._jobs()
            keep = [j for j in jobs if j["kind"] != kind]
            self._save_jobs(keep)
        return len(jobs) - len(keep)

    def announce(self, text, title=None):
        """Speaks something Buddy starts by itself (morning briefing), with the popup."""
        self.busy_reason = "announcing"
        try:
            self.ui(lambda: self.orb().show("speaking", title or self.title(), text))
            speaker.say(text)
            self.ui(lambda: self.orb().hide(6000))
        finally:
            self.busy_reason = None

    def _maybe_briefing(self):
        """Morning briefing: the first time you use the laptop between 5 am and noon."""
        s = config.load()
        now = datetime.now()
        if (not s.get("morning_briefing") or s.get("last_briefing") == now.date().isoformat()
                or not 5 <= now.hour < 12 or self.in_session or not self.ready or is_locked() or idle_seconds() > 20):
            return
        config.update(last_briefing=now.date().isoformat())
        log.info("morning briefing")
        self.announce(skills.briefing_text(self), "Good morning")

    def _scheduler(self):
        n = 0
        while True:
            time.sleep(1)
            n += 1
            if n % 30 == 0:
                try:
                    self._maybe_briefing()
                except Exception:
                    log.exception("briefing")
            try:
                with self.jobs_lock:
                    jobs = self._jobs()
                    due = [j for j in jobs if j["at"] <= datetime.now()]
                    if due:
                        self._save_jobs([j for j in jobs if j not in due])
                for j in due:
                    late = (datetime.now() - j["at"]).total_seconds() > 600
                    threading.Thread(target=self._ring, args=(j, late), daemon=True).start()
            except Exception:
                log.exception("scheduler")

    def _ring(self, job, late):
        icon = {"timer": "Timer", "alarm": "Alarm", "reminder": "Reminder"}[job["kind"]]
        text = job["text"] + (" (missed while the laptop was off)" if late else "")
        self.notify(icon, text)
        while self.in_session:
            time.sleep(0.5)
        self.cancel.clear()
        self.ui(lambda: self.orb().show("speaking", f"{icon}  ·  click or press Esc to stop", text))
        for _ in range(1 if late or job["kind"] == "reminder" else 3):
            for f in (988, 1319, 988, 1319):
                if self.cancel.is_set() or esc_pressed():
                    break
                winsound.Beep(f, 140)
            if self.cancel.is_set() or esc_pressed() or not speaker.say(text):
                break
            time.sleep(0.6)
        self.ui(lambda: self.orb().hide(2500))
