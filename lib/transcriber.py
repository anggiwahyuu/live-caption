"""Speech-to-text with faster-whisper: loading the models and turning segments into text."""

from __future__ import annotations

import os
import queue
import re
import shutil
import sys
import threading
from pathlib import Path

import numpy as np

from . import whispercpp
from .audio import AudioHub
from .common import IS_MAC, IS_WINDOWS, SAMPLE_RATE


def normalize(text: str) -> str:
    """Lower case, without punctuation: "Thank you!" and "thank you." are the same phrase."""
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


# Whole texts Whisper often "hallucinates" on (nearly) silent audio: the endings of the videos it learned from.
# People do say some of them ("Terima kasih."), so they're only dropped for audio shorter than SHORT_SECONDS: said
# for real, they take about 2 s with the pauses around them; a longer clip with only these words in it is noise.
SHORT_SECONDS = 6
HALLUCINATIONS = {normalize(phrase) for phrase in (
    # Indonesian, Malay
    "Terima kasih.", "Terima kasih telah menonton.", "Terima kasih sudah menonton.",
    "Sampai jumpa di video berikutnya.", "Jangan lupa like dan subscribe.", "Terima kasih kerana menonton.",
    # English
    "Thank you.", "Thanks for watching!", "Thank you for watching.", "Thank you so much for watching.",
    "Please subscribe.", "See you in the next video.", "Bye.", "You",
    # Japanese, Chinese, Korean
    "ご視聴ありがとうございました", "ご清聴ありがとうございました", "ありがとうございました",
    "谢谢观看", "感谢观看", "謝謝觀看", "谢谢", "謝謝",
    "시청해주셔서 감사합니다", "감사합니다", "구독과 좋아요 부탁드립니다",
    # Spanish, Portuguese, Italian, French, German, Dutch, Russian
    "Gracias.", "Gracias por ver.", "Gracias por ver el video.", "Muchas gracias.", "¡Suscríbete!",
    "Obrigado.", "Obrigada.", "Obrigado por assistir.",
    "Grazie.", "Grazie per la visione.",
    "Merci.", "Merci d'avoir regardé.", "Merci d'avoir regardé cette vidéo.",
    "Danke.", "Vielen Dank.", "Danke fürs Zuschauen.", "Vielen Dank fürs Zuschauen.", "Bis zum nächsten Mal.",
    "Bedankt voor het kijken.",
    "Спасибо.", "Спасибо за просмотр.", "Продолжение следует...",
)}
# Subtitle credits from the TV subtitles Whisper learned from: nobody says these in a meeting, so a text that
# contains one is dropped whatever its length.
CREDITS = tuple(normalize(c) for c in (
    "Amara.org", "Untertitel im Auftrag des ZDF", "Untertitelung des ZDF", "Sous-titrage ST'",
    "Sous-titres réalisés", "Субтитры сделал", "Субтитры подогнал", "Редактор субтитров",
    "请不吝点赞", "明镜与点点", "字幕製作人", "字幕制作人", "字幕by", "字幕由", "字幕提供", "Zither Harp",
    "Sottotitoli e revisione a cura di", "Copyright WDR", "Субтитры субтитров",
))
# Sound labels from the same subtitles ("[Music]", "MUZIEK."), and German broadcasters' credits ("SWR 2020"):
# dropped whatever their length, but only as the whole text.
SOUND_LABELS = {normalize(label) for label in (
    "Music", "Muziek", "Musik", "Musique", "Música", "Musica", "Музыка", "音乐", "音楽", "음악",
    "Applause", "Applaus", "Laughter",
)}
BROADCASTER = re.compile(r"(copyright )?(ard|br|hr|mdr|ndr|swr|wdr|zdf)( \d{4})?")


def is_hallucination(text: str, seconds: float) -> bool:
    """True for a text that's only punctuation, a subtitle credit or sound label, or a typical phrase on short audio."""
    norm = normalize(text)
    if not norm or any(credit in norm for credit in CREDITS):
        return True
    if norm in SOUND_LABELS or BROADCASTER.fullmatch(norm):
        return True
    return norm in HALLUCINATIONS and seconds < SHORT_SECONDS


