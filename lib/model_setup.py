"""Model downloads for setup.sh / setup.ps1: the Whisper models (speech-to-text) and the summary model (Ollama).

Every step is checked first (already there? enough disk space and RAM?), retried on network errors
(finished parts are kept, so a retry resumes), and if it still fails, the result list at the end says
what is missing and the command that fixes it. Summaries are optional: when the summary model can't
be installed, captions still work and only the Summary button shows an error.

Run with: .venv/bin/python -m lib.model_setup [--yes] [--no-whisper] [--summary | --no-summary | --summary-only]
                                               [app options…]
The app options (--model, --summary-model, …) and config.toml decide which models are downloaded,
so setup gets exactly the models the app will use.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from . import whispercpp
from .common import IS_LINUX, IS_MAC, IS_WINDOWS
from .config import config_dir, parse_args
from .summary import Summarizer


# Approximate download sizes in GB, for the free disk space check.
WHISPER_GB = {"tiny": 0.08, "base": 0.15, "small": 0.5, "medium": 1.5, "large-v1": 3.1, "large-v2": 3.1,
              "large-v3": 3.1, "large": 3.1, "large-v3-turbo": 1.6, "turbo": 1.6, "distil-large-v2": 1.5,
              "distil-large-v3": 1.5, "distil-large-v3.5": 1.5, "distil-medium.en": 0.8, "distil-small.en": 0.35}
OLLAMA_GB = {"qwen2.5:7b": 4.7, "qwen2.5:3b": 1.9}
OLLAMA_MIN_RAM_GB = {"qwen2.5:7b": 8}  # total RAM below which the smaller model is recommended (README: Meeting summary)
SMALL_SUMMARY_MODEL = "qwen2.5:3b"  # the fallback: half the size, twice as fast, less reliable
RETRIES = 3
# How the user starts setup and the app on this system, for the hints in the messages.
SETUP = r".\setup.ps1" if IS_WINDOWS else "./setup.sh"
RUN = r".\run.bat" if IS_WINDOWS else "./run.sh"
VENV_PY = r".venv\Scripts\python.exe" if IS_WINDOWS else ".venv/bin/python"

OK, WARN, FAIL, SKIP = "✓", "!", "✗", "–"
ASSUME_YES = False


def say(text: str = "") -> None:
    print(text, flush=True)


def step(title: str) -> None:
    say(f"\n==> {title}")


def ask(question: str, default: bool) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    if ASSUME_YES or not sys.stdin.isatty():
        say(f"{question} {hint} {'y' if default else 'n'} (automatic)")
        return default
    while True:
        answer = input(f"{question} {hint} ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes", "ya"):
            return True
        if answer in ("n", "no", "tidak"):
            return False


class Report:
    """What setup did, printed at the end with the fix for everything that failed."""

    def __init__(self):
        self.rows: list[tuple[str, str, str, list[str]]] = []

    def add(self, status: str, part: str, text: str, fixes: list[str] | None = None) -> None:
        self.rows.append((status, part, text, fixes or []))

    @property
    def failed(self) -> bool:
        return any(status == FAIL for status, *_ in self.rows)

    def print(self) -> None:
        step("Result")
        width = max((len(part) for _, part, _, _ in self.rows), default=0)
        for status, part, text, fixes in self.rows:
            say(f"  {status} {part.ljust(width)}  {text}")
            for fix in fixes:
                say(f"      → {fix}")


def with_retries(what: str, fn, permanent=lambda e: False):
    """fn() with up to RETRIES attempts. Errors that a retry can't fix (wrong name, disk full) are raised at once."""
    for attempt in range(1, RETRIES + 1):
        try:
            return fn()
        except Exception as e:
            if permanent(e) or attempt == RETRIES:
                raise
            wait = 5 * attempt
            say(f"   {what} failed ({e}). Trying again in {wait} s (attempt {attempt + 1} of {RETRIES})…")
            time.sleep(wait)


