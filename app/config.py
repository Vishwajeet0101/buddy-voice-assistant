"""Paths, settings and the log file."""
import base64
import ctypes
import json
import logging
import logging.handlers
import os
import sys
import threading
import time
from pathlib import Path

APP_NAME = "Buddy"
VERSION = "1.1.0"
REPO_URL = "https://github.com/Vishwajeet0101/buddy-voice-assistant"

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
DATA_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
DATA_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_PATH = DATA_DIR / "settings.json"
LOG_PATH = DATA_DIR / "buddy.log"
NOTES_PATH = Path.home() / "Documents" / "Buddy Notes.txt"

MODEL_NAME = "vosk-model-small-en-in-0.4"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"
PORTABLE = (ROOT / "python" / "pythonw.exe").exists()


def model_dir():
    """The offline speech model: bundled next to the app (release ZIP) or downloaded into AppData."""
    for base in (ROOT / "models", DATA_DIR / "models"):
        if (base / MODEL_NAME / "am").exists():
            return base / MODEL_NAME
    return None


DEFAULTS = {
    "setup_done": False,
    "assistant_name": "Buddy",        # say "Hey <name>"
    "user_name": "",
    "voice": "male",                  # male / female
    "wake_word": True,
    "follow_up": True,                # keep listening a few seconds after answering
    "speak_replies": True,
    "greetings": "Hi there!|Yes?|How can I help?|I'm listening.",
    "groq_key": "",                   # fastest free AI (console.groq.com/keys)
    "gemini_key": "",                 # free AI that can also see your screen (aistudio.google.com/apikey)
    "city": "",                       # for weather; empty = guess from your internet connection
    "start_with_windows": True,
    "alarms": [],                     # [{"at": "2026-11-20 07:00", "text": "Alarm"}]
    "only_my_voice": False,           # ignore "Hey Buddy" from other people / videos (needs voice training)
    "voice_print": [],                # average voice fingerprint from training
    "voice_match": 0.55,              # how similar a voice must be (0-1)
    "morning_briefing": False,        # speak a short briefing when the laptop is first used in the morning
    "last_briefing": "",
    "contacts": "",                   # "Mom = +91 98xxxxxxx" one per line, for WhatsApp
    "routines": ("study mode = open visual studio code; set volume to 30; dark mode on; set a timer for 25 minutes\n"
                 "movie mode = set volume to 80; brightness 100; open netflix\n"
                 "work mode = open chrome; open visual studio code; open whatsapp"),
}
SPK_MODEL_NAME = "vosk-model-spk-0.4"
SPK_MODEL_URL = f"https://alphacephei.com/vosk/models/{SPK_MODEL_NAME}.zip"


def spk_model_dir():
    for base in (ROOT / "models", DATA_DIR / "models"):
        if (base / SPK_MODEL_NAME / "final.ext.raw").exists():
            return base / SPK_MODEL_NAME
    return None
_lock = threading.Lock()
SECRET_KEYS = ("groq_key", "gemini_key")


# ----- API keys are encrypted with Windows (DPAPI): only your Windows account on this PC can read them -----

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data, protect):
    src = _Blob(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
    out = _Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(out)):     # 0x1 = no UI
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _encrypt(value):
    if not value or value.startswith("dpapi:"):
        return value
    return "dpapi:" + base64.b64encode(_dpapi(value.encode(), True)).decode()


def _decrypt(value):
    if not value or not value.startswith("dpapi:"):
        return value
    try:
        return _dpapi(base64.b64decode(value[6:]), False).decode()
    except Exception:
        return ""                     # copied from another PC/account: can't be read - paste the key again


def load():
    s = dict(DEFAULTS)
    for _ in range(10):
        try:
            with _lock:
                if SETTINGS_PATH.exists():
                    s.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
            break
        except (OSError, ValueError):
            time.sleep(0.05)
    for k in SECRET_KEYS:
        s[k] = _decrypt(s.get(k, ""))
    return s


def save(s):
    s = dict(s)
    for k in SECRET_KEYS:
        s[k] = _encrypt(s.get(k, ""))
    with _lock:
        tmp = SETTINGS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, indent=2), encoding="utf-8")
        for attempt in range(20):
            try:
                os.replace(tmp, SETTINGS_PATH)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)


def update(**changes):
    s = load()
    s.update(changes)
    save(s)
    return s


def _make_log():
    log = logging.getLogger("buddy")
    log.setLevel(logging.INFO)
    h = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=500_000, backupCount=1, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(h)
    if sys.stdout and not str(sys.executable).lower().endswith("pythonw.exe"):
        log.addHandler(logging.StreamHandler(sys.stdout))
    return log


log = _make_log()
