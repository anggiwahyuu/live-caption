"""The second speech-to-text engine: whisper.cpp, for computers without an NVIDIA GPU.

faster-whisper (CTranslate2) only runs on NVIDIA GPUs or the CPU. whisper.cpp runs on AMD, Intel and NVIDIA
GPUs through Vulkan (Linux, Windows) and on Apple GPUs through Metal, and on the CPU it is still about twice
as fast as faster-whisper for short sentences. Its whisper-server program is downloaded by setup from this
project's GitHub Releases (built by .github/workflows/whisper-cpp.yml from the official source) and runs in
the background: the model is loaded once and every sentence is sent to it over HTTP on 127.0.0.1.
"""

from __future__ import annotations

import atexit
import ctypes
import http.client
import io
import json
import os
import platform
import re
import socket
import subprocess
import time
import types
import urllib.request
import uuid
import wave
from pathlib import Path

import numpy as np

from .common import IS_LINUX, IS_MAC, IS_WINDOWS, SAMPLE_RATE

RELEASE_REPO = "anggiwahyuu/live-caption"
RELEASE_TAG = "whisper-bin-v1"  # whisper.cpp v1.9.5, see .github/workflows/whisper-cpp.yml
MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{}"
# The same names as faster-whisper's. 8-bit (q8_0) files: half the size, and faster on most GPUs and CPUs
# with the same text; large-v3 only exists as q5_0.
MODEL_FILES = {name: f"ggml-{name}-q8_0.bin" for name in
               ("tiny", "tiny.en", "base", "base.en", "small", "small.en", "medium", "medium.en", "large-v2",
                "large-v3-turbo")}
MODEL_FILES.update({"turbo": "ggml-large-v3-turbo-q8_0.bin", "large-v3": "ggml-large-v3-q5_0.bin",
                    "large": "ggml-large-v3-q5_0.bin"})
SHORT_AUDIO_CTX = 512  # encoder frames for clips up to ~10 s (1500 = the full 30 s window): 3x faster
LANG_CODES = {  # whisper.cpp reports the language by name (its g_lang table, src/whisper.cpp)
    "english": "en", "chinese": "zh", "german": "de", "spanish": "es", "russian": "ru", "korean": "ko",
    "french": "fr", "japanese": "ja", "portuguese": "pt", "turkish": "tr", "polish": "pl", "catalan": "ca",
    "dutch": "nl", "arabic": "ar", "swedish": "sv", "italian": "it", "indonesian": "id", "hindi": "hi",
    "finnish": "fi", "vietnamese": "vi", "hebrew": "he", "ukrainian": "uk", "greek": "el", "malay": "ms",
    "czech": "cs", "romanian": "ro", "danish": "da", "hungarian": "hu", "tamil": "ta", "norwegian": "no",
    "thai": "th", "urdu": "ur", "croatian": "hr", "bulgarian": "bg", "lithuanian": "lt", "latin": "la",
    "maori": "mi", "malayalam": "ml", "welsh": "cy", "slovak": "sk", "telugu": "te", "persian": "fa",
    "latvian": "lv", "bengali": "bn", "serbian": "sr", "azerbaijani": "az", "slovenian": "sl", "kannada": "kn",
    "estonian": "et", "macedonian": "mk", "breton": "br", "basque": "eu", "icelandic": "is", "armenian": "hy",
    "nepali": "ne", "mongolian": "mn", "bosnian": "bs", "kazakh": "kk", "albanian": "sq", "swahili": "sw",
    "galician": "gl", "marathi": "mr", "punjabi": "pa", "sinhala": "si", "khmer": "km", "shona": "sn",
    "yoruba": "yo", "somali": "so", "afrikaans": "af", "occitan": "oc", "georgian": "ka", "belarusian": "be",
    "tajik": "tg", "sindhi": "sd", "gujarati": "gu", "amharic": "am", "yiddish": "yi", "lao": "lo", "uzbek": "uz",
    "faroese": "fo", "haitian creole": "ht", "pashto": "ps", "turkmen": "tk", "nynorsk": "nn", "maltese": "mt",
    "sanskrit": "sa", "luxembourgish": "lb", "myanmar": "my", "tibetan": "bo", "tagalog": "tl", "malagasy": "mg",
    "assamese": "as", "tatar": "tt", "hawaiian": "haw", "lingala": "ln", "hausa": "ha", "bashkir": "ba",
    "javanese": "jw", "sundanese": "su", "cantonese": "yue"}


