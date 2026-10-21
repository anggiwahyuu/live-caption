# Live Caption

Real-time speech-to-text for online meetings (Zoom, Google Meet, Teams, Discord, and more) on **Linux, Windows and macOS**. Translation is optional and can be switched on or off right from the app window.

- Captures **the audio coming out of your speakers/headset**, so it works with any meeting app.
- Local STT with **faster-whisper** on an NVIDIA GPU (e.g. GTX 1050, `int8`). Falls back to the CPU automatically if the GPU can't be used. On macOS it always runs on the CPU.
- **Real-time with draft mode**: temporary text (grey italics) appears while people speak and updates about every 0.25 seconds, then locks into final text when the speaker pauses. Drafts use a small model (`base`) for speed; final text uses the main model for accuracy.
- A small always-on-top window, like subtitles.
- Transcripts are saved automatically to `~/LiveCaption/meeting-<date>.md`.

How audio is recorded on each OS:

| OS | System audio (other participants) | Microphone | Speech-to-text |
|---|---|---|---|
| Linux | `parec`, or `pw-record` if `parec` is missing or has no PulseAudio/pipewire-pulse server | Same | NVIDIA GPU (CUDA) or CPU |
| Windows | PyAudioWPatch (WASAPI loopback), nothing extra to install | PyAudioWPatch | NVIDIA GPU (CUDA) or CPU |
| macOS | sounddevice + the free **BlackHole** virtual audio device | sounddevice | CPU only |