def free_gb(path: Path) -> float:
    path = Path(path)
    while not path.exists() and path != path.parent:  # the folder may not exist before the first download
        path = path.parent
    return shutil.disk_usage(path).free / 1e9


def total_ram_gb() -> float | None:
    try:
        if IS_LINUX:
            kb = int(re.search(r"MemTotal:\s+(\d+)", Path("/proc/meminfo").read_text())[1])
            return kb / 1024 / 1024
        if IS_MAC:
            return int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout) / 1024**3
    except (OSError, ValueError, TypeError):
        pass
    return None  # unknown (e.g. Windows): skip the RAM check


def save_setting(path: Path, key: str, value: str) -> None:
    """Sets key = "value" in config.toml: replaces the active line for it, else appends one. Comments are kept."""
    line = f"{key} = {json.dumps(value)}"  # a JSON string is a valid TOML basic string
    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        text = "# Live Caption settings. See \"Saving your settings\" in README.md; " + RUN + " --init-config lists every option.\n"
    pattern = re.compile(rf"^{key.replace('-', '[-_]')}\s*=.*$", re.M)  # summary-model and summary_model both work
    if pattern.search(text):
        text = pattern.sub(lambda _: line, text, count=1)
    else:
        text = text.rstrip("\n") + "\n" + line + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---- Whisper ----

def setup_whisper(args, report: Report) -> None:
    step("Whisper models (speech-to-text)")
    from faster_whisper.utils import download_model
    from huggingface_hub.constants import HF_HUB_CACHE

    names = [args.model]
    if args.draft_interval and args.draft_model not in ("same", args.model):
        names.append(args.draft_model)
    for name in names:
        main = name == args.model
        part = f"Whisper '{name}' ({'main' if main else 'drafts'})"
        if Path(name).expanduser().is_dir():
            report.add(OK, part, "local folder, nothing to download")
            continue
        try:
            download_model(name, local_files_only=True)
            say(f"   {name}: already downloaded")
            report.add(OK, part, "already downloaded")
            continue
        except Exception:
            pass  # not in the cache yet

        if main:
            later = f"{RUN} also tries to download it on its first start"
        else:
            later = "Captions work without it: the app then uses the main model for drafts too (less live)"
        size = WHISPER_GB.get(name.removesuffix(".en"))
        if size and (free := free_gb(HF_HUB_CACHE)) < size + 0.5:
            report.add(FAIL, part, f"needs about {size} GB, but only {free:.1f} GB is free in {HF_HUB_CACHE}",
                       [f"Free some disk space, then run {SETUP} again"] +
                       ([f"Or pick a smaller model (docs/models.md), e.g.: {SETUP} --model base"] if main else []))
            continue

        say(f"   Downloading {name}" + (f" (about {size} GB)…" if size else "…"))
        try:
            with_retries(f"Downloading {name}", lambda: download_model(name),
                         permanent=lambda e: type(e).__name__ in ("RepositoryNotFoundError", "ValueError"))
            report.add(OK, part, "downloaded")
        except Exception as e:
            fixes = [f"Check the internet connection (and HTTPS_PROXY behind a proxy), then run {SETUP} again: "
                     "the part already downloaded is kept"]
            if type(e).__name__ in ("RepositoryNotFoundError", "ValueError"):
                fixes = [f"'{name}' isn't a Whisper model name. Names: tiny, base, small, medium, large-v3-turbo, …"]
            report.add(FAIL if main else WARN, part, f"download failed: {e}", fixes + [later])


# ---- Summary: Ollama ----

class OllamaError(Exception):
    """An error message from the Ollama server itself (not a network problem)."""