# ---- where things are

def data_dir() -> Path:
    """Per-user app data: ~/.local/share/live-caption, %LOCALAPPDATA%\\live-caption, ~/Library/Application Support/…"""
    if IS_WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif IS_MAC:
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "live-caption"


def install_dir() -> Path:
    return data_dir() / "whisper.cpp"


def server_path() -> Path:
    return install_dir() / ("whisper-server.exe" if IS_WINDOWS else "whisper-server")


def installed() -> bool:
    return server_path().is_file()


def asset_name(system: str | None = None, machine: str | None = None) -> str | None:
    """The release file for this computer, or None where there is no build (then faster-whisper is used)."""
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    x64 = machine in ("x86_64", "amd64")
    if system == "Linux" and x64:
        return "whisper-cpp-linux-x64.tar.gz"
    if system == "Windows" and x64:
        return "whisper-cpp-windows-x64.zip"
    if system == "Darwin" and machine == "arm64":
        return "whisper-cpp-macos-arm64.tar.gz"
    return None


def release_url(name: str) -> str:
    """LIVE_CAPTION_RELEASE_URL: another place with the same files (a mirror, or a local folder for testing)."""
    base = os.environ.get("LIVE_CAPTION_RELEASE_URL") or \
        f"https://github.com/{RELEASE_REPO}/releases/download/{RELEASE_TAG}"
    return f"{base.rstrip('/')}/{name}"


def model_file(name: str) -> str:
    """faster-whisper's model name -> whisper.cpp's file name. Raises ValueError for models it doesn't have."""
    if name in MODEL_FILES:
        return MODEL_FILES[name]
    if name.startswith("ggml-") and name.endswith(".bin"):
        return name
    raise ValueError(f"whisper.cpp has no model called '{name}'. Use one of: {', '.join(sorted(MODEL_FILES))}")


def model_path(name: str) -> Path:
    """A local .bin file as given, else where the named model is (or will be) downloaded to."""
    if Path(name).expanduser().is_file():
        return Path(name).expanduser()
    return install_dir() / "models" / model_file(name)


# ---- downloads (setup, and the app when a model is missing)

