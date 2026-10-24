"""Audio capture and segmentation: recorders -> Segmenter -> AudioHub (the queue Whisper reads from)."""

from __future__ import annotations

import collections
import datetime as dt
import subprocess
import threading

import numpy as np

from .common import FRAME_BYTES, FRAME_MS, FRAME_SAMPLES, IS_MAC, IS_WINDOWS, SAMPLE_RATE


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

    def put_final(self, label: str, seg_id: int, audio: np.ndarray, ts: dt.datetime,
                  end: dt.datetime | None = None) -> None:
        with self._cv:
            self._finals.append((label, seg_id, audio, ts, end or ts))
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
                label, seg_id, audio, ts, end = self._finals.popleft()
                return "final", label, seg_id, audio, ts, end
            if self._drafts:
                label = next(iter(self._drafts))  # take turns between sources (Participant/Me)
                seg_id, audio = self._drafts.pop(label)
                return "draft", label, seg_id, audio, None, None
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
                 min_voiced_ms: int = 250, preroll_ms: int = 300,
                 paused: threading.Event | None = None):
        self.label = label
        self.hub = hub
        self.paused = paused or threading.Event()  # set = audio is ignored (Pause button)
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
        self.start_len = 0  # frames in buf from before seg_start (the preroll)
        self.last_draft_len = 0

    def _is_speech(self, rms: float) -> bool:
        return rms > max(self.threshold, self.noise_floor * 3.0)

    def feed(self, frame: np.ndarray) -> None:
        if self.paused.is_set():
            # Lock what was said before the pause, then ignore audio until resumed.
            self.flush()
            self.speech_run = 0
            self.preroll.clear()
            return
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
                self.start_len = len(self.buf)
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
            start = self.seg_start or dt.datetime.now()
            spoken = max(0, len(self.buf) - self.start_len - self.silence_run)  # frames until the speaker stopped
            end = start + dt.timedelta(milliseconds=spoken * FRAME_MS)
            self.hub.put_final(self.label, self.seg_id, np.concatenate(self.buf), start, end)
        self.seg_id += 1
        self.start_len = 0
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
    no sound server, no device), the next one is used. When recording breaks off later (a Bluetooth headset
    disconnects, a USB device is unplugged), it reconnects on its own, to the default device of that moment.
    """

    RETRY_WAITS = (1.0, 2.0, 5.0)  # seconds between reconnect attempts; the last one repeats until it works
    STABLE_SECONDS = 2.0  # audio this long without a failure = reconnected (not a device that drops right away)

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
        self.attempt = 0  # reconnect attempts since the recording broke off (0 = recording normally)

    def _backends(self):
        if IS_WINDOWS:
            return [("PyAudioWPatch", self._blocks_pyaudio)]
        if IS_MAC:
            return [("sounddevice", self._blocks_sounddevice)]
        return [("parec", self._blocks_parec), ("pw-record", self._blocks_pw_record)]

    def run(self) -> None:
        label = self.segmenter.label
        recorded = False  # audio came through once: a failure after that is a lost device, so reconnect
        while not self.stop_event.is_set():
            failure = self._record_once()
            if failure is None or self.stop_event.is_set():
                break
            name, error, started = failure
            recorded = recorded or started
            if not recorded:  # never worked (no recorder, no sound server, no device): retrying won't help
                self.report(f"Recording '{label}' stopped ({name}): {error}")
                break
            self.segmenter.flush()  # keep what was said before the break
            self._pending = np.zeros(0, dtype=np.float32)
            if self.attempt == 0:
                self.report(f"Recording '{label}' lost at {dt.datetime.now():%H:%M:%S} ({name}: {error}). "
                            "Reconnecting…")
            self.stop_event.wait(self.RETRY_WAITS[min(self.attempt, len(self.RETRY_WAITS) - 1)])
            self.attempt += 1
        self.segmenter.flush()

    def _record_once(self):
        """Records with the first backend that works until stopped (-> None) or until it fails
        (-> (backend name, error, whether any audio came through))."""
        backends = self._backends()
        for i, (name, blocks) in enumerate(backends):
            started = False
            samples = 0
            try:
                for block in blocks():
                    started = True
                    if self.attempt:
                        samples += len(block)
                        if samples >= self.STABLE_SECONDS * SAMPLE_RATE:
                            self.attempt = 0
                            self.report(f"Recording '{self.segmenter.label}' again from "
                                        f"{dt.datetime.now():%H:%M:%S}", "recovered")
                    self._feed(block)
                    if self.stop_event.is_set():
                        break
                return None
            except Exception as e:  # missing library/command, no sound server, no device, ...
                if self.stop_event.is_set():
                    return None
                if started or i == len(backends) - 1:
                    return name, e, started
                if not self.attempt:  # while reconnecting, the same notice every few seconds is only noise
                    self.report(f"{name} unavailable ({e}), switching to {backends[i + 1][0]}", "info")
        return None

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

        if self.attempt:  # reconnecting: PortAudio only sees devices added or removed after a restart
            sd._terminate()
            sd._initialize()
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
