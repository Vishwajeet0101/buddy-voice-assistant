"""The floating, glowing popup in the bottom-right corner. Only call its methods on the UI thread."""
import ctypes
import ctypes.wintypes as wt
import math
import tkinter as tk

user32 = ctypes.windll.user32


class Orb:
    KEY = "#010203"                    # this colour is made transparent (gives the rounded corners)
    PANEL = "#16161e"
    BORDER = "#2e2e40"
    COLORS = ["#4f7cff", "#a07cff", "#ff5fa2", "#5fd3ff"]

    def __init__(self, root, on_click):
        self.root = root
        self.s = max(1.0, root.winfo_fpixels("1i") / 96)
        self.W = int(390 * self.s)
        self.H = int(100 * self.s)
        self.top = tk.Toplevel(root)
        self.top.withdraw()
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.attributes("-alpha", 0.0)
        self.top.configure(bg=self.KEY)
        self.top.attributes("-transparentcolor", self.KEY)
        self.c = tk.Canvas(self.top, width=self.W, height=self.H, bg=self.KEY, highlightthickness=0, bd=0)
        self.c.pack()
        self.c.bind("<Button-1>", lambda e: on_click())
        self.visible = False
        self.state = "idle"
        self.level = 0.0
        self.phase = 0.0
        self.alpha = 0.0
        self.target_alpha = 0.0
        self.hide_job = None
        self.title, self.text = "", ""
        self.top.update_idletasks()
        self._no_focus()
        self._animate()

    def _no_focus(self):
        """Never steal the keyboard, so 'type hello' still types into your app."""
        try:
            hwnd = user32.GetParent(self.top.winfo_id()) or self.top.winfo_id()
            style = user32.GetWindowLongW(hwnd, -20)
            user32.SetWindowLongW(hwnd, -20, style | 0x08000000 | 0x00000080 | 0x00000008)
        except Exception:
            pass

    def _place(self):
        rect = wt.RECT()
        user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0)     # screen minus the taskbar
        m = int(18 * self.s)
        self.top.geometry(f"{self.W}x{self.H}+{rect.right - self.W - m}+{rect.bottom - self.H - m}")

    # ----- public -----

    def show(self, state, title, text=""):
        if self.hide_job:
            self.root.after_cancel(self.hide_job)
            self.hide_job = None
        self.state, self.title, self.text = state, title, text
        self._layout()
        if not self.visible:
            self.visible = True
            self.top.deiconify()
            self.top.attributes("-topmost", True)
            self._no_focus()
        self.target_alpha = 0.97

    def update(self, state=None, title=None, text=None):
        if state is not None:
            self.state = state
        if title is not None:
            self.title = title
        if text is not None:
            self.text = text
        if self.visible:
            self._layout()

    def set_level(self, level):
        self.level = level

    def hide(self, delay_ms=0):
        if self.hide_job:
            self.root.after_cancel(self.hide_job)
            self.hide_job = None
        if delay_ms:
            self.hide_job = self.root.after(delay_ms, self._fade_out)
        else:
            self._fade_out()

    def _fade_out(self):
        self.hide_job = None
        self.target_alpha = 0.0

    # ----- drawing -----

    def _layout(self):
        s = self.s
        text = self.text if len(self.text) <= 900 else self.text[:900].rsplit(" ", 1)[0] + " ..."
        self.c.delete("txt")
        x = int(94 * s)
        self.c.create_text(x, int(20 * s), anchor="nw", text=self.title.upper(), fill="#8d8da3",
                           font=("Segoe UI", 8, "bold"), tags="txt")
        body = self.c.create_text(x, int(40 * s), anchor="nw", text=text, fill="#f4f4f8",
                                  width=self.W - x - int(20 * s), font=("Segoe UI", 11), tags="txt")
        bbox = self.c.bbox(body)
        h = max(int(100 * s), min(int(460 * s), (bbox[3] if bbox else 0) + int(22 * s)))
        if h != self.H:
            self.H = h
            self.c.configure(height=h)
        self._place()
        self._draw_panel()

    def _draw_panel(self):
        W, H, r = self.W, self.H, int(26 * self.s)
        self.c.delete("panel")
        pts = [r, 0, W - r, 0, W, 0, W, r, W, H - r, W, H, W - r, H, r, H, 0, H, 0, H - r, 0, r, 0, 0]
        self.c.create_polygon(pts, smooth=True, fill=self.PANEL, outline=self.BORDER, width=max(1, int(self.s)),
                              tags="panel")
        self.c.tag_lower("panel")

    @staticmethod
    def _mix(c1, c2, t):
        a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
        b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
        return "#" + "".join(f"{int(a[i] + (b[i] - a[i]) * t):02x}" for i in range(3))

    def _animate(self):
        try:
            if self.alpha != self.target_alpha:
                if self.target_alpha > self.alpha:
                    self.alpha = min(self.target_alpha, self.alpha + 0.18)
                else:
                    self.alpha = max(self.target_alpha, self.alpha - 0.08)
                self.top.attributes("-alpha", self.alpha)
                if self.alpha <= 0 and self.visible:
                    self.visible = False
                    self.top.withdraw()
            if self.visible:
                self._draw_orb()
        except tk.TclError:
            return
        self.root.after(33, self._animate)

    def _draw_orb(self):
        s = self.s
        self.phase += 0.08
        cx, cy = int(50 * s), int(min(self.H / 2, 50 * s))
        n = len(self.COLORS)
        k = (self.phase * 0.5) % n
        f = k - int(k)
        col = self._mix(self.COLORS[int(k)], self.COLORS[(int(k) + 1) % n], f)
        col2 = self._mix(self.COLORS[(int(k) + 2) % n], self.COLORS[(int(k) + 3) % n], f)
        if self.state == "listening":
            base = 15 + min(1.0, self.level) * 12
        elif self.state == "speaking":
            base = 17 + 3.5 * math.sin(self.phase * 2.2)
        elif self.state == "thinking":
            base = 14 + 1.0 * math.sin(self.phase * 3)
        else:
            base = 16 + 1.5 * math.sin(self.phase)
        self.c.delete("orb")
        for radius, t in [(base + 14, 0.14), (base + 9, 0.3), (base + 4.5, 0.55)]:
            r = radius * s
            self.c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=self._mix(self.PANEL, col, t), outline="", tags="orb")
        r = base * s
        self.c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=col, outline="", tags="orb")
        r2 = r * 0.58
        ox, oy = math.cos(self.phase * 1.3) * r * 0.32, math.sin(self.phase * 1.1) * r * 0.32
        self.c.create_oval(cx + ox - r2, cy + oy - r2, cx + ox + r2, cy + oy + r2, fill=col2, outline="", tags="orb")
        r3 = r * 0.3
        self.c.create_oval(cx - ox * 0.6 - r3, cy - oy * 0.6 - r3, cx - ox * 0.6 + r3, cy - oy * 0.6 + r3,
                           fill=self._mix(col, "#ffffff", 0.45), outline="", tags="orb")
        if self.state == "thinking":
            ra = (base + 9) * s
            self.c.create_arc(cx - ra, cy - ra, cx + ra, cy + ra, start=(self.phase * 150) % 360, extent=110,
                              style="arc", outline="#ffffff", width=max(2, int(2.5 * s)), tags="orb")
        self.c.tag_raise("txt")