def _ollama_request(url: str, path: str, body: dict | None = None, timeout: float = 10):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"{url}{path}", data=data, headers={"Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail)["error"]
        except (ValueError, KeyError, TypeError):
            pass
        raise OllamaError(detail or f"HTTP {e.code}") from None


def ollama_up(url: str, wait: float = 0) -> bool:
    deadline = time.monotonic() + wait
    while True:
        try:
            with _ollama_request(url, "/api/tags", timeout=3):
                return True
        except (OSError, OllamaError):
            if time.monotonic() >= deadline:
                return False
            time.sleep(1)


def has_model(url: str, model: str) -> bool:
    with _ollama_request(url, "/api/tags") as resp:
        names = {m.get("name") for m in json.load(resp).get("models", [])}
    return model in names or (":" not in model and f"{model}:latest" in names)


def pull_model(url: str, model: str) -> None:
    """Downloads a model through the Ollama API, with a progress line. Ollama resumes a broken download."""
    with _ollama_request(url, "/api/pull", {"model": model, "name": model, "stream": True}, timeout=300) as resp:
        shown = ""
        for raw in resp:
            if not raw.strip():
                continue
            msg = json.loads(raw)
            if msg.get("error"):
                raise OllamaError(msg["error"])
            status = msg.get("status", "")
            if msg.get("total") and status.startswith("pulling "):
                done, total = msg.get("completed", 0) / 1e9, msg["total"] / 1e9
                sys.stdout.write(f"\r   downloading {100 * done / total:3.0f}%  {done:.2f} / {total:.2f} GB ")
                sys.stdout.flush()
                shown = "progress"
            elif status != shown:
                say(("\n" if shown == "progress" else "") + f"   {status}")
                shown = status
            if status == "success":
                return
    raise ConnectionError("the download stopped before it finished")


def check_loads(url: str, model: str) -> None:
    """Loads the model once (and unloads it right after) to catch "not enough memory" now, not at the end of a meeting."""
    body = {"model": model, "prompt": "Reply with OK.", "stream": False, "keep_alive": 0,
            "options": {"num_predict": 2}}
    with _ollama_request(url, "/api/generate", body, timeout=600):
        pass


def _permanent_pull_error(e: Exception) -> bool:
    return isinstance(e, OllamaError) and bool(re.search(r"does not exist|not found|manifest unknown|no space", str(e)))


def _os_family() -> str:
    try:
        info = dict(re.findall(r'^(\w+)="?([^"\n]*)"?$', Path("/etc/os-release").read_text(), re.M))
    except OSError:
        return ""
    return f"{info.get('ID', '')} {info.get('ID_LIKE', '')}"


def ollama_install_command() -> list[str] | None:
    """The command that installs Ollama here (its last item is what the user sees), or None: the installer from ollama.com."""
    if IS_WINDOWS:
        if not shutil.which("winget"):
            return None
        return ["powershell", "-NoProfile", "-Command",
                "winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements"]
    if IS_MAC:
        return ["sh", "-c", "brew install ollama && brew services start ollama"] if shutil.which("brew") else None
    if "arch" in _os_family().split():
        return ["sh", "-c", "sudo pacman -S --needed ollama && sudo systemctl enable --now ollama"]
    return ["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"]


def find_ollama() -> bool:
    """Is the ollama command available? On Windows a fresh install isn't on this process's PATH yet: add its folder."""
    if IS_WINDOWS and not shutil.which("ollama"):
        folder = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama"
        if (folder / "ollama.exe").exists():
            os.environ["PATH"] = f"{folder}{os.pathsep}{os.environ.get('PATH', '')}"
    return bool(shutil.which("ollama"))


def background_hint() -> str:
    """How to keep the Ollama server running, on this system."""
    if IS_WINDOWS:
        return "Start the Ollama app from the Start menu (it stays in the tray), or run: ollama serve"
    if IS_MAC:
        return "brew services start ollama   (or open the Ollama app)"
    return "sudo systemctl enable --now ollama   (or: ollama serve, in its own terminal)"


def start_ollama(url: str, report: Report):
    """Makes the Ollama server reachable. Returns a server started just for setup (stopped at the end), or None.
    Raises LookupError, after reporting why, when there is no server to talk to."""
    part = "Summary (Ollama)"
    later = f"Captions work without it; only the Summary button needs Ollama. Afterwards: {SETUP} --summary-only"
    if urlparse(url).hostname not in ("localhost", "127.0.0.1", "::1"):
        report.add(FAIL, part, f"can't reach Ollama at {url}",
                   ["Start Ollama on that machine (and allow remote access: OLLAMA_HOST=0.0.0.0), or fix --ollama-url",
                    later])
        raise LookupError

    if not find_ollama():
        command = ollama_install_command()
        say("   Ollama is not installed. It runs the summary model locally, so the transcript never leaves your computer.")
        # Only with a terminal: the installer may ask for a password. --yes answers the question itself.
        if command and sys.stdin.isatty() and ask(f"   Install it now ({command[-1]})?", True):
            if subprocess.run(command).returncode != 0:
                say("   Installing Ollama failed (see the messages above).")
        if not find_ollama():
            fixes = ["Install it: " + (command[-1] if command else "download the installer from https://ollama.com/download"),
                     later,
                     "Or summarize with the Claude API instead (online, the transcript is sent to Anthropic): "
                     "--summarizer claude (README: Meeting summary)"]
            report.add(FAIL, part, "Ollama is not installed", fixes)
            raise LookupError
        if ollama_up(url, wait=15):  # the installer usually starts it as a service
            return None

    if IS_LINUX and subprocess.run(["systemctl", "cat", "ollama"], capture_output=True).returncode == 0:
        say("   The Ollama service is installed but not running.")
        if sys.stdin.isatty() and ask("   Start it, and start it at every boot (sudo systemctl enable --now ollama)?", True):
            subprocess.run(["sudo", "systemctl", "enable", "--now", "ollama"])
            if ollama_up(url, wait=15):
                return None

    say("   Starting Ollama just for this setup (ollama serve)…")
    host = urlparse(url)
    server = subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              env={**os.environ, "OLLAMA_HOST": f"{host.hostname}:{host.port or 11434}"})
    if not ollama_up(url, wait=20):
        server.terminate()
        report.add(FAIL, part, "Ollama is installed but won't start",
                   ["Run `ollama serve` in a terminal and read its error", later])
        raise LookupError
    report.add(WARN, "Ollama server", "not running in the background: start it before using Summary",
               [background_hint()])
    return server


