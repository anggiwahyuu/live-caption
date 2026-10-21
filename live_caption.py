#!/usr/bin/env python3
"""
Live Caption — real-time speech-to-text for online meetings on Linux, Windows and macOS.

Pipeline:
  AudioCapture (system audio and/or mic)
      Linux: parec, falling back to pw-record · Windows: PyAudioWPatch (WASAPI loopback)
      macOS: sounddevice (system audio through the BlackHole virtual device)
    -> Segmenter (draft every ~0.25 s while speaking + final on pause)
    -> AudioHub (finals are always processed, stale drafts are dropped so captions never fall behind)
    -> faster-whisper (STT, GPU via CUDA or CPU fallback; small model for drafts, main model for finals)
    -> TranslationWorker (optional, can be toggled from the UI)
    -> Tkinter overlay + transcript auto-saved to a .md file
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16_000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2  # s16le mono

IS_LINUX = sys.platform.startswith("linux")
IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# source name -> on-screen label
SOURCES = {
    "system": "Participant",  # audio coming out of the speakers/headset
    "mic": "Me",              # default microphone
}

# Phrases Whisper often "hallucinates" on short/near-silent audio (Indonesian and English output).
HALLUCINATIONS = {
    "terima kasih", "terima kasih.", "terima kasih telah menonton.",
    "sampai jumpa di video berikutnya.", "thank you", "thank you.",
    "thanks for watching!", "thanks for watching.", "you", "...",
}


# --------------------------------------------------------------------------- audio

class AudioHub:
    """Queue between the audio recorders and Whisper.

    - FINAL segments (finished sentences) are always processed, in order.
    - DRAFT segments (sentences still in progress) keep only the latest version per source.
      If the GPU is busy, old drafts are dropped right away, so the text never falls
      further and further behind the audio.
    """

    def __init__(self):
        self._finals: collections.deque = collections.deque()
        self._drafts: dict[str, tuple[int, np.ndarray]] = {}
        self._cv = threading.Condition()

    def put_final(self, label: str, seg_id: int, audio: np.ndarray, ts: dt.datetime) -> None:
        with self._cv:
            self._finals.append((label, seg_id, audio, ts))
            pending = self._drafts.get(label)
            if pending and pending[0] <= seg_id:
                del self._drafts[label]  # the draft for this sentence is now stale
            self._cv.notify()

    def put_draft(self, label: str, seg_id: int, audio: np.ndarray) -> None:
        with self._cv:
            self._drafts[label] = (seg_id, audio)
            self._cv.notify()

    def get(self, timeout: float):
        with self._cv:
            if not self._finals and not self._drafts:
                self._cv.wait(timeout)
            if self._finals:
                label, seg_id, audio, ts = self._finals.popleft()
                return "final", label, seg_id, audio, ts
            if self._drafts:
                label = next(iter(self._drafts))  # take turns between sources (Participant/Me)
                seg_id, audio = self._drafts.pop(label)
                return "draft", label, seg_id, audio, None
            return None

    def backlog(self) -> int:
        with self._cv:
            return len(self._finals)


class Segmenter:
    """Splits the audio stream into speech segments based on energy (RMS) and pauses.

    While someone is still speaking, the audio of the sentence in progress is sent periodically as a DRAFT
    so text can appear before the sentence ends. When the speaker pauses, the sentence is sent as FINAL.
    """

    def __init__(self, label: str, hub: AudioHub, threshold: float, silence_ms: int,
                 max_seconds: float, draft_interval: float = 0.5,
                 min_voiced_ms: int = 250, preroll_ms: int = 300):
        self.label = label
        self.hub = hub
        self.threshold = threshold
        self.silence_limit = max(1, silence_ms // FRAME_MS)
        self.max_frames = int(max_seconds * 1000 // FRAME_MS)
        self.draft_every = int(draft_interval * 1000 // FRAME_MS) if draft_interval > 0 else 0
        self.min_voiced = max(1, min_voiced_ms // FRAME_MS)  # minimum voiced duration (ignore coughs/clicks)
        self.preroll: collections.deque[np.ndarray] = collections.deque(maxlen=max(1, preroll_ms // FRAME_MS))
        self.noise_floor = 0.0
        self.buf: list[np.ndarray] = []
        self.in_speech = False
        self.speech_run = 0
        self.silence_run = 0
        self.voiced = 0
        self.seg_id = 0
        self.seg_start: dt.datetime | None = None
        self.last_draft_len = 0

    def _is_speech(self, rms: float) -> bool:
        return rms > max(self.threshold, self.noise_floor * 3.0)

    def feed(self, frame: np.ndarray) -> None:
        rms = float(np.sqrt(np.mean(frame * frame)))
        speech = self._is_speech(rms)

        if not self.in_speech:
            if speech:
                self.speech_run += 1
            else:
                self.speech_run = 0
                self.noise_floor = 0.95 * self.noise_floor + 0.05 * rms  # adapt to background noise
            self.preroll.append(frame)
            if self.speech_run >= 3:  # ~90 ms of continuous sound = speech has started
                self.in_speech = True
                self.buf = list(self.preroll)  # include the start of the word that was missed
                self.preroll.clear()
                self.silence_run = 0
                self.voiced = self.speech_run
                self.seg_start = dt.datetime.now()
                self.last_draft_len = len(self.buf)
            return

        self.buf.append(frame)
        if speech:
            self.voiced += 1
        self.silence_run = 0 if speech else self.silence_run + 1

        if self.silence_run >= self.silence_limit:
            self._emit()
            self.in_speech = False
            self.speech_run = 0
        elif len(self.buf) >= self.max_frames:
            self._emit()  # sentence too long: lock what we have, continue with the next segment
            self.seg_start = dt.datetime.now()
        elif (self.draft_every and speech and self.voiced >= self.min_voiced
              and len(self.buf) - self.last_draft_len >= self.draft_every):
            self.last_draft_len = len(self.buf)
            self.hub.put_draft(self.label, self.seg_id, np.concatenate(self.buf))

    def flush(self) -> None:
        if self.in_speech:
            self._emit()
            self.in_speech = False

    def _emit(self) -> None:
        if self.voiced >= self.min_voiced:
            self.hub.put_final(self.label, self.seg_id, np.concatenate(self.buf),
                               self.seg_start or dt.datetime.now())
        self.seg_id += 1
        self.buf = []
        self.silence_run = 0
        self.voiced = 0
        self.last_draft_len = 0


class Resampler:
    """Downmixes interleaved float32 audio to mono and resamples it to SAMPLE_RATE.

    Uses PyAV, which is already installed as a faster-whisper dependency.
    """

    def __init__(self, rate: int, channels: int):
        self.rate, self.channels = rate, channels
        self._av = None
        if rate != SAMPLE_RATE:
            import av
            self._av = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)

    def __call__(self, block: np.ndarray) -> np.ndarray:
        # Downmix ourselves: PyAV's own stereo -> mono mix boosts the level by ~3 dB.
        mono = block.reshape(-1, self.channels).mean(axis=1, dtype=np.float32) if self.channels > 1 else block
        if self._av is None:
            return mono
        import av
        frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(mono).reshape(1, -1), format="flt", layout="mono")
        frame.sample_rate = self.rate
        out = [f.to_ndarray().reshape(-1) for f in self._av.resample(frame)]
        return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)


class AudioCapture(threading.Thread):
    """Records one source ("system" or "mic") and feeds it to its Segmenter.

    Backends are tried in order: if one fails before delivering any audio (not installed,
    no sound server, no device), the next one is used.
    """

    def __init__(self, source: str, device: str | None, segmenter: Segmenter,
                 stop_event: threading.Event, report):
        super().__init__(daemon=True)
        self.source = source
        self.device = device  # --monitor: a specific output/loopback device for the "system" source
        self.segmenter = segmenter
        self.stop_event = stop_event
        self.report = report
        self.proc: subprocess.Popen | None = None
        self._pending = np.zeros(0, dtype=np.float32)

    def _backends(self):
        if IS_WINDOWS:
            return [("PyAudioWPatch", self._blocks_pyaudio)]
        if IS_MAC:
            return [("sounddevice", self._blocks_sounddevice)]
        return [("parec", self._blocks_parec), ("pw-record", self._blocks_pw_record)]

    def run(self) -> None:
        backends = self._backends()
        for i, (name, blocks) in enumerate(backends):
            started = False
            try:
                for block in blocks():
                    started = True
                    self._feed(block)
                    if self.stop_event.is_set():
                        break
                break
            except Exception as e:  # missing library/command, no sound server, no device, ...
                if self.stop_event.is_set():
                    break
                if started or i == len(backends) - 1:
                    self.report(f"Recording '{self.segmenter.label}' stopped ({name}): {e}")
                    break
                self.report(f"{name} unavailable ({e}), switching to {backends[i + 1][0]}", "info")
        self.segmenter.flush()

    def _feed(self, block: np.ndarray) -> None:
        """Re-chunk blocks of any length into the FRAME_SAMPLES frames the Segmenter expects."""
        buf = np.concatenate((self._pending, block)) if self._pending.size else block
        n = len(buf) // FRAME_SAMPLES * FRAME_SAMPLES
        for start in range(0, n, FRAME_SAMPLES):
            self.segmenter.feed(buf[start:start + FRAME_SAMPLES])
        self._pending = buf[n:]

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    # ---- Linux
    def _blocks_subprocess(self, cmd: list[str]):
        """Reads raw s16le mono audio at SAMPLE_RATE from a recorder command's stdout."""
        proc = self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert proc.stdout is not None and proc.stderr is not None
        try:
            while not self.stop_event.is_set():
                data = proc.stdout.read(FRAME_BYTES)
                if len(data) < FRAME_BYTES:
                    break
                yield np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            if not self.stop_event.is_set():
                err = proc.stderr.read().decode(errors="ignore").strip()
                raise RuntimeError(err or f"{cmd[0]} exited")
        finally:
            # Always reap the recorder, also when it failed and the next backend takes over.
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            proc.stdout.close()
            proc.stderr.close()

    def _blocks_parec(self):
        # parec is part of libpulse and runs on top of PulseAudio or pipewire-pulse.
        if self.source == "system":
            device = self.device or "@DEFAULT_MONITOR@"
        else:
            device = "@DEFAULT_SOURCE@"
        return self._blocks_subprocess(
            ["parec", f"--device={device}", "--format=s16le", f"--rate={SAMPLE_RATE}",
             "--channels=1", "--raw", "--latency-msec=50"])

    def _blocks_pw_record(self):
        # Native PipeWire recorder, for systems without parec or without pipewire-pulse.
        cmd = ["pw-record", "--raw", "--format", "s16", "--rate", str(SAMPLE_RATE),
               "--channels", "1", "--latency", "50ms"]
        if self.source == "system":
            cmd += ["-P", "stream.capture.sink=true"]  # record what a sink plays (its monitor)
            if self.device:
                # PulseAudio names monitors "<sink>.monitor"; PipeWire targets the sink itself.
                cmd += ["--target", self.device.removesuffix(".monitor")]
        return self._blocks_subprocess(cmd + ["-"])

    # ---- Windows
    def _blocks_pyaudio(self):
        import pyaudiowpatch as pyaudio  # pyrefly: ignore[missing-import]  (installed on Windows only)

        pa = pyaudio.PyAudio()
        stream = None
        try:
            if self.source == "system":
                if self.device:
                    info = next((d for d in pa.get_loopback_device_info_generator()
                                 if self.device.lower() in d["name"].lower()), None)
                    if info is None:
                        raise RuntimeError(f"no loopback device matching '{self.device}'")
                else:
                    info = pa.get_default_wasapi_loopback()  # loopback of the default speakers/headset
            else:
                info = pa.get_default_input_device_info()
            rate = int(info["defaultSampleRate"])
            channels = max(1, int(info["maxInputChannels"]))
            chunk = rate * FRAME_MS // 1000
            resample = Resampler(rate, channels)
            stream = pa.open(format=pyaudio.paFloat32, channels=channels, rate=rate, input=True,
                             input_device_index=info["index"], frames_per_buffer=chunk)
            while not self.stop_event.is_set():
                # WASAPI loopback delivers nothing while nothing is playing, so this read may block.
                data = stream.read(chunk, exception_on_overflow=False)
                yield resample(np.frombuffer(data, dtype=np.float32))
        finally:
            if stream is not None:
                stream.close()
            pa.terminate()

    # ---- macOS
    def _blocks_sounddevice(self):
        import sounddevice as sd  # pyrefly: ignore[missing-import]  (installed on macOS only)

        if self.source == "system":
            # macOS has no built-in loopback: system audio comes from a virtual device such as BlackHole.
            wanted = (self.device or "BlackHole").lower()
            index = next((i for i, d in enumerate(sd.query_devices())
                          if d["max_input_channels"] > 0 and wanted in d["name"].lower()), None)
            if index is None:
                raise RuntimeError(f"no input device matching '{self.device or 'BlackHole'}' "
                                   "(install BlackHole, see README)")
        else:
            index = None  # default microphone
        info = sd.query_devices(index, "input")
        rate = int(info["default_samplerate"])
        channels = min(2, int(info["max_input_channels"]))
        chunk = rate * FRAME_MS // 1000
        resample = Resampler(rate, channels)
        with sd.InputStream(device=index, channels=channels, samplerate=rate,
                            dtype="float32", blocksize=chunk) as stream:
            while not self.stop_event.is_set():
                data, _ = stream.read(chunk)
                yield resample(data.reshape(-1))


