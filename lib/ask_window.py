"""The "Ask about the meeting" window: a question box, and the answers with the transcript lines behind them.
Clicking such a line scrolls the main window to it."""

from __future__ import annotations

from .ask import Answer, AskWorker

EXAMPLES = "e.g. “When is the deadline?” · “What was said about the server?” · “Who takes on the report?”"


class AskWindow:
    def __init__(self, app):
        import tkinter as tk
        from tkinter import ttk

        self.app = app
        c, theme = app.theme.colors, app.theme
        self.asking: str | None = None  # the question being answered
        self.links = 0                   # source lines shown so far (each has its own click tag)

        win = self.win = tk.Toplevel(app.root)
        win.title("Ask about the meeting")
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

    def _ask(self) -> None:
        question = self.entry.get().strip()
        if not question or self.asking is not None:
            return
        transcript = self.app.transcript.for_summary()
        if not transcript:
            self._set_status("Nothing has been said yet", "warn")
            return
        self.asking = question
        self.ask_btn.configure(state="disabled")
        self.entry.delete(0, "end")
        lines = self.app.transcript.next_id
        self._set_status(f"Reading {lines} lines with {self.app.summarizer.describe()}…")
        self._write([(f"{question}\n", "question")])
        AskWorker(self.app.summarizer, transcript, question, self.app.ui_q).start()

    def on_message(self, kind: str, payload) -> None:
        """An AskWorker result, passed on by the main window."""
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
            link = f"src{self.links}"
            self.links += 1
            parts += [(f"{source.time}  ", ("source", "time")), (f"{source.speaker}: ", "source"),
                      (f"{source.text}\n", ("source", "link", link))]
            self.text.tag_bind(link, "<Button-1>", lambda _e, i=source.index: self._jump(i))
            self.text.tag_bind(link, "<Enter>", lambda _e: self.text.configure(cursor="hand2"))
            self.text.tag_bind(link, "<Leave>", lambda _e: self.text.configure(cursor="arrow"))
        if not answer.sources:
            note = ("The lines it quoted aren't in the transcript: check before relying on this answer"
                    if answer.unsupported else "No transcript line given for this answer")
            parts.append((f"{note}\n", "note"))
        self._write(parts)
        self._set_status("Click a line to see it in the captions" if answer.sources else EXAMPLES)

    def _jump(self, line_id: int) -> None:
        if not self.app.show_line(line_id):
            self._set_status("That line was cleared from the screen (it's still in the transcript file)", "warn")
