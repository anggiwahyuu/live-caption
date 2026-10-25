# Live Caption

Real-time captions for online meetings (Zoom, Google Meet, Teams, Discord, and more) on **Linux, Windows and macOS**, in a small always-on-top window, like subtitles.

- **Works with any meeting app**: it captions the audio coming out of your speakers or headset, and your microphone if you like.
- **Live**: grey draft text appears while people speak and locks into the final text when they pause.
- **Local speech-to-text on any computer**: NVIDIA, AMD, Intel and Apple GPUs, or the CPU. No audio leaves your computer.
- **Translation** of every sentence, online (Google) or offline (Argos), switched on and off from the window.
- **Meeting summary** with decisions, action items and open questions, by a local AI model (Ollama), ready to paste into WhatsApp, Slack or Word.
- **Ask about the meeting**: *"when is the deadline?"*, answered with the lines it comes from.
- **Alert words**: a notification when someone says your name.
- **Meeting history**: every transcript saved automatically, searchable, with subtitles (`.srt`) for recordings.

## Setup

One command installs everything the app needs, downloads the models, and asks whether you also want meeting summaries:

```bash
./setup.sh                                                    # Linux and macOS
```

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1            # Windows (PowerShell, in the project folder)
```

What it does, skipping whatever is already there (so it's safe to run again):

1. **System packages** (Linux/macOS only): the audio recorder, Tkinter and curl through your package manager (apt, dnf, pacman or zypper), the Vulkan driver for AMD/Intel GPUs, or BlackHole through Homebrew on macOS. This asks for your password.
2. **`uv` and the Python environment** (`.venv`), with the tested library versions. The CUDA libraries (2 GB) only on computers with an NVIDIA GPU.
3. **A shortcut in the application menu** (Linux: KDE, GNOME, XFCE, …; Windows: Start menu; macOS: `~/Applications/Live Caption.app`), so the app opens without a terminal. It starts with the settings in your [`config.toml`](#saving-your-settings), so put options like `lang = "id"` there.
4. **The speech-to-text engine and its models** (`small` + `base` for drafts, or whatever `config.toml` says). With a working NVIDIA GPU that's faster-whisper; otherwise whisper.cpp, after measuring which of your GPUs (or the CPU) is fastest ([Models and GPUs](docs/models.md)).
5. **Meeting summaries, if you say yes:** installs [Ollama](https://ollama.com) when it's missing (it asks first and shows the command), downloads `qwen2.5:7b` (`qwen2.5:3b` on computers with less than 8 GB of RAM) and checks that it loads.

At the end it lists what's ready and, for anything that failed, the command that fixes it.

| Option (the same on Windows) | Effect |
|---|---|
| `--summary` / `--no-summary` | Set up meeting summaries without asking / skip them |
| `--summary-only` | Only add the summary model later |
| `--no-whisper` | Don't download the Whisper models now (the app does it on its first start) |
| `-y`, `--yes` | Answer every question with its default |
| `--no-shortcut` | Don't add the app to the application menu |
| `--recreate-venv` | Delete `.venv` and build it again |
| app options | Pick other models, as with `./run.sh`: `--model medium`, `--summary-model qwen2.5:3b` |

Two things the script can't do for you:

- **The NVIDIA driver** (it needs a reboot and depends on your card): see [NVIDIA driver](docs/install.md#nvidia-driver-only-with-an-nvidia-gpu). Without an NVIDIA GPU there's nothing to do.
- **On macOS, sending the meeting audio to the app** (below).

Rather do everything by hand? See [Installing by hand](docs/install.md).

### macOS: route the meeting audio through BlackHole

macOS can't record system audio on its own, so the free virtual audio device **BlackHole** (installed by `setup.sh`) carries a copy of the meeting audio to the app, while you keep hearing it:

1. Open **Audio MIDI Setup** (Applications → Utilities).
2. Click **+** at the bottom left → **Create Multi-Output Device**.
3. Tick your speakers/headset **and** **BlackHole 2ch**. Put your speakers/headset first and enable *Drift Correction* for BlackHole.
4. Pick this Multi-Output Device as the output: either system-wide (System Settings → Sound → Output) or only in the meeting app's speaker setting.

The volume keys don't control a Multi-Output Device, so set the volume on the device itself or in the meeting app. On the first run, macOS asks whether your terminal may use the microphone: allow it, since BlackHole counts as one.

## Using it

### Before the meeting

1. **Join the meeting first** and make sure you hear the others through your speakers/headset. Live Caption records what *comes out* of the default output device.
2. **Start the app** from the application menu, or from the project folder with `./run.sh` (Linux/macOS) or `.\run.bat` (Windows). Always use these, not `python live_caption.py`: they use the project's environment and, on Linux, set up the CUDA libraries.

```bash
./run.sh --lang id
```

> **On Windows**, replace `./run.sh` with `.\run.bat` in this command and in every other command in this README.

The first start downloads the Whisper models if setup didn't. Then watch the **status line** in the top-left corner:

| Status | Meaning |
|---|---|
| `Loading model 'small' on CUDA (int8)…` | Loading the model onto the GPU |
| `Ready — small + draft base @ CUDA · language: id` | Ready. `@` shows where it runs: `CUDA`, `Vulkan (…)`, `Metal (…)` or `CPU` |
| `N sentences behind — try a smaller model` | The computer can't keep up with the audio ([Models and GPUs](docs/models.md)) |

Once it says **Ready**, captions appear as soon as someone speaks.

### Audio source and language

| Command | What gets transcribed | Label on screen |
|---|---|---|
| `./run.sh` (default `--source system`) | Other participants (audio coming out of your speakers/headset) | **Participant** |
| `./run.sh --source mic` | Your voice, from the default microphone | **Me**, or your name with `--my-name` |
| `./run.sh --source both` | Both at once | **Participant** and **Me** |
| `./run.sh --monitor <name>` | A specific output instead of the default one, e.g. HDMI or Bluetooth | **Participant** |

> **Use a headset with `--source both`.** With speakers, your microphone also picks up the other participants, so every sentence shows up twice.

The `--monitor` name: on Linux, a monitor source from `pactl list short sources | grep monitor`; on Windows, part of the speaker/headset name in the sound settings, e.g. `--monitor "Headphones"`; on macOS, part of the virtual device's name (default `BlackHole`). Without `--monitor`, the app follows the default device, also when it changes or reconnects during the meeting.

```bash
./run.sh --lang id     # meeting in Indonesian
./run.sh --lang en     # meeting in English
./run.sh               # --lang auto: detected per sentence
```

If you know the meeting language, **set `--lang`**: results are more stable and drafts appear faster. `auto` suits mixed-language meetings, but very short sentences are sometimes detected as the wrong language.

### The window

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

The dot shows the state at a glance: **green** listening, **amber** paused, **red** an error (read the message next to it), **grey** still loading. Consecutive sentences from the same speaker share one header; a new header appears when the speaker changes or after a 2-minute gap.

| Control | Where | Shortcut | What it does |
|---|---|---|---|
| **Pause / Resume** | button | `Ctrl+P` | Stops transcribing, e.g. for an off-the-record chat. Audio during the pause is discarded, never transcribed |
| **Summary** | button | `Ctrl+M` | Summarizes the meeting so far ([Meeting summary](#meeting-summary)) |
| **Translate → id** | `⋯` menu | `Ctrl+T` | Turns translation on/off ([Translation](#translation)) |
| **Always on top** | `⋯` menu | | Keeps the window above the meeting app, like subtitles |
| **Auto summary** | `⋯` menu | | Makes the summary by itself after a silence and when you close the window |
| **Alert words…** | `⋯` menu | | Tells you when someone says your name ([Alert words](#alert-words)) |
| **Ask about the meeting…** | `⋯` menu | `Ctrl+K` | Ask a question and get the answer with the lines it comes from ([Asking about the meeting](#asking-about-the-meeting)) |
| **Search…** | `⋯` menu | `Ctrl+F` | Search the captions on screen (see below) |
| **Meeting history…** | `⋯` menu | `Ctrl+H` | Past meetings ([Meeting history](#meeting-history)) |
| **Save as…** | `⋯` menu | `Ctrl+S` | A copy of the transcript file with its summary (`.md`), the summary and the whole transcript in one Word document, the transcript alone as a Word document (`.docx`) or plain text (`.txt`), or subtitles (`.srt`, `.vtt`) |
| **Copy transcript for chat** | `⋯` menu | | The whole transcript for WhatsApp, Slack or Telegram. A long meeting may be too long for one message (Telegram takes about 4,000 characters): the status line says so |
| **Clear screen** | `⋯` menu | `Ctrl+L` | Clears *the screen only*. The transcript file stays intact |

Shortcuts work while the window is focused. On macOS, use `Cmd` instead of `Ctrl`.

- **Draft** (grey italics): the sentence being spoken, updated about every 0.25 seconds. Words may change as the sentence grows: Whisper refines its guess as it hears more.
- **Final** (with a timestamp): locked when the speaker pauses for about 0.7 seconds. Only final lines go into the transcript.
- **Translation** (yellow italics, `↳`): appears under its final line.
- Scrolled up to read older text? New lines won't make the window jump; scroll back down to follow the latest again.
- **Searching** (`Ctrl+F`): every match is highlighted and the newest one selected. `Enter` / `F3` goes to the next, `Shift+Enter` / `Shift+F3` to the previous; `Esc` closes the search and jumps back to the live captions. Drafts aren't searched, and after **Clear screen** earlier lines are only in the transcript file.
- The window's size, position and the **Always on top** and **Auto summary** ticks are remembered for the next run. `--geometry 760x280` overrides them for one run.

### Translation

Turn it on from the start with `--translate`, or at any time with **Translate → id** in the `⋯` menu (`Ctrl+T`). Only final lines are translated, from the moment it's on, and sentences already in the target language are left alone.

```bash
./run.sh --lang en --translate                       # English → Indonesian, with Google (online)
./run.sh --lang id --translate --translate-to en     # Indonesian → English
./run.sh --lang en --translate --translator argos    # offline, with Argos
```

- **Google** (default) needs internet, and **every final sentence is sent to Google**. For confidential meetings, use Argos.
- **Argos** works offline. The first time you use a language pair (e.g. en→id), its package is downloaded (needs internet, 30–60 seconds), so do that once before the meeting. After that, ~0.1 s per sentence. Without a direct package, it translates through English (e.g. ja → en → id).

### Meeting summary

Press **Summary** (`Ctrl+M`) at any time, usually at the end. An AI model reads the final lines and writes the **summary**, the **decisions**, the **action items** (who does what, by when) and the **open questions**. It opens in its own window and is added at the top of the transcript file; press it again later for a summary of the longer meeting.

| Button | Gives you |
|---|---|
| **Copy for chat** | Text for WhatsApp, Slack or Telegram: bold headings and `•` bullets, which all three show as intended |
| **Copy Markdown** | The summary as written, for Notion, Obsidian, GitHub, … |
| **Save as…** | A **Word document** (`.docx`) with the summary only or with the **whole transcript after it**, plain text (`.txt`, e.g. for an email) or Markdown |

**Automatic summary:** tick **Auto summary** in the `⋯` menu and the summary is made by itself after **10 minutes without speech** (when at least 5 new lines were said; change the wait with `--auto-summary-after 5`), and when you **close the window** (it closes once the summary is saved; close it again to quit at once). Press **Pause** during a break, as the silence clock doesn't run while paused.

There are two engines:

**Ollama, local (default).** Nothing leaves your computer. `./setup.sh --summary-only` (Windows: `setup.ps1 --summary-only`) sets it up, or by hand:

```bash
curl -fsSL https://ollama.com/install.sh | sh   # any Linux distribution; Windows/macOS: installer from ollama.com
ollama pull qwen2.5:7b                          # ~4.7 GB, handles Indonesian and English
```

A summary takes about **1 minute** for a short meeting and **4 minutes** for a 50-minute one with a GPU (measured on a GTX 1050), about twice as long on the CPU; it needs about **5 GB of free RAM**, and the window stays usable meanwhile. On Arch and CachyOS, Ollama uses the GPU only with the `ollama-vulkan` package: setup installs it, or offers to when Ollama is already there without it. Small local models tend to make things up (a date nobody said, the wrong person for a task), so the app checks the model's work: every decision, task and question must come with a word-for-word quote from the transcript, items whose quote isn't there are dropped, and deadlines and names are kept only if they were said near the quote. Still, read the summary before sharing it. Less than 8 GB of RAM? `--summary-model qwen2.5:3b` is about twice as fast but much less reliable: in our tests it missed decisions, gave tasks to the wrong person and left most questions unanswered. Ollama on another machine: `--ollama-url http://192.168.1.10:11434`.

