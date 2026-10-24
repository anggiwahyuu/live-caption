#!/usr/bin/env bash
# One-command setup for Live Caption on Linux and macOS (Windows: setup.ps1).
# Safe to run again: steps that are already done are skipped. Run ./setup.sh --help for the options.
set -euo pipefail
cd "$(dirname "$0")"

RECREATE=0
SHORTCUT=1
PASS=()                # everything else goes to lib.model_setup (and from there to the app's own options)

usage() {
  cat <<'EOF'
Usage: ./setup.sh [options] [app options]

  --summary             Set up meeting summaries without asking
  --no-summary          Skip meeting summaries (saves a 2-5 GB download)
  --summary-only        Only (re)install the summary model
  --no-whisper          Don't download the Whisper models now (the app downloads them on its first start)
  -y, --yes             Answer every question with its default
  --no-shortcut         Don't add Live Caption to the application menu
  --recreate-venv       Delete .venv and build it again
  -h, --help            Show this help

App options decide which models are downloaded, as in ./run.sh and config.toml, e.g.:
  ./setup.sh --model medium                 Whisper model for final text (default: small)
  ./setup.sh --summary-model qwen2.5:3b     Ollama model for summaries (default: qwen2.5:7b)
EOF
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --recreate-venv) RECREATE=1 ;;
    --no-shortcut) SHORTCUT=0 ;;
    -h|--help) usage; exit 0 ;;
    *) PASS+=("$1") ;;
  esac
  shift
done

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
warn() { printf '\033[33m    ! %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31m    ✗ %s\033[0m\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

OS=$(uname -s)
SUDO=""
[[ $(id -u) -ne 0 ]] && SUDO=sudo
PY=.venv/bin/python

# ---------------------------------------------------------------------------- 1. system packages
step "System packages"
FAMILY=""
if [[ $OS == Linux ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  case " ${ID:-} ${ID_LIKE:-} " in
    *" arch "*|*" cachyos "*|*" manjaro "*|*" endeavouros "*) FAMILY=arch ;;
    *" fedora "*|*" rhel "*|*" nobara "*) FAMILY=fedora ;;
    *" debian "*|*" ubuntu "*) FAMILY=debian ;;
    *" suse "*|*" opensuse "*) FAMILY=suse ;;
  esac
  note "Linux: ${PRETTY_NAME:-unknown} (${FAMILY:-unknown family})"

  missing=()
  have parec || have pw-record || missing+=(audio)
  /usr/bin/python3 -c 'import tkinter' 2>/dev/null || missing+=(tk)
  have curl || missing+=(curl)
  have awk && have tar && have gzip || missing+=(awk/tar/gzip)  # uv's installer needs them; minimal images lack awk
  if [[ ${#missing[@]} -eq 0 ]]; then
    note "Already installed (audio recorder, Tkinter, curl, awk/tar/gzip)"
  else
    note "Missing: ${missing[*]}. Installing (asks for your password)…"
    case $FAMILY in
      debian) $SUDO apt-get update -q && $SUDO apt-get install -y pulseaudio-utils python3-tk curl gawk tar gzip ;;
      fedora) $SUDO dnf install -y pulseaudio-utils python3-tkinter curl gawk tar gzip ;;
      arch)   $SUDO pacman -S --needed --noconfirm libpulse python tk curl gawk tar gzip \
                || die "pacman failed. If it says 404, your package list is old: run 'sudo pacman -Syu' and try again." ;;
      suse)   $SUDO zypper install -y pulseaudio-utils python3-tk curl gawk tar gzip ;;
      *)      die "Unknown distribution. Install parec (pulseaudio-utils), Tkinter for python3, curl and awk, then run again." ;;
    esac
  fi

  # Without a working NVIDIA GPU, speech-to-text runs on whisper.cpp, which uses AMD / Intel GPUs through
  # Vulkan: the Vulkan loader plus Mesa's driver. Optional: without them whisper.cpp uses the CPU.
  if ! { have nvidia-smi && nvidia-smi >/dev/null 2>&1; } \
      && ! { ldconfig -p 2>/dev/null | grep -q 'libvulkan\.so\.1' && compgen -G '/usr/share/vulkan/icd.d/*.json' >/dev/null; }; then
    gpus=$( { have lspci && lspci; } 2>/dev/null | grep -iE 'vga|3d|display' || true)
    note "Installing the Vulkan driver for the GPU (AMD / Intel)…"
    case $FAMILY in
      debian) $SUDO apt-get update -q && $SUDO apt-get install -y libvulkan1 mesa-vulkan-drivers ;;
      fedora) $SUDO dnf install -y vulkan-loader mesa-vulkan-drivers ;;
      arch)   # Arch has one driver package per GPU maker
              drivers=(vulkan-icd-loader)
              grep -qiE 'amd|ati|radeon' <<< "$gpus" && drivers+=(vulkan-radeon)
              grep -qi 'intel' <<< "$gpus" && drivers+=(vulkan-intel)
              $SUDO pacman -S --needed --noconfirm "${drivers[@]}" ;;
      suse)   $SUDO zypper install -y libvulkan1 libvulkan_radeon libvulkan_intel ;;
    esac || warn "No Vulkan driver: whisper.cpp will use the CPU instead of the GPU."
  fi
