"""The meeting summary in formats ready to send: chat text (WhatsApp, Slack, Telegram), plain text, Word (alone
or with the transcript after it); and the transcript alone, without the summary, as chat text, plain text and Word.

The summary is the light Markdown the summarizer writes: "### Heading", "- bullet", "-" for an empty
section, and now and then **bold**. A transcript is a list of TranscriptLine.
"""

from __future__ import annotations

import datetime as dt
import io
import re
import zipfile
from xml.sax.saxutils import escape

TITLES = {"id": "Ringkasan rapat", "en": "Meeting summary"}
TRANSCRIPT_TITLES = {"id": "Transkrip rapat", "en": "Meeting transcript"}
TRANSCRIPT_SECTIONS = {"id": "Transkrip", "en": "Transcript"}  # the transcript's heading after a summary
WORD_WITH_TRANSCRIPT = ("Word: summary and transcript", "*.docx")  # a file type of the Save dialogs
# (time "10:00:05", speaker label, text, translation or None)
TranscriptLine = tuple[str, str, str, str | None]
MONTHS = {"id": ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober",
                 "November", "Desember"],
          "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                 "November", "December"]}


def title(started: dt.datetime, language: str | None, transcript: bool = False) -> str:
    """ "Ringkasan rapat — 22 Oktober 2026, 10:00" in the summary's language (English for others);
    with transcript=True, "Transkrip rapat — …"."""
    lang = language if language in TITLES else "en"
    month = MONTHS[lang][started.month - 1]
    date = f"{started.day} {month} {started.year}" if lang == "id" else f"{month} {started.day}, {started.year}"
    return f"{(TRANSCRIPT_TITLES if transcript else TITLES)[lang]} — {date}, {started:%H:%M}"


def transcript_section(language: str | None) -> str:
    return TRANSCRIPT_SECTIONS.get(language, TRANSCRIPT_SECTIONS["en"])


def blocks(summary: str) -> list[tuple[str, str]]:
    """The summary as [(kind, text)]: kind is "heading", "bullet", "empty" (a section with nothing) or "text"."""
    out = []
    for line in summary.splitlines():
        if not line.strip():
            continue
        if m := re.match(r"#{1,6}\s+(.*)", line):
            out.append(("heading", m[1].strip()))
        elif re.fullmatch(r"\s*[-*]\s*", line):
            out.append(("empty", "—"))
        elif m := re.match(r"\s*[-*]\s+(.*)", line):
            out.append(("bullet", m[1].strip()))
        else:
            out.append(("text", line.strip()))
    return out


