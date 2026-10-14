"""VoiceType desktop app: main window (pywebview) + tray + overlay + engine."""

from __future__ import annotations

import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

if getattr(sys, "frozen", False):
    BASE = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    BASE = Path(__file__).resolve().parent
    sys.path.insert(0, str(BASE))

import webview  # noqa: E402

from engine import APP_NAME, DATA_DIR, Engine, list_microphones  # noqa: E402
from overlay import Overlay  # noqa: E402

UI_PATH = BASE / "ui" / "index.html"
ICON_PATH = BASE / "assets" / "icon.ico"
STARTUP_LNK = (Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu"
               / "Programs" / "Startup" / f"{APP_NAME}.lnk")


def launch_command() -> tuple[str, str]:
    """Executable + arguments that start this app (frozen exe or pythonw + script)."""
    if getattr(sys, "frozen", False):
        return sys.executable, "--background"
    pyw = Path(sys.executable).with_name("pythonw.exe")
    return str(pyw), f'"{Path(__file__).resolve()}" --background'


def set_start_on_login(enabled: bool):
    if enabled:
        target, args = launch_command()
        ps = (
            "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK);"
            "$s.TargetPath=$env:TGT;$s.Arguments=$env:ARGS;"
            "$s.WorkingDirectory=$env:WD;$s.IconLocation=$env:ICO;$s.Save()"
        )
        env = dict(os.environ, LNK=str(STARTUP_LNK), TGT=target, ARGS=args,
                   WD=str(Path(target).parent), ICO=str(ICON_PATH))
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env,
                       creationflags=0x08000000, check=False)
    elif STARTUP_LNK.exists():
        STARTUP_LNK.unlink()


def single_instance() -> bool:
    ctypes.windll.kernel32.CreateMutexW(None, False, f"Local\\{APP_NAME}SingleInstance")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


class Api:
    """Methods callable from the UI as window.pywebview.api.<name>()."""

    def __init__(self, app: "DesktopApp"):
        self._app = app

    @property
    def _engine(self) -> Engine:
        return self._app.engine

    def get_state(self):
        e = self._engine
        return {"state": e.state, "status": e.status, "config": e.cfg,
                "stats": e.history.stats(), "data_dir": str(DATA_DIR)}

    def get_history(self, search="", limit=200):
        return self._engine.history.recent(limit, search or "")

    def delete_history(self, row_id):
        self._engine.history.delete(int(row_id))
        return self._engine.history.stats()

    def clear_history(self):
        self._engine.history.clear()
        return self._engine.history.stats()

    def get_stats(self):
        return self._engine.history.stats()

    def list_mics(self):
        return list_microphones()

    def save_config(self, changes: dict):
        if "start_on_login" in changes:
            set_start_on_login(bool(changes["start_on_login"]))
        self._engine.update_config(changes)
        return self._engine.cfg

    def toggle_dictation(self):
        self._engine.toggle_from_ui()

    def copy_text(self, text):
        import pyperclip
        pyperclip.copy(text)

    def open_data_folder(self):
        os.startfile(DATA_DIR)


class DesktopApp:
    def __init__(self, background: bool):
        self.engine = Engine()
        self.quitting = False
        self.overlay = Overlay(self.engine)
        show = self.engine.cfg.get("show_window_on_start", True) and not background
        self.window = webview.create_window(
            APP_NAME, url=str(UI_PATH), js_api=Api(self),
            width=1120, height=700, min_size=(860, 540), hidden=not show,
            background_color="#f7f6f3",
        )
        self.window.events.closing += self._on_closing
        self.ui_events: queue.Queue = queue.Queue()
        self.engine.subscribe(self._push_to_ui)
        threading.Thread(target=self._ui_pump, daemon=True).start()
        self.tray = self._make_tray()

    # -- window
    def _on_closing(self):
        if self.quitting:
            return True
        self.window.hide()  # closing the window keeps the app running in the tray
        return False

    def show_window(self):
        self.window.show()
        self.window.restore()

    def quit(self):
        self.quitting = True
        try:
            self.tray.stop()
        finally:
            self.window.destroy()
            os._exit(0)

    def _ui_pump(self):
        # evaluate_js blocks until the page has loaded, so engine threads never call it directly.
        self.window.events.loaded.wait()
        while True:
            event, data = self.ui_events.get()
            try:
                self.window.evaluate_js(f"window.onEngineEvent && window.onEngineEvent("
                                        f"{json.dumps(event)}, {json.dumps(data)})")
            except Exception as e:
                print(f"[ui] push failed: {e}")

    def _push_to_ui(self, event, data):
        self.ui_events.put((event, data))
        if event == "state":
            try:
                self.tray.title = f"{APP_NAME} - {data['status']}"
                self.tray.update_menu()
            except Exception:
                pass

    # -- tray
    def _make_tray(self):
        import pystray
        from PIL import Image

        menu = pystray.Menu(
            pystray.MenuItem(f"Open {APP_NAME}", lambda: self.show_window(), default=True),
            pystray.MenuItem(lambda _i: self.engine.status, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Start / stop dictation", lambda: self.engine.toggle_from_ui()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: self.quit()),
        )
        icon = pystray.Icon(APP_NAME, Image.open(ICON_PATH), APP_NAME, menu)
        icon.run_detached()
        return icon

    def run(self):
        self.engine.start()
        webview.start(gui="edgechromium", debug="--debug" in sys.argv,
                      icon=str(ICON_PATH))


def main():
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_NAME}.Desktop")
    if not single_instance():
        ctypes.windll.user32.MessageBoxW(None, f"{APP_NAME} is already running.\n"
                                         "Look for the microphone icon in the system tray.",
                                         APP_NAME, 0x40)
        return
    DesktopApp(background="--background" in sys.argv).run()


if __name__ == "__main__":
    main()