elif [[ $OS == Darwin ]]; then
  have brew || die "Homebrew is needed: install it from https://brew.sh, then run ./setup.sh again."
  note "macOS $(sw_vers -productVersion)"
  if system_profiler SPAudioDataType 2>/dev/null | grep -qi blackhole; then
    note "BlackHole virtual audio device: installed"
  else
    note "Installing BlackHole (carries the meeting audio to the app; asks for your password)…"
    brew install --cask blackhole-2ch
    warn "Still to do by hand: create a Multi-Output Device in Audio MIDI Setup (README, section 3, step 2)."
  fi
else
  die "This script is for Linux and macOS. On Windows, run setup.ps1."
fi

# ---------------------------------------------------------------------------- 2. uv
step "uv (Python environment manager)"
if have uv; then
  note "Installed: $(uv --version)"
elif [[ $OS == Darwin ]]; then
  brew install uv
elif [[ $FAMILY == fedora || $FAMILY == arch ]]; then
  if [[ $FAMILY == fedora ]]; then $SUDO dnf install -y uv; else $SUDO pacman -S --needed --noconfirm uv; fi
else
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
have uv || die "uv was installed but isn't on PATH: open a new terminal and run ./setup.sh again."

# ---------------------------------------------------------------------------- 3. Python environment
step "Python environment (.venv)"
# Linux: the distribution's Python, because its Tk has font smoothing (uv's own Python on Linux doesn't).
PY_SPEC=3.12
if [[ $OS == Linux ]]; then
  if /usr/bin/python3 -c 'import sys, tkinter; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PY_SPEC=/usr/bin/python3
  else
    warn "The system Python is older than 3.10 or has no Tkinter: using Python 3.12 from uv (text will look rough)."
  fi
fi
if [[ $RECREATE == 1 && -d .venv ]]; then
  note "Removing the old .venv"
  rm -rf .venv
fi
if [[ -x $PY ]] && $PY -c 'import sys' 2>/dev/null; then
  note "Using the existing .venv ($($PY --version))"
  # Built on uv's own Python (its Tk has no font smoothing) although the system Python would do?
  if [[ $OS == Linux && $PY_SPEC == /usr/bin/python3 ]] && [[ $($PY -c 'import sys; print(sys.base_prefix)') == */uv/python/* ]]; then
    warn "This .venv uses uv's Python, whose Tk draws rough text. Run ./setup.sh --recreate-venv to rebuild it on the system Python."
  fi
else
  uv venv --python "$PY_SPEC" .venv
fi
note "Installing the Python libraries (first time: about 1-2 GB)…"
uv pip install --python "$PY" -r requirements.txt
chmod +x run.sh

# ---------------------------------------------------------------------------- 4. menu shortcut
step "Application menu shortcut"
if [[ $SHORTCUT == 1 ]]; then
  "$PY" -m lib.shortcut || warn "No shortcut; start the app with ./run.sh instead."
else
  note "Skipped (--no-shortcut)"
fi

# ---------------------------------------------------------------------------- 5. models
# Whisper and the summary model (Ollama is installed there when it's missing), with checks and retries.
exec "$PY" -m lib.model_setup ${PASS[@]+"${PASS[@]}"}
