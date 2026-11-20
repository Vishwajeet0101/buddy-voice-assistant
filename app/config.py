"""Paths, settings and the log file."""
import json
import logging
import logging.handlers
import os
import sys
import threading
import time
from pathlib import Path

APP_NAME = "Buddy"
VERSION = "1.0.3"
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
}
_lock = threading.Lock()


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
    return s


def save(s):
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