**Claude API, online (opt-in).** Clearly better summaries, but **the whole transcript is sent to Anthropic**. Don't use it for confidential meetings.

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # or: ant auth login
./run.sh --lang id --summarizer claude
```

The default model is `claude-opus-5-5` (`--summary-model` changes it); each summary is billed to your API account (an hour-long meeting is roughly 15–20k input tokens).

Summaries are written in the meeting's main language; `--summary-lang id` picks another. Speakers aren't told apart (only **Me**, or your `--my-name`, vs **Participant**), so check names and action items before sending a summary on.

### Asking about the meeting

Joined late, or missed a part? `⋯` → **Ask about the meeting…** (`Ctrl+K`) and type a question, in any language:

- *Kapan deadline load test, dan siapa yang mengerjakan?*
- *What was said about the server budget?*

The summary model reads everything said so far and answers in a sentence or three, with the transcript lines it comes from; **click one** to scroll the captions to it. The app checks that these lines really are in the transcript: when the model "quotes" something nobody said, the answer is marked *check before relying on this answer*. Questions and answers are saved at the end of the transcript file. With `qwen2.5:7b` an answer takes about 10–20 seconds; follow-up questions are quicker. With Claude, the whole transcript is sent to Anthropic with every question.

### Meeting history

Every meeting is **saved automatically** as it goes, to `~/LiveCaption/meeting-<date>_<time>.md` (Windows: `C:\Users\<you>\LiveCaption\`; change it with `--out-dir`), so closing the app never loses anything:

```markdown
# Meeting transcript — October 21, 2026, 14:00

