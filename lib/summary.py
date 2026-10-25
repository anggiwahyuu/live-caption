"""Meeting summaries with a local Ollama model or the Claude API."""

from __future__ import annotations

import collections
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
{me} is the person who recorded the meeting; "Participant" is everyone else (speakers are not told apart).
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


OLLAMA_TIMEOUT = 900  # seconds Ollama may stay silent: reading a long meeting on a CPU
PLAIN_QUOTES = "Write each quote once, as plain text: no quotation marks, no backslashes."


class RepeatLimit(RuntimeError):
    """Ollama stopped the answer because the model kept repeating itself."""


def recorder(me: str) -> str:
    """How the prompts name the person who recorded the meeting: their label, which is "Me" unless --my-name
    gives their name (older transcripts still say "Me")."""
    return '"Me"' if me == "Me" else f'"{me}" (in older transcripts "Me")'


def language_name(code: str | None) -> str:
    if not code:
        return "the main language of the transcript"
    return LANGUAGE_NAMES.get(code, f"the language with the code '{code}'")


# ---- local two-pass summary: extract quoted items -> check them in code -> write the overview
#
# Small local models invent details (dates, who does what) and promote proposals to decisions. So the local path
# first asks for structured items, each with a word-for-word quote, and keeps only what the transcript supports.

LOCAL_SYSTEM = """You help summarize a meeting transcript produced by live speech-to-text.
Each line is "[HH:MM:SS] Speaker: text". {me} is the person who recorded the meeting; "Participant" is everyone
else (several people, not told apart). The transcript may contain recognition errors and cut-off sentences.
Never invent facts, names, numbers or dates."""

EXTRACT_TASK = """Go through the transcript above and list:
- decisions: only what the participants explicitly agreed on. Proposals, options, conditions ("if X, then Y")
  and questions are NOT decisions.
- action_items: tasks someone took on or was asked to do.
- open_questions: questions or issues raised but not settled.

Rules for every item:
- "quote": copied word for word from the transcript line that shows the item (the text after "Speaker:", without
  quotation marks around it).
- "who": who will do the task: "{me_name}" or "Participant".
- "named": the name of the person who will do the task if the quote or the lines around it say it
  (e.g. "Sari, please …" -> "Sari"; "Dimas, can you …?" answered with "Yes, I'll …" -> "Dimas"), otherwise "".
- "text": the decision or question in a short sentence that is clear on its own, not a copy of the quote.
- "task": what to do, without the deadline.
- "deadline": copied word for word from the transcript, in its own language, or "" if no deadline was said.
- "text" and "task" are written in {language}, like the transcript. Do not translate anything into English.
Leave a list empty if there is nothing for it. Answer with JSON only."""

# Lengths and counts are capped: on a long meeting full of "Oke oke." qwen2.5 can repeat it inside a quote until the
# context is full, hours on a CPU (Ollama's repeat check doesn't catch a loop that slow). Ollama enforces the caps.
QUOTE = {"type": "string", "maxLength": 400}  # up to 3 transcript lines
TEXT = {"type": "string", "maxLength": 300}
NAME = {"type": "string", "maxLength": 60}
MAX_ITEMS = 15  # per list


def _items(properties: dict) -> dict:
    return {"type": "array", "maxItems": MAX_ITEMS,
            "items": {"type": "object", "properties": properties, "required": list(properties)}}


EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": _items({"text": TEXT, "quote": QUOTE}),
        "action_items": _items({"who": NAME, "named": NAME, "task": TEXT,
                                "deadline": {"type": "string", "maxLength": 80}, "quote": QUOTE}),
        "open_questions": _items({"text": TEXT, "quote": QUOTE}),
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


def _english_sentence(sentence: str) -> bool:
    """Stricter than _looks_english, for one sentence: a sentence in another language may hold an English phrase
    ("Rapat membahas the new onboarding flow")."""
    words = _words(sentence)
    hints = sum(w in ENGLISH_HINTS for w in words)
    return hints >= 2 and hints / len(words) >= 0.15


def _looks_english(text: str) -> bool:
    words = _words(text)
    return bool(words) and sum(w in ENGLISH_HINTS for w in words) / len(words) > 0.06


