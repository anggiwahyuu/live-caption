#!/usr/bin/env bash
# Runs Live Caption from the virtualenv (Linux/macOS), with the CUDA libraries (cuBLAS/cuDNN) on Linux.
set -euo pipefail
cd "$(dirname "$0")"

# Call the venv's python directly instead of sourcing activate: activate stores an absolute
# path, so it silently picks the system python once the project folder is moved or renamed.
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
