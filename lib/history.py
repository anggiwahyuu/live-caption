"""Past meetings: the transcripts in the --out-dir folder, read back for the history window (history_window.py).

A transcript is the Markdown the window writes (transcript.Transcript.render): a "# ..." title, optionally a
"## Summary (...)" section, then "- **[10:00:05] Participant:** text" lines, each maybe followed by
"  - _translation_", and last, optionally, the "## Questions" asked about it (ask.Answer.to_markdown).
Transcripts from older versions say "Peserta" / "Saya" and have Indonesian titles.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

from .summary import _looks_english
from .transcript import QUESTIONS_HEADING

LINE = re.compile(r"^- \*\*\[(\d{1,2}:\d{2}:\d{2})\] ([^*\n]+?):\*\* ?(.*)$")
TRANSLATION = re.compile(r"^  - _(.*)_$")
SUMMARY_HEADING = re.compile(r"^## (?:Summary|Ringkasan)\b.*$")
TRANSCRIPT_HEADING = re.compile(r"^## (?:Transcript|Transkrip)\s*$")
LABELS = {"Peserta": "Participant", "Saya": "Me"}  # older transcripts
NAME_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})")
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
INDONESIAN_HINTS = {"yang", "dan", "itu", "ini", "tidak", "akan", "kita", "saya", "untuk", "dengan", "ada", "sudah",
                    "bisa", "kalau", "jadi", "juga", "nanti", "kamu", "mau", "ya"}


@dataclass
class Line:
    time: str
    label: str
    text: str
    translation: str | None = None


@dataclass
class Meeting:
    path: Path
    started: dt.datetime
    lines: list[Line] = field(default_factory=list)
    summary: str | None = None
    questions: str | None = None  # the "## Questions" section's Markdown

    @property
    def when(self) -> str:
        """ "Tue 21 Oct 2026, 16:01": month names fixed, so it doesn't depend on the system locale."""
        d = self.started
        return f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]} {d.year}, {d:%H:%M}"

    @property
    def duration_min(self) -> int:
        if len(self.lines) < 2:
            return 0
        first, last = (dt.datetime.strptime(x.time, "%H:%M:%S") for x in (self.lines[0], self.lines[-1]))
        return round(((last - first).total_seconds() % 86400) / 60)  # % 86400: past midnight

    def transcript(self) -> str:
        """The lines as the summarizer reads them: "[10:00:05] Participant: text"."""
        return "\n".join(f"[{x.time}] {x.label}: {x.text}" for x in self.lines)

    def export_lines(self) -> list[tuple[str, str, str, str | None]]:
        """The lines for export.transcript_to_text / transcript_to_docx."""
        return [(x.time, x.label, x.text, x.translation) for x in self.lines]


def started_from(path: Path) -> dt.datetime:
    """The start time is in the file name (meeting-2026-10-21_1601.md); else the file's time."""
    if m := NAME_DATE.search(path.name):
        try:
            return dt.datetime(*map(int, m.groups()))
        except ValueError:
            pass
    return dt.datetime.fromtimestamp(path.stat().st_mtime)


def parse(text: str) -> tuple[list[Line], str | None, str | None]:
    """A transcript file's text -> its lines, its summary and its questions (None if it has none)."""
    lines: list[Line] = []
    summary_rows: list[str] | None = None
    question_rows: list[str] | None = None
    in_summary = False
    for row in text.splitlines():
        if question_rows is not None:  # the last section: everything after its heading
            question_rows.append(row)
            continue
        if row.rstrip() == QUESTIONS_HEADING:
            question_rows = []
            continue
        if SUMMARY_HEADING.match(row):
            in_summary, summary_rows = True, []
            continue
        if TRANSCRIPT_HEADING.match(row):
            in_summary = False
            continue
        if in_summary:
            summary_rows.append(row)
        elif m := LINE.match(row):
            label = LABELS.get(m[2].strip(), m[2].strip())
            lines.append(Line(m[1], label, m[3].strip()))
        elif (m := TRANSLATION.match(row)) and lines:
            lines[-1].translation = m[1]
    summary = "\n".join(summary_rows).strip() if summary_rows is not None else None
    questions = "\n".join(question_rows).strip() if question_rows is not None else None
    return lines, summary or None, questions or None


def read_meeting(path: Path) -> Meeting:
    lines, summary, questions = parse(path.read_text(encoding="utf-8", errors="replace"))
    return Meeting(path, started_from(path), lines, summary, questions)


def list_meetings(folder: Path, exclude: Path | None = None) -> list[Meeting]:
    """Every transcript in the folder, newest first. Files that can't be read are left out."""
    meetings = []
    for path in Path(folder).expanduser().glob("*.md"):
        if exclude is not None and path.resolve() == Path(exclude).resolve():
            continue
        try:
            meeting = read_meeting(path)
        except OSError:
            continue
        if meeting.lines or meeting.summary:
            meetings.append(meeting)
    return sorted(meetings, key=lambda m: m.started, reverse=True)


def search(meetings: list[Meeting], query: str) -> list[tuple[Meeting, list[int]]]:
    """Meetings where every word of the query occurs (anywhere in the meeting, any case), with the indexes of the
    lines that contain at least one of the words. Translations, the summary and the questions count too."""
    words = [w.casefold() for w in query.split()]
    if not words:
        return [(m, []) for m in meetings]
    hits = []
    for m in meetings:
        texts = [f"{x.text} {x.translation or ''}".casefold() for x in m.lines]
        everything = " ".join([*texts, (m.summary or "").casefold(), (m.questions or "").casefold()])
        if all(w in everything for w in words):
            hits.append((m, [i for i, t in enumerate(texts) if any(w in t for w in words)]))
    return hits


def guess_language(meeting: Meeting) -> str | None:
    """ "en" or "id" for the summary of a past meeting (the live window knows it from Whisper), else None."""
    text = " ".join(x.text for x in meeting.lines)
    if _looks_english(text):
        return "en"
    words = re.findall(r"[a-z]+", text.lower())
    if words and sum(w in INDONESIAN_HINTS for w in words) / len(words) > 0.05:
        return "id"
    return None


def write_summary(path: Path, summary: str, note: str) -> None:
    """Puts the summary at the top of a transcript file (replacing an older one), as the live window does."""
    text = path.read_text(encoding="utf-8")
    rows = text.splitlines()
    title = rows[0] if rows and rows[0].startswith("# ") else f"# Meeting transcript — {path.stem}"
    body_start = next((i + 1 for i, r in enumerate(rows) if TRANSCRIPT_HEADING.match(r)), None)
    if body_start is None:  # no summary yet: the lines start right after the title
        body_start = 1 if rows and rows[0].startswith("# ") else 0
    body = "\n".join(rows[body_start:]).strip("\n")
    path.write_text(f"{title}\n\n## Summary ({note})\n\n{summary.strip()}\n\n## Transcript\n\n{body}\n",
                    encoding="utf-8")


def add_answer(path: Path, markdown: str) -> None:
    """Adds a question and its answer (ask.Answer.to_markdown) to the "## Questions" section at the end of a
    transcript file, starting that section if it's the first."""
    text = path.read_text(encoding="utf-8").rstrip("\n")
    if not any(row.rstrip() == QUESTIONS_HEADING for row in text.splitlines()):
        text += f"\n\n{QUESTIONS_HEADING}"
    path.write_text(f"{text}\n\n{markdown.strip()}\n", encoding="utf-8")
