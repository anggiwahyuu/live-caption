"""Constants shared by every module: audio format, platform and source labels."""

from __future__ import annotations

import sys


SAMPLE_RATE = 16_000

FRAME_MS = 30

FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000

FRAME_BYTES = FRAME_SAMPLES * 2  # s16le mono

IS_LINUX = sys.platform.startswith("linux")

IS_WINDOWS = sys.platform == "win32"

IS_MAC = sys.platform == "darwin"


# source name -> on-screen label
SOURCES = {
    "system": "Participant",  # audio coming out of the speakers/headset
    "mic": "Me",              # default microphone
}
