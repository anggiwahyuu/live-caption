"""Meeting summaries with a local Ollama model or the Claude API."""

from __future__ import annotations

import difflib
import json
import queue
import re
import threading
import urllib.error
import urllib.request


SUMMARY_PROMPT = """You summarize meeting transcripts produced by live speech-to-text.

The transcript may contain recognition errors, cut-off sentences and repeated fragments. Infer what was meant,
but never invent facts, names, numbers or dates that are not supported by the transcript.
"Me" is the person who recorded the meeting; "Participant" is everyone else (speakers are not told apart).
When people are addressed or named in the transcript, use those names.

Be precise about what was actually agreed:
- A decision is only something the participants explicitly agreed on. Proposals, options, conditions ("if X, then Y")
  and questions are not decisions.
- Anything raised but not settled goes under open questions.
- An action item is a task someone took on or was asked to do. Keep the deadline in the words used ("Wednesday",
  "this afternoon"); don't turn it into another date.

Write the whole summary in {language}, in Markdown, with exactly these four sections and these headings:

### {h_summary}
3-6 sentences on what the meeting was about and what came out of it.

### {h_decisions}
Bullet list of the decisions. Write "-" if there were none.

### {h_actions}
Bullet list: who does what, and by when if mentioned. Write "-" if there were none.

### {h_questions}
Bullet list of questions or issues left unresolved. Write "-" if there were none.

Output only these sections, nothing before or after them."""

# Whisper language codes -> names for the prompt (small models follow "Indonesian" better than "id").
LANGUAGE_NAMES = {
    "id": "Indonesian", "en": "English", "ms": "Malay", "jw": "Javanese", "su": "Sundanese", "tl": "Tagalog",
    "th": "Thai", "vi": "Vietnamese", "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "hi": "Hindi",
    "ar": "Arabic", "tr": "Turkish", "ru": "Russian", "de": "German", "fr": "French", "es": "Spanish",
    "pt": "Portuguese", "it": "Italian", "nl": "Dutch", "pl": "Polish",
}


# Ready-made section headings: small models follow "translate the headings" poorly and keep them in English.
HEADINGS = {
    "id": ("Ringkasan", "Keputusan", "Tindak lanjut", "Pertanyaan terbuka"),
    "en": ("Summary", "Decisions", "Action items", "Open questions"),
}


def language_name(code: str | None) -> str:
    if not code:
        return "the main language of the transcript"
    return LANGUAGE_NAMES.get(code, f"the language with the code '{code}'")


# ---- local two-pass summary: extract quoted items -> check them in code -> write the overview
#
# Small local models invent details (dates, who does what) and promote proposals to decisions. So the local path
# first asks for structured items, each with a word-for-word quote, and keeps only what the transcript supports.

LOCAL_SYSTEM = """You help summarize a meeting transcript produced by live speech-to-text.
Each line is "[HH:MM:SS] Speaker: text". "Me" is the person who recorded the meeting; "Participant" is everyone
else (several people, not told apart). The transcript may contain recognition errors and cut-off sentences.
Never invent facts, names, numbers or dates."""

EXTRACT_TASK = """Go through the transcript above and list:
- decisions: only what the participants explicitly agreed on. Proposals, options, conditions ("if X, then Y")
  and questions are NOT decisions.
- action_items: tasks someone took on or was asked to do.
- open_questions: questions or issues raised but not settled.

Rules for every item:
- "quote": copied word for word from the transcript line that shows the item (the text after "Speaker:").
- "who": who will do the task: "Me" or "Participant".
- "named": the name of the person who will do the task if the quote or the lines around it say it
  (e.g. "Sari, please …" -> "Sari"), otherwise "".
- "task": what to do, without the deadline.
- "deadline": copied word for word from the transcript, in its own language, or "" if no deadline was said.
- "text" and "task" are written in {language}, like the transcript. Do not translate anything into English.
Leave a list empty if there is nothing for it. Answer with JSON only."""

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {"type": "array", "items": {
            "type": "object", "properties": {"text": {"type": "string"}, "quote": {"type": "string"}},
            "required": ["text", "quote"]}},
        "action_items": {"type": "array", "items": {
            "type": "object",
            "properties": {"who": {"type": "string"}, "named": {"type": "string"}, "task": {"type": "string"},
                           "deadline": {"type": "string"}, "quote": {"type": "string"}},
            "required": ["who", "named", "task", "deadline", "quote"]}},
        "open_questions": {"type": "array", "items": {
            "type": "object", "properties": {"text": {"type": "string"}, "quote": {"type": "string"}},
            "required": ["text", "quote"]}},
    },
    "required": ["decisions", "action_items", "open_questions"],
}

