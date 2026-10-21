# Live Caption

Real-time speech-to-text for online meetings (Zoom, Google Meet, Teams, Discord, and more) on CachyOS. Translation is optional and can be switched on or off right from the app window.

- Captures **the audio coming out of your speakers/headset**, so it works with any meeting app.
- Local STT with **faster-whisper** on the GPU (GTX 1050, `int8`). Falls back to the CPU automatically if the GPU can't be used.
- **Real-time with draft mode**: temporary text (grey italics) appears while people speak and updates about every 0.25 seconds, then locks into final text when the speaker pauses. Drafts use a small model (`base`) for speed; final text uses the main model for accuracy.
- A small always-on-top window, like subtitles.
- Transcripts are saved automatically to `~/LiveCaption/meeting-<date>.md`.

---

## 1. Check the NVIDIA driver

GTX 10xx (Pascal) is **not supported** by NVIDIA driver 590 and newer. You need the **580xx** series.

```bash
nvidia-smi
```

If this shows your GPU and driver version `580.xx`, you're fine. Also note your **VRAM** (2 GB, 3 GB, or 4 GB), since it decides which model to pick below.

If `nvidia-smi` errors out, let CachyOS's built-in tool pick the right driver:

```bash
sudo chwd -a
```

Don't install `nvidia-open` or 590+ packages manually.

## 2. System packages

```bash
sudo pacman -S --needed libpulse tk uv
```

- `libpulse` provides the `parec` command used to record audio (it runs on top of pipewire-pulse, the default on CachyOS).
- `uv` is used to create the Python environment.

## 3. Python environment

CachyOS ships the latest Python, which often doesn't have wheels for AI libraries yet. That's why a separate **Python 3.12** is used via `uv`:

```bash
cd my-first-assistant
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -r requirements.txt
chmod +x run.sh
```

Whisper models are downloaded automatically on first run, to `~/.cache/huggingface`.

## 4. Running

This section walks you through everything from preparing before a meeting to saving the transcript. Run all commands from the project folder.

### Step 1 — Before the meeting

1. **Join the meeting first** (Zoom, Meet, Teams, etc.) and make sure you can hear the other participants through your speakers/headset. Live Caption records the audio that *comes out* of the default output device, so it works with any meeting app.
2. **Check which audio output is active.** If the sound goes through HDMI or Bluetooth instead of the default output, note the name of its monitor source:

   ```bash
   pactl list short sources | grep monitor
   ```

   You will pass that name to `--monitor` (see Step 3).
3. **Always start the app with `./run.sh`**, not `python live_caption.py`. The script activates the virtualenv and sets up the CUDA libraries. Without it, the app falls back to the CPU and captions become much slower.

### Step 2 — First run

```bash
./run.sh --lang id
```

On the **first run**, the Whisper models (`small` for final text and `base` for drafts) are downloaded automatically to `~/.cache/huggingface`, so you need an internet connection and a little patience. After that, the models load from the cache.

Watch the **status line** in the top-left corner of the window:

| Status | Meaning |
|---|---|
| `Loading model 'small' on CUDA (int8)…` | Loading the model onto the GPU |
| `Ready — small + draft base @ CUDA · language: id` | Ready, running on the GPU. This is what you want to see |
| `… @ CPU …` or the warning "running on CPU" | The GPU could not be used. See [Troubleshooting](#troubleshooting) |
| `N sentences behind — try a smaller model` | The GPU can't keep up with the audio |

Once the status says **Ready**, captions appear as soon as someone speaks.

### Step 3 — Choose the audio source

| Command | What gets transcribed | Label on screen |
|---|---|---|
| `./run.sh` (default `--source system`) | Other participants (audio coming out of your speakers/headset) | **Participant** |
| `./run.sh --source mic` | Your voice, from the default microphone | **Me** |
| `./run.sh --source both` | Both at once | **Participant** and **Me** |
| `./run.sh --monitor <name>.monitor` | A specific audio output (HDMI/Bluetooth) | **Participant** |

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

Requires an internet connection during the meeting.

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

If you scroll up to read older text, new sentences won't make the window jump back down (except when a new translation arrives). Scroll back to the bottom to follow the latest text again.

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

# Meeting audio going to a Bluetooth headset
./run.sh --monitor bluez_output.XX_XX_XX_XX_XX_XX.1.monitor --lang id

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
| `--monitor <name>` | Record a specific output only (list: `pactl list short sources`, pick one ending in `.monitor`) |

## Troubleshooting

- **`libcudnn...so: cannot open shared object`**: start the app with `./run.sh`, not `python live_caption.py` directly. The script sets `LD_LIBRARY_PATH`.
- **Captions feel slow / the "running on CPU" warning appears**: on a GTX 1050, the `small` model takes ~0.4 s per pass on the GPU but ~1.3 s on the CPU. Make sure the status says `CUDA`.
- **Status says CPU instead of CUDA**: read the error message in the status line. The most common causes are a driver that isn't the 580xx series, or running out of VRAM (use a smaller model).
- **`CUDA out of memory`**: use a smaller model, e.g. `medium` → `small`.
- **No text at all**: make sure the meeting audio goes through the default output. Test with `parec --device=@DEFAULT_MONITOR@ | head -c 100000 | wc -c` while playing any sound. If the output is on HDMI or Bluetooth, use `--monitor`.
- **Window doesn't stay on top (Wayland/KDE)**: right-click the title bar → *More Actions* → *Keep Above Others*.
- **Status "N sentences behind"**: the GPU can't keep up. Try `--draft-interval 0.5`, `--draft-model tiny`, or a smaller main model.
- **Odd text like "Terima kasih." / "Thank you." during silence**: a typical Whisper hallucination. Some of it is already filtered; raise `--threshold` if it still happens often.

## Ethics

Let meeting participants know the conversation is being transcribed, especially at work.
