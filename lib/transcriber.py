"""Speech-to-text with faster-whisper: loading the models and turning segments into text."""

from __future__ import annotations

import os
import queue
import sys
import threading
from pathlib import Path

import numpy as np

from .audio import AudioHub
from .common import IS_MAC, IS_WINDOWS, SAMPLE_RATE


# Phrases Whisper often "hallucinates" on short/near-silent audio (Indonesian and English output).
HALLUCINATIONS = {
    "terima kasih", "terima kasih.", "terima kasih telah menonton.",
    "sampai jumpa di video berikutnya.", "thank you", "thank you.",
    "thanks for watching!", "thanks for watching.", "you", "...",
}


class Transcriber(threading.Thread):
    def __init__(self, args, hub: AudioHub, ui_q: queue.Queue, stop_event: threading.Event):
        super().__init__(daemon=True)
        self.args, self.hub, self.ui_q, self.stop_event = args, hub, ui_q, stop_event

    def _status(self, msg: str) -> None:
        self.ui_q.put(("status", msg))

    def _load(self):
        add_cuda_dll_dirs()
        from faster_whisper import WhisperModel

        if self.args.device == "cpu" or (IS_MAC and self.args.device == "auto"):  # CTranslate2 has no GPU support on macOS
            attempts = [("cpu", "int8")]
        elif self.args.device == "cuda":
            attempts = [("cuda", self.args.compute_type)]
        else:  # auto: try the GPU first, fall back to the CPU if it fails
            attempts = [("cuda", self.args.compute_type), ("cpu", "int8")]

        def load_one(name, device, compute_type):
            self._status(f"Loading model '{name}' on {device.upper()} ({compute_type})…")
            model = WhisperModel(name, device=device, compute_type=compute_type)
            # Warm-up: make sure cuBLAS/cuDNN actually work, not just that they were detected.
            segments, _ = model.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), language="en")
            list(segments)
            return model

        errors = []
        for device, compute_type in attempts:
            try:
                model = load_one(self.args.model, device, compute_type)
                draft_name = self.args.draft_model
                if draft_name in ("same", self.args.model) or not self.args.draft_interval:
                    return model, model, device
                try:
                    return model, load_one(draft_name, device, compute_type), device
                except Exception as e:  # draft model failed (VRAM full, etc.): use the main model only
                    self.ui_q.put(("error", f"Draft model '{draft_name}' failed to load, using '{self.args.model}': {e}"))
                    return model, model, device
            except Exception as e:
                errors.append(f"{device}: {e}")
        raise RuntimeError("Failed to load the model. " + " | ".join(errors))

    def run(self) -> None:
        try:
            model, draft_model, device = self._load()
        except Exception as e:
            self.ui_q.put(("error", str(e)))
            return

        lang = None if self.args.lang == "auto" else self.args.lang
        last_lang: dict[str, str] = {}  # label -> language of the last final sentence (for drafts with --lang auto)
        models = self.args.model if draft_model is model else f"{self.args.model} + draft {self.args.draft_model}"
        ready = f"Ready — {models} @ {device.upper()} · language: {self.args.lang}"
        self._status(ready)
        if device == "cpu" and self.args.device != "cpu" and not IS_MAC:
            launcher = "run.bat" if IS_WINDOWS else "./run.sh"
            self.ui_q.put(("error", f"GPU unavailable, running on CPU (slower). Start the app with {launcher}"))
        lagging = False
        while not self.stop_event.is_set():
            item = self.hub.get(timeout=0.2)
            if item is None:
                continue
            kind, label, seg_id, audio, ts, end = item
            final = kind == "final"

            backlog = self.hub.backlog()
            if backlog >= 3 and not lagging:
                self._status(f"{backlog} sentences behind — try a smaller model")
            elif backlog < 3 and lagging:
                self._status(ready)
            lagging = backlog >= 3

            try:
                if final:
                    segments, info = self.args._whisper_transcribe(model, audio, lang, True)
                else:
                    # Short drafts + a small model easily misdetect the language: reuse the previous sentence's language.
                    segments, info = self.args._whisper_transcribe(
                        draft_model, audio, lang or last_lang.get(label), False)
                text = " ".join(s.text.strip() for s in segments).strip()
            except Exception as e:
                if final:
                    self.ui_q.put(("error", f"Transcription failed: {e}"))
                    self.ui_q.put(("drop", label, seg_id))
                continue

            useless = not text or (text.lower() in HALLUCINATIONS and len(audio) < SAMPLE_RATE * 3)
            if final:
                if useless:
                    self.ui_q.put(("drop", label, seg_id))  # remove its draft from the screen
                else:
                    last_lang[label] = info.language
                    self.ui_q.put(("line", ts, label, text, info.language, seg_id, end))
            elif not useless:
                self.ui_q.put(("draft", label, seg_id, text))


def add_cuda_dll_dirs() -> None:
    """Windows: make the cuBLAS/cuDNN DLLs from pip findable (run.sh does this on Linux via LD_LIBRARY_PATH)."""
    # `sys.platform == "win32"` spelled out (not IS_WINDOWS) so type checkers know add_dll_directory exists.
    if sys.platform == "win32":
        try:
            from nvidia import cublas, cudnn
        except ImportError:
            return
        for pkg in (cublas, cudnn):
            for base in pkg.__path__:
                dll_dir = os.path.join(base, "bin")
                if os.path.isdir(dll_dir):
                    os.add_dll_directory(dll_dir)
                    os.environ["PATH"] = dll_dir + os.pathsep + os.environ.get("PATH", "")


def make_transcribe_fn(beam_size: int, hotwords: str | None = None):
    def _transcribe(model, audio, lang, final: bool = True):
        # Drafts are made as fast as possible (beam 1, no VAD); finals use the full settings.
        return model.transcribe(
            audio,
            language=lang,
            beam_size=beam_size if final else 1,
            vad_filter=final,                 # faster-whisper's built-in Silero VAD removes non-speech parts
            condition_on_previous_text=False, # prevents hallucinations chaining across segments
            without_timestamps=True,
            hotwords=hotwords,                # --vocab: names/jargon Whisper should prefer
        )
    return _transcribe


def load_vocab(args) -> str | None:
    """--vocab "a, b" and/or --vocab-file (one term per line, # = comment) -> one hotwords string."""
    terms = [t.strip() for t in (args.vocab or "").split(",")]
    if args.vocab_file:
        path = Path(args.vocab_file).expanduser()
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as e:
            sys.exit(f"Can't read --vocab-file {path}: {e}")
        terms += [line.split("#", 1)[0].strip() for line in lines]
    terms = list(dict.fromkeys(t for t in terms if t))  # drop empties and duplicates, keep order
    return ", ".join(terms) or None