OVERVIEW_TASK = """Write 3-6 sentences in {language} on what the meeting above was about and what came out of it.
For decisions, tasks and open questions, rely only on these checked points:
{points}
Write plain sentences only: no heading, no list, no dates that aren't in the transcript."""

MONTHS = ("januari februari maret april mei juni juli agustus september oktober november desember "
          "january february march may june july august october december").split()
NEARBY_LINES = 3  # a deadline or name may be said a few lines before/after the quoted line


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


ENGLISH_HINTS = {"the", "and", "is", "are", "was", "will", "to", "of", "with", "that", "be", "by", "but"}


def _looks_english(text: str) -> bool:
    words = _words(text)
    return bool(words) and sum(w in ENGLISH_HINTS for w in words) / len(words) > 0.06


def parse_transcript(transcript: str) -> list[tuple[str, str]]:
    """'[10:00:05] Me: text' lines -> [(speaker, text), ...]."""
    return [(m[1], m[2]) for m in re.finditer(r"^\[[\d:]+\] ([^:\n]+): (.*)$", transcript, re.M)]


def locate_quote(quote: str, lines: list[tuple[str, str]]) -> int | None:
    """Index of the transcript line the quote comes from, or None if it isn't really in the transcript.
    A quote may run over up to 3 consecutive lines (Whisper splits sentences at short pauses)."""
    q = _words(quote)
    if len(q) < 2:
        return None
    for span in (1, 2, 3):  # prefer the shortest stretch that contains the quote
        best, best_score = None, 0.0
        for i in range(len(lines) - span + 1):
            t = _words(" ".join(text for _, text in lines[i:i + span]))
            found = sum(w in t for w in q) / len(q)  # share of the quote's words in this stretch
            score = found + difflib.SequenceMatcher(None, " ".join(q), " ".join(t)).ratio() / 10  # tie-break
            if found >= 0.8 and score > best_score:
                best, best_score = i, score
        if best is not None:
            return best
    return None


def _supported(phrase: str, context: set[str]) -> bool:
    return all(w in context for w in _words(phrase))


DAYS = "senin selasa rabu kamis jumat sabtu minggu monday tuesday wednesday thursday friday saturday sunday".split()


def _deadline_supported(deadline: str, context: set[str]) -> bool:
    """Most words must be near the quote (the model may say 'hingga' for 'sampai'), but days, months and numbers
    must be exactly there: those are what small models get wrong."""
    words = _words(deadline)
    if not words:
        return False
    if any((w in DAYS or w in MONTHS or w.isdigit()) and w not in context for w in words):
        return False
    return sum(w in context for w in words) / len(words) >= 0.5


def _drop_invented_dates(text: str, transcript_words: set[str]) -> str:
    """Remove '24 Februari' / 'Februari 2026' style dates whose month never came up in the meeting."""
    for month in MONTHS:
        if month in transcript_words:
            continue
        text = re.sub(rf",?\s*(\d{{1,2}}\s+)?{month}(\s+\d{{4}})?\b", "", text, flags=re.I)
    return text.strip()


def verify_items(data: dict, transcript: str) -> tuple[dict, int]:
    """Keep only items whose quote is in the transcript; fix names and deadlines it doesn't support.
    Returns (items, number of dropped items)."""
    lines = parse_transcript(transcript)
    all_words = set(_words(transcript))
    out = {"decisions": [], "action_items": [], "open_questions": []}
    dropped = 0
    seen = set()
    for kind in out:
        for item in data.get(kind) or []:
            if not isinstance(item, dict):
                continue
            i = locate_quote(str(item.get("quote", "")), lines)
            if i is None:
                dropped += 1
                continue
            nearby = set(_words(" ".join(t for _, t in lines[max(0, i - NEARBY_LINES):i + NEARBY_LINES + 1])))
            if kind == "action_items":
                who, task = str(item.get("who", "")).strip(), _drop_invented_dates(str(item.get("task", "")), all_words)
                named = str(item.get("named", "")).strip()
                deadline = str(item.get("deadline", "")).strip()
                if named and _supported(named, nearby):
                    who = named  # a name said right there beats the generic label
                elif who not in ("Me", "Participant") and not (who and _supported(who, nearby)):
                    who = lines[i][0]  # a name the transcript doesn't give here: use who said the quoted line
                if deadline and not _deadline_supported(deadline, nearby):
                    deadline = ""
                key = (kind, " ".join(_words(task)))
                clean = {"who": who, "task": task, "deadline": deadline}
            else:
                text = _drop_invented_dates(str(item.get("text", "")), all_words)
                key = (kind, " ".join(_words(text)))
                clean = {"text": text}
            if not key[1] or key in seen:
                continue
            seen.add(key)
            out[kind].append(clean)
    return out, dropped


