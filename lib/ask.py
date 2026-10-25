"""Questions about a meeting ("when is the deadline?"), answered from the transcript by the summary model,
together with the transcript lines that back the answer up, and saved in the transcript file's "## Questions"
section. No Tkinter here: ask_window.py shows it."""

from __future__ import annotations

import datetime as dt
import json
import queue
import re
import threading
from dataclasses import dataclass, field

from .summary import QUOTE, Summarizer, locate_quote, recorder

MAX_SOURCES = 3

ASK_SYSTEM = """You answer questions about a meeting, using only its transcript (live speech-to-text).
Each line is "[HH:MM:SS] Speaker: text". {me} is the person who recorded the meeting; "Participant" is everyone
else (several people, not told apart). The transcript may contain recognition errors and cut-off sentences.
Never invent facts, names, numbers or dates.
People in the meeting often use other words than the question: a "requirement" may be something someone asked
for, a "deadline" may be "by Thursday". Look for lines that answer the question in meaning, not only in wording.
Only when no line does, say in one sentence that it wasn't discussed or wasn't clear.
Answer in your own words. When the answer is a person, name them: by the name used in the meeting, else by their
speaker label; "I" in a line is that line's speaker."""

ASK_TASK = """Question: {question}

Answer in the language of the question, in 1-3 sentences, using only the transcript above.
"quotes": up to 3 transcript lines that show the answer, each copied word for word (the text after "Speaker:",
without quotation marks around it),
or none if the transcript doesn't answer the question.
Answer with JSON only: {{"answer": "...", "quotes": ["...", "..."]}}"""

ASK_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string", "maxLength": 600},  # capped like summary.EXTRACT_SCHEMA
                   "quotes": {"type": "array", "maxItems": MAX_SOURCES, "items": QUOTE}},
    "required": ["answer", "quotes"],
}

LINE = re.compile(r"^\[([\d:]+)\] ([^:\n]+): (.*)$", re.M)


@dataclass
class Source:
    """A transcript line that backs an answer up."""
    time: str
    speaker: str
    text: str
    index: int  # its position in the transcript, i.e. its line id in the live meeting


@dataclass
class Answer:
    question: str
    text: str
    sources: list[Source] = field(default_factory=list)
    unsupported: int = 0  # quotes the model gave that aren't in the transcript
    asked: dt.datetime = field(default_factory=dt.datetime.now)

    @property
    def warning(self) -> str | None:
        """What to tell the reader when no transcript line backs the answer up."""
        if self.sources:
            return None
        if self.unsupported:
            return "The lines it quoted aren't in the transcript: check before relying on this answer"
        return "No transcript line given for this answer"

    def to_markdown(self) -> str:
        """The answer as it is saved in the transcript file, under "## Questions"."""
        rows = [f"### {self.question}", "", f"_Asked on {self.asked:%Y-%m-%d %H:%M}_", "", self.text, ""]
        rows += [f"- [{s.time}] {s.speaker}: {s.text}" for s in self.sources]
        if self.warning:
            rows.append(f"_{self.warning}._")
        return "\n".join(rows).strip()


def parse_answer(raw: str) -> tuple[str, list[str]]:
    """The model's reply -> (answer, quotes). Tolerates text around the JSON (Claude gets no schema) and
    a plain-text reply without any JSON."""
    match = re.search(r"\{.*\}", raw, re.S)
    if match:
        try:
            data = json.loads(match[0])
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("answer"), str):
            quotes = [q for q in data.get("quotes") or [] if isinstance(q, str) and q.strip()]
            return data["answer"].strip(), quotes
    return raw.strip(), []


def check_sources(quotes: list[str], transcript: str) -> tuple[list[Source], int]:
    """Finds each quote in the transcript -> (the lines, in transcript order; how many quotes weren't there)."""
    lines = LINE.findall(transcript)
    pairs = [(speaker, text) for _, speaker, text in lines]
    found: dict[int, Source] = {}
    unsupported = 0
    for quote in quotes:
        i = locate_quote(quote, pairs)  # a copied "[10:00:25] Participant:" in front is left out
        if i is None:
            unsupported += 1
        elif i not in found:
            time, speaker, text = lines[i]
            found[i] = Source(time, speaker, text, i)
    return [found[i] for i in sorted(found)][:MAX_SOURCES], unsupported


def ask(summarizer: Summarizer, transcript: str, question: str) -> Answer:
    # The transcript comes first and stays the same from one question to the next, so Ollama reuses what it
    # already read (its prompt cache) and a follow-up question costs little more than its answer.
    system = ASK_SYSTEM.format(me=recorder(summarizer.me))
    raw = summarizer.complete(system, f"{transcript}\n\n{ASK_TASK.format(question=question)}", ASK_SCHEMA)
    text, quotes = parse_answer(raw)
    if not re.search(r"\w", text):  # qwen2.5:3b often answers "..." or nothing at all
        raise RuntimeError(f"the model gave no answer ({text or 'empty'!r}). Ask again, or use a larger "
                           f"--summary-model")
    sources, unsupported = check_sources(quotes, transcript)
    return Answer(question, text, sources, unsupported)


class AskWorker(threading.Thread):
    """Answers one question in the background: puts ("answer", meeting, Answer) or
    ("answer_failed", meeting, (question, error)). meeting: None for the live one, else the past meeting's file."""

    def __init__(self, summarizer: Summarizer, transcript: str, question: str, ui_q: queue.Queue, meeting=None):
        super().__init__(daemon=True)
        self.summarizer, self.transcript, self.question, self.ui_q = summarizer, transcript, question, ui_q
        self.meeting = meeting

    def run(self) -> None:
        try:
            self.ui_q.put(("answer", self.meeting, ask(self.summarizer, self.transcript, self.question)))
        except Exception as e:
            self.ui_q.put(("answer_failed", self.meeting, (self.question, str(e))))