def ollama_models_dir() -> Path:
    if os.environ.get("OLLAMA_MODELS"):
        return Path(os.environ["OLLAMA_MODELS"])
    for folder in (Path("/var/lib/ollama"), Path("/usr/share/ollama/.ollama/models")):  # distro package, official script
        if folder.exists():
            return folder
    return Path.home() / ".ollama" / "models"


def install_summary_model(url: str, model: str, local: bool) -> str | None:
    """Downloads model (if needed) and checks it loads. Returns None when it worked, else what went wrong."""
    try:
        if has_model(url, model):
            say(f"   {model}: already downloaded")
        else:
            size = OLLAMA_GB.get(model)
            if local and size and (free := free_gb(ollama_models_dir())) < size + 0.5:
                return f"needs about {size} GB, but only {free:.1f} GB is free in {ollama_models_dir()}"
            say(f"   Downloading {model}" + (f" (about {size} GB)…" if size else "…"))
            with_retries(f"Downloading {model}", lambda: pull_model(url, model), permanent=_permanent_pull_error)
        say(f"   Checking that {model} loads (can take a minute)…")
        check_loads(url, model)
        return None
    except (OSError, OllamaError, ValueError) as e:
        if isinstance(e, OllamaError) and re.search(r"does not exist|manifest unknown|not found", str(e)):
            return f"Ollama doesn't know a model called '{model}' (names: https://ollama.com/library): {e}"
        return str(e)