Follow the installation section for your OS (1, 2 or 3), then continue with [4. Running](#4-running).

---

## 1. Installation on Linux (CachyOS)

### 1.1 Check the NVIDIA driver

GTX 10xx (Pascal) is **not supported** by NVIDIA driver 590 and newer. You need the **580xx** series.

```bash
nvidia-smi
```

If this shows your GPU and driver version `580.xx`, you're fine. Also note your **VRAM** (2 GB, 3 GB, or 4 GB), since it decides which model to pick in [Step 8](#step-8--choosing-a-model-for-your-vram).

If `nvidia-smi` errors out, let CachyOS's built-in tool pick the right driver:

```bash
sudo chwd -a
```

Don't install `nvidia-open` or 590+ packages manually.

### 1.2 System packages

```bash
sudo pacman -S --needed libpulse tk uv
```

- `libpulse` provides the `parec` command used to record audio (it runs on top of pipewire-pulse, the default on CachyOS). If `parec` is missing or can't connect, the app automatically switches to `pw-record`, which ships with PipeWire.
- `uv` is used to create the Python environment.

On other distributions, `parec` is often in a separate package (Debian/Ubuntu: `pulseaudio-utils`).

### 1.3 Python environment

CachyOS ships the latest Python, which often doesn't have wheels for AI libraries yet. That's why a separate **Python 3.12** is used via `uv`:

```bash
cd live-caption
uv venv --python 3.12 .venv
uv pip install -r requirements.txt
chmod +x run.sh
```

Whisper models are downloaded automatically on first run, to `~/.cache/huggingface`.

## 2. Installation on Windows

> Not tested on real Windows hardware yet. Please report anything that doesn't work.

1. **NVIDIA GPU (optional):** install a recent NVIDIA driver with CUDA 12 support. GTX 10xx (Pascal) cards need the **580** series, the last one that supports them. Without an NVIDIA GPU, the app runs on the CPU.
2. **Install `uv`** in PowerShell:

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

   Close and reopen PowerShell afterwards so `uv` is on your `PATH`.
3. **Create the Python environment** (Tkinter is included in the Python that `uv` installs):

   ```powershell
   cd live-caption
   uv venv --python 3.12 .venv
   uv pip install -r requirements.txt
   ```

   This also installs **PyAudioWPatch**, which records the audio of your default speakers/headset through WASAPI loopback. No virtual audio cable is needed.
4. **Start the app with `run.bat`** instead of `./run.sh` in every command in this README:

   ```powershell
   .\run.bat --lang id
   ```

Transcripts are saved to `C:\Users\<you>\LiveCaption\`.

## 3. Installation on macOS

> Not tested on a real Mac yet. Please report anything that doesn't work.

macOS can't record system audio on its own, so a free virtual audio device called **BlackHole** carries a copy of the meeting audio to the app. Speech-to-text runs on the CPU (CTranslate2, the engine behind faster-whisper, has no GPU support on macOS). On Apple Silicon, `small` should still be usable; on Intel Macs, try `--model base` if captions fall behind.

1. **Install Homebrew** (if you don't have it) from <https://brew.sh>, then:

   ```bash
   brew install uv
   brew install --cask blackhole-2ch
   ```

2. **Route audio through BlackHole** while still hearing it yourself:
   1. Open **Audio MIDI Setup** (Applications → Utilities).
   2. Click **+** at the bottom left → **Create Multi-Output Device**.
   3. Tick your speakers/headset **and** **BlackHole 2ch**. Put your speakers/headset first and enable *Drift Correction* for BlackHole.
   4. Pick this Multi-Output Device as the output: either system-wide (System Settings → Sound → Output) or only in the meeting app's speaker setting.

   Note: the volume keys don't control a Multi-Output Device, so set the volume on the device itself or in the meeting app.
3. **Create the Python environment:**

   ```bash
   cd live-caption
   uv venv --python 3.12 .venv
   uv pip install -r requirements.txt
   chmod +x run.sh
   ```

4. **Allow microphone access.** On the first run, macOS asks whether your terminal app may use the microphone. Allow it. BlackHole counts as a microphone, so this is needed even with the default `--source system`. You can change it later in System Settings → Privacy & Security → Microphone.

If your virtual device has a different name (e.g. BlackHole 16ch or Loopback), pass part of its name: `./run.sh --monitor "BlackHole 16ch"`.

## 4. Running

This section walks you through everything from preparing before a meeting to saving the transcript. Run all commands from the project folder.

### Step 1 — Before the meeting

1. **Join the meeting first** (Zoom, Meet, Teams, etc.) and make sure you can hear the other participants through your speakers/headset. Live Caption records the audio that *comes out* of the default output device, so it works with any meeting app.
2. **Check which audio output is active.** If the sound goes through an output other than the default one (e.g. HDMI or Bluetooth), note its name for `--monitor` (see Step 3):
   - **Linux:** the monitor source name:

     ```bash
     pactl list short sources | grep monitor
     ```

   - **Windows:** part of the speaker/headset name as shown in Windows sound settings, e.g. `--monitor "Headphones"`.
   - **macOS:** not needed. The app records from BlackHole, so just make sure the meeting audio goes to your Multi-Output Device (see [3. Installation on macOS](#3-installation-on-macos)).
3. **Always start the app with `./run.sh`** (Linux/macOS) **or `run.bat`** (Windows), not `python live_caption.py`. The script uses the project's virtualenv and, on Linux, sets up the CUDA libraries. Without it, the app may fall back to the CPU and captions become much slower.

### Step 2 — First run

```bash
./run.sh --lang id
```

> **On Windows**, replace `./run.sh` with `.\run.bat` in this command and in every other command in this README.

On the **first run**, the Whisper models (`small` for final text and `base` for drafts) are downloaded automatically to `~/.cache/huggingface`, so you need an internet connection and a little patience. After that, the models load from the cache.

Watch the **status line** in the top-left corner of the window:

| Status | Meaning |
|---|---|
| `Loading model 'small' on CUDA (int8)…` | Loading the model onto the GPU |
| `Ready — small + draft base @ CUDA · language: id` | Ready, running on the GPU. This is what you want to see |
| `… @ CPU …` or the warning "running on CPU" | The GPU could not be used. See [Troubleshooting](#troubleshooting). On macOS, `CPU` is normal |
| `N sentences behind — try a smaller model` | The GPU/CPU can't keep up with the audio |

Once the status says **Ready**, captions appear as soon as someone speaks.

On Linux, if `parec` can't be used, a red line like `parec unavailable (…), switching to pw-record` appears once. That's only a notice: recording continues through PipeWire.

### Step 3 — Choose the audio source

| Command | What gets transcribed | Label on screen |
|---|---|---|
| `./run.sh` (default `--source system`) | Other participants (audio coming out of your speakers/headset) | **Participant** |
| `./run.sh --source mic` | Your voice, from the default microphone | **Me** |
| `./run.sh --source both` | Both at once | **Participant** and **Me** |
| `./run.sh --monitor <name>` | A specific audio output (HDMI/Bluetooth, see Step 1) | **Participant** |

> **Use a headset with `--source both`.** With speakers, your microphone also picks up the other participants, so every sentence shows up twice.

### Step 4 — Set the speaker's language

```bash
./run.sh --lang id     # meeting in Indonesian
./run.sh --lang en     # meeting in English
./run.sh               # --lang auto: detected per sentence
```

If you know the meeting language, **set `--lang`**. Results are more stable and drafts appear faster, because Whisper doesn't have to guess the language. `auto` suits mixed-language meetings, but very short sentences are sometimes detected as the wrong language.

### Step 5 — Translation (optional)

Turn translation on from the start with `--translate`, or at any time with the **"Translate → id"** checkbox in the window. There are two translation engines:

**a. Google (default): online and lightweight**

```bash
./run.sh --lang en --translate                    # English → Indonesian
./run.sh --lang id --translate --translate-to en  # Indonesian → English
```

Requires an internet connection during the meeting. **Every final sentence is sent to Google** to be translated. For confidential meetings, use Argos (below) instead.

**b. Argos: offline, no internet**

```bash
./run.sh --lang en --translate --translator argos
```

- The `argostranslate` package is already included in `requirements.txt`.
- **The first time you use each language pair** (e.g. en→id), its language package is downloaded automatically. This needs internet and takes about 30–60 seconds, so the first line's translation shows up late. After that, translation runs offline (~0.1 s per sentence).
- **To avoid waiting during a meeting**, run it once beforehand with the same language pair while you still have internet, and speak or play one sentence until its translation appears.
- If there is no direct package for a language pair, Argos translates through English (e.g. ja → en → id).

Notes for both engines:
- Only **final lines** are translated, not drafts.
- Sentences already in the target language are not translated again.
- Ticking the checkbox only affects *upcoming* sentences. Lines already on screen are not translated.

### Step 6 — The window

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Ready — small + draft base @ CUDA    [✓] Translate → id  [✓] Always on top  │
│                                                   [Save as…]  [Clear]       │
├────────────────────────────────────────────────────────────────────────────┤
│ [10:02:15] Participant: Good morning everyone, let's start the meeting.     │ ← final (locked)
│       ↳ Selamat pagi semuanya, mari kita mulai rapatnya.                    │ ← translation (if enabled)
│ … Participant: The first agenda today is                                   │ ← draft (still changing)
└────────────────────────────────────────────────────────────────────────────┘
```

| Control | What it does |
|---|---|
| **Translate → id** | Turns translation on/off at any time |
| **Always on top** | Keeps the window above the meeting app, like subtitles |
| **Save as…** | Saves a copy of the transcript elsewhere (`.md` or `.txt`) |
| **Clear** | Clears *the screen only*. The transcript file stays intact |

How to read the text:
- **Draft** (grey italics, starting with `…`): the sentence currently being spoken, updated about every 0.25 seconds. Words sometimes change slightly as the sentence grows. That's normal: Whisper refines its guess as it hears more context.
- **Final** (with a timestamp): locked when the speaker pauses for about 0.7 seconds. Only final lines go into the transcript file.
- **Translation** (yellow italics, starting with `↳`): appears under its final line.

If you scroll up to read older text, new sentences and translations won't make the window jump back down. Scroll back to the bottom to follow the latest text again.

### Step 7 — Transcript and closing the app

- The transcript is **saved automatically** whenever a new final line or translation arrives, to:

  ```
  ~/LiveCaption/meeting-<date>_<time>.md
  ```

  For example: `~/LiveCaption/meeting-2026-10-21_1400.md`. Change the folder with `--out-dir ~/Documents/Meetings`.
- The file is Markdown:

  ```markdown
  # Meeting transcript — October 21, 2026, 14:00

  - **[14:02:15] Participant:** Good morning everyone, let's start the meeting.
    - _Selamat pagi semuanya, mari kita mulai rapatnya._
  ```

- **To close:** click the window's close button, or press `Ctrl+C` in the terminal. Since the transcript is saved continuously, nothing is lost when you close it.

### Step 8 — Choosing a model for your VRAM

| GTX 1050 VRAM | Recommended model | Notes |
|---|---|---|
| 2 GB | `small` (default) | Fast, good enough accuracy for Indonesian/English |
| 3–4 GB | `medium` | More accurate, slightly slower |
| 4 GB | `large-v3-turbo` | Worth a try; if you see "N sentences behind", drop to `medium` |

```bash
./run.sh --model medium --lang id
```

`--model` is used for the final text. Drafts still use a small model (`--draft-model base`) so captions stay live. Watch VRAM usage with `watch -n1 nvidia-smi` while the app is running.

### Ready-to-use commands

```bash
# Internal meeting in Indonesian
./run.sh --lang id

# English webinar, translated to Indonesian (online)
./run.sh --lang en --translate

# Same, without internet (offline)
./run.sh --lang en --translate --translator argos

# Two-way discussion with a headset: participants + your own voice
./run.sh --source both --lang id

# Meeting audio going to a Bluetooth headset (Linux)
./run.sh --monitor bluez_output.XX_XX_XX_XX_XX_XX.1.monitor --lang id

# Meeting audio going to a specific headset (Windows)
.\run.bat --monitor "Headphones" --lang id

# Most accurate (4 GB VRAM), offline translation to Indonesian
./run.sh --model medium --lang en --translate --translator argos

# Most live (faster drafts, sentences lock sooner)
./run.sh --lang id --draft-model tiny --silence-ms 500
```

See every option with `./run.sh --help`, or in the [Other options](#other-options) table.

## Other options

| Option | What it does |
|---|---|
| `--translate-to en` | Change the translation target language |
| `--translator argos` | **Offline** translation (see Step 5b). Language packages are downloaded automatically on first use |
| `--threshold 0.015` | Raise it if noise gets transcribed; lower it if quiet speech is missed |
| `--draft-interval 0.25` | How often draft text updates (seconds). `0` turns draft mode off (text appears per sentence only, lighter on the GPU) |
| `--draft-model base` | Model used only for drafts. `tiny` = most live but rougher, `same` = use the main model (more accurate, slower, saves VRAM) |
| `--max-seconds 10` | Sentences longer than this are partially locked so drafts stay fast |
| `--silence-ms 500` | Shorter pause, so sentences lock into final text sooner but get cut more often |
| `--beam-size 3` | Better accuracy, slower |
| `--device cpu` | Force the CPU (a 9th-gen i7 can still handle the `small` model) |
| `--monitor <name>` | Record a specific output only. Linux: monitor source name (`pactl list short sources`, pick one ending in `.monitor`). Windows: part of the speaker/headset name. macOS: part of the virtual device name (default `BlackHole`) |

## Troubleshooting

- **`libcudnn...so: cannot open shared object`**: start the app with `./run.sh`, not `python live_caption.py` directly. The script sets `LD_LIBRARY_PATH`.
- **Captions feel slow / the "running on CPU" warning appears**: on a GTX 1050, the `small` model takes ~0.4 s per pass on the GPU but ~1.3 s on the CPU. Make sure the status says `CUDA`.
- **Status says CPU instead of CUDA**: read the error message in the status line. The most common causes are a driver that isn't the 580xx series, or running out of VRAM (use a smaller model).
- **`CUDA out of memory`**: use a smaller model, e.g. `medium` → `small`.
- **No text at all**: make sure the meeting audio goes through the default output. Test with `parec --device=@DEFAULT_MONITOR@ | head -c 100000 | wc -c` while playing any sound. If the output is on HDMI or Bluetooth, use `--monitor`.
- **`Neither parec nor pw-record was found` (Linux)**: install `libpulse` (provides `parec`) or `pipewire` (provides `pw-record`).
- **`Recording 'Participant' stopped (pw-record)` (Linux)**: both recorders failed. Check that PipeWire is running: `systemctl --user status pipewire`.
- **Window doesn't stay on top (Wayland/KDE)**: right-click the title bar → *More Actions* → *Keep Above Others*.
- **Status "N sentences behind"**: the GPU can't keep up. Try `--draft-interval 0.5`, `--draft-model tiny`, or a smaller main model.
- **Odd text like "Terima kasih." / "Thank you." during silence**: a typical Whisper hallucination. Some of it is already filtered; raise `--threshold` if it still happens often.

### Windows

- **`no loopback device matching '…'`**: the `--monitor` name doesn't match any output. List the names with:

  ```powershell
  .venv\Scripts\python -c "import pyaudiowpatch as p; [print(d['name']) for d in p.PyAudio().get_loopback_device_info_generator()]"
  ```

- **Status says CPU even with an NVIDIA GPU**: start the app with `run.bat`, update the NVIDIA driver, and check that `nvidia-smi` works in PowerShell.
- **No text while the meeting is silent**: normal. WASAPI loopback only delivers audio while something is playing.

### macOS

- **`no input device matching 'BlackHole'`**: BlackHole isn't installed, or macOS hasn't loaded it yet. Run `brew install --cask blackhole-2ch`, then restart the Mac.
- **No text at all**: check that the meeting audio goes to the Multi-Output Device that includes BlackHole, and that your terminal app has microphone access (System Settings → Privacy & Security → Microphone).
- **You hear nothing after picking BlackHole**: you selected BlackHole itself as the output. Pick the Multi-Output Device instead, so audio goes to your speakers/headset as well.
- **Captions fall behind**: speech-to-text runs on the CPU on macOS. Try `--model base`, or `--draft-model tiny`.

## Known limitations

- **No automatic reconnect.** If a recorder stops mid-meeting (e.g. a Bluetooth headset disconnects or an audio device is unplugged), that source stops recording and a red error appears. Restart the app to record again. On Windows, changing the default output during a meeting likely keeps recording the old device, so restart the app after switching.
- **Hallucination filter covers Indonesian and English only.** Fake phrases Whisper produces during silence (like "Thank you for watching") are filtered only in those two languages. For other languages, raise `--threshold` if they show up often.
- **Library versions aren't pinned.** `requirements.txt` lists package names only, so recreating the environment later may install newer versions. To lock today's versions: `uv pip compile requirements.txt -o requirements.lock`, then install with `uv pip install -r requirements.lock`.
- **Moving or renaming the project folder breaks `source .venv/bin/activate`**, because the virtualenv stores its absolute path. `./run.sh` and `run.bat` keep working since they call the venv's python directly. To fix `activate`, delete `.venv` and recreate it with the installation steps above.
- **Windows and macOS support is untested** on real hardware (see sections 2 and 3).

## Running the tests

The tests use only the Python standard library (`unittest`), so nothing extra needs to be installed:

```bash
.venv/bin/python -m unittest discover -s tests -v       # Linux/macOS
.venv\Scripts\python -m unittest discover -s tests -v   # Windows
```

They cover the audio pipeline (segmenting, re-chunking, resampling, recorder fallback), the transcriber's draft/final handling, translation skipping and the transcript format, without loading any Whisper model. Tests that record from real `parec` / `pw-record` run only on Linux, and are skipped when those commands aren't available.

## Ethics and privacy

- Let meeting participants know the conversation is being transcribed, especially at work.
- Speech-to-text always runs **locally** on your computer. No audio leaves your machine.
- Translation with the default `--translator google` **sends the text of every final sentence to Google**. For confidential meetings, use `--translator argos` (fully offline after the first language package download) or leave translation off.
- Transcripts are saved as plain-text Markdown in `~/LiveCaption/`. Delete them when you no longer need them.