def _plain_bold(text: str, mark: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", lambda m: f"{mark}{m[1]}{mark}", text)


def to_chat(summary: str, heading: str) -> str:
    """For WhatsApp, Slack and Telegram: *bold* headings and • bullets, which all three show as intended."""
    lines = [f"*{heading}*"]
    for kind, text in blocks(summary):
        text = _plain_bold(text, "*")
        if kind == "heading":
            lines += ["", f"*{text}*"]
        elif kind == "bullet":
            lines.append(f"• {text}")
        else:
            lines.append(text)
    return "\n".join(lines) + "\n"


def to_text(summary: str, heading: str) -> str:
    """Plain text, e.g. for an email: no formatting characters at all."""
    lines = [heading, "=" * len(heading)]
    for kind, text in blocks(summary):
        text = _plain_bold(text, "")
        if kind == "heading":
            lines += ["", text.upper()]
        elif kind == "bullet":
            lines.append(f"  • {text}")
        else:
            lines.append(text)
    return "\n".join(lines) + "\n"


CHAT_LIMITS = (("Telegram", 4096), ("Slack", 40000), ("WhatsApp", 65536))  # characters in one message


def transcript_to_chat(lines: list[TranscriptLine], heading: str) -> str:
    """For WhatsApp, Slack and Telegram: the time and speaker in *bold*, the translation in _italics_."""
    out = [f"*{heading}*", ""]
    for time, label, text, translation in lines:
        out.append(f"*[{time}] {label}:* {text}")
        if translation:
            out.append(f"_↳ {translation}_")
    return "\n".join(out) + "\n"


def copied_message(chat: str) -> tuple[str, str]:
    """What the status line says after the transcript was copied for chat -> (message, color)."""
    message = f"Transcript copied for chat ({len(chat):,} characters)"
    apps = [app for app, limit in CHAT_LIMITS if len(chat) > limit]
    if not apps:
        return message, "muted"
    return f"{message}: too long for one {' / '.join(apps)} message, send it as a Word file there", "warn"


def transcript_to_text(lines: list[TranscriptLine], heading: str) -> str:
    """The transcript alone as plain text: "[10:00:05] Participant: text", the translation indented below."""
    out = [heading, "=" * len(heading), ""]
    for time, label, text, translation in lines:
        out.append(f"[{time}] {label}: {text}")
        if translation:
            out.append(f"    ↳ {translation}")
    return "\n".join(out) + "\n"


# ---- Word (.docx): the smallest valid document, built by hand so no extra package is needed

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""


def _runs(text: str, size: int, bold: bool = False, color: str | None = None, italic: bool = False,
          markdown: bool = True) -> str:
    """Word runs for text with **bold** parts (markdown=False: "**" is just text). size in half-points (22 = 11 pt)."""
    out = []
    for i, part in enumerate(re.split(r"\*\*(.+?)\*\*", text) if markdown else [text]):
        if not part:
            continue
        props = f'<w:sz w:val="{size}"/>' + ("<w:b/>" if bold or i % 2 else "") + ("<w:i/>" if italic else "") + \
            (f'<w:color w:val="{color}"/>' if color else "")
        out.append(f'<w:r><w:rPr>{props}</w:rPr><w:t xml:space="preserve">{escape(part)}</w:t></w:r>')
    return "".join(out)


def _paragraph(runs: str, before: int = 0, after: int = 80, indent: int = 0) -> str:
    ind = f'<w:ind w:left="{indent}" w:hanging="240"/>' if indent else ""
    return f'<w:p><w:pPr><w:spacing w:before="{before}" w:after="{after}"/>{ind}</w:pPr>{runs}</w:p>'


def to_docx(summary: str, heading: str, transcript: list[TranscriptLine] | None = None,
            transcript_heading: str = "") -> bytes:
    """The summary; with transcript, the whole transcript after it, under transcript_heading."""
    body = [_paragraph(_runs(heading, 32, bold=True), after=200)]
    for kind, text in blocks(summary):
        if kind == "heading":
            body.append(_paragraph(_runs(text, 26, bold=True, color="1F4E79"), before=240))
        elif kind == "bullet":
            body.append(_paragraph(_runs(f"•\t{text}", 22), indent=360))
        elif kind == "empty":
            body.append(_paragraph(_runs(text, 22, color="808080"), indent=360))
        else:
            body.append(_paragraph(_runs(text, 22)))
    if transcript is not None:
        body.append(_paragraph(_runs(transcript_heading, 28, bold=True), before=480, after=160))
        body += _transcript_paragraphs(transcript)
    return _document(body)


def transcript_to_docx(lines: list[TranscriptLine], heading: str) -> bytes:
    """The transcript alone."""
    return _document([_paragraph(_runs(heading, 32, bold=True), after=200)] + _transcript_paragraphs(lines))


def _transcript_paragraphs(lines: list[TranscriptLine]) -> list[str]:
    """The time and speaker in bold, the translation in grey italics below."""
    body = []
    for time, label, text, translation in lines:
        # what was said is text as heard: a "**" in it is not bold
        head = _runs(f"[{time}] {label}: ", 22, bold=True, color="1F4E79", markdown=False)
        body.append(_paragraph(head + _runs(text, 22, markdown=False), after=40 if translation else 80))
        if translation:
            body.append(_paragraph(_runs(translation, 20, color="808080", italic=True, markdown=False), indent=360))
    return body


def _document(body: list[str]) -> bytes:
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                + "".join(body) +
                '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'  # A4
                '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/></w:sectPr>'
                "</w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", document)
    return buf.getvalue()
