"""VoiceType engine: hotkey -> audio capture -> local Whisper -> cleanup -> paste.

UI-agnostic. Anything that wants to react (overlay, main window, tray) subscribes
via Engine.subscribe(callback) and receives (event, data) tuples.
"""

from __future__ import annotations

import ctypes
import glob
import json
import os
import re
import sqlite3
import sys
import threading
import time
import winsound
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

APP_NAME = "VoiceType"
DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
CONFIG_PATH = DATA_DIR / "config.json"
DB_PATH = DATA_DIR / "history.db"
SAMPLE_RATE = 16000

DEFAULT_CONFIG = {
    "hotkey": "ctrl+windows",          # hold to talk
    "toggle_hotkey": "ctrl+alt+space",  # press once to start, again to stop (hands-free)
    "model": "small",                  # tiny | base | small | medium | large-v3 | *.en variants
    "device": "auto",                  # auto | cuda | cpu
    "language": None,                  # None = auto-detect, or "en", "ta", "hi", ...
    "beam_size": 5,
    "remove_fillers": True,
    "add_trailing_space": True,
    "sounds": True,
    "min_duration_sec": 0.3,
    "max_duration_sec": 360,
    "restore_clipboard": True,
    "mic_device": None,                # None = system default, or device index
    "start_on_login": False,
    "show_window_on_start": True,
    # Words Whisper should know (names, jargon) - fed to it as a hint.
    "vocabulary": ["VoiceType"],
    # Spoken -> written replacements, applied after transcription.
    "replacements": {"new line": "\n", "new paragraph": "\n\n"},
}

RELOAD_KEYS = {"model", "device"}
HOTKEY_KEYS = {"hotkey", "toggle_hotkey"}


# --------------------------------------------------------------------------- config / storage

def load_config() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception as e:  # keep running on a broken config
            print(f"[config] could not read {CONFIG_PATH}: {e}")
    save_config(cfg)
    return cfg


def save_config(cfg: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


class History:
    def __init__(self, path: Path):
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                raw_text TEXT,
                final_text TEXT,
                language TEXT,
                duration_sec REAL,
                latency_sec REAL,
                num_words INTEGER,
                app_window TEXT)"""
        )
        self.db.commit()

    def add(self, **row) -> int:
        with self.lock:
            cols = ", ".join(row)
            qs = ", ".join("?" for _ in row)
            cur = self.db.execute(f"INSERT INTO history ({cols}) VALUES ({qs})",
                                  tuple(row.values()))
            self.db.commit()
            return cur.lastrowid

    def get(self, row_id: int) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM history WHERE id = ?", (row_id,)).fetchone()
        return dict(r) if r else None

    def recent(self, limit=100, search: str = "") -> list[dict]:
        with self.lock:
            if search:
                rows = self.db.execute(
                    "SELECT * FROM history WHERE final_text LIKE ? ORDER BY id DESC LIMIT ?",
                    (f"%{search}%", limit)).fetchall()
            else:
                rows = self.db.execute(
                    "SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def delete(self, row_id: int):
        with self.lock:
            self.db.execute("DELETE FROM history WHERE id = ?", (row_id,))
            self.db.commit()

    def clear(self):
        with self.lock:
            self.db.execute("DELETE FROM history")
            self.db.commit()

    def stats(self) -> dict:
        with self.lock:
            total_words, total_secs, sessions = self.db.execute(
                "SELECT COALESCE(SUM(num_words),0), COALESCE(SUM(duration_sec),0), COUNT(*) "
                "FROM history").fetchone()
            week_ago = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
            week_words = self.db.execute(
                "SELECT COALESCE(SUM(num_words),0) FROM history WHERE timestamp >= ?",
                (week_ago,)).fetchone()[0]
            days = {r[0] for r in self.db.execute(
                "SELECT DISTINCT substr(timestamp,1,10) FROM history").fetchall()}
        streak, day = 0, datetime.now().date()
        if day.isoformat() not in days:
            day -= timedelta(days=1)  # today not used yet doesn't break the streak
        while day.isoformat() in days:
            streak += 1
            day -= timedelta(days=1)
        wpm = round(total_words / (total_secs / 60)) if total_secs > 5 else 0
        # Typing averages ~40 WPM; time saved = time it would take to type minus time spoken.
        saved_min = max(0, total_words / 40 - total_secs / 60)
        return {"total_words": int(total_words), "week_words": int(week_words),
                "sessions": sessions, "wpm": wpm, "streak": streak,
                "minutes_saved": round(saved_min)}


# --------------------------------------------------------------------------- win32 helpers

user32 = ctypes.windll.user32


def foreground_window_title() -> str:
    hwnd = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def suppress_start_menu():
    """Tap an unassigned virtual key so releasing the Win key doesn't open Start."""
    VK_UNASSIGNED = 0xE8
    KEYEVENTF_KEYUP = 0x0002
    user32.keybd_event(VK_UNASSIGNED, 0, 0, 0)
    user32.keybd_event(VK_UNASSIGNED, 0, KEYEVENTF_KEYUP, 0)


