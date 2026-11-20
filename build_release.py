"""Builds dist/Buddy-<version>-windows.zip - unzip and double-click, nothing to install.

Contains: embeddable Python + tkinter (copied from this PC's Python), the packages, the app and the offline
speech model. Run with the same Python version you want to ship:   python build_release.py
"""
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "app"))
import config  # noqa: E402

CACHE = HERE / ".build_cache"
DIST = HERE / "dist"
OUT = DIST / "Buddy"
PYVER = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
PYHOME = Path(sys.base_prefix)
PACKAGES = ["vosk", "sounddevice", "edge-tts", "customtkinter", "pystray", "Pillow", "psutil"]
DOWNLOADS = {
    "python.zip": f"https://www.python.org/ftp/python/{PYVER}/python-{PYVER}-embed-amd64.zip",
    "model.zip": config.MODEL_URL,
    "spk.zip": config.SPK_MODEL_URL,
}

START_BAT = r"""@echo off
start "" "%~dp0python\pythonw.exe" "%~dp0app\buddy.pyw" --settings
"""

README = """BUDDY - a free voice assistant for Windows (like Siri)
====================================================

FIRST TIME
1. Unzip this whole folder somewhere you'll keep it (e.g. Documents\\Buddy). Don't run it from inside the ZIP.
2. Double-click "Start Buddy".
   If Windows says "Windows protected your PC": click "More info" -> "Run anyway".
3. The Settings window opens. Paste a FREE AI key (Groq is fastest: console.groq.com/keys), pick a name and
   voice, and click Save. A "Buddy" shortcut appears on your Desktop and it starts with Windows.

USE
- Say "Hey Buddy" -> Buddy answers -> say what you want.  Or in one go: "Hey Buddy, open Chrome".
- Ctrl + Alt + B = talk without saying the name.   Esc or a click on the popup = stop talking.
- Try: what's the weather / set a timer for 5 minutes / wake me up at 7 am / volume 40 /
       play Believer on YouTube / take a note / what's on my screen / any question.

Settings: right-click the round tray icon (bottom-right, maybe under the ^ arrow).
Help & updates: """ + config.REPO_URL + "\n"


def fetch(name, url):
    dest = CACHE / name
    if not dest.exists():
        print(f"downloading {name} ...", flush=True)
        CACHE.mkdir(exist_ok=True)
        tmp = dest.with_suffix(".part")
        with urllib.request.urlopen(url) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        tmp.rename(dest)
    return dest


def main():
    files = {n: fetch(n, u) for n, u in DOWNLOADS.items()}
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    py = OUT / "python"

    print("python ...", flush=True)
    with zipfile.ZipFile(files["python.zip"]) as z:
        z.extractall(py)
    pth = next(py.glob("python*._pth"))
    lines = [ln for ln in pth.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    pth.write_text("\n".join(lines + ["Lib", "Lib\\site-packages", "..\\app", "import site", ""]))

    print("tkinter ...", flush=True)          # the embeddable Python has no tkinter - copy it from this install
    for dll in ("_tkinter.pyd", "tcl86t.dll", "tk86t.dll", "zlib1.dll"):
        shutil.copy2(PYHOME / "DLLs" / dll, py / dll)
    shutil.copytree(PYHOME / "Lib" / "tkinter", py / "Lib" / "tkinter",
                    ignore=shutil.ignore_patterns("__pycache__", "test*"))
    for sub in ("tcl8.6", "tk8.6", "tcl8"):
        shutil.copytree(PYHOME / "tcl" / sub, py / "tcl" / sub, ignore=shutil.ignore_patterns("demos", "images", "msgs"))

    print("packages ...", flush=True)
    site = py / "Lib" / "site-packages"
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                    "--only-binary=:all:", "--no-compile", "--target", str(site), *PACKAGES], check=True)
    shutil.rmtree(site / "bin", ignore_errors=True)
    for junk in list(site.rglob("__pycache__")) + list(site.glob("*.dist-info/RECORD")):
        shutil.rmtree(junk) if junk.is_dir() else junk.unlink()

    print("app + model ...", flush=True)
    shutil.copytree(HERE / "app", OUT / "app", ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("model.zip", "spk.zip"):
        with zipfile.ZipFile(files[name]) as z:
            z.extractall(OUT / "models")
    (OUT / "Start Buddy.bat").write_text(START_BAT)
    (OUT / "README.txt").write_text(README, encoding="utf-8")
    shutil.copy2(HERE / "LICENSE", OUT / "LICENSE.txt")

    print("checking ...", flush=True)          # make sure everything imports with the bundled Python
    r = subprocess.run([str(py / "python.exe"), "-c",
                        "import tkinter, customtkinter, pystray, PIL, psutil, sounddevice, vosk, edge_tts, "
                        "config, speech, orb, brain, apps, skills, listener; print('imports ok')"],
                       capture_output=True, text=True, cwd=OUT / "app")
    print(r.stdout.strip() or r.stderr[-1500:])
    if r.returncode:
        sys.exit("bundled Python can't import everything - see above")

    print("zipping ...", flush=True)
    zpath = DIST / f"Buddy-{config.VERSION}-windows.zip"
    zpath.unlink(missing_ok=True)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(OUT.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                z.write(p, Path("Buddy") / p.relative_to(OUT))
    print(f"done: {zpath}  ({zpath.stat().st_size / 1_048_576:.0f} MB)")


if __name__ == "__main__":
    main()
