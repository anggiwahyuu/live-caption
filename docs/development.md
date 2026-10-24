# Development

How the code is organized, how to test it, and how the libraries and the whisper.cpp builds are kept up to date. Back to the [README](../README.md).

## Project structure

`live_caption.py` is only the launcher that `run.sh` / `run.bat` start. The code lives in the `lib/` package:

| Module | What's in it |
|---|---|
| `main.py` | Wires everything together: creates the recorders, workers and window, and starts them |
| `config.py` | Command-line options, the `config.toml` settings file, the remembered window state |
| `common.py` | Shared constants: audio format (16 kHz, 30 ms frames), OS checks, source labels |
| `audio.py` | Recording (`parec` / `pw-record` / WASAPI / BlackHole) and reconnecting when a device drops, resampling, splitting speech into sentences, the queue to Whisper |
| `winaudio.py` | Windows only: the current default output/microphone, asked from Windows itself (Core Audio through `ctypes`), so recording follows a change |
| `transcriber.py` | Loading the engine (faster-whisper on CUDA, whisper.cpp, faster-whisper on the CPU) and turning audio into text; `--vocab`; the hallucination filter |
| `whispercpp.py` | The whisper.cpp engine for non-NVIDIA GPUs: downloading it, choosing the GPU, running `whisper-server` in the background (and again if it stops) |
| `translation.py` | Google / Argos translation of final lines |
| `summary.py` | Meeting summaries with Ollama or the Claude API |
| `ask.py`, `ask_window.py` | Questions about a meeting (live or past): the answer and the transcript lines that back it up (checked against the transcript), saved in its file; the Ask window |
| `subtitles.py` | SRT / WebVTT export |
| `export.py` | The summary as chat text, plain text and Word (`.docx`, built without extra packages) |
| `history.py`, `history_window.py` | Reading past transcripts back, searching them, adding a summary or answers to them; the Meeting history window |
| `alerts.py` | Alert words: matching them in a line, desktop notifications on each OS |
| `ui.py` | The main window: status header, `⋯` menu, live captions with drafts and translations, alert words, the summary (on demand and automatic) |
| `transcript.py` | The meeting being transcribed: its lines, translations, summary and questions, and the Markdown file they're saved to (no Tkinter) |
| `theme.py` | Colors, fonts and button styles shared by every window |
| `search_bar.py` | The `Ctrl+F` search bar |
| `summary_window.py` | The summary window with its Copy / Save buttons (live and past meetings) |
| `shortcut.py` | The application menu shortcut of `setup.sh` / `setup.ps1` (`.desktop` file, Start menu, `.app`); the icon is in `assets/` |
| `model_setup.py` | The model downloads of `setup.sh` / `setup.ps1`: Whisper or whisper.cpp (with its device measurement), Ollama and the summary model, with checks and retries |

Data flows one way: `audio` → `transcriber` → `ui` (→ `translation` / `summary` / `ask` on demand). Modules talk through queues, so only the window modules (`ui`, `search_bar`, `summary_window`, `history_window`, `ask_window`, `theme`) touch Tkinter, and only `main` knows all the pieces. What a window shows but doesn't draw (`transcript`, `history`, `export`, `alerts`, `ask`) has no Tkinter in it, so it can be tested without a window. A new feature usually means one new module plus a few lines in `ui.py`, `config.py` (its options) and `main.py` (to wire it in).

Other files:

| File | What it is |
|---|---|
| `setup.sh`, `setup.ps1` | One-command setup (system packages, `uv`, `.venv`, menu shortcut), then `lib/model_setup.py` |
| `run.sh`, `run.bat` | Start the app from `.venv` (on Linux with the CUDA libraries on `LD_LIBRARY_PATH`) |
| `requirements*.txt`, `requirements*.lock` | The Python libraries, and their tested versions ([Library versions](#library-versions)) |
| `tools/build-whisper-cpp.sh` | The Linux build of whisper.cpp ([Building whisper.cpp](#building-whispercpp)) |
| `tools/lock-requirements.sh` | Writes the `.lock` files |
| `.github/workflows/setup.yml` | Runs the setup scripts on Ubuntu, Debian, Fedora, Arch, openSUSE, macOS and Windows, twice, then starts the app and Whisper; and updates an environment an older setup left on an NVIDIA computer (with a stand-in `nvidia-smi`), checking the CUDA libraries survive |
| `.github/workflows/whisper-cpp.yml` | Builds and tests whisper.cpp for Linux, Windows and macOS, and publishes it as a release |
| `.github/workflows/lock.yml` | Monthly update of the `.lock` files, tested before it's proposed |

## Running the tests

The tests use only the Python standard library (`unittest`), so nothing extra needs to be installed:

```bash
.venv/bin/python -m unittest discover -s tests -v       # Linux/macOS
.venv\Scripts\python -m unittest discover -s tests -v   # Windows
```

They are lightweight and finish in a few seconds: no Whisper model, no audio recording, no window and no internet. Where something outside is involved, a stand-in plays its part: a fake local Ollama server for summaries and questions (the Claude API is not called), a small Python script for `whisper-server`, a fake PyAudioWPatch for Windows recording, a local web server for the whisper.cpp release.

## Library versions

`requirements.txt` and `requirements-nvidia.txt` name the libraries; `requirements.lock` and `requirements-nvidia.lock` pin every one of them, dependencies included, to the exact versions the setup workflow tested on every OS. Setup installs the lock files with `uv pip sync`, so the environment matches them exactly: a new release of faster-whisper or torch can't break an installation until it has been tested. If the lock files can't be installed (e.g. a Python too new for them), setup warns and installs the newest versions from `requirements.txt` instead. Intel Macs always use `requirements.txt`: today's torch has no build for them.

- **Changed `requirements*.txt`?** Run `tools/lock-requirements.sh` and commit the `.lock` files with it. It changes only what the edit needs.
- **Newer versions:** on the 1st of every month, the *update locked versions* workflow (`.github/workflows/lock.yml`) runs `tools/lock-requirements.sh --upgrade`, tests the result with the setup workflow on every OS, and when that passes opens a pull request from the branch `lock-update`. Merging it is the update. To let it open the pull request: Settings → Actions → General → *Allow GitHub Actions to create and approve pull requests*. Run it by hand from the Actions tab at any time.

## Building whisper.cpp

Setup downloads whisper.cpp from this repository's GitHub release `whisper-bin-v1` (the tag `lib/whispercpp.py` names). The `.github/workflows/whisper-cpp.yml` workflow builds it from the official source (version pinned in the workflow) for Linux x64 and Windows x64 with Vulkan, and for macOS on Apple Silicon with Metal, and tests each build by transcribing a sample sentence.

- **Try a build:** Actions tab → *whisper.cpp builds* → *Run workflow*. It builds and tests, without a release.
- **Publish:** `git tag whisper-bin-v1 && git push origin whisper-bin-v1`. The release holds the three archives and their `SHA256SUMS`, which setup checks.
- **A new whisper.cpp version:** change `WHISPER_CPP_VERSION` in the workflow, `RELEASE_TAG` in `lib/whispercpp.py` (e.g. `whisper-bin-v2`), and push that tag. Setup installs the new release on its next run.
- **Other download location** (a mirror, or a local folder while testing): `LIVE_CAPTION_RELEASE_URL=http://… ./setup.sh`.

The Linux build (`tools/build-whisper-cpp.sh`) runs on Ubuntu 22.04, so it also runs on older distributions, and is portable on purpose: one CPU library per instruction set, the best one picked when it starts. Ready-made builds compiled for the build machine's CPU (with AVX-512) crash with "illegal instruction" on most AMD Ryzen and Intel desktop CPUs.
