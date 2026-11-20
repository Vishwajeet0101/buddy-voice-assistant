"""Buddy - a free, Siri-style voice assistant for Windows.

Say "Hey Buddy" (or the name you pick), or press Ctrl+Alt+B. Runs quietly in the tray.
    pythonw buddy.pyw              -> starts (opens Settings the first time)
    pythonw buddy.pyw --settings   -> opens Settings
"""
import ctypes
import ctypes.wintypes as wt
import os
import queue
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import customtkinter as ctk
import pystray
from PIL import Image, ImageDraw, ImageFilter, ImageTk

import apps
import config
from listener import Engine
from orb import Orb
from speech import speaker

log = config.log
NO_WINDOW = 0x08000000
ACCENT, ACCENT_HOVER = "#6a5cff", "#5848e8"
DIM, GREEN, ORANGE, RED = "#9a9aab", "#6ec878", "#f0aa50", "#eb5f5f"
CARD, SOFT, SOFT_HOVER = ("#ffffff", "#20202a"), ("#e9e9f1", "#2c2c38"), ("#dcdce6", "#363644")
STARTUP_LNK = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup/Buddy.lnk"
DESKTOP_LNK = Path.home() / "Desktop" / "Buddy.lnk"
START_MENU_LNK = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Buddy.lnk"
ICON_PATH = config.DATA_DIR / "buddy.ico"