def setup_ollama(args, report: Report) -> None:
    url = args.ollama_url.rstrip("/")
    wanted = args.summary_model or Summarizer.DEFAULT_MODELS["ollama"]
    try:
        server = None if ollama_up(url) else start_ollama(url, report)
    except LookupError:
        return
    try:
        candidates = [wanted]
        ram = total_ram_gb()
        if ram and ram < OLLAMA_MIN_RAM_GB.get(wanted, 0):
            say(f"   This computer has {ram:.0f} GB of RAM; {wanted} needs about {OLLAMA_MIN_RAM_GB[wanted]} GB.")
            if ask(f"   Use the smaller {SMALL_SUMMARY_MODEL} instead (twice as fast, but less reliable)?", True):
                candidates = [SMALL_SUMMARY_MODEL]
        if candidates[0] != SMALL_SUMMARY_MODEL:
            candidates.append(SMALL_SUMMARY_MODEL)  # the fallback if the first one fails

        local = urlparse(url).hostname in ("localhost", "127.0.0.1", "::1")
        problems = []
        for i, model in enumerate(candidates):
            if i and not ask(f"   Install the smaller {model} instead (about {OLLAMA_GB[model]} GB, "
                             f"twice as fast, but less reliable)?", True):
                break
            problem = install_summary_model(url, model, local)
            if problem is None:
                finish_summary_model(args, model, report)
                return
            say(f"   {model} failed: {problem}")
            problems.append(f"{model}: {problem}")
        report.add(FAIL, "Summary (Ollama)", "no summary model installed. " + " | ".join(problems),
                   [f"Fix the problem above (internet, disk space, RAM), then: {SETUP} --summary-only",
                    f"By hand: ollama pull {wanted}",
                    "Captions work without it; only the Summary button shows an error"])
    finally:
        if server:
            server.terminate()
            server.wait(timeout=10)


def finish_summary_model(args, model: str, report: Report) -> None:
    """Makes the app use the model that was installed, if it isn't the one it uses already."""
    plain = ["--config", str(args._config_path)] if args._config_path else []  # without the options given to setup
    configured = parse_args(plain).summary_model or Summarizer.DEFAULT_MODELS["ollama"]
    if model == configured:
        report.add(OK, "Summary (Ollama)", f"{model} ready")
        return
    path = args._config_path or config_dir() / "config.toml"
    say(f"   {RUN} uses {configured} for summaries unless told otherwise.")
    if ask(f'   Save summary-model = "{model}" in {path}, so it uses {model}?', True):
        save_setting(path, "summary-model", model)
        report.add(OK, "Summary (Ollama)", f"{model} ready (saved in {path})")
    else:
        report.add(WARN, "Summary (Ollama)", f"{model} ready, but the app uses {configured}",
                   [f"Start it with: {RUN} --summary-model {model}"])


# ---- Summary: Claude API ----

def setup_claude(report: Report) -> None:
    part = "Summary (Claude API)"
    try:
        import anthropic  # noqa: F401
    except ImportError:
        report.add(FAIL, part, "the 'anthropic' package is missing",
                   [f"uv pip install --python {VENV_PY} anthropic"])
        return
    if os.environ.get("ANTHROPIC_API_KEY"):
        report.add(OK, part, "API key found. The transcript is sent to Anthropic for every summary")
    else:
        report.add(WARN, part, "no ANTHROPIC_API_KEY in this terminal",
                   [("set" if IS_WINDOWS else "export") + f" ANTHROPIC_API_KEY=sk-ant-...   (or: ant auth login), before {RUN}"])


def gpu_info(report: Report) -> bool:
    """Reports the NVIDIA GPU; True when faster-whisper can use it (CUDA works)."""
    if IS_MAC:
        return False  # no CUDA on a Mac (whisper.cpp uses its GPU through Metal)
    if not shutil.which("nvidia-smi"):
        report.add(SKIP, "NVIDIA GPU", "none found")
        return False
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        out = None
    if out is None or out.returncode != 0 or not out.stdout.strip():
        report.add(WARN, "NVIDIA GPU", "nvidia-smi doesn't work, so CUDA can't be used",
                   ["Check the NVIDIA driver (docs/install.md: NVIDIA driver)"])
        return False
    else:
        gpu = out.stdout.strip().splitlines()[0]
        try:
            import ctranslate2
            cuda = ctranslate2.get_cuda_device_count() > 0
        except Exception:
            cuda = False
        if cuda:
            report.add(OK, "NVIDIA GPU", f"{gpu}: docs/models.md lists the model that fits")
        else:
            report.add(WARN, "NVIDIA GPU", f"{gpu}, but CUDA isn't usable from Python",
                       ["Check the NVIDIA driver and CUDA libraries (docs/install.md: NVIDIA driver)"])
        return cuda