# --------------------------------------------------------------------------- translation

class Translator:
    """Optional translation. Backend 'google' (online, lightweight) or 'argos' (offline)."""

    def __init__(self, target: str, backend: str):
        self.target = target
        self.backend = backend
        self.enabled = False
        self._google = None
        self._argos_ready: set[tuple[str, str]] = set()

    def translate(self, text: str, src_lang: str | None) -> str | None:
        if not text:
            return None
        if src_lang and src_lang.split("-")[0] == self.target.split("-")[0]:
            return None  # already in the target language
        try:
            if self.backend == "google":
                return self._via_google(text)
            return self._via_argos(text, src_lang or "en")
        except Exception as e:  # network down, missing package, etc.
            return f"[translation failed: {e}]"

    def _via_google(self, text: str) -> str:
        if self._google is None:
            from deep_translator import GoogleTranslator
            self._google = GoogleTranslator(source="auto", target=self.target)
        return self._google.translate(text)

    def _via_argos(self, text: str, src: str) -> str:
        import argostranslate.translate
        if (src, self.target) not in self._argos_ready:
            self._install_argos(src)
            self._argos_ready.add((src, self.target))
        return argostranslate.translate.translate(text, src, self.target)

    def _install_argos(self, src: str) -> None:
        import argostranslate.package as pkg
        installed = {(p.from_code, p.to_code) for p in pkg.get_installed_packages()}
        if (src, self.target) in installed:
            return
        pkg.update_package_index()
        available = {(p.from_code, p.to_code): p for p in pkg.get_available_packages()}
        if (src, self.target) in available:
            needed = [(src, self.target)]
        else:  # pivot through English
            needed = [pair for pair in [(src, "en"), ("en", self.target)] if pair[0] != pair[1]]
        for pair in needed:
            if pair in installed:
                continue
            if pair not in available:
                raise RuntimeError(f"Argos package {pair[0]}→{pair[1]} is not available")
            pkg.install_from_path(available[pair].download())


