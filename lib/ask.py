"""Questions about the meeting ("when is the deadline?"), answered from the transcript by the summary model,
together with the transcript lines that back the answer up. No Tkinter here: ask_window.py shows it."""

from __future__ import annotations

import json
import queue
import re
import threading
from dataclasses import dataclass, field

from .summary import Summarizer, locate_quote

MAX_SOURCES = 3

ASK_SYSTEM = """You answer questions about a meeting, using only its transcript (live speech-to-text).
Each line is "[HH:MM:SS] Speaker: text". "Me" is the person who recorded the meeting; "Participant" is everyone
else (several people, not told apart). The transcript may contain recognition errors and cut-off sentences.
Never invent facts, names, numbers or dates. If the transcript doesn't answer the question, say in one sentence
that it wasn't discussed or wasn't clear."""

ASK_TASK = """Question: {question}

Answer in the language of the question, in 1-3 sentences, using only the transcript above.
"quotes": up to 3 transcript lines that show the answer, each copied word for word (the text after "Speaker:"),
or none if the transcript doesn't answer the question.
Answer with JSON only: {{"answer": "...", "quotes": ["...", "..."]}}"""

ASK_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}, "quotes": {"type": "array", "items": {"type": "string"}}},
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
    # Models often copy the whole line, "[10:00:25] Participant: …", with a time that may be off: only the text counts.
    prefix = re.compile(r"^\s*(\[[\d:]+\]\s*)?(?:(?:{}):\s*)?".format("|".join(map(re.escape, {s for s, _ in pairs}))))
    found: dict[int, Source] = {}
    unsupported = 0
    for quote in quotes:
        i = locate_quote(prefix.sub("", quote, count=1), pairs)
        if i is None:
            unsupported += 1
        elif i not in found:
            time, speaker, text = lines[i]
            found[i] = Source(time, speaker, text, i)
    return [found[i] for i in sorted(found)][:MAX_SOURCES], unsupported


def ask(summarizer: Summarizer, transcript: str, question: str) -> Answer:
    # The transcript comes first and stays the same from one question to the next, so Ollama reuses what it
    # already read (its prompt cache) and a follow-up question costs little more than its answer.
    raw = summarizer.complete(ASK_SYSTEM, f"{transcript}\n\n{ASK_TASK.format(question=question)}", ASK_SCHEMA)
    text, quotes = parse_answer(raw)
    sources, unsupported = check_sources(quotes, transcript)
    return Answer(question, text, sources, unsupported)


class AskWorker(threading.Thread):
    """Answers one question in the background: puts ("answer", Answer) or ("answer_failed", (question, error))."""

    def __init__(self, summarizer: Summarizer, transcript: str, question: str, ui_q: queue.Queue):
        super().__init__(daemon=True)
        self.summarizer, self.transcript, self.question, self.ui_q = summarizer, transcript, question, ui_q

    def run(self) -> None:
        try:
            self.ui_q.put(("answer", ask(self.summarizer, self.transcript, self.question)))
        except Exception as e:
            self.ui_q.put(("answer_failed", (self.question, str(e))))