def parse_transcript(transcript: str) -> list[tuple[str, str]]:
    """'[10:00:05] Me: text' lines -> [(speaker, text), ...]."""
    return [(m[1], m[2]) for m in re.finditer(r"^\[[\d:]+\] ([^:\n]+): (.*)$", transcript, re.M)]


# A line that starts by asking someone by name: "Budi tolong …", "Sari, please …", "Dimas, can you …"
ADDRESSED = re.compile(r"^([A-Z][\w'-]+),?\s+(?i:tolong|mohon|bisa|kamu|nanti|please|can you|could you|would you)\b")
# "Siap, mulai minggu depan saya bikin." / "Yeah, I'll fix them": taking on what the line before asked
# (not "I'll …" or "Saya …" alone: that can be someone else taking on something else)
ACCEPTS = re.compile(r"^(?i:siap|oke|ok|okay|baik|iya|ya|bisa|yeah|yes|sure|will do)\b")
NOT_NAMES = {"oke", "ok", "okay", "kita", "kami", "saya", "aku", "kamu", "ya", "jadi", "nah", "terus", "nanti", "itu",
             "ini", "yang", "dan", "so", "yeah", "yes", "we", "you", "i", "and", "then", "now", "please", "can", "also",
             "juga", "mungkin", "maybe", "tolong", "mohon", "bisa"}


# qwen2.5 lists proposals, doubts and requests as decisions, though told not to. The line it quotes shows it.
TENTATIVE = re.compile(r"\?|\b(?:maybe|might|could|perhaps|not sure|i think|mungkin|belum|apakah|gimana|bagaimana|"
                       r"kalau|usul\w*)\b", re.IGNORECASE)
AGREED = re.compile(r"\b(?:agreed|agree|decided|deal|setuju|sepakat|disepakati|diputuskan)\b", re.IGNORECASE)
NOT_AGREED = re.compile(r"\b(?:no decision|not decided|disagree|don't agree|belum ada keputusan|belum diputuskan|"
                        r"kurang setuju|tidak setuju|gak setuju|nggak setuju)\b", re.IGNORECASE)


def _addressed(text: str) -> str | None:
    """The name a line asks something of: "Budi tolong …" -> "Budi"."""
    m = ADDRESSED.match(text)
    return m[1] if m and m[1].casefold() not in NOT_NAMES else None


def _agreed(text: str) -> bool:
    return bool(AGREED.search(text)) and not NOT_AGREED.search(text)


def _settled(lines: list[tuple[str, str]], i: int) -> bool:
    """Whether line i says something settled: not a proposal or a doubt, or agreed on in it or the 3 lines after."""
    return not TENTATIVE.search(lines[i][1]) or any(_agreed(text) for _, text in lines[i:i + 4])


def strip_line_prefix(text: str, lines: list[tuple[str, str]]) -> str:
    """Models often copy the whole line, "[10:00:25] Participant: …", with a time that may be off: only the text
    counts."""
    speakers = "|".join(map(re.escape, {speaker for speaker, _ in lines}))
    return re.sub(rf"^\s*(\[[\d:]+\]\s*)?(?:(?:{speakers}):\s*)?", "", text, count=1) if speakers else text


def locate_quote(quote: str, lines: list[tuple[str, str]]) -> int | None:
    """Index of the transcript line the quote comes from, or None if it isn't really in the transcript.
    A quote may run over up to 3 consecutive lines (Whisper splits sentences at short pauses)."""
    q = _words(strip_line_prefix(quote, lines))
    if len(q) < 2:
        return None
    for span in (1, 2, 3):  # prefer the shortest stretch that contains the quote
        best, best_score = None, 0.0
        for i in range(len(lines) - span + 1):
            t = _words(" ".join(text for _, text in lines[i:i + span]))
            # share of the quote's words in this stretch, each at most as often as it is there: a model looping on
            # "Oke oke. Oke oke. …" doesn't match the one "Oke oke." it started from
            have = collections.Counter(t)
            found = sum(min(n, have[w]) for w, n in collections.Counter(q).items()) / len(q)
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


