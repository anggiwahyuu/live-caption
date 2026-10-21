"""Optional translation of final lines (Google online or Argos offline)."""

from __future__ import annotations

import queue
import threading


class Translator:
    """Optional translation. Backend 'google' (online, lightweight) or 'argos' (offline)."""

    def __init__(self, target: str, backend: str):
        self.target = target
        self.backend = backend
        self.enabled = False
        self._google = None
        self._argos_ready: set[tuple[str, str]] = set()

    def translate(self, text: str, src_lang: str | None) -> str | None:
        if not text:
            return None
        if src_lang and src_lang.split("-")[0] == self.target.split("-")[0]:
            return None  # already in the target language
        try:
            if self.backend == "google":
                return self._via_google(text)
            return self._via_argos(text, src_lang or "en")
        except Exception as e:  # network down, missing package, etc.
            return f"[translation failed: {e}]"

    def _via_google(self, text: str) -> str:
        if self._google is None:
            from deep_translator import GoogleTranslator
            self._google = GoogleTranslator(source="auto", target=self.target)
        return self._google.translate(text)

    def _via_argos(self, text: str, src: str) -> str:
        import argostranslate.translate
        if (src, self.target) not in self._argos_ready:
            self._install_argos(src)
            self._argos_ready.add((src, self.target))
        return argostranslate.translate.translate(text, src, self.target)

    def _install_argos(self, src: str) -> None:
        import argostranslate.package as pkg
        installed = {(p.from_code, p.to_code) for p in pkg.get_installed_packages()}
        if (src, self.target) in installed:
            return
        pkg.update_package_index()
        available = {(p.from_code, p.to_code): p for p in pkg.get_available_packages()}
        if (src, self.target) in available:
            needed = [(src, self.target)]
        else:  # pivot through English
            needed = [pair for pair in [(src, "en"), ("en", self.target)] if pair[0] != pair[1]]
        for pair in needed:
            if pair in installed:
                continue
            if pair not in available:
                raise RuntimeError(f"Argos package {pair[0]}→{pair[1]} is not available")
            pkg.install_from_path(available[pair].download())


class TranslationWorker(threading.Thread):
    """Translates on a separate thread so transcription is never held up."""

    def __init__(self, translator: Translator, tr_q: queue.Queue, ui_q: queue.Queue, stop_event: threading.Event):
        super().__init__(daemon=True)
        self.translator, self.tr_q, self.ui_q, self.stop_event = translator, tr_q, ui_q, stop_event

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                line_id, text, lang = self.tr_q.get(timeout=0.2)
            except queue.Empty:
                continue
            result = self.translator.translate(text, lang)
            if result:
                self.ui_q.put(("translation", line_id, result))