def download(url: str, dest: Path, progress=None) -> None:
    """Downloads to dest via dest.part, resuming a broken download. progress(done_bytes, total_bytes)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    done = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url, headers={"User-Agent": "live-caption", **({"Range": f"bytes={done}-"} if done else {})})
    with urllib.request.urlopen(req, timeout=60) as resp:
        if done and resp.status != 206:  # the server ignored the range: start over
            done = 0
        total = done + int(resp.headers.get("Content-Length") or 0)
        with open(part, "ab" if done else "wb") as f:
            while chunk := resp.read(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    if total and done != total:
        raise ConnectionError(f"download stopped at {done} of {total} bytes")
    part.replace(dest)


def ensure_model(name: str, progress=None) -> Path:
    path = model_path(name)
    if not path.is_file():
        download(MODEL_URL.format(model_file(name)), path, progress)
    return path


def install_binaries(progress=None) -> str:
    """Downloads this computer's whisper.cpp build from the release, checks it against the release's
    SHA256SUMS and unpacks it into install_dir(). Returns the version it installed."""
    import hashlib
    import shutil
    import tarfile
    import zipfile

    name = asset_name()
    if name is None:
        raise OSError(f"no whisper.cpp build for {platform.system()} {platform.machine()}")
    target = install_dir()
    archive = target.parent / name
    with urllib.request.urlopen(urllib.request.Request(release_url("SHA256SUMS"),
                                                       headers={"User-Agent": "live-caption"}), timeout=60) as r:
        sums = dict(reversed(line.split(maxsplit=1)) for line in r.read().decode().splitlines() if line.strip())
    expected = sums.get(name) or sums.get(f"*{name}")
    if expected is None:
        raise OSError(f"{name} is not in the release's SHA256SUMS")
    download(release_url(name), archive, progress)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != expected:
        archive.unlink()
        raise OSError(f"{name} is damaged (SHA-256 {digest[:12]}…, expected {expected[:12]}…): run setup again")

    staging = target.with_name("whisper.cpp.new")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(staging)
    else:
        with tarfile.open(archive) as t:
            # "data": no absolute paths, no links out of the folder (Python 3.10.12+ / 3.11.4+)
            t.extractall(staging, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
    models = target / "models"
    if models.is_dir():  # keep downloaded models across updates
        models.replace(staging / "models")
    shutil.rmtree(target, ignore_errors=True)
    staging.replace(target)
    archive.unlink()
    if not IS_WINDOWS:
        server_path().chmod(0o755)
    return (target / "VERSION").read_text().strip() if (target / "VERSION").exists() else RELEASE_TAG


# ---- devices

def list_gpus() -> list[dict]:
    """The GPUs whisper.cpp can use, as it numbers them: [{"index", "name", "integrated"}].
    Read from what whisper-server prints when it starts (it then stops at --help)."""
    if not installed():
        return []
    try:
        out = subprocess.run([str(server_path()), "--help"], capture_output=True, text=True, timeout=60,
                             errors="replace", creationflags=_NO_WINDOW).stderr
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_gpus(out)


def parse_gpus(log: str) -> list[dict]:
    gpus = []
    for m in re.finditer(r"ggml_vulkan: (\d+) = (.+?) \(.*?\) \| uma: (\d)", log):  # Vulkan (Linux, Windows)
        gpus.append({"index": int(m[1]), "name": m[2].strip(), "integrated": m[3] == "1"})
    if not gpus and (m := re.search(r"ggml_metal\w*: (?:found device|GPU name): +(.+)", log)):  # Apple
        gpus.append({"index": 0, "name": m[1].strip(), "integrated": True})
    return gpus


def choose_device(setting: str, gpus: list[dict]) -> int | None:
    """setting "cpu" / "gpu" / "gpu:N" / "auto" -> the GPU index to use, or None for the CPU.
    auto: the first separate (discrete) GPU; a built-in one only on a Mac. Built-in PC graphics are often
    slower than the CPU (setup measures it and saves "gpu:N" or "cpu" when the GPU wins or loses)."""
    if setting == "cpu" or not gpus:
        return None
    if m := re.fullmatch(r"gpu(?::(\d+))?", setting):
        index = int(m[1] or 0)
        return index if any(g["index"] == index for g in gpus) else gpus[0]["index"]
    discrete = [g for g in gpus if not g["integrated"]]
    if discrete:
        return discrete[0]["index"]
    return gpus[0]["index"] if IS_MAC else None


# ---- the server

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Windows: no console window for the server


def _die_with_parent() -> None:
    """Linux: the server stops when the app does, even if the app is killed (no orphan holding the GPU)."""
    try:
        ctypes.CDLL("libc.so.6").prctl(1, 15)  # PR_SET_PDEATHSIG, SIGTERM
    except OSError:
        pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wav_bytes(audio: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def multipart(fields: dict, audio: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
             f"Content-Type: audio/wav\r\n\r\n".encode() + audio + b"\r\n"]
    for key, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def parse_result(data: dict) -> tuple[list, types.SimpleNamespace]:
    """verbose_json -> (segments, info) shaped like faster-whisper's, which the transcriber reads."""
    texts = [s.get("text", "") for s in data.get("segments") or []] or [data.get("text", "")]
    segments = [types.SimpleNamespace(text=t) for t in texts if t.strip()]
    name = str(data.get("language", "")).lower()
    return segments, types.SimpleNamespace(language=LANG_CODES.get(name, name or None))


