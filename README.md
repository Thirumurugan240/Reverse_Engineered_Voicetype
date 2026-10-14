# VoiceType

Push-to-talk dictation for Windows that runs fully offline with local Whisper.

- **Hold `Ctrl + Win`**, speak, release: the text is typed into the focused app.
- **`Ctrl + Alt + Space`**: hands-free mode. Press it once to start and again to stop.
- A small status pill at the bottom of the screen shows the mic level while you speak.
- A tray icon gives you status, history, settings and quit.

## Install (Windows 10/11)
1. Install [Python 3.10+](https://www.python.org/downloads/). During setup, tick **"Add python.exe to PATH"**.
2. Download this repo: **Code → Download ZIP**, then extract it, or use `git clone`.
3. Double-click **`setup.bat`**. It installs everything, and if it finds an NVIDIA GPU it also installs the CUDA libraries (about 2 GB).
4. Double-click **`run.bat`** to start VoiceType. The first launch downloads the Whisper model (about 480 MB).
5. Optional: run **`build.bat`** to make a standalone `dist\VoiceType\VoiceType.exe`, then pin that to your taskbar or Start Menu.

> If you also have Wispr Flow installed, quit it first or change VoiceType's shortcut in Settings, because both use `Ctrl + Win` by default.

## Use
- The window has three pages: **Home** (stats and searchable history), **Dictionary** (vocabulary and replacements) and **Settings** (shortcuts, microphone, model, language and behaviour).
- Closing the window keeps VoiceType running in the tray. To exit, choose **Quit** from the tray menu.
- `run.bat` runs the app from source with a console for logs. `build.bat` rebuilds the exe after you change the code.

## Code layout
- `app/engine.py`: hotkey, recording, Whisper, text cleanup, pasting and history
- `app/overlay.py`: the floating status pill
- `app/main.py`: the window (pywebview), tray icon, start-at-login and single-instance check
- `app/ui/index.html`: the window's interface

## Settings
You can change everything on the Settings page, and changes apply immediately. The settings are stored in `%APPDATA%\VoiceType\config.json`.

| key | meaning |
|---|---|
| `hotkey` | Hold-to-talk combo, e.g. `ctrl+windows`, `right ctrl`, `ctrl+shift` |
| `toggle_hotkey` | Hands-free start/stop combo |
| `model` | `tiny`, `base`, `small` (default), `medium`, `large-v3`, `distil-large-v3`, or `*.en` for English-only |
| `device` | `auto` (GPU if CUDA works, otherwise CPU), `cuda` or `cpu` |
| `language` | `null` to auto-detect, or a code like `"en"`, `"ta"` or `"hi"` |
| `vocabulary` | Names and jargon that Whisper should spell correctly |
| `replacements` | Spoken phrase → written text (`"new line": "\n"`) |
| `remove_fillers` | Removes um, uh and similar filler words |

History is saved in `%APPDATA%\VoiceType\history.db` (SQLite).

## Tips
- **GPU:** an NVIDIA card with 4 GB or more runs even `large-v3-turbo` in under a second per clip. On a CPU, use `base` or `small`.
- **Non-English languages:** pick the language and a larger model (`large-v3-turbo`) in Settings for much better accuracy.

## How it works (clean-room design)
These are the parts of a dictation tool like Wispr Flow, as seen by analyzing the shipped app with REA. Each part was then written from scratch:

| Component | Observed in Wispr Flow | VoiceType |
|---|---|---|
| Global hotkey | Native helper with a low-level keyboard hook (`SetWindowsHookEx`) | `keyboard` lib (same WH_KEYBOARD_LL hook) |
| Audio capture | Renderer `getUserMedia` + AudioWorklet, Opus encoding | `sounddevice` 16 kHz mono |
| Speech-to-text | Streamed over WebSocket to their cloud API | Local `faster-whisper` (GPU/CPU) |
| Text insertion | Clipboard + `SendInput` paste | Clipboard + Ctrl+V, then the old clipboard is restored |
| Context reading | UI Automation (TextPattern/ValuePattern) | Not used (only the window title is logged) |
| Local data | SQLite + migrations (history, dictionary, notes) | SQLite history |

VoiceType contains no code, assets or API access from Wispr Flow. It is an independent project and is not affiliated with Wispr.