class TranslationWorker(threading.Thread):
    """Translates on a separate thread so transcription is never held up."""

    def __init__(self, translator: Translator, tr_q: queue.Queue, ui_q: queue.Queue, stop_event: threading.Event):
        super().__init__(daemon=True)
        self.translator, self.tr_q, self.ui_q, self.stop_event = translator, tr_q, ui_q, stop_event

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                line_id, text, lang = self.tr_q.get(timeout=0.2)
            except queue.Empty:
                continue
            result = self.translator.translate(text, lang)
            if result:
                self.ui_q.put(("translation", line_id, result))


# --------------------------------------------------------------------------- STT

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
            kind, label, seg_id, audio, ts = item
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
                    self.ui_q.put(("line", ts, label, text, info.language, seg_id))
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


def make_transcribe_fn(beam_size: int):
    def _transcribe(model, audio, lang, final: bool = True):
        # Drafts are made as fast as possible (beam 1, no VAD); finals use the full settings.
        return model.transcribe(
            audio,
            language=lang,
            beam_size=beam_size if final else 1,
            vad_filter=final,                 # faster-whisper's built-in Silero VAD removes non-speech parts
            condition_on_previous_text=False, # prevents hallucinations chaining across segments
            without_timestamps=True,
        )
    return _transcribe


