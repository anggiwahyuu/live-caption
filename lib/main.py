"""Wires everything together and starts the app."""

from __future__ import annotations

import queue
import shutil
import sys
import threading

from .audio import AudioCapture, AudioHub, Segmenter
from .common import IS_LINUX, SOURCES
from .config import parse_args
from .summary import Summarizer
from .transcriber import Transcriber
from .translation import TranslationWorker, Translator
from .ui import App


def main() -> None:
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
        seg = Segmenter(SOURCES[name], hub, args.threshold, args.silence_ms, args.max_seconds, args.draft_interval,
                        paused=paused)
        captures.append(AudioCapture(name, args.monitor if name == "system" else None, seg, stop, report))

    workers = [*captures, Transcriber(args, hub, ui_q, stop), TranslationWorker(translator, tr_q, ui_q, stop)]

    root = tk.Tk()

    def shutdown() -> None:
        stop.set()
        for c in captures:
            c.stop()
        root.destroy()

    summarizer = Summarizer(args.summarizer, args.summary_model, args.ollama_url)
    App(root, args, ui_q, tr_q, translator, paused, summarizer, shutdown)
    for w in workers:
        w.start()
    try:
        root.mainloop()
    except KeyboardInterrupt:
        shutdown()
