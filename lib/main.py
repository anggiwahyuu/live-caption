"""Wires everything together and starts the app."""

from __future__ import annotations

import datetime as dt
import queue
import shutil
import sys
import threading
from pathlib import Path

from .audio import AudioCapture, AudioHub, Segmenter
from .common import IS_LINUX, SOURCES
from .config import log_path, parse_args
from .summary import Summarizer
from .transcriber import Transcriber
from .translation import TranslationWorker, Translator
from .ui import App


ICON = Path(__file__).resolve().parent.parent / "assets" / "live-caption.png"


def log_to_file() -> None:
    """The same log run.sh writes on Linux/macOS when started from the menu: only this one and the previous one."""
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 1_000_000:
        path.replace(path.with_name(path.name + ".old"))
    sys.stdout = sys.stderr = open(path, "a", encoding="utf-8", buffering=1)
    print(f"==== {dt.datetime.now():%Y-%m-%d %H:%M:%S} Live Caption started")


def main() -> None:
    if sys.stderr is None:  # pythonw, from the Windows Start menu shortcut: no console, so messages go to a log
        log_to_file()
    for stream in (sys.stdout, sys.stderr):
        # Redirected on Windows, output uses the old code page (cp1252): print "?" for what it can't show, don't crash
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = parse_args()
    if IS_LINUX and not (shutil.which("parec") or shutil.which("pw-record")):
        sys.exit("Neither parec nor pw-record was found. Install parec with one of:\n"
                 "  Debian/Ubuntu/Mint:  sudo apt install pulseaudio-utils\n"
                 "  Fedora / openSUSE:   sudo dnf install pulseaudio-utils / sudo zypper install pulseaudio-utils\n"
                 "  Arch / CachyOS:      sudo pacman -S libpulse")

    import tkinter as tk

    stop = threading.Event()
    paused = threading.Event()
    hub = AudioHub()
    ui_q: queue.Queue = queue.Queue()
    tr_q: queue.Queue = queue.Queue()

    translator = Translator(args.translate_to, args.translator)
    translator.enabled = args.translate

    def report(msg: str, kind: str = "error") -> None:
        ui_q.put((kind, msg))

    names = ["system", "mic"] if args.source == "both" else [args.source]
    captures = []
    for name in names:
        label = args.my_name if name == "mic" else SOURCES[name]
        seg = Segmenter(label, hub, args.threshold, args.silence_ms, args.max_seconds, args.draft_interval,
                        paused=paused)
        captures.append(AudioCapture(name, args.monitor if name == "system" else None, seg, stop, report))

    workers = [*captures, Transcriber(args, hub, ui_q, stop), TranslationWorker(translator, tr_q, ui_q, stop)]

    root = tk.Tk(className="live-caption")  # the class the menu shortcut names (lib/shortcut.py), for the taskbar
    try:
        root.iconphoto(True, tk.PhotoImage(file=ICON))
    except tk.TclError:
        pass  # no icon is no reason not to start

    def shutdown() -> None:
        stop.set()
        for c in captures:
            c.stop()
        root.destroy()

    summarizer = Summarizer(args.summarizer, args.summary_model, args.ollama_url, args.my_name)
    App(root, args, ui_q, tr_q, translator, paused, summarizer, shutdown)
    for w in workers:
        w.start()
    try:
        root.mainloop()
    except KeyboardInterrupt:
        shutdown()