# --------------------------------------------------------------------------- UI

class App:
    COLORS = {"bg": "#111418", "fg": "#e8eaed", "muted": "#8a919a",
              "Participant": "#7dd3fc", "Me": "#86efac", "tr": "#fbbf24", "err": "#f87171"}

    def __init__(self, root, args, ui_q: queue.Queue, tr_q: queue.Queue, translator: Translator, on_close):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.root, self.args, self.ui_q, self.tr_q, self.translator = root, args, ui_q, tr_q, translator
        self.records: dict[int, dict] = {}
        self.next_id = 0
        self.view_floor = 0  # lines with id < this have been cleared from the screen
        self.drafts: dict[str, tuple[int, str]] = {}  # label -> (seg_id, temporary text)
        self.finalized: dict[str, int] = {}           # label -> next seg_id that may still be shown as a draft

        out_dir = Path(args.out_dir).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        self.started = dt.datetime.now()
        self.transcript_path = out_dir / f"meeting-{self.started:%Y-%m-%d_%H%M}.md"

        root.title("Live Caption")
        root.geometry(args.geometry)
        root.configure(bg=self.COLORS["bg"])
        root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", args.opacity)
        except tk.TclError:
            pass
        root.protocol("WM_DELETE_WINDOW", on_close)

        bar = tk.Frame(root, bg=self.COLORS["bg"])
        bar.pack(fill="x", padx=8, pady=(6, 0))

        self.status = tk.Label(bar, text="Starting…", anchor="w", bg=self.COLORS["bg"], fg=self.COLORS["muted"])
        self.status.pack(side="left", fill="x", expand=True)

        self.tr_var = tk.BooleanVar(value=translator.enabled)
        tk.Checkbutton(bar, text=f"Translate → {translator.target}", variable=self.tr_var,
                       command=self._toggle_translate, bg=self.COLORS["bg"], fg=self.COLORS["fg"],
                       selectcolor=self.COLORS["bg"], activebackground=self.COLORS["bg"],
                       activeforeground=self.COLORS["fg"], highlightthickness=0).pack(side="left", padx=6)

        self.pin_var = tk.BooleanVar(value=True)
        tk.Checkbutton(bar, text="Always on top", variable=self.pin_var,
                       command=lambda: root.attributes("-topmost", self.pin_var.get()),
                       bg=self.COLORS["bg"], fg=self.COLORS["fg"], selectcolor=self.COLORS["bg"],
                       activebackground=self.COLORS["bg"], activeforeground=self.COLORS["fg"],
                       highlightthickness=0).pack(side="left", padx=6)

        ttk.Button(bar, text="Save as…", command=self._save_as).pack(side="left", padx=3)
        ttk.Button(bar, text="Clear", command=self._clear).pack(side="left", padx=3)

        body = tk.Frame(root, bg=self.COLORS["bg"])
        body.pack(fill="both", expand=True, padx=8, pady=6)
        self.text = tk.Text(body, wrap="word", bg=self.COLORS["bg"], fg=self.COLORS["fg"],
                            insertbackground=self.COLORS["fg"], relief="flat", padx=8, pady=6,
                            font=("Sans", args.font_size), state="disabled", highlightthickness=0)
        scroll = ttk.Scrollbar(body, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)

        small = ("Sans", max(8, args.font_size - 3))
        self.text.tag_configure("time", foreground=self.COLORS["muted"], font=small)
        self.text.tag_configure("Participant", foreground=self.COLORS["Participant"], font=("Sans", args.font_size, "bold"))
        self.text.tag_configure("Me", foreground=self.COLORS["Me"], font=("Sans", args.font_size, "bold"))
        self.text.tag_configure("tr", foreground=self.COLORS["tr"], font=("Sans", args.font_size, "italic"))
        self.text.tag_configure("err", foreground=self.COLORS["err"])
        self.text.tag_configure("info", foreground=self.COLORS["muted"], font=small)
        self.text.tag_configure("draft", foreground=self.COLORS["muted"], font=("Sans", args.font_size, "italic"))
        # Draft area = from the "draft" mark to the end of the text. Final lines are always inserted right before it.
        self.text.mark_set("draft", "end-1c")

        root.after(30, self._poll)

    # ---- events from other threads
    def _poll(self) -> None:
        try:
            while True:
                msg = self.ui_q.get_nowait()
                kind = msg[0]
                if kind == "status":
                    self.status.configure(text=msg[1], fg=self.COLORS["muted"])
                elif kind == "error":
                    self.status.configure(text=msg[1], fg=self.COLORS["err"])
                    self._append([(f"⚠ {msg[1]}\n", "err")])
                elif kind == "info":  # notice only (e.g. recorder fallback): keep the status line as it is
                    self._append([(f"ℹ {msg[1]}\n", "info")])
                elif kind == "line":
                    self._add_line(*msg[1:])
                elif kind == "draft":
                    self._set_draft(*msg[1:])
                elif kind == "drop":
                    self._finish_draft(msg[1], msg[2])
                    self._render_drafts()
                elif kind == "translation":
                    self._add_translation(msg[1], msg[2])
        except queue.Empty:
            pass
        self.root.after(30, self._poll)

    def _append(self, parts, mark: str | None = None) -> None:
        """Insert final text right above the draft area."""
        at_bottom = self.text.yview()[1] > 0.98
        self.text.configure(state="normal")
        self.text.mark_gravity("draft", "right")  # the draft mark gets pushed down below the new text
        for content, tag in parts:
            if tag:
                self.text.insert("draft", content, tag)
            else:
                self.text.insert("draft", content)
        if mark:
            # Mark at the end of this line (left gravity) = where the translation is inserted later,
            # staying right below its original line even after new lines have come in.
            self.text.mark_set(mark, "draft")
            self.text.mark_gravity(mark, "left")
        self.text.configure(state="disabled")
        if at_bottom:
            self.text.see("end")

    # ---- drafts (temporary text while someone is still speaking)
    def _set_draft(self, label: str, seg_id: int, text: str) -> None:
        if seg_id < self.finalized.get(label, 0):
            return  # this sentence is already final, its draft arrived late
        self.drafts[label] = (seg_id, text)
        self._render_drafts()

    def _finish_draft(self, label: str, seg_id: int) -> None:
        self.finalized[label] = max(self.finalized.get(label, 0), seg_id + 1)
        current = self.drafts.get(label)
        if current and current[0] <= seg_id:
            del self.drafts[label]

    def _render_drafts(self) -> None:
        at_bottom = self.text.yview()[1] > 0.98
        self.text.configure(state="normal")
        self.text.delete("draft", "end-1c")
        self.text.mark_gravity("draft", "left")  # drafts are written after the mark, the mark stays in front of them
        for label, (_, text) in self.drafts.items():
            self.text.insert("end", f"… {label}: {text}\n", "draft")
        self.text.configure(state="disabled")
        if at_bottom:
            self.text.see("end")

    def _add_line(self, ts: dt.datetime, label: str, text: str, lang: str, seg_id: int) -> None:
        line_id = self.next_id
        self.next_id += 1
        self.records[line_id] = {"ts": ts, "label": label, "text": text, "lang": lang, "tr": None}
        self._append([(f"[{ts:%H:%M:%S}] ", "time"), (f"{label}: ", label), (f"{text}\n", None)],
                     mark=f"tr{line_id}")
        self._finish_draft(label, seg_id)
        self._render_drafts()
        if self.translator.enabled:
            self.tr_q.put((line_id, text, lang))
        self._autosave()

    def _add_translation(self, line_id: int, translation: str) -> None:
        rec = self.records.get(line_id)
        if rec is None:
            return
        rec["tr"] = translation
        mark = f"tr{line_id}"
        if line_id >= self.view_floor and mark in self.text.mark_names():
            at_bottom = self.text.yview()[1] > 0.98
            self.text.configure(state="normal")
            self.text.mark_gravity("draft", "right")  # translations must not end up in the draft area
            self.text.insert(mark, f"      ↳ {translation}\n", "tr")
            self.text.mark_unset(mark)
            self.text.configure(state="disabled")
            if at_bottom:  # don't yank the view down while the user is reading older lines
                self.text.see("end")
        self._autosave()

    # ---- buttons
    def _toggle_translate(self) -> None:
        self.translator.enabled = self.tr_var.get()

    def _clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.mark_set("draft", "end-1c")
        self.text.configure(state="disabled")
        self.view_floor = self.next_id  # only the screen is cleared; the transcript file stays intact
        self._render_drafts()

    def _save_as(self) -> None:
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(defaultextension=".md", initialfile=self.transcript_path.name,
                                            filetypes=[("Markdown", "*.md"), ("Text", "*.txt")])
        if path:
            Path(path).write_text(self._render(), encoding="utf-8")
            self.status.configure(text=f"Saved: {path}", fg=self.COLORS["muted"])

    # ---- transcript
    def _render(self) -> str:
        # Month names are hard-coded so the header doesn't depend on the system locale.
        months = ["January", "February", "March", "April", "May", "June", "July",
                  "August", "September", "October", "November", "December"]
        d = self.started
        lines = [f"# Meeting transcript — {months[d.month - 1]} {d.day}, {d.year}, {d:%H:%M}", ""]
        for rec in self.records.values():
            lines.append(f"- **[{rec['ts']:%H:%M:%S}] {rec['label']}:** {rec['text']}")
            if rec["tr"]:
                lines.append(f"  - _{rec['tr']}_")
        return "\n".join(lines) + "\n"

    def _autosave(self) -> None:
        try:
            self.transcript_path.write_text(self._render(), encoding="utf-8")
        except OSError as e:
            self.status.configure(text=f"Failed to save transcript: {e}", fg=self.COLORS["err"])