class Transcriber(threading.Thread):
    def __init__(self, args, hub: AudioHub, ui_q: queue.Queue, stop_event: threading.Event):
        super().__init__(daemon=True)
        self.args, self.hub, self.ui_q, self.stop_event = args, hub, ui_q, stop_event

    def _status(self, msg: str) -> None:
        self.ui_q.put(("status", msg))

    def _load(self):
        """-> (model, draft model, where it runs). Tries the engines in order; see _attempts."""
        errors = []
        self.cuda_error: str | None = None  # why the NVIDIA GPU couldn't be used, when it was tried
        for engine, device in self._attempts():
            try:
                if engine == "whisper.cpp":
                    return self._load_whispercpp()
                return self._load_faster_whisper(device)
            except Exception as e:
                errors.append(f"{engine} {device or ''}: {e}".replace("  ", " "))
                if device == "cuda":
                    self.cuda_error = str(e)
        raise RuntimeError("Failed to load the model. " + " | ".join(errors))

    def _attempts(self) -> list[tuple[str, str | None]]:
        """auto: an NVIDIA GPU with faster-whisper first (CUDA), else whisper.cpp when setup installed it
        (AMD/Intel/NVIDIA GPUs through Vulkan, Apple GPUs through Metal, or its faster CPU code),
        else faster-whisper on the CPU."""
        engine, device = self.args.engine, self.args.device
        if engine == "whisper.cpp":
            return [("whisper.cpp", None)]
        cuda = device == "cuda" or (device == "auto" and not IS_MAC)  # CTranslate2 has no GPU support on macOS
        attempts = [("faster-whisper", "cuda")] if cuda else []
        if device == "cuda":
            return attempts
        if engine == "auto" and whispercpp.installed():
            attempts.append(("whisper.cpp", None))
        return attempts + [("faster-whisper", "cpu")]

    def _load_faster_whisper(self, device: str):
        add_cuda_dll_dirs()
        from faster_whisper import WhisperModel

        compute_type = self.args.compute_type if device == "cuda" else "int8"

        def load_one(name):
            self._status(f"Loading model '{name}' on {device.upper()} ({compute_type})…")
            model = WhisperModel(name, device=device, compute_type=compute_type)
            # Warm-up: make sure cuBLAS/cuDNN actually work, not just that they were detected.
            segments, _ = model.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), language="en")
            list(segments)
            return model

        return self._with_draft(load_one, device.upper())

    def _load_whispercpp(self):
        setting = "cpu" if self.args.device == "cpu" else self.args.whispercpp_device
        gpu = whispercpp.choose_device(setting, whispercpp.list_gpus() if setting != "cpu" else [])
        role = iter(("final", "draft"))

        def load_one(name):
            def progress(done, total):
                self._status(f"Downloading whisper.cpp model '{name}' {100 * done // max(total, 1)}%…")
            path = whispercpp.ensure_model(name, progress)
            self._status(f"Loading model '{name}' with whisper.cpp…")
            server = whispercpp.WhisperCppServer(path, gpu, log=whispercpp.install_dir() / f"server-{next(role)}.log")
            try:
                server.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), language="en")  # warm-up
            except Exception:
                server.close()  # don't leave it holding the GPU while another engine takes over
                raise
            return server

        model, draft, _ = self._with_draft(load_one, "")
        return model, draft, model.device_name()

    def _with_draft(self, load_one, where: str):
        """Loads the main model, then the draft model (unless it's the same one or drafts are off)."""
        model = load_one(self.args.model)
        draft_name = self.args.draft_model
        if draft_name in ("same", self.args.model) or not self.args.draft_interval:
            return model, model, where
        try:
            return model, load_one(draft_name), where
        except Exception as e:  # draft model failed (VRAM full, etc.): use the main model only
            self.ui_q.put(("error", f"Draft model '{draft_name}' failed to load, using '{self.args.model}': {e}"))
            return model, model, where

    def run(self) -> None:
        try:
            model, draft_model, device = self._load()
        except Exception as e:
            self.ui_q.put(("error", str(e)))
            return

        lang = None if self.args.lang == "auto" else self.args.lang
        last_lang: dict[str, str] = {}  # label -> language of the last final sentence (for drafts with --lang auto)
        models = self.args.model if draft_model is model else f"{self.args.model} + draft {self.args.draft_model}"
        ready = f"Ready — {models} @ {device} · language: {self.args.lang}"
        self._status(ready)
        if self.cuda_error and shutil.which("nvidia-smi"):  # an NVIDIA GPU is there, but CUDA failed
            launcher = "run.bat" if IS_WINDOWS else "./run.sh"
            if device == "CPU" and re.search(r"cudnn|cublas", self.cuda_error, re.I):
                setup = "setup.ps1" if IS_WINDOWS else "./setup.sh"
                self.ui_q.put(("error", f"NVIDIA GPU unavailable, running on CPU (slower): the CUDA libraries are "
                                        f"missing or incomplete. Start the app with {launcher}, or run {setup} to "
                                        f"repair them ({self.cuda_error})"))
            elif device == "CPU":
                self.ui_q.put(("error", f"NVIDIA GPU unavailable, running on CPU (slower). "
                                        f"Start the app with {launcher} ({self.cuda_error})"))
            else:
                self.ui_q.put(("info", f"CUDA unavailable, so whisper.cpp runs on {device} instead"))
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

            useless = not text or is_hallucination(text, len(audio) / SAMPLE_RATE)
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
    """--vocab "a, b" and/or --vocab-file (one term per line, # = comment) -> one hotwords string.
    The --alert words are added too: a name has to be spelled right to be noticed."""
    terms = [t.strip() for t in (args.vocab or "").split(",") + (getattr(args, "alert", None) or "").split(",")]
    if args.vocab_file:
        path = Path(args.vocab_file).expanduser()
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as e:
            sys.exit(f"Can't read --vocab-file {path}: {e}")
        terms += [line.split("#", 1)[0].strip() for line in lines]
    terms = list(dict.fromkeys(t for t in terms if t))  # drop empties and duplicates, keep order
    return ", ".join(terms) or None