class WhisperCppServer:
    """One model loaded by a whisper-server process. transcribe() takes the same arguments as
    faster_whisper.WhisperModel.transcribe (those whisper.cpp has no use for are ignored)."""

    def __init__(self, model: Path, gpu: int | None, threads: int | None = None, log: Path | None = None):
        self.model, self.gpu = model, gpu
        self.port = _free_port()
        threads = threads or max(2, min(8, (os.cpu_count() or 4) // 2))  # about the number of physical cores
        # -nlp: verbose_json would otherwise score every language for each sentence, which doubles the time
        cmd = [str(server_path()), "-m", str(model), "--host", "127.0.0.1", "--port", str(self.port),
               "-t", str(threads), "-nlp"]
        cmd += ["-ng"] if gpu is None else ["-dev", str(gpu)]
        self.log_path = log or install_dir() / "server.log"
        self.log = open(self.log_path, "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(cmd, stdout=self.log, stderr=subprocess.STDOUT, creationflags=_NO_WINDOW,
                                     preexec_fn=_die_with_parent if IS_LINUX else None)
        self.conn: http.client.HTTPConnection | None = None
        atexit.register(self.close)  # the app quits normally: stop the server with it (Linux also covers kills)
        self._wait_ready()

    def _log_tail(self) -> str:
        try:
            lines = self.log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        except OSError:
            return ""
        return " / ".join(lines[-3:])

    def _wait_ready(self, timeout: float = 300) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"whisper-server stopped (exit code {self.proc.returncode}): {self._log_tail()}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as r:
                    if json.load(r).get("status") == "ok":
                        return
            except (OSError, ValueError):
                pass
            time.sleep(0.2)
        self.close()
        raise RuntimeError("whisper-server didn't load the model in time")

    def device_name(self) -> str:
        """ "Vulkan (AMD Radeon RX 6600)", "Metal (Apple M2)" or "CPU", from what the server printed."""
        if self.gpu is None:
            return "CPU"
        try:
            log = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            log = ""
        gpu = next((g for g in parse_gpus(log) if g["index"] == self.gpu), None)
        api = "Metal" if IS_MAC else "Vulkan"
        return f"{api} ({gpu['name']})" if gpu else api

    def transcribe(self, audio, language=None, beam_size=1, hotwords=None, **_ignored):
        fields = {"response_format": "verbose_json", "language": language or "auto", "beam_size": beam_size,
                  "temperature": "0.0", "no_timestamps": "true",
                  # shorter encoder window for short clips (the whole clip must still fit in it)
                  "audio_ctx": SHORT_AUDIO_CTX if len(audio) <= 10.2 * SAMPLE_RATE else 0}
        if hotwords:
            fields["prompt"] = hotwords  # whisper.cpp has no hotwords: the initial prompt does a similar job
        body, ctype = multipart(fields, wav_bytes(audio))
        for attempt in (1, 2):  # one retry with a fresh connection (the kept-alive one may have been closed)
            try:
                if self.conn is None:
                    self.conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
                self.conn.request("POST", "/inference", body, {"Content-Type": ctype})
                resp = self.conn.getresponse()
                data = resp.read()
                if resp.status != 200:
                    raise RuntimeError(f"whisper-server: HTTP {resp.status} {data[:200]!r}")
                return parse_result(json.loads(data))
            except (OSError, http.client.HTTPException):
                self.conn = None
                if attempt == 2 or self.proc.poll() is not None:
                    raise RuntimeError(f"whisper-server is not responding: {self._log_tail()}") from None
        raise AssertionError("unreachable")

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.log.close()

