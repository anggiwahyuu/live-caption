#!/usr/bin/env bash
# Pins requirements.txt and requirements-nvidia.txt to exact versions in requirements.lock and
# requirements-nvidia.lock, which setup.sh / setup.ps1 install. One file for every OS and Python 3.10+.
#
#   tools/lock-requirements.sh             after editing requirements*.txt: changes only what that needs
#   tools/lock-requirements.sh --upgrade   everything to the newest versions (.github/workflows/lock.yml, monthly)
#
# --torch-backend cpu: torch's CPU build, without the CUDA libraries the CUDA build would bring (setup installs
# it with the same option).
set -euo pipefail
cd "$(dirname "$0")/.."
export UV_CUSTOM_COMPILE_COMMAND="tools/lock-requirements.sh"
uv pip compile --quiet --universal --python-version 3.10 --torch-backend cpu "$@" requirements.txt -o requirements.lock
uv pip compile --quiet --universal --python-version 3.10 "$@" requirements-nvidia.txt -o requirements-nvidia.lock
git diff --stat -- requirements.lock requirements-nvidia.lock 2>/dev/null || true