def add_cuda_dll_dirs():
    """pip-installed NVIDIA libs (cublas/cudnn) live in site-packages/nvidia/*/bin."""
    bases = list(sys.path)
    if getattr(sys, "frozen", False):
        bases.insert(0, getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    for base in bases:
        for d in glob.glob(os.path.join(base, "nvidia", "*", "bin")):
            try:
                os.add_dll_directory(d)
            except OSError:
                pass
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


def beep(freq: int, ms: int = 60):
    threading.Thread(target=winsound.Beep, args=(freq, ms), daemon=True).start()


def list_microphones() -> list[dict]:
    import sounddevice as sd

    try:
        default_api = sd.default.hostapi
        default_in = sd.default.device[0]
    except Exception:
        default_api, default_in = 0, None
    mics = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and d["hostapi"] == default_api:
            mics.append({"index": i, "name": d["name"], "default": i == default_in})
    return mics


# --------------------------------------------------------------------------- text

FILLERS = re.compile(r"\b(um+|uh+|erm+|er|ah+|hmm+|mm+)\b[,.]?\s*", re.IGNORECASE)


def clean_text(text: str, cfg: dict) -> str:
    if cfg.get("remove_fillers"):
        text = FILLERS.sub("", text)
    for spoken, written in (cfg.get("replacements") or {}).items():
        pattern = re.compile(r"\s*\b" + re.escape(spoken) + r"\b[,.]?\s*", re.IGNORECASE)
        text = pattern.sub(lambda _m, w=written: w, text)
    text = re.sub(r"[ \t]{2,}", " ", text).strip(" ")
    if text and text[0].isalpha():
        text = text[0].upper() + text[1:]
    if text and cfg.get("add_trailing_space") and not text.endswith(("\n", " ")):
        text += " "
    return text


def _safe_is_pressed(key: str) -> bool:
    import keyboard
    try:
        return keyboard.is_pressed(key)
    except Exception:
        return False


def insert_text(text: str, cfg: dict, hotkey_keys: list[str]):
    import keyboard
    import pyperclip

    # Wait until the user has let go of the hotkey; otherwise Ctrl+V becomes Win+Ctrl+V etc.
    deadline = time.time() + 3
    while time.time() < deadline and any(_safe_is_pressed(k) for k in hotkey_keys):
        time.sleep(0.02)

    previous = None
    if cfg.get("restore_clipboard"):
        try:
            previous = pyperclip.paste()
        except Exception:
            previous = None
    pyperclip.copy(text)
    time.sleep(0.05)
    keyboard.send("ctrl+v")
    if previous is not None:
        def restore():
            time.sleep(0.4)
            try:
                pyperclip.copy(previous)
            except Exception:
                pass
        threading.Thread(target=restore, daemon=True).start()


# --------------------------------------------------------------------------- audio

class Recorder:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.chunks: list[np.ndarray] = []
        self.stream = None
        self.level = 0.0
        self.started_at = 0.0

    def _callback(self, indata, frames, t, status):
        mono = indata[:, 0].copy()
        self.chunks.append(mono)
        self.level = float(np.sqrt(np.mean(mono ** 2)))

    def start(self):
        import sounddevice as sd

        self.chunks = []
        self.level = 0.0
        self.started_at = time.time()
        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32",
            device=self.cfg.get("mic_device"), callback=self._callback,
        )
        self.stream.start()

    def stop(self) -> np.ndarray:
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        if not self.chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self.chunks)

    @property
    def elapsed(self) -> float:
        return time.time() - self.started_at