def verify_items(data: dict, transcript: str, me: str = "Me") -> tuple[dict, int]:
    """Keep only items whose quote is in the transcript; fix names and deadlines it doesn't support.
    Returns (items, number of dropped items)."""
    lines = parse_transcript(transcript)
    all_words = set(_words(transcript))
    out = {"decisions": [], "action_items": [], "open_questions": []}
    dropped = 0
    seen = set()
    taken = set()  # (list, line): a decision moved to another list doesn't repeat an item from the same line
    for kind in ("action_items", "open_questions", "decisions"):  # decisions last: some move to the other lists
        for item in data.get(kind) or []:
            if not isinstance(item, dict):
                continue
            i = locate_quote(str(item.get("quote", "")), lines)
            if i is None:
                dropped += 1
                continue
            nearby = set(_words(" ".join(t for _, t in lines[max(0, i - NEARBY_LINES):i + NEARBY_LINES + 1])))
            addressed = _addressed(lines[i][1])
            if not addressed and i > 0 and lines[i][0] != me and ACCEPTS.match(lines[i][1]):
                addressed = _addressed(lines[i - 1][1])  # the answer to "Sari nanti tolong …"
            target = kind
            if kind == "action_items":
                who, task = str(item.get("who", "")).strip(), _drop_invented_dates(str(item.get("task", "")), all_words)
                named = str(item.get("named", "")).strip()
                deadline = strip_line_prefix(str(item.get("deadline", "")), lines).strip()
                if named and _supported(named, nearby):
                    who = named  # a name said right there beats the generic label
                elif who not in ("Me", me, "Participant") and not (who and _supported(who, nearby)):
                    # a name the transcript doesn't give here: who was asked, else who spoke
                    who = addressed or lines[i][0]
                elif who == "Participant" and addressed:
                    who = addressed  # "Budi tolong urus upgrade-nya": the model left the name out
                if deadline and not _deadline_supported(deadline, nearby):
                    deadline = ""
                line_words = set(_words(lines[i][1]))
                if deadline and sum(w in line_words for w in _words(deadline)) >= 0.8 * len(line_words):
                    deadline = ""  # the whole quoted line copied again, not a deadline
                key = (kind, " ".join(_words(task)))
                clean = {"who": who, "task": task, "deadline": deadline}
            else:
                text = _drop_invented_dates(str(item.get("text", "")), all_words)
                clean = {"text": text}
                if kind == "decisions" and addressed:  # "Rina, kamu yang susun draftnya ya": a task for Rina
                    target, clean = "action_items", {"who": addressed, "task": text, "deadline": ""}
                elif kind == "decisions" and not _settled(lines, i):
                    target = "open_questions"  # "We could also do a beta, maybe." and nobody agreed
                key = (target, " ".join(_words(text)))
            if not key[1] or key in seen or (target != kind and (target, i) in taken):
                continue
            seen.add(key)
            taken.add((target, i))
            out[target].append(clean)
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

    def __init__(self, provider: str, model: str | None, ollama_url: str, me: str = "Me"):
        self.provider = provider
        self.me = me  # the label of the person who recorded the meeting (--my-name)
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
                                       h_actions=headings[2], h_questions=headings[3], me=recorder(self.me))
        # Repeated after the transcript: small local models otherwise drift into English.
        user = f"{transcript}\n\n(Write the summary in {name}.)"
        if self.provider == "claude":
            return self._claude(system, user)
        return self._ollama(system, user, self._num_ctx(user))

    def complete(self, system: str, user: str, schema: dict | None = None) -> str:
        """One answer from the model (ask.py). schema: a JSON schema Ollama holds the answer to; Claude only
        gets the instructions in the prompt."""
        if self.provider == "claude":
            return self._claude(system, user)
        # In steps of 4096: a new num_ctx makes Ollama reload the model, so questions of different lengths
        # about the same transcript keep the same one.
        num_ctx = min(32768, -(-self._num_ctx(user) // 4096) * 4096)
        return self._ollama(system, user, num_ctx, temperature=0.0, fmt=schema)

    @staticmethod
    def _num_ctx(text: str) -> int:
        # Ollama's default context window (2–4k tokens) silently cuts long transcripts: size it to the input.
        return min(32768, max(4096, len(text) // 3 + 2048))

    def _ollama_two_pass(self, transcript: str, language: str, headings) -> str:
        # Both requests start with the same system prompt + transcript, so Ollama reuses what it already read
        # (its prompt cache) and the second pass costs little more than its own short answer.
        num_ctx = self._num_ctx(transcript + EXTRACT_TASK) + 1024
        system = LOCAL_SYSTEM.format(me=recorder(self.me))
        raw = self._ollama(system, f"{transcript}\n\n{EXTRACT_TASK.format(language=language, me_name=self.me)}",
                           num_ctx, temperature=0.0, fmt=EXTRACT_SCHEMA)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"no JSON from the model: {e}")
        if not isinstance(data, dict):
            raise ValueError("unexpected JSON from the model")
        items, _dropped = verify_items(data, transcript, self.me)

        points = [f"- decision: {d['text']}" for d in items["decisions"]]
        points += [f"- task: {a['who']}: {a['task']}" + (f" ({a['deadline']})" if a["deadline"] else "")
                   for a in items["action_items"]]
        points += [f"- open question: {q['text']}" for q in items["open_questions"]]
        task = OVERVIEW_TASK.format(language=language, points="\n".join(points) or "- (none)")
        overview = self._ollama(system, f"{transcript}\n\n{task}", num_ctx, temperature=0.0)
        if language != "English" and _looks_english(overview):  # small models sometimes drift mid-paragraph
            overview = self._ollama(system, f"{transcript}\n\n{task}\nEvery sentence must be in {language}.",
                                    num_ctx, temperature=0.0)
        overview = re.sub(r"^#+.*\n?", "", overview).strip()  # in case it added a heading anyway
        all_words = set(_words(transcript))
        sentences = re.split(r"(?<=[.!?])\s+", overview)
        kept = [x for x in sentences if _drop_invented_dates(x, all_words) == x.strip()
                and (language == "English" or not _english_sentence(x))]  # "Decided to shift …" amid Indonesian
        overview = " ".join(kept) or overview
        return render_summary(overview, items, headings)

    def _ollama(self, system: str, user: str, num_ctx: int, temperature: float = 0.2, fmt=None) -> str:
        try:
            return self._ollama_once(system, user, num_ctx, temperature, fmt)
        except RepeatLimit:
            # In JSON, qwen2.5 sometimes puts a quote in escaped quotation marks and then loops on the closing ones
            # until Ollama gives up; the same request does it every time, a plain-text reminder doesn't.
            return self._ollama_once(system, f"{user}\n{PLAIN_QUOTES}", num_ctx, temperature, fmt)

    def _ollama_once(self, system: str, user: str, num_ctx: int, temperature: float, fmt) -> str:
        # Streamed: the time limit then only counts while Ollama sends nothing. On a CPU, reading a long meeting
        # takes minutes before the first word; writing the answer may take many more, word by word.
        body = {"model": self.model, "stream": True,
                "options": {"num_ctx": num_ctx, "temperature": temperature},  # low: stick to the transcript
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}]}
        if fmt is not None:
            body["format"] = fmt  # JSON schema: Ollama constrains the answer to it
        req = urllib.request.Request(f"{self.ollama_url}/api/chat", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        parts = []
        try:
            with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
                for line in resp:
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if "error" in chunk:  # it went wrong after the answer had started
                        self._fail(500, chunk["error"], fmt)
                    parts.append(chunk.get("message", {}).get("content", ""))
        except urllib.error.HTTPError as e:
            self._fail(e.code, e.read().decode("utf-8", "replace"), fmt)
        except (urllib.error.URLError, OSError) as e:
            if isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError):
                # Ollama is there, just slow: without a GPU a long meeting can take many minutes
                raise RuntimeError(f"Ollama ({self.model}) sent nothing for {OLLAMA_TIMEOUT // 60} minutes. "
                                   f"Long meetings are slow without a GPU that fits the model: try a smaller "
                                   f"--summary-model, or --summarizer claude")
            raise RuntimeError(f"Can't reach Ollama at {self.ollama_url} ({e}). "
                               f"Install it from ollama.com and make sure it is running.")
        return "".join(parts).strip()

    def _fail(self, code: int, detail: str, fmt) -> None:
        if code == 404:
            raise RuntimeError(f"Ollama model '{self.model}' not found. Download it with: ollama pull {self.model}")
        if "repeat limit" in detail and fmt is not None:
            raise RepeatLimit(f"Ollama error {code}: {detail}")
        raise RuntimeError(f"Ollama error {code}: {detail}")

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
            raise RuntimeError("Claude declined to work on this transcript.")
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