# --------------------------------------------------------------------------- main

def parse_args():
    p = argparse.ArgumentParser(description="Live captions + optional translation for online meetings.")
    p.add_argument("--model", default="small",
                   help="tiny | base | small | medium | large-v3-turbo | distil-large-v3 (default: small)")
    p.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    p.add_argument("--compute-type", default="int8",
                   help="Compute type on the GPU. int8 suits GTX 10xx (Pascal) best. (default: int8)")
    p.add_argument("--lang", default="auto", help="Speaker language code: id, en, ... or auto (default)")
    p.add_argument("--source", default="system", choices=["system", "mic", "both"],
                   help="system = other participants, mic = your voice, both = both (use a headset)")
    p.add_argument("--monitor", default=None,
                   help="Record a specific output instead of the default one. Linux: monitor source name "
                        "(pactl list short sources); Windows: part of the speaker/headset name; "
                        "macOS: part of the virtual input name (default: BlackHole)")
    p.add_argument("--translate", action="store_true", help="Enable translation from the start")
    p.add_argument("--translate-to", default="id", help="Translation target language (default: id)")
    p.add_argument("--translator", default="google", choices=["google", "argos"],
                   help="google = online & lightweight, argos = offline (large dependencies)")
    p.add_argument("--threshold", type=float, default=0.008,
                   help="Speech volume threshold (0..1). Raise it if noise gets transcribed.")
    p.add_argument("--silence-ms", type=int, default=700, help="Pause (ms) treated as the end of a sentence")
    p.add_argument("--max-seconds", type=float, default=10.0, help="Maximum length of one sentence before it is locked")
    p.add_argument("--draft-interval", type=float, default=0.25,
                   help="How often the temporary text updates while someone speaks (seconds). 0 = off")
    p.add_argument("--draft-model", default="base",
                   help="Small model used only for draft text, to keep captions live (tiny | base | ...). "
                        "'same' = use the main model (default: base)")
    p.add_argument("--beam-size", type=int, default=1, help="1 = fastest; 3–5 = more accurate but slower")
    p.add_argument("--font-size", type=int, default=13)
    p.add_argument("--opacity", type=float, default=0.93)
    p.add_argument("--geometry", default="760x280")
    p.add_argument("--out-dir", default="~/LiveCaption", help="Folder for auto-saved transcripts")
    args = p.parse_args()
    args._whisper_transcribe = make_transcribe_fn(args.beam_size)
    return args


def main() -> None:
    args = parse_args()
    if IS_LINUX and not (shutil.which("parec") or shutil.which("pw-record")):
        sys.exit("Neither parec nor pw-record was found. Install one with: sudo pacman -S libpulse (or pipewire)")

    import tkinter as tk

    stop = threading.Event()
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
        seg = Segmenter(SOURCES[name], hub, args.threshold, args.silence_ms, args.max_seconds, args.draft_interval)
        captures.append(AudioCapture(name, args.monitor if name == "system" else None, seg, stop, report))

    workers = [*captures, Transcriber(args, hub, ui_q, stop), TranslationWorker(translator, tr_q, ui_q, stop)]

    root = tk.Tk()

    def shutdown() -> None:
        stop.set()
        for c in captures:
            c.stop()
        root.destroy()

    App(root, args, ui_q, tr_q, translator, shutdown)
    for w in workers:
        w.start()
    try:
        root.mainloop()
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
