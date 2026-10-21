#!/usr/bin/env bash
# Runs Live Caption with the CUDA libraries (cuBLAS/cuDNN) from the virtualenv.
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -d .venv ]]; then
  echo "Virtualenv not found. Follow the installation steps in README.md first" >&2
  exit 1
fi
source .venv/bin/activate

NV_LIBS=$(python - <<'PY'
import os
try:
    import nvidia.cublas.lib, nvidia.cudnn.lib
    # namespace package: __file__ is None, so use __path__
    print(list(nvidia.cublas.lib.__path__)[0] + ":" + list(nvidia.cudnn.lib.__path__)[0])
except Exception:
    print("")
PY
)
export LD_LIBRARY_PATH="${NV_LIBS}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

exec python live_caption.py "$@"
