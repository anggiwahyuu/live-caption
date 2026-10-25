"""The meeting being transcribed: its final lines, their translations, its summary, and the Markdown file
they are saved to after every change. No Tkinter here: the window (ui.py) shows what this holds."""

from __future__ import annotations

import collections
import datetime as dt
from pathlib import Path

from .subtitles import subtitle_cues, to_srt, to_vtt

QUESTIONS_HEADING = "## Questions"  # read back by history.py
# Month names are fixed so the file doesn't depend on the system locale.
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


class Transcript:
    def __init__(self, out_dir: Path, started: dt.datetime | None = None):
        self.started = started or dt.datetime.now()
        out_dir = Path(out_dir).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        self.path = out_dir / f"meeting-{self.started:%Y-%m-%d_%H%M}.md"
        # line id -> {"ts", "end", "label", "text", "lang", "tr"} (what subtitles.subtitle_cues reads)
        self.records: dict[int, dict] = {}
        self.summary: tuple[str, str] | None = None  # (note for its heading, Markdown) of the latest summary
        self.answers: list[str] = []  # questions asked about the meeting, as Markdown (ask.Answer.to_markdown)

    @property
    def next_id(self) -> int:
        """The id the next line gets, i.e. how many lines there are."""
        return len(self.records)

    def add(self, ts: dt.datetime, label: str, text: str, lang: str, end: dt.datetime | None = None) -> int:
        line_id = self.next_id
        self.records[line_id] = {"ts": ts, "end": end or ts, "label": label, "text": text, "lang": lang, "tr": None}
        return line_id

    def set_translation(self, line_id: int, translation: str) -> bool:
        rec = self.records.get(line_id)
        if rec is None:
            return False
        rec["tr"] = translation
        return True

    def set_summary(self, text: str, note: str) -> None:
        self.summary = (note, text)

    def add_answer(self, markdown: str) -> None:
        self.answers.append(markdown)

    # ---- for the summarizer
    def for_summary(self) -> str:
        """The lines as the summarizer reads them: "[10:00:05] Participant: text"."""
        return "\n".join(f"[{r['ts']:%H:%M:%S}] {r['label']}: {r['text']}" for r in self.records.values())

    def export_lines(self) -> list[tuple[str, str, str, str | None]]:
        """The lines for export.transcript_to_text / transcript_to_docx."""
        return [(f"{r['ts']:%H:%M:%S}", r["label"], r["text"], r["tr"]) for r in self.records.values()]

    def main_language(self) -> str | None:
        """The language Whisper heard most, weighted by how much was said in each."""
        spoken = collections.Counter()
        for r in self.records.values():
            spoken[r["lang"]] += len(r["text"])
        return spoken.most_common(1)[0][0] if spoken else None

    # ---- files
    def render(self) -> str:
        d = self.started
        lines = [f"# Meeting transcript — {MONTHS[d.month - 1]} {d.day}, {d.year}, {d:%H:%M}", ""]
        if self.summary:
            note, text = self.summary
            lines += [f"## Summary ({note})", "", text, "", "## Transcript", ""]
        for rec in self.records.values():
            lines.append(f"- **[{rec['ts']:%H:%M:%S}] {rec['label']}:** {rec['text']}")
            if rec["tr"]:
                lines.append(f"  - _{rec['tr']}_")
        if self.answers:  # last, so a summary added from the history window (history.write_summary) keeps them
            lines += ["", QUESTIONS_HEADING, ""]
            lines += "\n\n".join(self.answers).splitlines()
        return "\n".join(lines) + "\n"

    def save(self) -> None:
        """Raises OSError when the file can't be written."""
        self.path.write_text(self.render(), encoding="utf-8")

    def subtitles(self, origin: dt.datetime, fmt: str) -> tuple[str, int]:
        """SubRip ("srt") or WebVTT ("vtt") with times counted from origin -> (content, number of cues)."""
        cues = subtitle_cues(self.records.values(), origin)
        return (to_srt(cues) if fmt == "srt" else to_vtt(cues)), len(cues)
