"""The ears: waits for "Hey Buddy", listens to what you say, and runs one conversation at a time.

Offline: wake word + speech-to-text with Vosk (small Indian-English model, ~40 MB).
When the offline text looks unsure (Hinglish, names, songs...) the recording is re-checked online (Groq/Gemini).
"""
import array
import io
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
from speech import esc_pressed, speaker

log = config.log
RATE = 16000
BLOCK = RATE // 10                 # 0.1 s per read
STOP_WORDS = {"stop", "cancel", "nothing", "never mind", "nevermind", "bas", "rehne do", "chhodo", "no", "nope",
              "thank you", "thanks", "ok thanks", "okay thanks", "thank you so much", "shukriya", "that's all",
              "that's it", "bye", "goodbye", "go away", "shut up", "be quiet"}


def rms(data):
    a = array.array("h", data)
    return (sum(s * s for s in a) / max(1, len(a))) ** 0.5


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
        self.name = "buddy"
        self.pending = None             # a skill asked a question; the next answer goes to it
        self.show_text = None
        self._after = []
        self.jobs_lock = threading.Lock()

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
        name = config.load()["assistant_name"]
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

    def _recognizer(self, grammar=None):
        import vosk
        rec = vosk.KaldiRecognizer(self.model, RATE, json.dumps(grammar)) if grammar else vosk.KaldiRecognizer(self.model, RATE)
        rec.SetWords(True)
        return rec

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
        rec = self._recognizer()
        frames = []
        last_check = started = time.time()
        silent_for = 99.0                  # seconds since anything louder than the room was heard
        pre = []
        while True:
            if self.trigger_event.is_set():
                self.trigger_event.clear()
                self.session(stream, greet=False)
                return
            if time.time() - last_check > 2.5:
                last_check = time.time()
                if self.pause_reason() or self._wake_name() != self.name:
                    return
            data = bytes(stream.read(BLOCK)[0])
            level = rms(data)
            self.levels.append(level)
            del self.levels[:-80]
            silent_for = 0.0 if level > self.threshold() * 0.6 else silent_for + 0.1
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
            after = self.wake_split(text) if text else None
            if after is None:
                if final:
                    frames = []
                    if time.time() - started > 120:      # fresh recogniser now and then
                        return
                continue
            log.info("wake word heard: %r", text)
            if final:
                if after.strip():                         # whole command already said: "Hey Buddy what's the time"
                    words = r.get("result", [])[-len(after.split()):]
                    self.session(stream, greet=False, heard=(after, words, to_wav(frames)))
                else:
                    self.session(stream, greet=True)
            else:                                         # still talking - finish the sentence with this recogniser
                self.session(stream, greet=None, rec=rec, frames=frames)
            return

    def threshold(self):
        if not self.levels:
            return 400
        return max(300, sorted(self.levels)[len(self.levels) // 5] * 2.5)

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

    def session(self, stream, greet=True, heard=None, rec=None, frames=None):
        """One conversation. greet=None: woke mid-sentence - finish it and greet only if nothing followed the name."""
        self.in_session = True
        self.cancel.clear()
        self.pending = None
        try:
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
                finished = self.respond(text)
                heard = None
                if not finished or self.cancel.is_set():
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

    def listen(self, stream, wait_seconds, rec=None, frames=None, quiet_end=1.0):
        """Records one sentence. Returns (offline_text, words, wav) or None if nothing was said.
        rec/frames: continue a sentence the wake-word listener already started hearing."""
        started = rec is not None
        rec = rec or self._recognizer()
        frames, texts, words = list(frames or []), [], []
        quiet = 0.0
        t0 = time.time()
        threshold = self.threshold()

        def feed(data):
            frames.append(data)
            if rec.AcceptWaveform(data):
                r = json.loads(rec.Result())
                if r.get("text"):
                    texts.append(r["text"])
                    words.extend(r.get("result", []))
                return None
            return json.loads(rec.PartialResult()).get("partial", "")

        while True:
            if self.cancel.is_set() or esc_pressed():
                self.cancel.set()
                return None
            data = bytes(stream.read(BLOCK)[0])
            level = rms(data)
            self.ui(lambda lv=min(1.0, level / (threshold * 4)): self.orb().set_level(lv))
            partial = feed(data)
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
        text = " ".join(texts).strip()
        if not text and len(frames) < 8:
            return None
        return text, words, to_wav(frames)

    def understand(self, text, words, wav):
        """Final text of what was said (offline, or re-checked online when unsure). None = stop."""
        confs = [w["conf"] for w in words if w["word"] != "[unk]"]
        avg = sum(confs) / len(confs) if confs else 0
        k = skills.kind(text) if text else None
        sure = (text and k == "fast" and avg >= 0.5) or (
            text and k is None and avg >= 0.88 and min(confs) >= 0.6 and len(confs) >= 3)
        if self.pending and text and avg >= 0.8:
            sure = True
        log.info("heard offline: %r (avg conf %.2f, %s)", text, avg, k)
        final = text
        if not sure and self.brain.has_ai():
            self.ui(lambda: self.orb().update("thinking", self.title("thinking"), (text or "...") + " ..."))
            try:
                online = self.brain.transcribe(wav)
                log.info("heard online: %r", online)
                final = online or text
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

    def respond(self, text):
        """Runs a command or asks the AI, and says the answer. Returns False if you stopped it."""
        if not text:
            return True
        self.show_text, self._after = None, []
        shown = text if len(text) < 120 else text[:117] + "..."
        self.ui(lambda: self.orb().update("thinking", self.title("thinking"), f"“{shown}”"))
        reply = skills.run(text, self)
        speak = config.load().get("speak_replies", True)
        if reply is None:
            if not self.brain.has_ai():
                reply = ("I can do laptop tasks without the internet, but to answer questions I need a free AI key. "
                         "Right-click my tray icon and open Settings to add one.")
            else:
                on_text = lambda t: self.ui(lambda: self.orb().update("speaking", self.title(), t))
                if speak:
                    _, finished = speaker.say_stream(self.brain.ask_stream(text), on_text=on_text)
                else:
                    try:
                        on_text(self.brain.ask(text))
                    except Exception as e:
                        on_text(getattr(e, "friendly", str(e)))
                    finished = True
                self._run_after()
                return finished
        shown = self.show_text or reply
        self.ui(lambda: self.orb().update("speaking", self.title(), shown))
        finished = speaker.say(reply) if speak else True
        self._run_after()
        return finished

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

    def _scheduler(self):
        while True:
            time.sleep(1)
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
