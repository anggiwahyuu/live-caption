"""The window showing a meeting summary, with ways to send it on (chat text, Markdown, Word, plain text).
Opened for the live meeting (ui.py) and for past ones (history_window.py)."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from . import export
from .theme import ask_save_path


def open_summary_window(root, theme, text: str, started: dt.datetime, language: str | None, transcript: Path,
                        topmost: bool, set_status):
    """set_status(message, color): where "copied" / "saved" messages go (the main window's status line)."""
    import tkinter as tk
    from tkinter import ttk

    c = theme.colors
    heading = export.title(started, language)
    win = tk.Toplevel(root)
    win.title(heading)
    win.geometry("680x480")
    win.configure(bg=c["bg"])
    win.attributes("-topmost", topmost)

    def copy(content: str, what: str) -> None:
        root.clipboard_clear()
        root.clipboard_append(content)
        set_status(f"Summary copied {what}", "muted")

    def save() -> None:
        path = ask_save_path(win, transcript.parent, f"{transcript.stem}-summary",
                             [("Word document", "*.docx"), ("Text", "*.txt"), ("Markdown", "*.md")])
        if path is None:
            return
        ext = path.suffix.lower()
        try:
            if ext == ".docx":
                path.write_bytes(export.to_docx(text, heading))
            elif ext == ".md":
                path.write_text(f"# {heading}\n\n{text}\n", encoding="utf-8")
            else:
                path.write_text(export.to_text(text, heading), encoding="utf-8")
        except OSError as e:
            set_status(f"Failed to save: {e}", "err")
            return
        set_status(f"Summary saved: {path}", "muted")

    buttons = tk.Frame(win, bg=c["bg"])
    buttons.pack(side="bottom", fill="x", padx=12, pady=10)
    ttk.Button(buttons, text="Close", command=win.destroy).pack(side="right")
    ttk.Button(buttons, text="Save as…", command=save).pack(side="right", padx=(0, 6))
    ttk.Button(buttons, text="Copy Markdown",
               command=lambda: copy(f"# {heading}\n\n{text}", "as Markdown")).pack(side="right", padx=(0, 6))
    ttk.Button(buttons, text="Copy for chat", style="Accent.TButton",
               command=lambda: copy(export.to_chat(text, heading), "for WhatsApp / Slack / Telegram")
               ).pack(side="right", padx=(0, 6))
    tk.Frame(win, bg=c["line"], height=1).pack(side="bottom", fill="x")

    box = tk.Text(win, wrap="word", bg=c["bg"], fg=c["fg"], relief="flat", padx=18, pady=12,
                  font=theme.body, highlightthickness=0, selectbackground=c["press"])
    box.tag_configure("title", font=theme.bold, foreground=c["fg"], spacing3=2)
    box.tag_configure("heading", font=theme.bold, foreground=c["Participant"], spacing1=12, spacing3=4)
    box.tag_configure("bullet", lmargin1=8, lmargin2=24, spacing1=2)
    box.tag_configure("empty", lmargin1=8, foreground=c["muted"])
    box.insert("end", heading + "\n", "title")
    for kind, line in export.blocks(text):
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        if kind == "bullet":
            box.insert("end", f"•  {line}\n", "bullet")
        elif kind == "text":
            box.insert("end", line + "\n")
        else:
            box.insert("end", line + "\n", kind)
    box.configure(state="disabled")  # still selectable, so parts can be copied
    box.pack(fill="both", expand=True)
    return win
