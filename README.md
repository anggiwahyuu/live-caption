# Live Caption

Real-time speech-to-text for online meetings (Zoom, Google Meet, Teams, Discord, and more) on **Linux, Windows and macOS**. Translation is optional and can be switched on or off right from the app window.

- Captures **the audio coming out of your speakers/headset**, so it works with any meeting app.
- Local speech-to-text on **any GPU**: **faster-whisper** on NVIDIA GPUs (e.g. GTX 1050, `int8`), **whisper.cpp** on AMD and Intel GPUs (Vulkan) and Apple Silicon (Metal). Without a usable GPU it runs on the CPU. See [GPUs other than NVIDIA](#gpus-other-than-nvidia-amd-intel-apple).
- **Real-time with draft mode**: temporary text (grey italics) appears while people speak and updates about every 0.25 seconds, then locks into final text when the speaker pauses. Drafts use a small model (`base`) for speed; final text uses the main model for accuracy.
- A small always-on-top window, like subtitles.
- Transcripts are saved automatically to `~/LiveCaption/meeting-<date>.md`.

How audio is recorded on each OS:

| OS | System audio (other participants) | Microphone | Speech-to-text |
|---|---|---|---|
| Linux | `parec`, or `pw-record` if `parec` is missing or has no PulseAudio/pipewire-pulse server | Same | NVIDIA GPU (CUDA), AMD/Intel GPU (Vulkan) or CPU |
| Windows | PyAudioWPatch (WASAPI loopback), nothing extra to install | PyAudioWPatch | NVIDIA GPU (CUDA), AMD/Intel GPU (Vulkan) or CPU |
| macOS | sounddevice + the free **BlackHole** virtual audio device | sounddevice | Apple Silicon GPU (Metal), or CPU on Intel Macs |

Follow the installation section for your OS (1, 2 or 3), then continue with [4. Running](#4-running). Or let a setup script do it for you:

### Quick setup

One command installs everything the app needs, downloads the models, and asks whether you also want meeting summaries:

```bash
./setup.sh                                                    # Linux and macOS
```

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1            # Windows (PowerShell, in the project folder)
```

What it does, skipping whatever is already there (so it's safe to run again):

1. **System packages** (Linux/macOS only): the audio recorder, Tkinter and curl through your package manager (apt, dnf, pacman or zypper), or BlackHole through Homebrew on macOS. This asks for your password.
2. **`uv` and the Python environment** (`.venv`). On Linux it's built on your distribution's Python, for smooth fonts (see [1.3](#13-python-environment)).
3. **A shortcut in the application menu** (Linux: KDE, GNOME, XFCE, …; Windows: Start menu; macOS: `~/Applications/Live Caption.app`), so the app opens without a terminal. It starts with the settings in your [`config.toml`](#saving-your-settings), so put options like `lang = "id"` there.
4. **The speech-to-text engine and its models** (`small` + `base` for drafts, or whatever `config.toml` says), with a free disk space check and retries on network errors. With a working NVIDIA GPU that's faster-whisper; otherwise setup installs whisper.cpp, measures which of your GPUs (or the CPU) is fastest and saves it ([details](#gpus-other-than-nvidia-amd-intel-apple)).
5. **Meeting summaries, if you say yes:** installs [Ollama](https://ollama.com) when it's missing (it asks first and shows the command), downloads `qwen2.5:7b` (offers `qwen2.5:3b` on computers with less than 8 GB of RAM) and checks that it loads.

At the end it lists what's ready and, for anything that failed, the command that fixes it. Options (the same on Windows):

| Option | Effect |
|---|---|
| `--summary` / `--no-summary` | Set up meeting summaries without asking / skip them |
| `--summary-only` | Only add the summary model later |
| `--no-whisper` | Don't download the Whisper models now (the app does it on its first start) |
| `-y`, `--yes` | Answer every question with its default |
| `--no-shortcut` | Don't add the app to the application menu |
| `--recreate-venv` | Delete `.venv` and build it again |
| app options | Pick other models, as with `./run.sh`: `--model medium`, `--summary-model qwen2.5:3b` |

To remove the menu shortcut again: `.venv/bin/python -m lib.shortcut --remove` (Windows: `.venv\Scripts\python.exe -m lib.shortcut --remove`). Started from the menu, the app has no terminal for its messages, so they go to a log file: `~/.config/live-caption/live-caption.log` on Linux and macOS, `%APPDATA%\live-caption\live-caption.log` on Windows. Look there when the app doesn't open from the menu. Only this run's log and the previous one (`.log.old`) are kept once it passes 1 MB.

The NVIDIA driver isn't installed by the script (it needs a reboot and depends on your card): see [1.1](#11-nvidia-driver-only-with-an-nvidia-gpu) and [2](#2-installation-on-windows). On macOS, the Multi-Output Device still has to be created by hand ([section 3, step 2](#3-installation-on-macos)).

---

## 1. Installation on Linux

Works on any distribution with PulseAudio or PipeWire (the default on practically every desktop distribution). Commands are given for the common families:

| Family | Examples |
|---|---|
| **Debian/Ubuntu** | Ubuntu, Kubuntu, Linux Mint, Pop!_OS, Zorin OS, elementary OS, Debian |
| **Fedora** | Fedora Workstation/KDE, Nobara |
| **Arch** | CachyOS, Arch Linux, EndeavourOS, Manjaro, Garuda |
| **openSUSE** | Tumbleweed, Leap |

### 1.1 NVIDIA driver (only with an NVIDIA GPU)

No NVIDIA GPU? Skip this step: the app runs on the CPU (slower; use `--model base` if captions fall behind).

You only need the **driver**. The CUDA libraries the app uses (cuBLAS, cuDNN) are installed by `pip` in step 1.3, so **don't install the CUDA toolkit**.

Check whether a driver is already working:

```bash
nvidia-smi
```

If it shows your GPU, note the driver version and your **VRAM** (2 GB, 3 GB, 4 GB, …): it decides which model to pick in [Step 9](#step-9--choosing-a-model-for-your-vram). Then skip to 1.2.

**Which driver version?** NVIDIA's 590 and newer drivers dropped **GTX 9xx and GTX 10xx** (Maxwell/Pascal, e.g. the GTX 1050). Those cards need the **580** series, the last one that supports them. RTX 20xx and newer work with any recent driver.

<details>
<summary><b>Debian/Ubuntu</b> (Ubuntu, Mint, Pop!_OS, …)</summary>

```bash
ubuntu-drivers devices                 # lists the drivers for your GPU; "recommended" is usually right
sudo apt install nvidia-driver-580     # GTX 9xx/10xx. Newer GPUs: the recommended version
sudo reboot
```

- **Linux Mint:** you can also use *Driver Manager* from the menu; pick `nvidia-driver-580` for a GTX 9xx/10xx.
- **Pop!_OS:** if you installed the NVIDIA ISO, the driver is already there; check with `nvidia-smi`.
- **Debian:** enable the `non-free` and `non-free-firmware` components and follow the [Debian NVIDIA guide](https://wiki.debian.org/NvidiaGraphicsDrivers); `ubuntu-drivers` doesn't exist there.
- With **Secure Boot** on, the install asks for a password and, after the reboot, a blue *MOK* screen asks for it again: choose *Enroll MOK* and type it, or the driver won't load.

</details>

<details>
<summary><b>Fedora</b></summary>

The driver comes from the [RPM Fusion](https://rpmfusion.org/Configuration) **nonfree** repository:

```bash
sudo dnf install https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm \
                 https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm

# GTX 9xx/10xx (580 legacy branch):
sudo dnf install akmod-nvidia-580xx xorg-x11-drv-nvidia-580xx-cuda
# RTX 20xx and newer:
sudo dnf install akmod-nvidia xorg-x11-drv-nvidia-cuda
```

Wait about 5 minutes before rebooting, so the kernel module can finish building in the background (`modinfo -F version nvidia` prints a version once it's done). If `580xx` packages are "not found", the full RPM Fusion nonfree repository isn't enabled: run the first command above.

</details>

<details>
<summary><b>Arch</b> (CachyOS, Arch Linux, EndeavourOS, Manjaro)</summary>

- **CachyOS:** let its hardware tool pick the right driver (it knows about the 580 branch):

  ```bash
  sudo chwd -a
  ```

- **Arch Linux / EndeavourOS:** GTX 9xx/10xx need `nvidia-580xx-dkms` from the AUR (with an AUR helper such as `yay`); the official `nvidia-open` packages no longer support them. RTX 20xx and newer: `sudo pacman -S nvidia-open`.
- **Manjaro:** use *Manjaro Settings Manager → Hardware Configuration*. On a GTX 9xx/10xx, check with `nvidia-smi` afterwards that it installed a 580 driver.

Don't install `nvidia-open` or 590+ packages manually on a GTX 9xx/10xx.

</details>

<details>
<summary><b>openSUSE</b></summary>

Use the NVIDIA repository as described in the [openSUSE NVIDIA guide](https://en.opensuse.org/SDB:NVIDIA_drivers). For a GTX 9xx/10xx, choose the 580 driver branch, not the newest one.

</details>

### 1.2 System packages

Three things are needed: the `parec` command to record audio, Tkinter for the window, and `uv` to create the Python environment.

| Family | Command |
|---|---|
| Debian/Ubuntu | `sudo apt install pulseaudio-utils python3-tk curl` |
| Fedora | `sudo dnf install pulseaudio-utils python3-tkinter uv` |
| Arch / CachyOS | `sudo pacman -S --needed libpulse tk uv` |
| openSUSE | `sudo zypper install pulseaudio-utils python3-tk curl` |

Where `uv` isn't in the list above (Debian/Ubuntu, openSUSE, and any other distribution), install it with its official script, then **close and reopen the terminal** so the `uv` command is found:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

About the audio packages:

- `parec` (in `pulseaudio-utils`, or `libpulse` on Arch) works with both PulseAudio and PipeWire (through `pipewire-pulse`, which every desktop distribution with PipeWire includes).
- If `parec` is missing or can't connect, the app automatically switches to `pw-record`, which comes with PipeWire. So on a PipeWire system this step is optional for audio, but installing `parec` is still recommended.

### 1.3 Python environment

The same on every distribution. The environment is built on **your distribution's own Python** (3.10 or newer: Ubuntu 22.04 and later, Debian 12 and later, current Fedora, Arch and openSUSE all qualify). All the libraries have ready-made packages for Python 3.10 to 3.14. Installing into `.venv` doesn't touch your system Python.

```bash
cd live-caption
uv venv --python /usr/bin/python3 .venv
uv pip install torch --index-url https://download.pytorch.org/whl/cpu   # see below
uv pip install -r requirements.txt
uv pip install -r requirements-nvidia.txt   # only with an NVIDIA GPU (step 1.1)
chmod +x run.sh
```

Why the distribution's Python, not one downloaded by `uv` (`uv venv --python 3.12`)? On Linux, the Python that `uv` downloads comes with a Tk that has **no font smoothing**: the window can only use a small bitmap font, ignores `--font-size`, and can't show characters such as `↳`, `…` or `—`. The distribution's Tk (the `python3-tk` / `tk` package from step 1.2) uses your desktop's fonts. The app shows a notice in the window if it detects the rough fonts.

Already set up the environment with `uv venv --python 3.12` before? Recreate it (your settings and transcripts are kept, they live outside the project folder):

```bash
rm -rf .venv
uv venv --python /usr/bin/python3 .venv
uv pip install torch --index-url https://download.pytorch.org/whl/cpu
uv pip install -r requirements.txt
uv pip install -r requirements-nvidia.txt   # only with an NVIDIA GPU
```

If your distribution's Python is older than 3.10, use `uv venv --python 3.12 .venv` instead: everything works, only the text looks rough.

This downloads about 1 GB, plus 2 GB of CUDA libraries with an NVIDIA GPU. The `torch` line comes first because torch (needed by offline translation, which runs on the CPU) would otherwise bring another ~2.5 GB of CUDA libraries it never uses. Whisper models are downloaded automatically on first run, to `~/.cache/huggingface`.

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
   uv pip install -r requirements-nvidia.txt   # only with an NVIDIA GPU (step 1)
   ```

   This also installs **PyAudioWPatch**, which records the audio of your default speakers/headset through WASAPI loopback. No virtual audio cable is needed.
4. **Start the app with `run.bat`** instead of `./run.sh` in every command in this README:

   ```powershell
   .\run.bat --lang id
   ```

Transcripts are saved to `C:\Users\<you>\LiveCaption\`.

## 3. Installation on macOS

> Not tested on a real Mac yet. Please report anything that doesn't work.

macOS can't record system audio on its own, so a free virtual audio device called **BlackHole** carries a copy of the meeting audio to the app. On Apple Silicon, speech-to-text runs on the GPU through whisper.cpp (Metal), which `setup.sh` installs. Intel Macs use the CPU (try `--model base` if captions fall behind).

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

Turn translation on from the start with `--translate`, or at any time with **Translate → id** in the window's `⋯` menu (or `Ctrl+T`). There are two translation engines:

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
- Turning it on only affects *upcoming* sentences. Lines already on screen are not translated.

### Step 6 — The window

```
┌──────────────────────────────────────────────────────────────┐
│ ● Ready — small + draft base @ CUDA   [⏸ Pause] [✦ Summary] [⋯]│
├──────────────────────────────────────────────────────────────┤
│ Participant  10:02:15                                        │ ← speaker and time
│   Good morning everyone, let's start the meeting.            │ ← final (locked)
│   ↳ Selamat pagi semuanya, mari kita mulai rapatnya.         │ ← translation (if enabled)
│   The first agenda today is                                  │ ← draft (grey italics, still changing)
└──────────────────────────────────────────────────────────────┘
```

The dot shows the state at a glance: **green** listening, **amber** paused, **red** an error (read the message next to it), **grey** still loading the model. Consecutive sentences from the same speaker share one header; a new header (with its time) appears when the speaker changes or after a 2-minute gap.

| Control | Where | Shortcut | What it does |
|---|---|---|---|
| **Pause / Resume** | button | `Ctrl+P` | Stops transcribing until resumed, e.g. for an off-the-record chat. The sentence being spoken is locked first; audio during the pause is discarded, never transcribed |
| **Summary** | button | `Ctrl+M` | Summarizes the meeting so far: summary, decisions, action items, open questions (see Step 8) |
| **Translate → id** | `⋯` menu | `Ctrl+T` | Turns translation on/off at any time |
| **Always on top** | `⋯` menu | | Keeps the window above the meeting app, like subtitles |
| **Auto summary** | `⋯` menu | | Makes the summary by itself after a silence and when you close the window ([Step 8](#step-8--meeting-summary)) |
| **Alert words…** | `⋯` menu | | Tells you when someone says your name ([Alert words](#alert-words)) |
| **Search…** | `⋯` menu | `Ctrl+F` | Opens a search bar above the text (see below) |
| **Meeting history…** | `⋯` menu | `Ctrl+H` | Past meetings: search all of them, summarize them ([Step 7](#step-7--transcript-and-closing-the-app)) |
| **Save as…** | `⋯` menu | `Ctrl+S` | Saves a copy of the transcript elsewhere (`.md` or `.txt`), or exports subtitles (`.srt` or `.vtt`, see Step 7) |
| **Clear screen** | `⋯` menu | `Ctrl+L` | Clears *the screen only*. The transcript file stays intact |

Shortcuts work while the Live Caption window is focused. On macOS, use `Cmd` instead of `Ctrl`.

How to read the text:
- **Draft** (grey italics, starting with `…`): the sentence currently being spoken, updated about every 0.25 seconds. Words sometimes change slightly as the sentence grows. That's normal: Whisper refines its guess as it hears more context.
- **Final** (with a timestamp): locked when the speaker pauses for about 0.7 seconds. Only final lines go into the transcript file.
- **Translation** (yellow italics, starting with `↳`): appears under its final line.

If you scroll up to read older text, new sentences and translations won't make the window jump back down. Scroll back to the bottom to follow the latest text again.

**Searching:** press `Ctrl+F` and type, e.g. `deadline`. Every match is highlighted (upper/lower case doesn't matter) and the newest one is selected, since you're usually looking for something said recently. `Enter` / `F3` goes to the next match, `Shift+Enter` / `Shift+F3` to the previous one; the counter shows e.g. `2 / 5`. New lines that arrive while the search is open are highlighted too, and the view stays where you are. Draft text is not searched, because it is still changing. Only the text on screen is searched: after **Clear screen**, earlier lines are only in the transcript file. `Esc` closes the search and jumps back to the live captions.

The window's size, position and the **Always on top** and **Auto summary** ticks are remembered when you close it, and restored on the next run. If the saved position is off-screen (e.g. a monitor was unplugged), only the size is restored. `--geometry 760x280` overrides it for one run.

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

- **Subtitles for a meeting recording:** if you recorded the meeting (Zoom/Meet recording, OBS, …), choose **Save as…** in the `⋯` menu (or `Ctrl+S`) and pick **SubRip (`.srt`)** or **WebVTT (`.vtt`)**. The app asks what time the recording started (pre-filled with the time Live Caption started); subtitle times are counted from that moment, and lines spoken before it are left out. Put the file next to the video with the same name (`meeting.mp4` + `meeting.srt`) and most players (VLC, mpv, YouTube upload) pick it up.

  ```
  1
  00:00:05,000 --> 00:00:06,500
  Participant: Good morning everyone.
  Selamat pagi semuanya.
  ```

  The speaker label is only added when both **Me** and **Participant** were transcribed, and the translation (if any) becomes a second line. If the subtitles run consistently early or late, export again with the start time adjusted by that many seconds.
- **Past meetings:** `⋯` → **Meeting history…** (or `Ctrl+H`) lists every transcript in the folder, newest first, with its length and whether it has a summary. Type in the search box to search **all meetings at once**: all words must occur in the meeting (in any order, translations count too), and the lines that contain them are shown with the words highlighted. With nothing typed, the selected meeting's summary is shown, or its first lines. **Summarize** makes the summary of a past meeting (also of transcripts from before the Summary button existed) and adds it to the top of its file; you can close the history window meanwhile. **Open file** (or a double click) opens the transcript in your text editor, **Open folder** the folder.
- **To close:** click the window's close button, or press `Ctrl+C` in the terminal. Since the transcript is saved continuously, nothing is lost when you close it.

### Step 8 — Meeting summary

Press **Summary** (or `Ctrl+M`) at any time, usually at the end of the meeting. An AI model reads the final lines and writes:

- **Summary**: what the meeting was about and what came out of it
- **Decisions**
- **Action items**: who does what, and by when
- **Open questions**

The summary opens in its own window and is added at the top of the transcript file. Press it again later to replace it with a summary of the longer meeting. To send it on:

| Button | Gives you |
|---|---|
| **Copy for chat** | Text for WhatsApp, Slack or Telegram: bold headings (`*Keputusan*`) and `•` bullets, which all three show as intended |
| **Copy Markdown** | The summary as written, for Notion, Obsidian, GitHub, … |
| **Save as…** | A **Word document** (`.docx`, opens in Word, LibreOffice, Google Docs), plain text (`.txt`, e.g. for an email) or Markdown |

All three start with a title like *Ringkasan rapat — 22 Oktober 2026, 10:00*, in the summary's language.

**Automatic summary.** Tick **Auto summary** in the `⋯` menu (it's off until you do; the app remembers your choice) and the summary is made by itself:

- after **10 minutes without speech**, when at least 5 new lines were said since the last summary. Change the wait with `--auto-summary-after 5` (minutes). If people talk again afterwards, the next silence makes a new summary that replaces it.
- when you **close the window**: it stays open until the summary of the latest lines is saved, then closes. Close it a second time to quit at once.

A short break doesn't need the tick off: press **Pause** during the break, as the silence clock doesn't run while paused. To start with it on (or off) whatever the tick says, use `--auto-summary` / `--no-auto-summary`, or `auto-summary = true` in `config.toml`.

There are two engines:

**a) Ollama, local (default).** Nothing leaves your computer. `./setup.sh --summary-only` (Windows: `setup.ps1 --summary-only`) sets it up, or install [Ollama](https://ollama.com) once and download a model by hand:

```bash
curl -fsSL https://ollama.com/install.sh | sh   # any Linux distribution; Windows/macOS: installer from ollama.com
# (Arch/CachyOS alternative: sudo pacman -S ollama && sudo systemctl enable --now ollama)
ollama pull qwen2.5:7b           # ~4.7 GB, handles Indonesian and English
```

Then just run the app as usual. On the CPU (recommended with a small GPU like the GTX 1050, so Whisper keeps its VRAM) a summary takes about **2 minutes** and needs about **5 GB of free RAM**; the window stays usable meanwhile.

Small local models tend to make things up (a date nobody said, the wrong person for a task) and to list proposals as decisions. So with Ollama the summary is made in two passes, and the app checks the model's work in between:

1. The model lists the decisions, action items and open questions as structured data, each with a **word-for-word quote** from the transcript.
2. The app checks every item against the transcript: items whose quote isn't in the transcript are dropped; a deadline is kept only if its words (and always its day, month or number) were said near the quote; a person's name only if it was said there (otherwise the item gets the label of who said the quoted line).
3. The model writes the short overview from the checked items only.

Something said in the meeting can still be missed, so read the summary before sharing it.

Less than 8 GB of RAM? Use the smaller model: `ollama pull qwen2.5:3b` and `--summary-model qwen2.5:3b` (twice as fast, 2 GB). Be aware it's noticeably less reliable: in our test on an Indonesian sprint meeting it listed proposals as decisions and dropped an action item, while `qwen2.5:7b` got all decisions, action items and open questions right. Ollama on another machine: `--ollama-url http://192.168.1.10:11434`.

**b) Claude API, online (opt-in).** Clearly better summaries, but **the whole transcript is sent to Anthropic**. Don't use it for confidential meetings.

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # or: ant auth login
./run.sh --lang id --summarizer claude
```

The default model is `claude-opus-5-5`; change it with `--summary-model`. Each summary is billed to your API account (an hour-long meeting is roughly 15–20k input tokens).

Summaries are written in the meeting's main language (the one Whisper detected for most of the speech). For an English meeting summarized in Indonesian, use `--summary-lang id` (or `summary-lang = "id"` in `config.toml`). Since speech-to-text makes mistakes and speakers aren't told apart (only **Me** vs **Participant**), check names and action items before sending the summary to others.

### Step 9 — Choosing a model for your VRAM

| GTX 1050 VRAM | Recommended model | Notes |
|---|---|---|
| 2 GB | `small` (default) | Fast, good enough accuracy for Indonesian/English |
| 3–4 GB | `medium` | More accurate, slightly slower |
| 4 GB | `large-v3-turbo` | Worth a try; if you see "N sentences behind", drop to `medium` |

```bash
./run.sh --model medium --lang id
```

`--model` is used for the final text. Drafts still use a small model (`--draft-model base`) so captions stay live. Watch VRAM usage with `watch -n1 nvidia-smi` while the app is running.

### GPUs other than NVIDIA (AMD, Intel, Apple)

faster-whisper, the engine used on NVIDIA GPUs, can't use other GPUs. On every other computer the app uses **whisper.cpp** instead, which runs on:

- **AMD and Intel GPUs** through **Vulkan** (Linux and Windows): Radeon RX cards, Radeon graphics in Ryzen laptops, Intel Arc and Iris Xe;
- the **Apple Silicon GPU** through **Metal** (M1 and newer);
- the **CPU**, where it's still about twice as fast as faster-whisper for a short sentence.

`./setup.sh` / `setup.ps1` set it up when there's no working NVIDIA GPU: they download whisper.cpp (built from the official source for this project, see `.github/workflows/whisper-cpp.yml`) into `~/.local/share/live-caption/whisper.cpp` (Windows: `%LOCALAPPDATA%\live-caption\whisper.cpp`), download the model (`small` is a 260 MB file), and then **measure** each GPU and the CPU with a test sentence. The fastest is saved in `config.toml`, because built-in graphics can be slower than the CPU. Measured on one laptop with the `small` model, per 3-second sentence:

| Device | Time |
|---|---|
| NVIDIA GTX 1050, faster-whisper (CUDA) | 0.37 s |
| NVIDIA GTX 1050, whisper.cpp (Vulkan) | 0.27 s |
| Intel Core i7-9750H CPU, whisper.cpp | 0.79 s |
| Intel Core i7-9750H CPU, faster-whisper | 1.48 s |
| Intel UHD 630 (built-in graphics), whisper.cpp (Vulkan) | 1.40 s: slower than the CPU, so setup picks the CPU there |

A separate AMD card is much faster than a GTX 1050, so `small` or `medium` should keep up easily. On Linux, setup also installs the Vulkan driver if it's missing (Mesa's `mesa-vulkan-drivers`; on Arch `vulkan-radeon` / `vulkan-intel`). Without one, whisper.cpp uses the CPU.

| Option | Meaning |
|---|---|
| `--engine auto` | Default: faster-whisper on a working NVIDIA GPU, else whisper.cpp if setup installed it, else faster-whisper on the CPU |
| `--engine whisper.cpp` | whisper.cpp even with an NVIDIA GPU (it can be the faster one, see the table) |
| `--whispercpp-device gpu:1` | Use GPU number 1 (`cpu` for the CPU, `auto` for the first separate GPU). Setup saves the fastest one |

The status line shows where it runs, e.g. `Ready — small + draft base @ Vulkan (AMD Radeon RX 6600)`. To measure again after changing the GPU, delete the `whispercpp-device` line from `config.toml` and run setup again.

Differences from faster-whisper: `--vocab` words are given to whisper.cpp as an initial prompt (it has no hotwords), which helps less with spelling; and the models are separate files (`--model` takes the same names: `tiny`, `base`, `small`, `medium`, `large-v3-turbo`; `distil-*` models don't exist for whisper.cpp).

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

# Names and jargon recognized better (see "Custom vocabulary")
./run.sh --lang id --vocab "Anggi, Kubernetes, OKR, sprint review"

# Most live (faster drafts, sentences lock sooner)
./run.sh --lang id --draft-model tiny --silence-ms 500
```

See every option with `./run.sh --help`, or in the [Other options](#other-options) table.

### Custom vocabulary

Whisper often misspells people's names, product names and jargon it rarely hears. List them and Whisper will prefer them:

```bash
./run.sh --lang id --vocab "Anggi, Kubernetes, OKR"
./run.sh --lang id --vocab-file ~/LiveCaption/vocab.txt
```

The file has one term per line; `#` starts a comment. Both options can be used together. Keep the list short (a few dozen terms at most): Whisper only reads a limited number of tokens, and a very long list makes it more likely to "hear" those words when nobody said them.

### Alert words

Following a long meeting with half an eye? Let the app watch for your name. Open `⋯` → **Alert words…** and type the words, separated by commas, e.g. `Anggi, backend team`. When someone else says one:

- the word is **highlighted** in the captions and the status line says when it was said;
- a **desktop notification** appears with the whole sentence (Linux: through `notify-send`, which every desktop has; macOS: Notification Center; Windows: a notification from Windows PowerShell). It's skipped while the Live Caption window is in front, as the highlight is enough then, and comes at most once every 20 seconds.

Words match whole and in any case: `Anggi` also catches `anggi,` but not `Anggia`. Your own microphone ("Me" lines) never triggers an alert. The words are remembered for the next meeting, and from then on they're also passed to Whisper as [vocabulary](#custom-vocabulary), so a name is spelled right more often. Clear the box to turn alerts off.

From the command line or `config.toml`: `--alert "Anggi, backend team"` or `alert = ["Anggi", "backend team"]`.

### Saving your settings

Instead of typing the same options every time, put them in a settings file once:

```bash
./run.sh --init-config     # writes config.toml with every option, commented out, at its default
```

| OS | Location |
|---|---|
| Linux / macOS | `~/.config/live-caption/config.toml` (or `$XDG_CONFIG_HOME/live-caption/`) |
| Windows | `%APPDATA%\live-caption\config.toml` |

Uncomment the lines you want and change their values. The keys are the command-line options without the leading `--`:

```toml
lang = "id"
model = "medium"
translate = true
vocab-file = "~/LiveCaption/vocab.txt"
summarizer = "ollama"
```

After that, a plain `./run.sh` uses these settings, and the window shows which file was loaded. Options typed on the command line always win for that run, e.g. `./run.sh --model small`. To turn off an option set to `true` in the file, use its `--no-` form: `./run.sh --no-translate`.

- `vocab` also accepts a list: `vocab = ["Anggi", "Kubernetes", "OKR"]`.
- An empty value (`monitor = ""`) means "not set", i.e. the default.
- A mistake (unknown key, wrong value) stops the app with a message naming the file, so it never runs with settings you didn't intend.
- Use another file for one run with `--config ~/work.toml`, e.g. one file per type of meeting.

## Other options

| Option | What it does |
|---|---|
| `--config <path>` / `--init-config` | Use another settings file / write a fresh one (see [Saving your settings](#saving-your-settings)) |
| `--no-translate` | Turn translation off when `translate = true` is in the settings file |
| `--geometry 760x280+0+800` | Window size (and position) for this run, instead of the remembered one |
| `--translate-to en` | Change the translation target language |
| `--translator argos` | **Offline** translation (see Step 5b). Language packages are downloaded automatically on first use |
| `--threshold 0.015` | Raise it if noise gets transcribed; lower it if quiet speech is missed |
| `--draft-interval 0.25` | How often draft text updates (seconds). `0` turns draft mode off (text appears per sentence only, lighter on the GPU) |
| `--draft-model base` | Model used only for drafts. `tiny` = most live but rougher, `same` = use the main model (more accurate, slower, saves VRAM) |
| `--max-seconds 10` | Sentences longer than this are partially locked so drafts stay fast |
| `--silence-ms 500` | Shorter pause, so sentences lock into final text sooner but get cut more often |
| `--beam-size 3` | Better accuracy, slower |
| `--summarizer claude` | Summaries via the Claude API instead of local Ollama (see Step 8). Sends the transcript to Anthropic |
| `--summary-lang id` | Language of the summary. Default `auto`: the meeting's main language |
| `--summary-model <name>` | Model for summaries. Default `qwen2.5:7b` (Ollama; `qwen2.5:3b` for less than 8 GB of RAM) or `claude-opus-5-5` (Claude) |
| `--ollama-url <url>` | Ollama server address (default `http://localhost:11434`) |
| `--vocab "a, b"` / `--vocab-file <path>` | Names and terms Whisper should recognize (see [Custom vocabulary](#custom-vocabulary)) |
| `--device cpu` | Force the CPU (a 9th-gen i7 can still handle the `small` model) |
| `--monitor <name>` | Record a specific output only. Linux: monitor source name (`pactl list short sources`, pick one ending in `.monitor`). Windows: part of the speaker/headset name. macOS: part of the virtual device name (default `BlackHole`) |

## Troubleshooting

- **`libcudnn...so: cannot open shared object`**: start the app with `./run.sh`, not `python live_caption.py` directly. The script sets `LD_LIBRARY_PATH`.
- **Captions feel slow / the "running on CPU" warning appears**: on a GTX 1050, the `small` model takes ~0.4 s per pass on the GPU but ~1.3 s on the CPU. Make sure the status says `CUDA`.
- **Status says CPU instead of CUDA**: read the error message in the status line. The most common causes are a driver that isn't the 580xx series, or running out of VRAM (use a smaller model).
- **`CUDA out of memory`**: use a smaller model, e.g. `medium` → `small`.
- **No text at all**: make sure the meeting audio goes through the default output. Test with `parec --device=@DEFAULT_MONITOR@ | head -c 100000 | wc -c` while playing any sound. If the output is on HDMI or Bluetooth, use `--monitor`.
- **`Neither parec nor pw-record was found` (Linux)**: install the package that provides `parec` for your distribution (see [1.2 System packages](#12-system-packages)): `pulseaudio-utils` on Debian/Ubuntu, Fedora and openSUSE, `libpulse` on Arch.
- **`Recording 'Participant' stopped (pw-record)` (Linux)**: both recorders failed. Check that PipeWire is running: `systemctl --user status pipewire`.
- **`Recording 'Me' lost at 10:42:05 … Reconnecting…`**: the device disconnected mid-meeting (Bluetooth headset, unplugged USB microphone, restarted sound server). The app keeps trying every few seconds and records again by itself, from the default device of that moment; `Recording 'Me' again from …` marks where the gap ends in the transcript. Nothing to do unless it never comes back: then check the device in the system's sound settings.
- **Window doesn't stay on top (Wayland)**: KDE: right-click the title bar → *More Actions* → *Keep Above Others*. GNOME (Ubuntu, Fedora): right-click the title bar → *Always on Top*.
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

- **Switching the default output on Windows mid-meeting** likely keeps recording the old device, so restart the app after switching. (A device that disconnects or is unplugged is handled: see [Troubleshooting](#troubleshooting).)
- **Hallucination filter covers Indonesian and English only.** Fake phrases Whisper produces during silence (like "Thank you for watching") are filtered only in those two languages. For other languages, raise `--threshold` if they show up often.
- **Library versions aren't pinned.** `requirements.txt` lists package names only, so recreating the environment later may install newer versions. To lock today's versions: `uv pip compile requirements.txt -o requirements.lock`, then install with `uv pip install -r requirements.lock`.
- **Moving or renaming the project folder breaks `source .venv/bin/activate`**, because the virtualenv stores its absolute path. `./run.sh` and `run.bat` keep working since they call the venv's python directly. To fix `activate`, delete `.venv` and recreate it with the installation steps above.
- **Windows and macOS support is untested** on real hardware (see sections 2 and 3).

## Project structure

`live_caption.py` is only the launcher that `run.sh` / `run.bat` start. The code lives in the `lib/` package:

| Module | What's in it |
|---|---|
| `main.py` | Wires everything together: creates the recorders, workers and window, and starts them |
| `config.py` | Command-line options, the `config.toml` settings file, the remembered window state |
| `common.py` | Shared constants: audio format (16 kHz, 30 ms frames), OS checks, source labels |
| `audio.py` | Recording (`parec` / `pw-record` / WASAPI / BlackHole) and reconnecting when a device drops, resampling, splitting speech into sentences, the queue to Whisper |
| `transcriber.py` | Loading faster-whisper (GPU with CPU fallback) and turning audio into text; `--vocab` |
| `translation.py` | Google / Argos translation of final lines |
| `summary.py` | Meeting summaries with Ollama or the Claude API |
| `subtitles.py` | SRT / WebVTT export |
| `export.py` | The summary as chat text, plain text and Word (`.docx`, built without extra packages) |
| `history.py`, `history_window.py` | Reading past transcripts back, searching them, adding a summary to them; the Meeting history window |
| `alerts.py` | Alert words: matching them in a line, desktop notifications on each OS |
| `ui.py` | The main window: status header, `⋯` menu, live captions with drafts and translations, alert words, the summary (on demand and automatic) |
| `transcript.py` | The meeting being transcribed: its lines, translations and summary, and the Markdown file they're saved to (no Tkinter) |
| `theme.py` | Colors, fonts and button styles shared by every window |
| `search_bar.py` | The `Ctrl+F` search bar |
| `summary_window.py` | The summary window with its Copy / Save buttons (live and past meetings) |
| `shortcut.py` | The application menu shortcut of `setup.sh` / `setup.ps1` (`.desktop` file, Start menu, `.app`); the icon is in `assets/` |
| `whispercpp.py` | The whisper.cpp engine for non-NVIDIA GPUs: downloading it, choosing the GPU, running `whisper-server` in the background; `tools/build-whisper-cpp.sh` and `.github/workflows/whisper-cpp.yml` build it |
| `model_setup.py` | The model downloads of `setup.sh` / `setup.ps1`: Whisper, Ollama and the summary model, with checks and retries |

Data flows one way: `audio` → `transcriber` → `ui` (→ `translation` / `summary` on demand). Modules talk through queues, so only the window modules (`ui`, `search_bar`, `summary_window`, `history_window`, `theme`) touch Tkinter, and only `main` knows all the pieces. What a window shows but doesn't draw (`transcript`, `history`, `export`, `alerts`) has no Tkinter in it, so it can be tested without a window. A new feature usually means one new module plus a few lines in `ui.py`, `config.py` (its options) and `main.py` (to wire it in).

## Running the tests

The tests use only the Python standard library (`unittest`), so nothing extra needs to be installed:

```bash
.venv/bin/python -m unittest discover -s tests -v       # Linux/macOS
.venv\Scripts\python -m unittest discover -s tests -v   # Windows
```

They are lightweight and finish in about a second: no Whisper model, no audio recording, no window and no internet. They cover the audio pipeline (segmenting, pause, re-chunking, resampling), the Whisper settings (`--vocab`, draft vs. final), translation skipping, the transcript format and the summary (against a fake local Ollama server; the Claude API is not called).

## Ethics and privacy

- Let meeting participants know the conversation is being transcribed, especially at work.
- Speech-to-text always runs **locally** on your computer. No audio leaves your machine.
- Translation with the default `--translator google` **sends the text of every final sentence to Google**. For confidential meetings, use `--translator argos` (fully offline after the first language package download) or leave translation off.
- Summaries with the default Ollama engine run **locally**. With `--summarizer claude`, **the whole transcript is sent to Anthropic** each time you press Summary.
- Transcripts are saved as plain-text Markdown in `~/LiveCaption/`. Delete them when you no longer need them.
