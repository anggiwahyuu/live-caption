"""The "Meeting history" window: past transcripts, a search across all of them, their summaries, and questions
about them (ask_window.py)."""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

from . import export, history
from .summary import SummaryWorker
from .theme import ask_save_path

PREVIEW_LINES = 40  # lines shown for a meeting without a summary


def open_externally(path: Path) -> None:
    """Opens a file or folder in its usual app (text editor, file manager)."""
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - the user's own transcript
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class HistoryWindow:
    def __init__(self, app):
        import tkinter as tk
        from tkinter import ttk

        self.app = app
        c, theme = app.theme.colors, app.theme
        self.folder = Path(app.args.out_dir).expanduser()
        self.meetings: list[history.Meeting] = []
        self.shown: list[tuple[history.Meeting, list[int]]] = []
        self.summarizing: Path | None = None  # the meeting whose summary is being made
        self.search_job = None

        win = self.win = tk.Toplevel(app.root)
        win.title("Meeting history")
        win.geometry("760x560")
        win.minsize(520, 360)
        win.configure(bg=c["bg"])
        win.attributes("-topmost", app.pin_var.get())
        win.bind("<Escape>", lambda _e: win.destroy())

        # ---- search
        top = tk.Frame(win, bg=c["bg"])
        top.pack(fill="x", padx=12, pady=(12, 8))
        self.query = tk.StringVar()
        entry = theme.entry(top, textvariable=self.query)
        entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.count = tk.Label(top, text="", bg=c["bg"], fg=c["muted"], font=theme.ui)
        self.count.pack(side="left", padx=(8, 0))
        self.query.trace_add("write", lambda *_: self._search_soon())
        entry.focus_set()

        # ---- buttons (packed before the list and preview, so they keep their place when the window shrinks)
        buttons = tk.Frame(win, bg=c["bg"])
        buttons.pack(side="bottom", fill="x", padx=12, pady=10)
        ttk.Button(buttons, text="Close", command=win.destroy).pack(side="right")
        # the ⋯ menu, as in the main window: what is used now and then (the buttons don't all fit beside each other)
        self.menu = tk.Menu(win, tearoff=0, bg=c["field"], fg=c["fg"], activebackground=c["hover"],
                            activeforeground=c["fg"], bd=0, relief="flat", font=theme.ui)
        self.menu.add_command(label="Save as…", command=self._save_as)
        self.menu.add_command(label="Copy transcript for chat", command=self._copy_transcript)
        self.menu.add_command(label="Open file", command=self._open_file)
        self.menu.add_command(label="Open folder", command=lambda: open_externally(self.folder))
        self.menu_btn = ttk.Button(buttons, text="⋯", width=3, command=self._open_menu)
        self.menu_btn.pack(side="right", padx=(0, 6))
        self.ask_btn = ttk.Button(buttons, text="Ask…", command=self._ask)
        self.ask_btn.pack(side="right", padx=(0, 6))
        self.summary_btn = ttk.Button(buttons, text="Show summary", command=self._show_summary)
        self.summary_btn.pack(side="right", padx=(0, 6))
        self.summarize_btn = ttk.Button(buttons, text="✦  Summarize", style="Accent.TButton", command=self._summarize)
        self.summarize_btn.pack(side="right", padx=(0, 6))
        self.status = tk.Label(win, text="", anchor="w", bg=c["bg"], fg=c["muted"], font=theme.ui)
        self.status.pack(side="bottom", fill="x", padx=12)  # its own row: the buttons leave no room beside them
        tk.Frame(win, bg=c["line"], height=1).pack(side="bottom", fill="x")

        # ---- list of meetings
        listing = tk.Frame(win, bg=c["bg"])
        listing.pack(fill="x", padx=12)
        self.tree = ttk.Treeview(listing, style="History.Treeview", columns=("when", "length", "summary", "found"),
                                 show="headings", height=5, selectmode="browse")
        for col, text, width, anchor in (("when", "Meeting", 220, "w"), ("length", "Length", 140, "w"),
                                         ("summary", "Summary", 90, "center"), ("found", "", 160, "w")):
            self.tree.heading(col, text=text, anchor=anchor)
            self.tree.column(col, width=width, anchor=anchor, stretch=col in ("when", "found"))
        scroll = ttk.Scrollbar(listing, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="x", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._preview())
        self.tree.bind("<Double-1>", lambda _e: self._open_file())

        # ---- preview of the selected meeting: its summary, or the lines the search found
        self.text = tk.Text(win, wrap="word", bg=c["bg"], fg=c["fg"], relief="flat", padx=14, pady=10,
                            font=theme.body, highlightthickness=0, selectbackground=c["press"], height=8)
        self.text.pack(fill="both", expand=True, pady=(8, 0))
        self.text.tag_configure("muted", foreground=c["muted"], font=theme.small)
        self.text.tag_configure("heading", foreground=c["Participant"], font=theme.bold, spacing1=8)
        self.text.tag_configure("Participant", foreground=c["Participant"], font=theme.bold)
        self.text.tag_configure("Me", foreground=c["Me"], font=theme.bold)
        self.text.tag_configure("found", background=c["match_cur"], foreground="#ffffff")
        self.text.tag_configure("tr", foreground=c["tr"], font=theme.italic)

        self.reload()

    def alive(self) -> bool:
        return self.win.winfo_exists()

    # ---- list
    def reload(self, keep: Path | None = None) -> None:
        self.meetings = history.list_meetings(self.folder, exclude=self.app.transcript.path)
        self._search(keep)

    def _search_soon(self) -> None:
        if self.search_job:
            self.win.after_cancel(self.search_job)
        self.search_job = self.win.after(200, self._search)  # wait for a pause in typing

    def _search(self, keep: Path | None = None) -> None:
        self.search_job = None
        query = self.query.get().strip()
        self.shown = history.search(self.meetings, query)
        self.tree.delete(*self.tree.get_children())
        for i, (m, found) in enumerate(self.shown):
            length = f"{m.duration_min} min · {len(m.lines)} lines"
            hits = f"{len(found)} line{'s' if len(found) != 1 else ''}" if query else ""
            self.tree.insert("", "end", iid=str(i), values=(m.when, length, "✓" if m.summary else "", hits))
        if query:
            self.count.configure(text=f"{len(self.shown)} of {len(self.meetings)} meetings")
        else:
            self.count.configure(text=f"{len(self.meetings)} meetings in {self.folder}")
        if self.shown:
            index = next((i for i, (m, _) in enumerate(self.shown) if m.path == keep), 0)
            self.tree.selection_set(str(index))
            self.tree.see(str(index))
        else:
            self._preview()

    def _selected(self) -> tuple[history.Meeting, list[int]] | None:
        sel = self.tree.selection()
        return self.shown[int(sel[0])] if sel else None

    # ---- preview
    def _preview(self) -> None:
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        selected = self._selected()
        if selected is None:
            t.insert("end", "No meetings found." if self.meetings else
                     f"No transcripts yet in {self.folder}.", "muted")
            self._update_buttons(None)
            t.configure(state="disabled")
            return
        m, found = selected
        words = [w.casefold() for w in self.query.get().split()]
        if words:
            t.insert("end", f"Lines with “{self.query.get().strip()}”\n", "muted")
            for i in found[:200]:
                self._insert_line(m.lines[i], words)
            if not found:
                t.insert("end", "Found only in the summary or the questions.\n", "muted")
        elif m.summary:
            for kind, line in history_blocks(m.summary):
                t.insert("end", line + "\n", "heading" if kind == "heading" else ())
            self._insert_questions(m)
        else:
            self._insert_questions(m)
            t.insert("end", "No summary yet: press Summarize. The first lines:\n", "muted")
            for line in m.lines[:PREVIEW_LINES]:
                self._insert_line(line, [])
        t.configure(state="disabled")
        self._update_buttons(m)

    def _insert_questions(self, m: history.Meeting) -> None:
        """The questions asked about the meeting (ask.Answer.to_markdown), after its summary."""
        if not m.questions:
            return
        t = self.text
        t.insert("end", "Questions\n", "heading")
        for row in m.questions.splitlines():
            if row.startswith("### "):
                t.insert("end", f"{row[4:]}\n", "Participant")
            elif row.startswith("- "):
                t.insert("end", f"    {row[2:]}\n", "muted")
            elif row.startswith("_"):
                t.insert("end", f"{row.strip('_')}\n", "muted")
            elif row.strip():
                t.insert("end", f"{row}\n")
        t.insert("end", "\n")

    def _insert_line(self, line: history.Line, words: list[str]) -> None:
        t = self.text
        t.insert("end", f"{line.time}  ", "muted")
        t.insert("end", f"{line.label}: ", line.label if line.label in ("Me", "Participant") else "Participant")
        start = t.index("end-1c")
        t.insert("end", line.text + "\n")
        if line.translation:
            t.insert("end", f"    {line.translation}\n", "tr")
        lowered = line.text.casefold()  # same length as the text for the scripts Whisper writes
        for w in words:
            pos = lowered.find(w)
            while pos >= 0:
                t.tag_add("found", f"{start}+{pos}c", f"{start}+{pos + len(w)}c")
                pos = lowered.find(w, pos + len(w))

    def _update_buttons(self, m: history.Meeting | None) -> None:
        busy = self.summarizing is not None
        for entry in ("Save as…", "Open file"):
            self.menu.entryconfigure(entry, state="normal" if m else "disabled")
        self.menu.entryconfigure("Copy transcript for chat", state="normal" if m and m.lines else "disabled")
        self.ask_btn.configure(state="normal" if m and m.lines else "disabled")
        self.summary_btn.configure(state="normal" if m and m.summary else "disabled")
        self.summarize_btn.configure(state="normal" if m and m.lines and not busy else "disabled",
                                     text="✦  Summarize again" if m and m.summary else "✦  Summarize")

    # ---- actions
    def _open_menu(self) -> None:
        self.menu.update_idletasks()
        btn = self.menu_btn
        x = btn.winfo_rootx() + btn.winfo_width() - self.menu.winfo_reqwidth()  # right-aligned above ⋯
        self.menu.tk_popup(x, btn.winfo_rooty() - self.menu.winfo_reqheight() - 2)

    def _open_file(self) -> None:
        if selected := self._selected():
            open_externally(selected[0].path)

    def _save_as(self) -> None:
        """A copy of the whole file, the summary and transcript as Word, or the transcript alone (no summary,
        no questions) as Word or text."""
        if not (selected := self._selected()):
            return
        m = selected[0]
        chosen = ask_save_path(self.win, self.folder, m.path.stem,
                               [("Markdown: transcript, summary and questions", "*.md")]
                               + ([export.WORD_WITH_TRANSCRIPT] if m.summary else [])
                               + [("Word: transcript only", "*.docx"), ("Text: transcript only", "*.txt")])
        if chosen is None:
            return
        path, kind = chosen
        language = history.guess_language(m)
        heading = export.title(m.started, language, transcript=True)
        c = self.app.theme.colors
        try:
            if kind == export.WORD_WITH_TRANSCRIPT[0]:
                path.write_bytes(export.to_docx(m.summary, export.title(m.started, language), m.export_lines(),
                                                export.transcript_section(language)))
            elif path.suffix.lower() == ".docx":
                path.write_bytes(export.transcript_to_docx(m.export_lines(), heading))
            elif path.suffix.lower() == ".txt":
                path.write_text(export.transcript_to_text(m.export_lines(), heading), encoding="utf-8")
            else:
                path.write_bytes(m.path.read_bytes())
        except OSError as e:
            self.status.configure(text=f"Failed to save: {e}", fg=c["err"])
            return
        self.status.configure(text=f"Saved: {path}", fg=c["muted"])

    def _copy_transcript(self) -> None:
        if not (selected := self._selected()) or not selected[0].lines:
            return
        m = selected[0]
        chat = export.transcript_to_chat(m.export_lines(),
                                         export.title(m.started, history.guess_language(m), transcript=True))
        self.win.clipboard_clear()
        self.win.clipboard_append(chat)
        message, color = export.copied_message(chat)
        self.status.configure(text=message, fg=self.app.theme.colors[color])

    def _ask(self) -> None:
        if (selected := self._selected()) and selected[0].lines:
            self.app.open_ask(selected[0])

    def _show_summary(self) -> None:
        if (selected := self._selected()) and selected[0].summary:
            m = selected[0]
            self.app.show_summary_window(m.summary, m.started, history.guess_language(m), m.path)

    def _summarize(self) -> None:
        selected = self._selected()
        if selected is None or self.summarizing is not None:
            return
        m = selected[0]
        self.summarizing = m.path
        self._update_buttons(m)
        language = self.app.args.summary_lang
        if language == "auto":
            language = history.guess_language(m)
        self.status.configure(text=f"Summarizing {m.when} with {self.app.summarizer.describe()}…",
                              fg=self.app.theme.colors["muted"])
        SummaryWorker(self.app.summarizer, m.transcript(), self.app.ui_q, language, kind="history_summary").start()

    def on_message(self, kind: str, payload: str) -> None:
        """A summary from SummaryWorker(kind="history_summary"), passed on by the main window.
        The history window may have been closed meanwhile: the summary is still saved and shown."""
        path, self.summarizing = self.summarizing, None
        if path is None:
            return
        c = self.app.theme.colors
        if kind == "history_summary_failed":
            message, color = f"Summary failed: {payload}", "err"
        else:
            note = f"made on {dt.datetime.now():%Y-%m-%d %H:%M}, {self.app.summarizer.describe()}"
            try:
                history.write_summary(path, payload, note)
                message, color = f"Summary saved in {path.name}", "muted"
            except OSError as e:
                message, color = f"Summary made, but saving it failed: {e}", "err"
            m = history.read_meeting(path) if path.exists() else history.Meeting(path, history.started_from(path))
            self.app.show_summary_window(payload, m.started, history.guess_language(m), path)
        if self.alive():
            self.status.configure(text=message, fg=c[color])
            self.reload(keep=path)
        else:
            self.app._set_status(message, color, always=True)


def history_blocks(summary: str) -> list[tuple[str, str]]:
    out = []
    for kind, text in export.blocks(summary):
        text = text.replace("**", "")
        out.append((kind, f"•  {text}" if kind == "bullet" else text))
    return out