def make_icon(size=256):
    """The glowing gradient orb used for the tray and shortcuts."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    stops = [(79, 124, 255), (160, 124, 255), (255, 95, 162)]
    for i in range(size // 2, 0, -1):
        t = 1 - i / (size / 2)
        a, b = (stops[0], stops[1]) if t < 0.5 else (stops[1], stops[2])
        f = t * 2 if t < 0.5 else (t - 0.5) * 2
        col = tuple(int(a[k] + (b[k] - a[k]) * f) for k in range(3))
        r = i * 0.92
        d.ellipse((size / 2 - r, size / 2 - r, size / 2 + r, size / 2 + r), fill=col + (255,))
    hl = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(hl).ellipse((size * 0.28, size * 0.2, size * 0.58, size * 0.5), fill=(255, 255, 255, 110))
    return Image.alpha_composite(img, hl.filter(ImageFilter.GaussianBlur(size / 20)))


def pythonw():
    return Path(sys.executable).with_name("pythonw.exe")


def make_shortcut(lnk, args=""):
    script = Path(__file__).resolve()
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}'); $s.TargetPath='{pythonw()}'; "
          f"$s.Arguments='\"{script}\" {args}'; $s.WorkingDirectory='{script.parent}'; "
          f"$s.IconLocation='{ICON_PATH}'; $s.Description='Buddy voice assistant'; $s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], creationflags=NO_WINDOW, timeout=30)


def set_startup(on):
    try:
        if on:
            make_shortcut(STARTUP_LNK, "--startup")
        elif STARTUP_LNK.exists():
            STARTUP_LNK.unlink()
    except Exception as e:
        log.warning("startup shortcut: %r", e)


def single_instance():
    ctypes.windll.kernel32.CreateMutexW(None, False, "BuddyVoiceAssistant_v1")
    return ctypes.windll.kernel32.GetLastError() != 183          # ERROR_ALREADY_EXISTS


class App(ctk.CTk):
    def __init__(self, open_settings=False):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        super().__init__()
        self.withdraw()
        self.title("Buddy")
        self.uiq = queue.Queue()
        icon = make_icon(256)
        try:
            if not ICON_PATH.exists():
                icon.save(ICON_PATH, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)])
        except Exception:
            pass
        self._icon_photo = ImageTk.PhotoImage(icon.resize((64, 64)))
        self.after(200, lambda: self.iconphoto(True, self._icon_photo))
        self.protocol("WM_DELETE_WINDOW", self.withdraw)

        self.orb = Orb(self, on_click=lambda: self.engine.stop_session())
        self.engine = Engine(self.ui, lambda: self.orb, self.notify)
        self.engine.quiet_start = "--startup" in sys.argv       # started with Windows: no pop-up
        self.built = False
        apps.load()
        self.engine.start()
        self.start_tray(icon)
        threading.Thread(target=self.hotkey_loop, daemon=True).start()
        self.after(40, self.process_queue)

        s = config.load()
        if s["start_with_windows"]:                  # (re)create it every start, so it always points at this copy
            threading.Thread(target=set_startup, args=(True,), daemon=True).start()
        if not s["setup_done"]:
            threading.Thread(target=self.first_run_shortcuts, daemon=True).start()
        if open_settings or not s["setup_done"]:
            self.after(300, self.show_settings)

    def first_run_shortcuts(self):
        for lnk in (DESKTOP_LNK, START_MENU_LNK):
            try:
                if not lnk.exists():
                    make_shortcut(lnk, "--settings")
            except Exception as e:
                log.warning("shortcut %s: %r", lnk, e)

    # ----- threads -----

    def ui(self, fn):
        self.uiq.put(fn)

    def process_queue(self):
        while True:
            try:
                fn = self.uiq.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                log.exception("ui")
        self.after(40, self.process_queue)

    def notify(self, title, text):
        try:
            self.tray.notify(text[:250], title[:60])
        except Exception:
            pass

    def hotkey_loop(self):
        u = ctypes.windll.user32
        if not u.RegisterHotKey(None, 1, 0x0001 | 0x0002 | 0x4000, 0x42):     # Ctrl+Alt+B
            log.warning("Ctrl+Alt+B is used by another app")
            return
        msg = wt.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == 0x0312:
                self.engine.trigger()

    def start_tray(self, icon):
        def name():
            return config.load().get("assistant_name") or "Buddy"

        def toggle_wake():
            config.update(wake_word=not config.load().get("wake_word", True))
            self.tray.update_menu()
            if self.built:
                self.ui(lambda: self.sw_wake.select() if config.load()["wake_word"] else self.sw_wake.deselect())

        menu = pystray.Menu(
            pystray.MenuItem(lambda i: f"Talk to {name()}   (Ctrl+Alt+B)", lambda: self.engine.trigger(), default=True),
            pystray.MenuItem(lambda i: f'Listen for "Hey {name()}"', toggle_wake,
                             checked=lambda i: bool(config.load().get("wake_word", True))),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Settings", lambda: self.ui(self.show_settings)),
            pystray.MenuItem("Open log file", lambda: os.startfile(config.LOG_PATH)),
            pystray.MenuItem("Help (GitHub)", lambda: webbrowser.open(config.REPO_URL)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: self.ui(self.quit_app)),
        )
        self.tray = pystray.Icon("Buddy", icon.resize((64, 64)), "Buddy voice assistant", menu)
        self.tray.run_detached()

    def quit_app(self):
        try:
            self.tray.stop()
        except Exception:
            pass
        self.destroy()
        os._exit(0)

    # ===================== Settings window =====================

    def show_settings(self):
        if not self.built:
            self.build_settings()
            self.built = True
        self.load_fields()
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(300, lambda: self.attributes("-topmost", False))
        self.focus_force()

    def label(self, parent, text, row, **kw):
        ctk.CTkLabel(parent, text=text, anchor="w").grid(row=row, column=0, sticky="w", padx=(18, 10), pady=7, **kw)

    def card(self, parent, row, title, sub=None):
        f = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=16)
        f.grid(row=row, column=0, sticky="ew", padx=4, pady=(0, 14))
        f.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(f, text=title, font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=18, pady=(14, 0 if sub else 6))
        if sub:
            ctk.CTkLabel(f, text=sub, text_color=DIM, justify="left", anchor="w").grid(
                row=1, column=0, columnspan=3, sticky="w", padx=18, pady=(0, 6))
        return f

    def soft_button(self, parent, text, cmd, width=110):
        return ctk.CTkButton(parent, text=text, width=width, command=cmd, fg_color=SOFT, hover_color=SOFT_HOVER,
                             text_color=("gray10", "gray90"))

    def build_settings(self):
        scale = ctk.ScalingTracker.get_window_scaling(self)
        sh = self.winfo_screenheight() / scale
        w, h = 640, int(min(820, sh - 100))
        self.geometry(f"{w}x{h}+{int((self.winfo_screenwidth() / scale - w) / 2)}+{int((sh - h) / 2) - 10}")
        self.minsize(560, 480)
        self.configure(fg_color=("#f3f3f7", "#121218"))
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=22, pady=(18, 6))
        img = ctk.CTkImage(make_icon(128), size=(46, 46))
        ctk.CTkLabel(head, image=img, text="").pack(side="left")
        txt = ctk.CTkFrame(head, fg_color="transparent")
        txt.pack(side="left", padx=12)
        self.head_title = ctk.CTkLabel(txt, text="Buddy", font=ctk.CTkFont(size=22, weight="bold"), anchor="w")
        self.head_title.pack(anchor="w")
        self.status_label = ctk.CTkLabel(txt, text="", text_color=DIM, anchor="w")
        self.status_label.pack(anchor="w")

        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=16, pady=(4, 0))
        body.grid_columnconfigure(0, weight=1)

        self.welcome = ctk.CTkLabel(body, text="", justify="left", anchor="w", wraplength=560)
        self.welcome.grid(row=0, column=0, sticky="ew", padx=8, pady=(0, 12))

        # --- assistant ---
        a = self.card(body, 1, "Your assistant")
        self.label(a, "Name (wake word)", 1)
        self.e_name = ctk.CTkEntry(a, width=220)
        self.e_name.grid(row=1, column=1, sticky="w", pady=7)
        self.label(a, "Your name", 2)
        self.e_user = ctk.CTkEntry(a, width=220, placeholder_text="so I can call you by name")
        self.e_user.grid(row=2, column=1, sticky="w", pady=7)
        self.label(a, "Voice", 3)
        vrow = ctk.CTkFrame(a, fg_color="transparent")
        vrow.grid(row=3, column=1, sticky="w", pady=7)
        self.seg_voice = ctk.CTkSegmentedButton(vrow, values=["Male", "Female"], selected_color=ACCENT,
                                                selected_hover_color=ACCENT_HOVER)
        self.seg_voice.pack(side="left")
        self.soft_button(vrow, "▶  Test voice", self.test_voice).pack(side="left", padx=10)
        self.label(a, "Greetings", 4)
        self.e_greet = ctk.CTkEntry(a)
        self.e_greet.grid(row=4, column=1, sticky="ew", padx=(0, 18), pady=7)
        ctk.CTkLabel(a, text="One is picked at random when you call me. Separate them with  |", text_color=DIM,
                     anchor="w").grid(row=5, column=1, sticky="w", pady=(0, 6))
        self.label(a, "City for weather", 6)
        self.e_city = ctk.CTkEntry(a, width=220, placeholder_text="empty = detect automatically")
        self.e_city.grid(row=6, column=1, sticky="w", pady=(7, 16))

        # --- AI ---
        b = self.card(body, 2, "AI brain (free)",
                      "Needed only for questions. Laptop commands work without it. One key is enough.\n"
                      "Groq = fastest answers.  Gemini = can also look at your screen.  Both are free, no card.")
        self.label(b, "Groq key", 2)
        self.e_groq = ctk.CTkEntry(b, show="•", placeholder_text="gsk_...")
        self.e_groq.grid(row=2, column=1, sticky="ew", pady=7)
        self.soft_button(b, "Get free key", lambda: webbrowser.open("https://console.groq.com/keys")).grid(
            row=2, column=2, padx=(8, 18))
        self.label(b, "Gemini key", 3)
        self.e_gemini = ctk.CTkEntry(b, show="•", placeholder_text="AIza...")
        self.e_gemini.grid(row=3, column=1, sticky="ew", pady=7)
        self.soft_button(b, "Get free key", lambda: webbrowser.open("https://aistudio.google.com/apikey")).grid(
            row=3, column=2, padx=(8, 18))
        trow = ctk.CTkFrame(b, fg_color="transparent")
        trow.grid(row=4, column=1, columnspan=2, sticky="w", pady=(4, 14))
        self.soft_button(trow, "Test AI", self.test_ai).pack(side="left")
        self.ai_msg = ctk.CTkLabel(trow, text="", text_color=DIM, justify="left", anchor="w", wraplength=380)
        self.ai_msg.pack(side="left", padx=10)

        # --- behaviour ---
        c = self.card(body, 3, "Behaviour")
        self.sw_wake = ctk.CTkSwitch(c, text='Always listen for "Hey <name>"  (pauses during calls and when locked)',
                                     progress_color=ACCENT)
        self.sw_follow = ctk.CTkSwitch(c, text="Keep listening a few seconds after answering (follow-up questions)",
                                       progress_color=ACCENT)
        self.sw_speak = ctk.CTkSwitch(c, text="Speak answers out loud", progress_color=ACCENT)
        self.sw_startup = ctk.CTkSwitch(c, text="Start with Windows (and keep working after sleep)", progress_color=ACCENT)
        self.sw_brief = ctk.CTkSwitch(c, text="Morning briefing: weather, reminders and news when I first use the laptop",
                                      progress_color=ACCENT)
        for i, sw in enumerate((self.sw_wake, self.sw_follow, self.sw_speak, self.sw_startup, self.sw_brief)):
            sw.grid(row=1 + i, column=0, columnspan=3, sticky="w", padx=18, pady=(6, 16 if i == 4 else 6))

        # --- only my voice ---
        v = self.card(body, 4, "Only my voice",
                      "Teach me your voice, so other people, the TV or YouTube can't wake me.")
        vrow2 = ctk.CTkFrame(v, fg_color="transparent")
        vrow2.grid(row=2, column=0, columnspan=3, sticky="w", padx=18, pady=(4, 6))
        self.btn_train = ctk.CTkButton(vrow2, text="🎙  Train my voice", width=150, fg_color=ACCENT,
                                       hover_color=ACCENT_HOVER, command=self.train_voice)
        self.btn_train.pack(side="left")
        self.sw_only = ctk.CTkSwitch(vrow2, text="Only wake for my voice", progress_color=ACCENT)
        self.sw_only.pack(side="left", padx=14)
        self.train_msg = ctk.CTkLabel(v, text="", text_color=DIM, justify="left", anchor="w", wraplength=540,
                                      font=ctk.CTkFont(size=14))
        self.train_msg.grid(row=3, column=0, columnspan=3, sticky="w", padx=18, pady=(0, 14))

        # --- contacts & routines ---
        r = self.card(body, 5, "WhatsApp contacts & routines")
        ctk.CTkLabel(r, text="WhatsApp contacts (one per line:  Mom = +91 98765 43210). Saved contacts get messages\n"
                             "sent directly; anyone else is found by search and I ask before sending.",
                     text_color=DIM, justify="left", anchor="w").grid(row=1, column=0, columnspan=3, sticky="w", padx=18)
        self.t_contacts = ctk.CTkTextbox(r, height=80)
        self.t_contacts.grid(row=2, column=0, columnspan=3, sticky="ew", padx=18, pady=(4, 10))
        ctk.CTkLabel(r, text="Routines - say \"start study mode\" (one per line:  name mode = command; command; ...)",
                     text_color=DIM, justify="left", anchor="w").grid(row=3, column=0, columnspan=3, sticky="w", padx=18)
        self.t_routines = ctk.CTkTextbox(r, height=90)
        self.t_routines.grid(row=4, column=0, columnspan=3, sticky="ew", padx=18, pady=(4, 16))

        # --- how to ---
        d = self.card(body, 6, "How to use")
        self.howto = ctk.CTkLabel(d, text="", justify="left", anchor="w", text_color=("gray20", "gray80"))
        self.howto.grid(row=1, column=0, columnspan=3, sticky="w", padx=18, pady=(0, 16))

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=22, pady=14)
        ctk.CTkButton(foot, text="Save", width=130, height=38, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      command=self.save).pack(side="left")
        self.soft_button(foot, "Close", self.withdraw, width=90).pack(side="left", padx=10)
        self.save_msg = ctk.CTkLabel(foot, text="", text_color=DIM)
        self.save_msg.pack(side="left", padx=6)
        ctk.CTkLabel(foot, text=f"v{config.VERSION}", text_color=DIM).pack(side="right")
        self.tick()

    def load_fields(self):
        s = config.load()
        for e, key in ((self.e_name, "assistant_name"), (self.e_user, "user_name"), (self.e_greet, "greetings"),
                       (self.e_city, "city"), (self.e_groq, "groq_key"), (self.e_gemini, "gemini_key")):
            e.delete(0, "end")
            if s.get(key):
                e.insert(0, s[key])
        self.seg_voice.set("Female" if s["voice"] == "female" else "Male")
        for sw, key in ((self.sw_wake, "wake_word"), (self.sw_follow, "follow_up"), (self.sw_speak, "speak_replies"),
                        (self.sw_startup, "start_with_windows"), (self.sw_brief, "morning_briefing"),
                        (self.sw_only, "only_my_voice")):
            sw.select() if s.get(key) else sw.deselect()
        for box, key in ((self.t_contacts, "contacts"), (self.t_routines, "routines")):
            box.delete("1.0", "end")
            box.insert("1.0", s.get(key) or "")
        self.train_msg.configure(text="Your voice is trained." if s.get("voice_print") else
                                 "Not trained yet - click the button and say 3 short sentences.")
        self.refresh_texts()

    def train_voice(self):
        self.btn_train.configure(state="disabled")
        self.train_msg.configure(text_color=("gray10", "gray95"))

        def step(msg):
            self.ui(lambda: self.train_msg.configure(text=msg))

        def work():
            ok, msg = self.engine.train_voice(step)

            def done():
                self.btn_train.configure(state="normal")
                self.train_msg.configure(text=msg, text_color=GREEN if ok else ORANGE)
                if ok:
                    self.sw_only.select()
            self.ui(done)
        threading.Thread(target=work, daemon=True).start()

    def refresh_texts(self):
        s = config.load()
        n = s["assistant_name"]
        self.head_title.configure(text=f"{n}  ·  voice assistant")
        self.welcome.configure(text=(
            f"Welcome! I'm {n}, your free voice assistant - like Siri, for Windows.\n"
            "1. Paste a free AI key below (Groq is the fastest).  2. Pick my name and voice.  3. Click Save.\n"
            f"Then just say \"Hey {n}\"." if not s["setup_done"] else ""))
        self.welcome.grid() if not s["setup_done"] else self.welcome.grid_remove()
        self.howto.configure(text=(
            f"• Say \"Hey {n}\" - I'll answer, then just talk.  Or say it in one go: \"Hey {n}, open Chrome\".\n"
            "• No wake word? Press Ctrl + Alt + B (or click my tray icon).\n"
            "• Esc or a click on the popup stops me talking.\n"
            "• Say \"stop\" or \"Hey " + n + "\" while I'm talking to interrupt me.\n"
            "• Try: what's the weather · set a timer for 5 minutes · wake me up at 7 am · volume 40\n"
            "  play Believer on YouTube · send WhatsApp to Rahul saying I'm coming · good morning\n"
            "  new tab · scroll down · close this window · open my resume PDF · start study mode\n"
            "  what's on my screen · remind me to call mom at 6 pm · bhai bluetooth band kar do\n"
            f"• Notes are saved in Documents\\Buddy Notes.txt.  Log: {config.LOG_PATH}"))

    def tick(self):
        if self.state() != "withdrawn":
            self.status_label.configure(text=self.engine.status)
        self.after(1000, self.tick)

    def save(self):
        s = config.load()
        name = " ".join(self.e_name.get().split()).title() or "Buddy"
        problem = self.engine.check_name(name) if self.engine.ready else None
        if problem:
            self.save_msg.configure(text=problem, text_color=ORANGE, wraplength=330)
            return False
        s.update(assistant_name=name, user_name=self.e_user.get().strip(),
                 greetings=self.e_greet.get().strip() or config.DEFAULTS["greetings"], city=self.e_city.get().strip(),
                 groq_key=self.e_groq.get().strip(), gemini_key=self.e_gemini.get().strip(),
                 voice=self.seg_voice.get().lower(), wake_word=bool(self.sw_wake.get()),
                 follow_up=bool(self.sw_follow.get()), speak_replies=bool(self.sw_speak.get()),
                 start_with_windows=bool(self.sw_startup.get()), setup_done=True,
                 morning_briefing=bool(self.sw_brief.get()),
                 only_my_voice=bool(self.sw_only.get()) and bool(s.get("voice_print")),
                 contacts=self.t_contacts.get("1.0", "end").strip(),
                 routines=self.t_routines.get("1.0", "end").strip())
        if self.sw_only.get() and not s.get("voice_print"):
            self.sw_only.deselect()
            self.train_msg.configure(text="Train your voice first (button above).", text_color=ORANGE)
        config.save(s)
        threading.Thread(target=set_startup, args=(s["start_with_windows"],), daemon=True).start()
        threading.Thread(target=self.engine.warm_greetings, daemon=True).start()
        self.engine.brain.history = []
        self.tray.update_menu()
        self.refresh_texts()
        self.save_msg.configure(text=f'Saved! Say "Hey {name}".', text_color=GREEN)
        return True

    def test_voice(self):
        if not self.save():
            return
        n = config.load()["assistant_name"]
        threading.Thread(target=speaker.say, args=(f"Hi! I'm {n}. Just say hey {n} whenever you need me.",),
                         daemon=True).start()

    def test_ai(self):
        if not self.save():
            return
        self.ai_msg.configure(text="Testing...", text_color=DIM)

        def work():
            res = self.engine.brain.test()
            if not res:
                msg, col = "Paste at least one key first.", ORANGE
            else:
                msg = "\n".join(f"{n}: {m}" for n, ok, m in res)
                col = GREEN if all(ok for _, ok, _ in res) else (ORANGE if any(ok for _, ok, _ in res) else RED)
            self.ui(lambda: self.ai_msg.configure(text=msg, text_color=col))
        threading.Thread(target=work, daemon=True).start()


def main():
    if not single_instance():
        # Already running: ask it to open Settings (via a tiny marker file) and quit.
        (config.DATA_DIR / "open_settings").touch()
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    log.info("Buddy %s starting (portable=%s)", config.VERSION, config.PORTABLE)
    app = App(open_settings="--settings" in sys.argv)

    def watch_marker():
        marker = config.DATA_DIR / "open_settings"
        if marker.exists():
            marker.unlink(missing_ok=True)
            app.show_settings()
        app.after(700, watch_marker)
    (config.DATA_DIR / "open_settings").unlink(missing_ok=True)
    app.after(700, watch_marker)
    app.mainloop()


if __name__ == "__main__":
    main()
