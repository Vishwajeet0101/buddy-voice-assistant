"""Finds and opens any installed app by name (works on anyone's laptop - reads the Start menu)."""
import difflib
import json
import os
import re
import subprocess
import threading
from pathlib import Path

import psutil

import config

NO_WINDOW = 0x08000000
CACHE = config.DATA_DIR / "apps.json"
HOME = Path.home()

ALIASES = {
    "chrome": "google chrome", "vs code": "visual studio code", "vscode": "visual studio code",
    "code": "visual studio code", "edge": "microsoft edge", "word": "word", "excel": "excel",
    "powerpoint": "powerpoint", "ppt": "powerpoint", "teams": "microsoft teams", "store": "microsoft store",
    "calc": "calculator", "files": "file explorer", "explorer": "file explorer", "my computer": "file explorer",
    "this pc": "file explorer", "camera": "camera", "photos": "photos", "settings": "settings",
    "paint": "paint", "clock": "clock", "terminal": "terminal", "notepad": "notepad", "spotify": "spotify",
    "whatsapp": "whatsapp", "control panel": "control panel", "task manager": "task manager",
}
BUILTIN = {   # always available, even before the Start menu list is ready
    "notepad": "notepad.exe", "calculator": "calc.exe", "paint": "mspaint.exe", "command prompt": "cmd.exe",
    "cmd": "cmd.exe", "powershell": "powershell.exe", "task manager": "taskmgr.exe", "file explorer": "explorer.exe",
    "settings": "ms-settings:", "control panel": "control.exe", "terminal": "wt.exe",
    "camera": "microsoft.windows.camera:", "clock": "ms-clock:", "snipping tool": "ms-screenclip:",
    "microsoft store": "ms-windows-store:", "photos": "ms-photos:",
    "wifi settings": "ms-settings:network-wifi", "wifi": "ms-settings:network-wifi",
    "bluetooth": "ms-settings:bluetooth", "bluetooth settings": "ms-settings:bluetooth",
    "display settings": "ms-settings:display", "sound settings": "ms-settings:sound",
    "battery settings": "ms-settings:batterysaver", "windows update": "ms-settings:windowsupdate",
}
FOLDERS = {
    "downloads": HOME / "Downloads", "documents": HOME / "Documents", "desktop": HOME / "Desktop",
    "pictures": HOME / "Pictures", "photos folder": HOME / "Pictures", "music": HOME / "Music",
    "videos": HOME / "Videos", "screenshots": HOME / "Pictures" / "Screenshots",
    "recycle bin": "shell:RecycleBinFolder", "notes": config.NOTES_PATH,
}
WEBSITES = {
    "youtube": "https://www.youtube.com", "github": "https://github.com", "gmail": "https://mail.google.com",
    "google": "https://www.google.com", "chatgpt": "https://chatgpt.com", "claude": "https://claude.ai",
    "gemini": "https://gemini.google.com", "linkedin": "https://www.linkedin.com",
    "instagram": "https://www.instagram.com", "facebook": "https://www.facebook.com", "twitter": "https://x.com",
    "x": "https://x.com", "whatsapp web": "https://web.whatsapp.com", "netflix": "https://www.netflix.com",
    "amazon": "https://www.amazon.in", "flipkart": "https://www.flipkart.com", "leetcode": "https://leetcode.com",
    "stack overflow": "https://stackoverflow.com", "stackoverflow": "https://stackoverflow.com",
    "google drive": "https://drive.google.com", "drive": "https://drive.google.com",
    "maps": "https://maps.google.com", "google maps": "https://maps.google.com",
    "calendar": "https://calendar.google.com", "classroom": "https://classroom.google.com",
    "wikipedia": "https://www.wikipedia.org", "reddit": "https://www.reddit.com", "hotstar": "https://www.hotstar.com",
    "spotify web": "https://open.spotify.com", "translate": "https://translate.google.com",
}

_apps = {}          # lowercase name -> AppID (for shell:AppsFolder)
_lock = threading.Lock()


def _norm(name):
    name = name.lower().replace("&", "and")
    name = re.sub(r"[^\w\s+#.]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def refresh():
    """Reads the Start menu (Get-StartApps). Takes ~1-2 s, so it runs in the background and is cached."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=30, creationflags=NO_WINDOW)
        items = json.loads(r.stdout or "[]")
        if isinstance(items, dict):
            items = [items]
        found = {}
        for it in items:
            n = _norm(it.get("Name") or "")
            if n and it.get("AppID") and not re.search(r"\b(uninstall|readme|help|documentation|release notes)\b", n):
                found[n] = it["AppID"]
        if found:
            with _lock:
                _apps.clear()
                _apps.update(found)
            CACHE.write_text(json.dumps(found), encoding="utf-8")
            config.log.info("found %d apps", len(found))
    except Exception as e:
        config.log.warning("app list failed: %r", e)


def load():
    try:
        with _lock:
            _apps.update(json.loads(CACHE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    threading.Thread(target=refresh, daemon=True).start()


def find(name):
    """Returns (label, target) for an app, folder or website, or None."""
    q = _norm(re.sub(r"^(the|my)\s+", "", name.strip().lower()))
    q = re.sub(r"\s+(app|application|website|site|folder)$", "", q)
    q = ALIASES.get(q, ALIASES.get(q.replace(" ", ""), q))
    if q in FOLDERS:
        return q.title(), str(FOLDERS[q])
    if q in BUILTIN and BUILTIN[q].startswith("ms-settings:"):       # wifi / bluetooth / display settings...
        return q.title(), BUILTIN[q]
    with _lock:
        apps = dict(_apps)
    nospace = {k.replace(" ", ""): k for k in apps}
    hit = (q if q in apps else nospace.get(q.replace(" ", ""))
           or next((k for k in sorted(apps, key=len) if k.startswith(q + " ") or k.startswith(q)), None)
           or next((k for k in sorted(apps, key=len) if len(q) >= 4 and q in k), None))
    if not hit:
        close = difflib.get_close_matches(q, list(apps), n=1, cutoff=0.8)
        hit = close[0] if close else None
    if hit:
        return hit.title(), "shell:AppsFolder\\" + apps[hit]
    if q in BUILTIN:
        return q.title(), BUILTIN[q]
    for key in (q, q.replace(" ", "")):
        if key in WEBSITES:
            return key.title(), WEBSITES[key]
    if re.fullmatch(r"[\w-]+(\.[\w-]+)+(/\S*)?", q):
        return q, "https://" + q
    return None


def open_target(target):
    os.startfile(target)


def close(name):
    """Closes a running app by name. Returns how many windows/processes were closed."""
    q = _norm(name)
    q = ALIASES.get(q, q)
    exe_hints = {q.replace(" ", ""), q.split(" ")[-1], q.split(" ")[0]}
    special = {"google chrome": "chrome", "microsoft edge": "msedge", "visual studio code": "code",
               "file explorer": None, "word": "winword", "powerpoint": "powerpnt", "microsoft teams": "ms-teams",
               "calculator": "calculatorapp", "camera": "windowscamera", "settings": "systemsettings"}
    if q in special:
        if special[q] is None:
            return 0
        exe_hints = {special[q]}
    count = 0
    for p in psutil.process_iter(["name", "pid"]):
        n = (p.info["name"] or "").lower().removesuffix(".exe")
        if n and n in exe_hints and p.info["pid"] != os.getpid() and n not in ("python", "pythonw", "explorer"):
            try:
                p.terminate()
                count += 1
            except psutil.Error:
                pass
    return count