# ---- whisper.cpp (computers without CUDA)

def _progress(what: str):
    def show(done: int, total: int) -> None:
        if total:
            sys.stdout.write(f"\r   {what} {100 * done / total:3.0f}%  {done / 1e6:.0f} / {total / 1e6:.0f} MB ")
            sys.stdout.flush()
    return show


def benchmark_whispercpp(model: Path) -> list[tuple[int | None, str, float]]:
    """Seconds per 3-second sentence on every GPU whisper.cpp sees and on the CPU: [(gpu, name, seconds)].
    Built-in graphics are often slower than the CPU, so setup measures instead of guessing."""
    import statistics
    import wave

    import numpy as np

    with wave.open(str(whispercpp.install_dir() / "jfk.wav")) as w:  # a sentence that comes with the build
        audio = np.frombuffer(w.readframes(3 * w.getframerate()), np.int16).astype(np.float32) / 32768
    results = []
    for gpu in [g["index"] for g in whispercpp.list_gpus()] + [None]:
        server = None
        try:
            server = whispercpp.WhisperCppServer(model, gpu, log=whispercpp.install_dir() / "server-setup.log")
            server.transcribe(audio, language="en")  # warm-up (the GPU compiles its shaders on first use)
            times = []
            for _ in range(3):
                start = time.perf_counter()
                server.transcribe(audio, language="en")
                times.append(time.perf_counter() - start)
            results.append((gpu, server.device_name(), statistics.median(times)))
            say(f"   {server.device_name()}: {results[-1][2]:.2f} s per sentence")
        except Exception as e:  # this device doesn't work: the others may
            say(f"   GPU {gpu if gpu is not None else 'CPU'}: doesn't work ({e})")
        finally:
            if server:
                server.close()
    return results


def setup_whispercpp(args, report: Report, device_fixed: bool) -> bool:
    """Installs whisper.cpp and its models and picks its fastest device. True when the app can use it."""
    step("whisper.cpp (speech-to-text on AMD / Intel / Apple GPUs, or a faster CPU engine)")
    part = "whisper.cpp"
    fallback = "Captions still work: the app uses faster-whisper instead"
    if whispercpp.asset_name() is None:
        report.add(SKIP, part, "no build for this computer: faster-whisper runs on the CPU")
        return False
    marker = whispercpp.install_dir() / "RELEASE"
    current = marker.read_text().strip() if marker.exists() else None
    if whispercpp.installed() and current == whispercpp.RELEASE_TAG:
        say(f"   {whispercpp.RELEASE_TAG}: already installed")
    else:
        say(f"   Downloading whisper.cpp ({whispercpp.RELEASE_TAG}) from GitHub…")
        try:
            version = with_retries("Downloading whisper.cpp",
                                   lambda: whispercpp.install_binaries(_progress("whisper.cpp")),
                                   permanent=lambda e: "404" in str(e) or "SHA256SUMS" in str(e))
            marker.write_text(whispercpp.RELEASE_TAG)
            say(f"\n   Installed: {version}")
        except Exception as e:
            if "404" in str(e):  # the release with the builds isn't there (yet)
                fixes = [f"The whisper.cpp builds aren't published at {whispercpp.release_url('')} "
                         f"(release {whispercpp.RELEASE_TAG}): see .github/workflows/whisper-cpp.yml", fallback]
            else:
                fixes = [f"Check the internet connection and run {SETUP} again", fallback]
            report.add(WARN, part, f"couldn't install it: {e}", fixes)
            return False

    names = [args.model]
    if args.draft_interval and args.draft_model not in ("same", args.model):
        names.append(args.draft_model)
    for name in names:
        try:
            path = whispercpp.model_path(name)
            if path.is_file():
                say(f"   {path.name}: already downloaded")
            else:
                with_retries(f"Downloading {name}", lambda: whispercpp.ensure_model(name, _progress(path.name)),
                             permanent=lambda e: isinstance(e, ValueError))
                say("")
        except Exception as e:
            main = name == args.model
            report.add(WARN if main else SKIP, f"{part} model '{name}'", f"not downloaded: {e}",
                       [f"Run {SETUP} again", fallback] if main else ["The app uses the main model for drafts too"])
            if main:
                return False

    config = args._config_path or config_dir() / "config.toml"
    if args.engine == "whisper.cpp":  # asked for by name: the app must use it too, even with a working CUDA
        save_setting(config, "engine", "whisper.cpp")
    if device_fixed:
        report.add(OK, part, f"ready, on the device set in your settings ({args.whispercpp_device})")
        return True
    say("   Measuring which is fastest here…")
    results = benchmark_whispercpp(whispercpp.model_path(args.model))
    if not results:
        report.add(WARN, part, "installed, but it didn't run on this computer", [fallback,
                   f"Details: {whispercpp.install_dir() / 'server-setup.log'}"])
        return False
    gpu, name, seconds = min(results, key=lambda r: r[2])
    others = ", ".join(f"{n} {s:.2f} s" for g, n, s in results if g != gpu)
    value = "cpu" if gpu is None else f"gpu:{gpu}"
    save_setting(config, "whispercpp-device", value)
    report.add(OK, part, f"{name}, {seconds:.2f} s per sentence" + (f" (also tried: {others})" if others else "")
               + f'; saved whispercpp-device = "{value}"')
    return True