- **[14:02:15] Participant:** Good morning everyone, let's start the meeting.
  - _Selamat pagi semuanya, mari kita mulai rapatnya._
```

`⋯` → **Meeting history…** (`Ctrl+H`) lists them, newest first. Type in the search box to search **all meetings at once** (all words must occur in the meeting; translations, summaries and questions count too). For the selected meeting: **Summarize** makes or redoes its summary, **Ask…** asks questions about it, and the `⋯` menu has **Save as…** (like `Ctrl+S`: the whole file, the summary and transcript as Word, or the transcript alone as Word or plain text), **Copy transcript for chat** and **Open file**, which opens the transcript in your editor (or double-click the meeting).

**Subtitles for a recording:** if you recorded the meeting (Zoom/Meet recording, OBS, …), choose **Save as…** (`Ctrl+S`) → **SubRip (`.srt`)** or **WebVTT (`.vtt`)**. The app asks when the recording started; subtitle times count from there. Put the file next to the video with the same name (`meeting.mp4` + `meeting.srt`) and VLC, mpv or a YouTube upload picks it up. If they run consistently early or late, export again with the start time adjusted.

### Alert words

Following a long meeting with half an eye? `⋯` → **Alert words…** and type the words, separated by commas, e.g. `Anggi, backend team`. When someone else says one, it's **highlighted** in the captions and a **desktop notification** shows the sentence (not while the window is in front, and at most once every 20 seconds).

Words match whole and in any case: `Anggi` also catches `anggi,` but not `Anggia`. Your own microphone never triggers an alert. The words are remembered, and also passed to Whisper as [vocabulary](#custom-vocabulary) so a name is spelled right more often. From `config.toml`: `alert = ["Anggi", "backend team"]`.

### Custom vocabulary

Whisper often misspells names, product names and jargon it rarely hears. List them and it will prefer them:

```bash
./run.sh --lang id --vocab "Anggi, Kubernetes, OKR"
./run.sh --lang id --vocab-file ~/LiveCaption/vocab.txt   # one term per line; # starts a comment
```

Keep the list short (a few dozen terms at most): a very long list makes Whisper "hear" those words when nobody said them.

### Saving your settings

Instead of typing the same options every time, put them in a settings file once:

```bash
./run.sh --init-config     # writes config.toml with every option, commented out, at its default
```

It's in `~/.config/live-caption/config.toml` (Linux, macOS) or `%APPDATA%\live-caption\config.toml` (Windows). Uncomment the lines you want; the keys are the command-line options without the leading `--`:

```toml
lang = "id"
model = "medium"
translate = true
vocab = ["Anggi", "Kubernetes", "OKR"]
```

After that, a plain `./run.sh` (and the menu shortcut) uses these settings. Options typed on the command line win for that run; `--no-translate` turns off an option set to `true` in the file. A mistake in the file stops the app with a message naming it. `--config ~/work.toml` uses another file for one run, e.g. one per type of meeting.

### Ready-to-use commands

```bash
./run.sh --lang id                                            # internal meeting in Indonesian
./run.sh --lang en --translate                                # English webinar, translated to Indonesian (online)
./run.sh --lang en --translate --translator argos             # the same, offline
./run.sh --source both --lang id                              # participants + your own voice (with a headset)
./run.sh --monitor bluez_output.XX_XX_XX_XX_XX_XX.1.monitor   # meeting audio on a Bluetooth headset (Linux)
.\run.bat --monitor "Headphones" --lang id                    # a specific headset (Windows)
./run.sh --model medium --lang en                             # more accurate (needs 4 GB of VRAM)
./run.sh --lang id --draft-model tiny --silence-ms 500        # most live: faster drafts, sentences lock sooner
```

### All options

`./run.sh --help` lists every option. The ones not covered above:

| Option | What it does |
|---|---|
| `--model <name>` / `--draft-model <name>` | The model for final text (default `small`) / for drafts (default `base`; `tiny` = most live, `same` = the main model). See [Models and GPUs](docs/models.md) |
| `--device cpu` | Use the CPU even when there's a GPU |
| `--engine`, `--whispercpp-device` | Which engine and GPU ([Models and GPUs](docs/models.md#gpus-other-than-nvidia-amd-intel-apple)) |
| `--threshold 0.015` | Raise it if noise gets transcribed; lower it if quiet speech is missed |
| `--draft-interval 0.25` | How often drafts update (seconds); `0` turns drafts off (lighter on the GPU) |
| `--silence-ms 500` | A shorter pause locks sentences sooner, but cuts them more often |
| `--max-seconds 10` | Longer sentences are partly locked, so drafts stay fast |
| `--beam-size 3` | Better accuracy, slower |
| `--translate-to en`, `--translator argos` | Translation target and engine ([Translation](#translation)) |
| `--summarizer`, `--summary-model`, `--summary-lang`, `--ollama-url` | Summary engine, model, language and Ollama address ([Meeting summary](#meeting-summary)) |
| `--auto-summary` / `--no-auto-summary` | Start with **Auto summary** on / off, whatever the tick says |
| `--alert "Anggi, backend team"` | Alert words for this run ([Alert words](#alert-words)) |
| `--my-name "Anggi"` | Your name in place of **Me**: on screen, in the transcript file, summaries (e.g. action items), answers and exports. Put `my-name = "Anggi"` in `config.toml` to keep it. Meetings saved before still say **Me** |
| `--out-dir <folder>` | Where transcripts are saved (default `~/LiveCaption`) |
| `--font-size 15`, `--opacity 1` | Text size (default 13) and window opacity (default 0.93) |
| `--geometry 760x280+0+800` | Window size and position for this run |
| `--compute-type int8` | Number format on an NVIDIA GPU; `int8` (default) suits GTX 10xx best, `float16` newer cards |
| `--config <path>`, `--init-config` | Another settings file / write a fresh one ([Saving your settings](#saving-your-settings)) |

## Troubleshooting

- **The app doesn't open from the menu**: started from the menu, it writes its messages to `~/.config/live-caption/live-caption.log` (Windows: `%APPDATA%\live-caption\live-caption.log`). To remove the shortcut: `.venv/bin/python -m lib.shortcut --remove` (Windows: `.venv\Scripts\python.exe -m lib.shortcut --remove`).
- **No text at all**: make sure the meeting audio goes through the default output. On Linux, test with `parec --device=@DEFAULT_MONITOR@ | head -c 100000 | wc -c` while playing any sound. If the output is HDMI or Bluetooth, use `--monitor`.
- **Status says `CPU` although there's an NVIDIA GPU**: read the message in the status line. Start the app with `./run.sh` / `run.bat` (a `libcudnn…so: cannot open shared object` error means it wasn't), check that `nvidia-smi` works, and on a GTX 9xx/10xx that the driver is the 580 series. `The GPU's memory is full`: another program uses it; close it, or use a smaller model. When that program is Ollama, which keeps the summary model in the GPU for 5 minutes after each summary, the app asks it to let go and says `unloaded Ollama's …`. On a 4 GB GPU, a summary during the meeting slows the captions a little (medium: from 1.4 to 1.9 s per sentence while it runs).
- **"N sentences behind" / captions feel slow**: try `--draft-interval 0.5`, `--draft-model tiny`, or a smaller model ([Models and GPUs](docs/models.md)).
- **`Recording 'Me' lost at 10:42:05 … Reconnecting…`**: the device disconnected (Bluetooth headset, unplugged USB microphone, restarted sound server). The app keeps trying and records again by itself, from the default device of that moment; `Recording 'Me' again from …` marks where the gap ends. Check the device only if it never comes back.
- **`Neither parec nor pw-record was found`** (Linux): install `pulseaudio-utils` (Debian/Ubuntu, Fedora, openSUSE) or `libpulse` (Arch). **`Recording 'Participant' stopped (pw-record)`**: both recorders failed; check that PipeWire runs: `systemctl --user status pipewire`. A one-time `parec unavailable (…), switching to pw-record` is only a notice.
- **Window doesn't stay on top (Wayland)**: KDE: right-click the title bar → *More Actions* → *Keep Above Others*. GNOME: right-click the title bar → *Always on Top*.
- **Odd text like "Terima kasih." / "Thank you." during silence**: a typical Whisper hallucination, from the videos it learned from. The usual ones are dropped in 13 languages, and subtitle credits ("Amara.org") or sound labels ("[Music]") always. If something else still shows up often, raise `--threshold`.
- **Rough, pixelated text in the window** (Linux): the environment was built on a Python without font smoothing; rebuild it with `./setup.sh --recreate-venv` ([why](docs/install.md#python-environment)).

**Windows**

- **`no loopback device matching '…'`**: the `--monitor` name doesn't match any output. List them with:

  ```powershell
  .venv\Scripts\python -c "import pyaudiowpatch as p; [print(d['name']) for d in p.PyAudio().get_loopback_device_info_generator()]"
  ```

- **`Recording 'Participant' now from Headphones (…) [Loopback]`**: you changed the default output, or Windows did when a headset was connected; the app moved along with it.
- **No text while the meeting is silent**: normal. WASAPI loopback only delivers audio while something is playing.

**macOS**

- **`no input device matching 'BlackHole'`**: BlackHole isn't installed, or macOS hasn't loaded it yet. Run `brew install --cask blackhole-2ch`, then restart the Mac.
- **No text at all**: check that the meeting audio goes to the Multi-Output Device that includes BlackHole, and that your terminal has microphone access (System Settings → Privacy & Security → Microphone).
- **You hear nothing after picking BlackHole**: you selected BlackHole itself as the output. Pick the Multi-Output Device instead.

## Known limitations

- **Windows and macOS support is untested** on real hardware. Please report anything that doesn't work.
- **Windows, `--source both`: a device plugged in after the app started** isn't seen until the app restarts (the app says so). Switching between devices that were there at the start works.
- **Moving or renaming the project folder breaks `source .venv/bin/activate`**. `./run.sh` and `run.bat` keep working; to fix `activate`, run `./setup.sh --recreate-venv`.

## Privacy

- Let meeting participants know the conversation is being transcribed, especially at work.
- Speech-to-text always runs **locally**. No audio leaves your computer.
- Translation with the default `--translator google` **sends the text of every final sentence to Google**. For confidential meetings, use `--translator argos` or leave translation off.
- Summaries and questions with the default Ollama engine run **locally**. With `--summarizer claude`, **the whole transcript is sent to Anthropic** each time.
- Transcripts are saved as plain-text Markdown in `~/LiveCaption/`. Delete them when you no longer need them.

## More

- [Installing by hand](docs/install.md): every step of the setup scripts, per OS, and the NVIDIA driver per distribution
- [Models and GPUs](docs/models.md): which model fits your GPU, AMD/Intel/Apple GPUs, speed measurements
- [Development](docs/development.md): how the code is organized, tests, library versions, building whisper.cpp
