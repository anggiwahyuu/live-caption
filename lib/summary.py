"""Meeting summaries with a local Ollama model or the Claude API."""

from __future__ import annotations

import json
import queue
import threading
import urllib.error
import urllib.request


SUMMARY_PROMPT = """You summarize meeting transcripts produced by live speech-to-text.

The transcript may contain recognition errors, cut-off sentences and repeated fragments. Infer what was meant,
but never invent facts, names, numbers or dates that are not supported by the transcript.
"Me" is the person who recorded the meeting; "Participant" is everyone else (speakers are not told apart).

Write the summary in the main language of the transcript, in Markdown, with exactly these sections
(translate the headings into that language):

### Summary
3-6 sentences on what the meeting was about and what came out of it.

### Decisions
Bullet list of decisions that were made. Write "-" if there were none.

### Action items
Bullet list: who does what, and by when if mentioned. Write "-" if there were none.

### Open questions
Bullet list of questions or issues left unresolved. Write "-" if there were none.

Output only these sections, nothing before or after them."""


class Summarizer:
    """Summarizes the transcript with a local Ollama model (default) or the Claude API (opt-in)."""

    DEFAULT_MODELS = {"ollama": "qwen2.5:3b", "claude": "claude-opus-5-5"}

    def __init__(self, provider: str, model: str | None, ollama_url: str):
        self.provider = provider
        self.model = model or self.DEFAULT_MODELS[provider]
        self.ollama_url = ollama_url.rstrip("/")

    def describe(self) -> str:
        return f"{self.model} via {'Ollama (local)' if self.provider == 'ollama' else 'Claude API (online)'}"

    def summarize(self, transcript: str) -> str:
        if self.provider == "claude":
            return self._claude(transcript)
        return self._ollama(transcript)

    def _ollama(self, transcript: str) -> str:
        # Ollama's default context window (2–4k tokens) silently cuts long transcripts: size it to the input.
        num_ctx = min(32768, max(4096, len(transcript) // 3 + 2048))
        body = {"model": self.model, "stream": False, "options": {"num_ctx": num_ctx},
                "messages": [{"role": "system", "content": SUMMARY_PROMPT},
                             {"role": "user", "content": transcript}]}
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

    def _claude(self, transcript: str) -> str:
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
                system=SUMMARY_PROMPT,
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"],
                extra_body={"fallbacks": "default"},  # if the model declines, the API retries on a fallback model
                messages=[{"role": "user", "content": transcript}],
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

    def __init__(self, summarizer: Summarizer, transcript: str, ui_q: queue.Queue):
        super().__init__(daemon=True)
        self.summarizer, self.transcript, self.ui_q = summarizer, transcript, ui_q

    def run(self) -> None:
        try:
            self.ui_q.put(("summary", self.summarizer.summarize(self.transcript)))
        except Exception as e:
            self.ui_q.put(("summary_failed", str(e)))
