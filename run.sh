#!/usr/bin/env bash
# Runs Live Caption from the virtualenv (Linux/macOS), with the CUDA libraries (cuBLAS/cuDNN) on Linux.
set -euo pipefail
cd "$(dirname "$0")"

# Call the venv's python directly instead of sourcing activate: activate stores an absolute
# path, so it silently picks the system python once the project folder is moved or renamed.
# Started from the application menu (lib/shortcut.py sets this): no terminal, so messages go to a log file.
if [[ -n ${LIVE_CAPTION_LOG:-} ]]; then
  mkdir -p "$(dirname "$LIVE_CAPTION_LOG")"
  if [[ -f $LIVE_CAPTION_LOG && $(wc -c < "$LIVE_CAPTION_LOG") -gt 1000000 ]]; then
    mv -f "$LIVE_CAPTION_LOG" "$LIVE_CAPTION_LOG.old"  # keep it small: only this one and the previous log
  fi
  exec >> "$LIVE_CAPTION_LOG" 2>&1
  echo "==== $(date '+%Y-%m-%d %H:%M:%S') Live Caption started"
fi

PY=.venv/bin/python
if [[ ! -x $PY ]]; then
  echo "Virtualenv not found. Follow the installation steps in README.md first" >&2
  exit 1
fi

NV_LIBS=$("$PY" - <<'PY'
try:
    import nvidia.cublas.lib, nvidia.cudnn.lib
    # namespace package: __file__ is None, so use __path__
    print(list(nvidia.cublas.lib.__path__)[0] + ":" + list(nvidia.cudnn.lib.__path__)[0])
except Exception:
    print("")  # macOS, or no NVIDIA libraries installed
PY
)
export LD_LIBRARY_PATH="${NV_LIBS}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

exec "$PY" live_caption.py "$@"