# --------------------------------------------------------------------------- engine

class Engine:
    def __init__(self):
        self.cfg = load_config()
        self.history = History(DB_PATH)
        self.recorder = Recorder(self.cfg)
        self.listeners = []
        self.status = "Starting..."
        self.state = "loading"      # loading | ready | recording | transcribing | error
        self.recording = False
        self.mode = None            # "hold" | "toggle"
        self.busy = threading.Lock()
        self.model = None
        self.device = None
        self.ready = threading.Event()
        self.target_window = ""
        self._load_gen = 0

    # -- events
    def subscribe(self, fn):
        self.listeners.append(fn)

    def emit(self, event: str, data=None):
        for fn in list(self.listeners):
            try:
                fn(event, data)
            except Exception as e:
                print(f"[engine] listener error on {event}: {e}")

    def set_state(self, state: str, status: str):
        self.state, self.status = state, status
        print(f"[status] {status}")
        self.emit("state", {"state": state, "status": status})

    # -- lifecycle
    def start(self):
        self.load_model()
        self._install_hotkeys()
        self._watchdog()

    def load_model(self):
        self.ready.clear()
        self._load_gen += 1
        threading.Thread(target=self._load, args=(self._load_gen,), daemon=True).start()

    def _load(self, gen: int):
        from faster_whisper import WhisperModel

        add_cuda_dll_dirs()
        name = self.cfg["model"]
        attempts = []
        if self.cfg["device"] in ("auto", "cuda"):
            attempts.append(("cuda", "float16"))
        if self.cfg["device"] in ("auto", "cpu"):
            attempts.append(("cpu", "int8"))
        self.set_state("loading", f"Loading Whisper '{name}' (first time downloads it)...")
        model = device = None
        for dev, compute in attempts:
            try:
                m = WhisperModel(name, device=dev, compute_type=compute)
                # Warm-up: also verifies the CUDA libraries actually load.
                list(m.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32))[0])
                model, device = m, dev
                break
            except Exception as e:
                print(f"[whisper] {dev}/{compute} failed: {e}")
        if gen != self._load_gen:
            return  # a newer reload superseded this one
        if model is None:
            self.set_state("error", "Failed to load Whisper model")
            return
        self.model, self.device = model, device
        self.ready.set()
        self.set_state("ready", f"Ready - {name} on {device.upper()}")

    def update_config(self, changes: dict):
        old = dict(self.cfg)
        self.cfg.update(changes)
        save_config(self.cfg)
        if any(old.get(k) != self.cfg.get(k) for k in RELOAD_KEYS):
            self.load_model()
        if any(old.get(k) != self.cfg.get(k) for k in HOTKEY_KEYS):
            self._install_hotkeys()
        self.emit("config", self.cfg)

    # -- hotkeys
    @property
    def hotkey_keys(self) -> list[str]:
        return [k.strip() for k in self.cfg["hotkey"].split("+") if k.strip()]

    def _install_hotkeys(self):
        import keyboard

        keyboard.unhook_all()
        keyboard.hook(self._on_key)
        if self.cfg.get("toggle_hotkey"):
            try:
                keyboard.add_hotkey(self.cfg["toggle_hotkey"], self._on_toggle, suppress=True)
            except Exception as e:
                print(f"[hotkey] invalid toggle hotkey {self.cfg['toggle_hotkey']!r}: {e}")

    def _hold_combo_down(self) -> bool:
        keys = self.hotkey_keys
        return bool(keys) and all(_safe_is_pressed(k) for k in keys)

    def _on_key(self, event):
        if event.event_type == "down":
            if not self.recording and self._hold_combo_down():
                self.mode = "hold"
                self._start_recording()
        elif event.event_type == "up":
            if self.recording and self.mode == "hold" and not self._hold_combo_down():
                self._stop_recording()

    def _on_toggle(self):
        if self.recording and self.mode == "toggle":
            self._stop_recording()
        elif not self.recording:
            self.mode = "toggle"
            self._start_recording()

    def toggle_from_ui(self):
        self._on_toggle()

    def _watchdog(self):
        def loop():
            while True:
                time.sleep(0.25)
                if self.recording and self.recorder.elapsed > self.cfg["max_duration_sec"]:
                    self._stop_recording()
        threading.Thread(target=loop, daemon=True).start()

    # -- pipeline
    def _start_recording(self):
        if not self.ready.is_set():
            self.emit("notice", "Model is still loading...")
            return
        if self.busy.locked():
            return
        if any(k in ("windows", "win", "left windows", "right windows") for k in self.hotkey_keys):
            suppress_start_menu()
        try:
            self.recorder.start()
        except Exception as e:
            self.emit("notice", f"Microphone error: {e}")
            return
        self.recording = True
        self.target_window = foreground_window_title()
        if self.cfg["sounds"]:
            beep(880)
        self.set_state("recording", "Listening...")

    def _stop_recording(self):
        if not self.recording:
            return
        self.recording = False
        audio = self.recorder.stop()
        if self.cfg["sounds"]:
            beep(620)
        duration = len(audio) / SAMPLE_RATE
        if duration < self.cfg["min_duration_sec"]:
            self.set_state("ready", self._ready_status())
            return
        self.set_state("transcribing", "Transcribing...")
        threading.Thread(target=self._process, args=(audio, duration), daemon=True).start()

    def _ready_status(self) -> str:
        return f"Ready - {self.cfg['model']} on {(self.device or 'cpu').upper()}"

    def transcribe(self, audio: np.ndarray) -> tuple[str, str]:
        self.ready.wait()
        vocab = self.cfg.get("vocabulary") or []
        segments, info = self.model.transcribe(
            audio,
            language=self.cfg.get("language") or None,
            beam_size=self.cfg.get("beam_size", 5),
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            initial_prompt=(", ".join(vocab) + ".") if vocab else None,
            condition_on_previous_text=False,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        return text, info.language

    def _process(self, audio: np.ndarray, duration: float):
        with self.busy:
            t0 = time.time()
            try:
                raw, lang = self.transcribe(audio)
            except Exception as e:
                print(f"[whisper] error: {e}")
                self.emit("notice", "Transcription failed")
                self.set_state("ready", self._ready_status())
                return
            final = clean_text(raw, self.cfg)
            latency = time.time() - t0
            self.set_state("ready", self._ready_status())
            if not final.strip():
                self.emit("notice", "No speech detected")
                return
            insert_text(final, self.cfg, self.hotkey_keys)
            print(f"[{latency:.2f}s] {final!r}")
            row_id = self.history.add(
                timestamp=datetime.now().isoformat(timespec="seconds"),
                raw_text=raw, final_text=final, language=lang,
                duration_sec=round(duration, 2), latency_sec=round(latency, 2),
                num_words=len(final.split()), app_window=self.target_window,
            )
            self.emit("transcript", self.history.get(row_id))