def render_summary(overview: str, items: dict, headings: tuple[str, str, str, str]) -> str:
    def bullets(entries: list[str]) -> list[str]:
        return [f"- {e[:1].upper()}{e[1:]}" for e in entries] or ["-"]

    actions = []
    for a in items["action_items"]:
        line = f"{a['who']}: {a['task']}"
        if a["deadline"] and not _supported(a["deadline"], set(_words(a["task"]))):
            line += f" ({a['deadline']})"
        actions.append(line)
    out = [f"### {headings[0]}", overview.strip(), "",
           f"### {headings[1]}", *bullets([d["text"] for d in items["decisions"]]), "",
           f"### {headings[2]}", *bullets(actions), "",
           f"### {headings[3]}", *bullets([q["text"] for q in items["open_questions"]])]
    return "\n".join(out)


class Summarizer:
    """Summarizes the transcript with a local Ollama model (default) or the Claude API (opt-in)."""

    DEFAULT_MODELS = {"ollama": "qwen2.5:7b", "claude": "claude-opus-5-5"}

    def __init__(self, provider: str, model: str | None, ollama_url: str):
        self.provider = provider
        self.model = model or self.DEFAULT_MODELS[provider]
        self.ollama_url = ollama_url.rstrip("/")

    def describe(self) -> str:
        return f"{self.model} via {'Ollama (local)' if self.provider == 'ollama' else 'Claude API (online)'}"

    def summarize(self, transcript: str, language: str | None = None) -> str:
        """language: Whisper code ("id", "en", …) to write the summary in; None = the transcript's main language."""
        name = language_name(language)
        if language in HEADINGS:
            headings = HEADINGS[language]
        else:  # other languages: English headings the model is asked to translate
            headings = tuple(f"{h} (translated into {name})" for h in HEADINGS["en"])
        if self.provider == "ollama" and parse_transcript(transcript):
            try:
                return self._ollama_two_pass(transcript, name, headings)
            except ValueError:
                pass  # the model didn't return usable JSON: fall back to the one-pass summary below
        system = SUMMARY_PROMPT.format(language=name, h_summary=headings[0], h_decisions=headings[1],
                                       h_actions=headings[2], h_questions=headings[3])
        # Repeated after the transcript: small local models otherwise drift into English.
        user = f"{transcript}\n\n(Write the summary in {name}.)"
        if self.provider == "claude":
            return self._claude(system, user)
        return self._ollama(system, user, self._num_ctx(user))

    @staticmethod
    def _num_ctx(text: str) -> int:
        # Ollama's default context window (2–4k tokens) silently cuts long transcripts: size it to the input.
        return min(32768, max(4096, len(text) // 3 + 2048))

    def _ollama_two_pass(self, transcript: str, language: str, headings) -> str:
        # Both requests start with the same system prompt + transcript, so Ollama reuses what it already read
        # (its prompt cache) and the second pass costs little more than its own short answer.
        num_ctx = self._num_ctx(transcript + EXTRACT_TASK) + 1024
        raw = self._ollama(LOCAL_SYSTEM, f"{transcript}\n\n{EXTRACT_TASK.format(language=language)}", num_ctx,
                           temperature=0.0, fmt=EXTRACT_SCHEMA)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"no JSON from the model: {e}")
        if not isinstance(data, dict):
            raise ValueError("unexpected JSON from the model")
        items, _dropped = verify_items(data, transcript)

        points = [f"- decision: {d['text']}" for d in items["decisions"]]
        points += [f"- task: {a['who']}: {a['task']}" + (f" ({a['deadline']})" if a["deadline"] else "")
                   for a in items["action_items"]]
        points += [f"- open question: {q['text']}" for q in items["open_questions"]]
        task = OVERVIEW_TASK.format(language=language, points="\n".join(points) or "- (none)")
        overview = self._ollama(LOCAL_SYSTEM, f"{transcript}\n\n{task}", num_ctx, temperature=0.0)
        if language != "English" and _looks_english(overview):  # small models sometimes drift mid-paragraph
            overview = self._ollama(LOCAL_SYSTEM, f"{transcript}\n\n{task}\nEvery sentence must be in {language}.",
                                    num_ctx, temperature=0.0)
        overview = re.sub(r"^#+.*\n?", "", overview).strip()  # in case it added a heading anyway
        all_words = set(_words(transcript))
        sentences = re.split(r"(?<=[.!?])\s+", overview)
        overview = " ".join(x for x in sentences if _drop_invented_dates(x, all_words) == x.strip()) or overview
        return render_summary(overview, items, headings)

    def _ollama(self, system: str, user: str, num_ctx: int, temperature: float = 0.2, fmt=None) -> str:
        body = {"model": self.model, "stream": False,
                "options": {"num_ctx": num_ctx, "temperature": temperature},  # low: stick to the transcript
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}]}
        if fmt is not None:
            body["format"] = fmt  # JSON schema: Ollama constrains the answer to it
        req = urllib.request.Request(f"{self.ollama_url}/api/chat", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = json.load(resp)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            if e.code == 404:
                raise RuntimeError(f"Ollama model '{self.model}' not found. Download it with: ollama pull {self.model}")
            raise RuntimeError(f"Ollama error {e.code}: {detail}")
        except (urllib.error.URLError, OSError) as e:
            raise RuntimeError(f"Can't reach Ollama at {self.ollama_url} ({e}). "
                               f"Install it from ollama.com and make sure it is running.")
        return data["message"]["content"].strip()

    def _claude(self, system: str, user: str) -> str:
        try:
            import anthropic
        except ImportError:
            raise RuntimeError("The 'anthropic' package is missing. Install it with: uv pip install anthropic")
        client = anthropic.Anthropic()  # ANTHROPIC_API_KEY or an `ant auth login` profile
        no_credentials = "Claude API: no valid credentials. Set ANTHROPIC_API_KEY or run `ant auth login`."
        try:
            with client.beta.messages.stream(
                model=self.model,
                max_tokens=16000,
                system=system,
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"],
                extra_body={"fallbacks": "default"},  # if the model declines, the API retries on a fallback model
                messages=[{"role": "user", "content": user}],
            ) as stream:
                message = stream.get_final_message()
        except anthropic.AuthenticationError:
            raise RuntimeError(no_credentials)
        except TypeError as e:  # raised by the SDK when no credentials are configured at all
            if "authentication" not in str(e).lower():
                raise
            raise RuntimeError(no_credentials)
        except anthropic.RateLimitError:
            raise RuntimeError("Claude API: rate limited. Try again in a minute.")
        except anthropic.APIStatusError as e:
            raise RuntimeError(f"Claude API error {e.status_code}: {e.message}")
        except anthropic.APIConnectionError:
            raise RuntimeError("Claude API: network error. Check your internet connection.")
        if message.stop_reason == "refusal":
            raise RuntimeError("Claude declined to summarize this transcript.")
        text = "".join(b.text for b in message.content if b.type == "text").strip()
        if message.stop_reason == "max_tokens":
            text += "\n\n_(summary cut off)_"
        return text


class SummaryWorker(threading.Thread):
    """Runs one summary in the background so the window stays responsive."""

    def __init__(self, summarizer: Summarizer, transcript: str, ui_q: queue.Queue, language: str | None = None,
                 kind: str = "summary"):
        """kind: the message sent to the window, "<kind>" with the text or "<kind>_failed" with the error
        (the history window uses its own, so a past meeting's summary isn't taken for the live one)."""
        super().__init__(daemon=True)
        self.summarizer, self.transcript, self.ui_q, self.language = summarizer, transcript, ui_q, language
        self.kind = kind

    def run(self) -> None:
        try:
            self.ui_q.put((self.kind, self.summarizer.summarize(self.transcript, self.language)))
        except Exception as e:
            self.ui_q.put((f"{self.kind}_failed", str(e)))
