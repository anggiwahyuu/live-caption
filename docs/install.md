# Installing by hand

`./setup.sh` (Linux, macOS) and `setup.ps1` (Windows) do all of this for you: see [Quick setup](../README.md#setup) in the README. This page is for doing it step by step, or for seeing what the scripts do. Two things the scripts never do are also here: [the NVIDIA driver](#nvidia-driver-only-with-an-nvidia-gpu) and, on macOS, [routing the audio through BlackHole](../README.md#macos-route-the-meeting-audio-through-blackhole).

- [Linux](#linux)
- [Windows](#windows)
- [macOS](#macos)

How each OS records the audio and runs speech-to-text:

| OS | System audio (other participants) | Microphone | Speech-to-text |
|---|---|---|---|
| Linux | `parec`, or `pw-record` if `parec` is missing or has no PulseAudio/pipewire-pulse server | Same | NVIDIA GPU (CUDA), AMD/Intel GPU (Vulkan) or CPU |
| Windows | PyAudioWPatch (WASAPI loopback), nothing extra to install | PyAudioWPatch | NVIDIA GPU (CUDA), AMD/Intel GPU (Vulkan) or CPU |
| macOS | sounddevice + the free **BlackHole** virtual audio device | sounddevice | Apple Silicon GPU (Metal), or CPU on Intel Macs |

## Linux

Works on any distribution with PulseAudio or PipeWire (the default on practically every desktop distribution). Commands are given for the common families:

| Family | Examples |
|---|---|
| **Debian/Ubuntu** | Ubuntu, Kubuntu, Linux Mint, Pop!_OS, Zorin OS, elementary OS, Debian |
| **Fedora** | Fedora Workstation/KDE, Nobara |
| **Arch** | CachyOS, Arch Linux, EndeavourOS, Manjaro, Garuda |
| **openSUSE** | Tumbleweed, Leap |

### NVIDIA driver (only with an NVIDIA GPU)

No NVIDIA GPU? Skip this step: AMD and Intel GPUs are used through whisper.cpp ([Models and GPUs](models.md#gpus-other-than-nvidia-amd-intel-apple)), and without a usable GPU the app runs on the CPU.

You only need the **driver**. The CUDA libraries the app uses (cuBLAS, cuDNN) are installed with the Python libraries below, so **don't install the CUDA toolkit**.

Check whether a driver is already working:

```bash
nvidia-smi
```

If it shows your GPU, note the driver version and your **VRAM** (2 GB, 3 GB, 4 GB, …): it decides which model to pick ([Models and GPUs](models.md#choosing-a-model-for-your-vram)). Then skip to the system packages.

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

### System packages

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

Without an NVIDIA GPU, install the Vulkan driver too, so whisper.cpp can use an AMD or Intel GPU: `libvulkan1 mesa-vulkan-drivers` (Debian/Ubuntu), `vulkan-loader mesa-vulkan-drivers` (Fedora), `vulkan-icd-loader` plus `vulkan-radeon` or `vulkan-intel` (Arch), `libvulkan1 libvulkan_radeon libvulkan_intel` (openSUSE).

### Python environment

The same on every distribution. The environment is built on **your distribution's own Python** (3.10 or newer: Ubuntu 22.04 and later, Debian 12 and later, current Fedora, Arch and openSUSE all qualify). Installing into `.venv` doesn't touch your system Python.

```bash
cd live-caption
uv venv --python /usr/bin/python3 .venv
uv pip sync --torch-backend cpu requirements.lock                            # without an NVIDIA GPU
uv pip sync --torch-backend cpu requirements.lock requirements-nvidia.lock   # with one
chmod +x run.sh
```

This downloads about 1 GB, plus 2 GB of CUDA libraries with an NVIDIA GPU. The `.lock` files hold the exact versions that were tested ([Library versions](development.md#library-versions)). `--torch-backend cpu` installs torch (needed by offline translation, which runs on the CPU) without the ~2.5 GB of CUDA libraries it would otherwise bring. Whisper models are downloaded automatically on first run, to `~/.cache/huggingface`.

Why the distribution's Python, not one downloaded by `uv` (`uv venv --python 3.12`)? On Linux, the Python that `uv` downloads comes with a Tk that has **no font smoothing**: the window can only use a small bitmap font, ignores `--font-size`, and can't show characters such as `↳`, `…` or `—`. The distribution's Tk (the `python3-tk` / `tk` package above) uses your desktop's fonts. The app shows a notice in the window if it detects the rough fonts.

Already set up the environment with `uv venv --python 3.12` before? Recreate it (your settings and transcripts are kept, they live outside the project folder):

```bash
rm -rf .venv
uv venv --python /usr/bin/python3 .venv
uv pip sync --torch-backend cpu requirements.lock   # add requirements-nvidia.lock with an NVIDIA GPU
```

If your distribution's Python is older than 3.10, use `uv venv --python 3.12 .venv` instead: everything works, only the text looks rough.

## Windows

> Not tested on real Windows hardware yet. Please report anything that doesn't work.

1. **NVIDIA GPU (optional):** install a recent NVIDIA driver with CUDA 12 support. GTX 10xx (Pascal) cards need the **580** series, the last one that supports them. AMD and Intel GPUs are used through whisper.cpp ([Models and GPUs](models.md#gpus-other-than-nvidia-amd-intel-apple)), which `setup.ps1` installs; without a usable GPU, the app runs on the CPU.
2. **Install `uv`** in PowerShell:

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

   Close and reopen PowerShell afterwards so `uv` is on your `PATH`.
3. **Create the Python environment** (Tkinter is included in the Python that `uv` installs):

   ```powershell
   cd live-caption
   uv venv --python 3.12 .venv
   uv pip sync --torch-backend cpu requirements.lock                            # without an NVIDIA GPU
   uv pip sync --torch-backend cpu requirements.lock requirements-nvidia.lock   # with one
   ```

   This also installs **PyAudioWPatch**, which records the audio of your default speakers/headset through WASAPI loopback. No virtual audio cable is needed.
4. **Start the app with `run.bat`** instead of `./run.sh` in every command in the README:

   ```powershell
   .\run.bat --lang id
   ```

Transcripts are saved to `C:\Users\<you>\LiveCaption\`.

## macOS

> Not tested on a real Mac yet. Please report anything that doesn't work.

macOS can't record system audio on its own, so a free virtual audio device called **BlackHole** carries a copy of the meeting audio to the app. On Apple Silicon, speech-to-text runs on the GPU through whisper.cpp (Metal), which `setup.sh` installs. Intel Macs use the CPU (try `--model base` if captions fall behind).

1. **Install Homebrew** (if you don't have it) from <https://brew.sh>, then:

   ```bash
   brew install uv
   brew install --cask blackhole-2ch
   ```

2. **Route the meeting audio through BlackHole**, as described in the README: [macOS: route the meeting audio through BlackHole](../README.md#macos-route-the-meeting-audio-through-blackhole).
3. **Create the Python environment:**

   ```bash
   cd live-caption
   uv venv --python 3.12 .venv
   uv pip sync --torch-backend cpu requirements.lock   # Intel Mac: uv pip install -r requirements.txt
   chmod +x run.sh
   ```

4. **Allow microphone access.** On the first run, macOS asks whether your terminal app may use the microphone. Allow it. BlackHole counts as a microphone, so this is needed even with the default `--source system`. You can change it later in System Settings → Privacy & Security → Microphone.
