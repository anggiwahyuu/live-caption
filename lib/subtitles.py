"""SRT/WebVTT subtitle export."""

from __future__ import annotations

import datetime as dt
import re


MIN_CUE_SECONDS = 1.0  # shorter subtitles flash by too fast to read


def subtitle_cues(records, origin: dt.datetime) -> list[tuple[float, float, str]]:
    """(start, end, text) in seconds from `origin` (when the recording started), for SRT/VTT export."""
    records = sorted(records, key=lambda r: r["ts"])
    show_label = len({r["label"] for r in records}) > 1  # "Me:" / "Participant:" only when both speak
    seconds = lambda t: (t - origin).total_seconds()  # noqa: E731
    cues = []
    for i, r in enumerate(records):
        start = seconds(r["ts"])
        end = max(seconds(r.get("end") or r["ts"]), start + MIN_CUE_SECONDS)
        nxt = next((n for n in records[i + 1:] if n["label"] == r["label"]), None)
        if nxt:  # don't overlap the same speaker's next sentence
            end = min(end, max(seconds(nxt["ts"]), start + 0.5))
        if end <= 0:
            continue  # said before the recording started
        text = f"{r['label']}: {r['text']}" if show_label else r["text"]
        if r.get("tr"):
            text += "\n" + r["tr"]
        cues.append((max(0.0, start), end, text))
    return cues


def _cue_time(t: float, sep: str) -> str:
    ms = round(t * 1000)
    return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d}{sep}{ms % 1000:03d}"


def to_srt(cues) -> str:
    blocks = [f"{i}\n{_cue_time(a, ',')} --> {_cue_time(b, ',')}\n{text.replace('-->', '->')}"
              for i, (a, b, text) in enumerate(cues, 1)]
    return "\n\n".join(blocks) + "\n" if blocks else ""


def to_vtt(cues) -> str:
    def escape(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    blocks = [f"{_cue_time(a, '.')} --> {_cue_time(b, '.')}\n{escape(text)}" for a, b, text in cues]
    return "WEBVTT\n\n" + "\n\n".join(blocks) + ("\n" if blocks else "")


def parse_clock(text: str, near: dt.datetime) -> dt.datetime | None:
    """'14:02' or '14:02:15' -> the datetime with that clock time closest to `near` (handles midnight)."""
    m = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?\s*", text or "")
    if not m or int(m[1]) > 23 or int(m[2]) > 59 or int(m[3] or 0) > 59:
        return None
    t = near.replace(hour=int(m[1]), minute=int(m[2]), second=int(m[3] or 0), microsecond=0)
    return min((t + dt.timedelta(days=d) for d in (-1, 0, 1)), key=lambda c: abs(c - near))