def want_summary(own) -> bool:
    """Set up meeting summaries? Asked unless an option decides it; without a terminal the answer is no."""
    if own.summary or own.summary_only:
        return True
    if own.no_summary:
        return False
    if not (ASSUME_YES or sys.stdin.isatty()):
        return False
    return ask("   Set up meeting summaries (the Summary button)? With Ollama this downloads 2-5 GB.", True)


def main(argv: list[str] | None = None) -> int:
    global ASSUME_YES
    p = argparse.ArgumentParser(prog=SETUP, add_help=False)
    p.add_argument("-y", "--yes", action="store_true")
    p.add_argument("--no-whisper", action="store_true")
    p.add_argument("--summary", action="store_true", help="set up summaries without asking")
    p.add_argument("--no-summary", action="store_true")
    p.add_argument("--summary-only", action="store_true", help="only the summary model (implies --no-whisper)")
    own, app_argv = p.parse_known_args(argv)
    ASSUME_YES = own.yes
    args = parse_args(app_argv)  # the same settings the app gets: config.toml, then these options

    # The device for whisper.cpp was chosen by hand (command line or config.toml): don't measure over it.
    device_fixed = args.whispercpp_device != "auto"

    report = Report()
    report.add(OK, "Python packages", "ready")
    try:
        cuda = gpu_info(report)
        if own.no_whisper or own.summary_only:
            report.add(SKIP, "Whisper models", f"skipped ({RUN} downloads them on its first start)")
        else:
            # Without CUDA, whisper.cpp is the engine (it falls back to faster-whisper if it can't be installed).
            wants_whispercpp = args.engine == "whisper.cpp" or (args.engine == "auto" and not cuda)
            if not (wants_whispercpp and setup_whispercpp(args, report, device_fixed)):
                setup_whisper(args, report)
        step(f"Summary model ({args.summarizer})")
        if not want_summary(own):
            report.add(SKIP, "Summary model", f"skipped (add it later: {SETUP} --summary-only)")
        else:
            if args.summarizer == "claude":
                setup_claude(report)
            else:
                setup_ollama(args, report)
    except KeyboardInterrupt:
        say(f"\n\nStopped. Run {SETUP} again to continue: finished downloads are kept.")
        return 130

    report.print()
    if report.failed:
        say(f"\nSome parts failed (✗). Fix them as shown and run {SETUP} again: finished steps are skipped.")
        return 1
    say(f"\nReady. Start Live Caption with: {RUN}   (e.g. {RUN} --lang id)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
