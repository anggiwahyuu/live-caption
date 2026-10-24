"""The "Ask about the meeting" window: a question box, and the answers with the transcript lines behind them.
For the live meeting, clicking such a line scrolls the main window to it; for a past one (from the history
window) the answers asked before are shown first. The main window saves every answer (ui.App._answer_done)."""

from __future__ import annotations

import re

from . import history
from .ask import Answer, AskWorker

EXAMPLES = "e.g. “When is the deadline?” · “What was said about the server?” · “Who takes on the report?”"


class AskWindow:
    def __init__(self, app, meeting: history.Meeting | None = None):
        """meeting: a past meeting from the history window; None = the one being transcribed."""
        import tkinter as tk
        from tkinter import ttk

        self.app = app
        self.meeting = meeting
        c, theme = app.theme.colors, app.theme
        self.asking: str | None = None  # the question being answered
        self.links = 0                   # source lines shown so far (each has its own click tag)

        win = self.win = tk.Toplevel(app.root)
        win.title("Ask about the meeting" if meeting is None else f"Ask about the meeting of {meeting.when}")
        win.geometry("640x460")
        win.minsize(420, 260)
        win.configure(bg=c["bg"])
        win.attributes("-topmost", app.pin_var.get())
        win.bind("<Escape>", lambda _e: win.destroy())

        top = tk.Frame(win, bg=c["bg"])
        top.pack(fill="x", padx=12, pady=(12, 4))
        self.ask_btn = ttk.Button(top, text="Ask", style="Accent.TButton", command=self._ask)
        self.ask_btn.pack(side="right", padx=(8, 0))
        self.entry = theme.entry(top)
        self.entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.entry.bind("<Return>", lambda _e: self._ask())
        self.entry.focus_set()
        self.status = tk.Label(win, text=EXAMPLES, anchor="w", bg=c["bg"], fg=c["muted"], font=theme.small)
        self.status.pack(fill="x", padx=12, pady=(0, 8))
        tk.Frame(win, bg=c["line"], height=1).pack(fill="x")

        self.text = tk.Text(win, wrap="word", bg=c["bg"], fg=c["fg"], relief="flat", padx=14, pady=10,
                            font=theme.body, highlightthickness=0, selectbackground=c["press"], cursor="arrow")
        scroll = ttk.Scrollbar(win, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set, state="disabled")
        scroll.pack(side="right", fill="y")
        self.text.pack(fill="both", expand=True)
        indent = theme.body_font.measure("    ")
        tag = self.text.tag_configure
        tag("question", foreground=c["Participant"], font=theme.bold, spacing1=14, spacing3=4)
        tag("answer", spacing3=4)
        tag("source", font=theme.small, lmargin1=indent, lmargin2=indent * 2, spacing1=2)
        tag("time", foreground=c["muted"])
        tag("link", underline=True)
        tag("note", foreground=c["muted"], font=theme.small, lmargin1=indent, spacing1=2)
        tag("err", foreground=c["err"], font=theme.small, spacing1=2)
        tag("earlier", foreground=c["muted"], font=theme.small, spacing1=2)
        if meeting is not None and meeting.questions:
            self._show_earlier(meeting.questions)

    def alive(self) -> bool:
        return self.win.winfo_exists()

    def show(self) -> None:
        self.win.deiconify()
        self.win.lift()
        self.entry.focus_force()

    def _set_status(self, text: str, color: str = "muted") -> None:
        self.status.configure(text=text, fg=self.app.theme.colors[color])

    def _write(self, parts) -> None:
        self.text.configure(state="normal")
        for content, tags in parts:
            self.text.insert("end", content, tags)
        self.text.configure(state="disabled")
        self.text.see("end")

    def _show_earlier(self, questions: str) -> None:
        """The questions asked about a past meeting before, as saved in its file (ask.Answer.to_markdown)."""
        parts = []
        for row in questions.splitlines():
            if row.startswith("### "):
                parts.append((f"{row[4:]}\n", "question"))
            elif m := re.match(r"- \[([\d:]+)\] ([^:]+): (.*)", row):
                parts += [(f"{m[1]}  ", ("source", "time")), (f"{m[2]}: ", "source"), (f"{m[3]}\n", "source")]
            elif m := re.fullmatch(r"_(.+)_", row.strip()):
                parts.append((f"{m[1]}\n", "earlier" if m[1].startswith("Asked on") else "note"))
            elif row.strip():
                parts.append((f"{row}\n", "answer"))
        self._write(parts)

    def _ask(self) -> None:
        question = self.entry.get().strip()
        if not question or self.asking is not None:
            return
        if self.meeting is None:
            transcript, lines = self.app.transcript.for_summary(), self.app.transcript.next_id
        else:
            transcript, lines = self.meeting.transcript(), len(self.meeting.lines)
        if not transcript:
            self._set_status("Nothing has been said yet", "warn")
            return
        self.asking = question
        self.ask_btn.configure(state="disabled")
        self.entry.delete(0, "end")
        self._set_status(f"Reading {lines} lines with {self.app.summarizer.describe()}…")
        self._write([(f"{question}\n", "question")])
        AskWorker(self.app.summarizer, transcript, question, self.app.ui_q,
                  None if self.meeting is None else self.meeting.path).start()

    def on_message(self, kind: str, payload, saved: str | None) -> None:
        """An AskWorker result, passed on by the main window after saving it. saved: where to, or why not."""
        self.asking = None
        self.ask_btn.configure(state="normal")
        if kind == "answer_failed":
            _question, error = payload
            self._write([(f"{error}\n", "err")])
            self._set_status("Couldn't answer: see the error above", "err")
            return
        answer: Answer = payload
        parts = [(f"{answer.text}\n", "answer")]
        for source in answer.sources:
            if self.meeting is None:  # live: the line can be shown in the main window
                link = f"src{self.links}"
                self.links += 1
                self.text.tag_bind(link, "<Button-1>", lambda _e, i=source.index: self._jump(i))
                self.text.tag_bind(link, "<Enter>", lambda _e: self.text.configure(cursor="hand2"))
                self.text.tag_bind(link, "<Leave>", lambda _e: self.text.configure(cursor="arrow"))
                tags = ("source", "link", link)
            else:
                tags = "source"
            parts += [(f"{source.time}  ", ("source", "time")), (f"{source.speaker}: ", "source"),
                      (f"{source.text}\n", tags)]
        if answer.warning:
            parts.append((f"{answer.warning}\n", "note"))
        self._write(parts)
        if saved and saved.startswith("Saved"):
            hint = "Click a line to see it in the captions · " if answer.sources and self.meeting is None else ""
            self._set_status(f"{hint}{saved}")
        else:
            self._set_status(saved or EXAMPLES, "err" if saved else "muted")

    def _jump(self, line_id: int) -> None:
        if not self.app.show_line(line_id):
            self._set_status("That line was cleared from the screen (it's still in the transcript file)", "warn")
