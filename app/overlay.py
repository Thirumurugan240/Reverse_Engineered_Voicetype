"""Floating status pill shown at the bottom of the screen while dictating.

Runs its own Tk loop on a background thread (pywebview owns the main thread) and
never takes focus, so the app being dictated into keeps the caret.
"""

from __future__ import annotations

import ctypes
import math
import queue
import threading
import time
import tkinter as tk

user32 = ctypes.windll.user32
BG = "#16161a"
W, H = 200, 40


class Overlay:
    def __init__(self, engine):
        self.engine = engine
        self.q: queue.Queue = queue.Queue()
        self.mode = "hidden"     # hidden | recording | transcribing | notice
        self.notice_until = 0.0
        self.notice_text = ""
        self.levels = [0.0] * 22
        engine.subscribe(self._on_event)
        threading.Thread(target=self._run, daemon=True).start()

    def _on_event(self, event, data):
        if event == "state":
            self.q.put(("state", data["state"]))
        elif event == "notice":
            self.q.put(("notice", data))

    def _run(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.94)
        self.root.configure(bg="#000001")
        self.root.attributes("-transparentcolor", "#000001")
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{W}x{H}+{(sw - W) // 2}+{sh - H - 64}")
        self.canvas = tk.Canvas(self.root, width=W, height=H, bg="#000001",
                                highlightthickness=0)
        self.canvas.pack()
        self.root.withdraw()
        self.root.update_idletasks()
        self._no_activate()
        self._tick()
        self.root.mainloop()

    def _no_activate(self):
        GWL_EXSTYLE = -20
        flags = 0x08000000 | 0x00000080 | 0x00000020  # NOACTIVATE | TOOLWINDOW | TRANSPARENT
        hwnd = user32.GetParent(self.root.winfo_id())
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, user32.GetWindowLongW(hwnd, GWL_EXSTYLE) | flags)

    def _pill(self):
        c, r = self.canvas, H // 2
        c.create_oval(0, 0, H, H, fill=BG, outline="")
        c.create_oval(W - H, 0, W, H, fill=BG, outline="")
        c.create_rectangle(r, 0, W - r, H, fill=BG, outline="")

    def _tick(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "state":
                    self.mode = {"recording": "recording",
                                 "transcribing": "transcribing"}.get(val, "hidden")
                    if val == "recording":
                        self.levels = [0.0] * len(self.levels)
                elif kind == "notice":
                    self.notice_text = val if len(val) <= 30 else val[:29] + "…"
                    self.notice_until = time.time() + 1.8
        except queue.Empty:
            pass

        mode = self.mode
        if mode == "hidden" and time.time() < self.notice_until:
            mode = "notice"

        c = self.canvas
        c.delete("all")
        if mode == "hidden":
            self.root.withdraw()
        else:
            self._pill()
            if mode == "recording":
                self.levels = self.levels[1:] + [min(1.0, self.engine.recorder.level * 14)]
                c.create_oval(14, 14, 26, 26, fill="#ff453a", outline="")
                for i, lv in enumerate(self.levels):
                    h = 3 + lv * 22
                    x = 38 + i * 7
                    c.create_rectangle(x, (H - h) / 2, x + 3, (H + h) / 2,
                                       fill="#f2f2f7", outline="")
            elif mode == "transcribing":
                t = time.time() * 6
                for i in range(3):
                    y = H / 2 + math.sin(t - i * 0.9) * 4
                    x = W / 2 - 14 + i * 14
                    c.create_oval(x - 4, y - 4, x + 4, y + 4, fill="#a78bfa", outline="")
            else:
                c.create_text(W / 2, H / 2, text=self.notice_text, fill="#e5e5ea",
                              font=("Segoe UI", 9))
            self.root.deiconify()
            self.root.lift()
        self.root.after(33, self._tick)
