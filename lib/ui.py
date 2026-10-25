"""The main window: status header, ⋯ menu, live captions with drafts and translations, alert words,
and the meeting summary (on demand and automatic). The pieces it shows live elsewhere: the meeting's
data in transcript.py, the search bar in search_bar.py, the summary window in summary_window.py,
the Ask window in ask_window.py."""

from __future__ import annotations

import datetime as dt
import queue
import re
import sys
import threading
import time
from pathlib import Path

from . import alerts, export, history, theme
from .ask_window import AskWindow
from .common import IS_MAC
from .config import DEFAULT_GEOMETRY, load_window_state, restore_geometry, save_window_state
from .history_window import HistoryWindow
from .search_bar import SearchBar
from .subtitles import parse_clock
from .summary import Summarizer, SummaryWorker
from .summary_window import open_summary_window
from .transcript import Transcript
from .translation import Translator

NEW_SPEAKER_GAP = dt.timedelta(minutes=2)  # same speaker after this long gets a new header (with its time)
AUTO_SUMMARY_MIN_LINES = 5  # fewer new lines than this aren't worth an automatic summary
ALERT_COOLDOWN = 20  # seconds: at most one desktop notification in this time, however often the word comes


class App:
    def __init__(self, root, args, ui_q: queue.Queue, tr_q: queue.Queue, translator: Translator,
                 paused: threading.Event, summarizer: Summarizer, on_close):
        import tkinter as tk
        from tkinter import ttk

        self.root, self.args, self.ui_q, self.tr_q, self.translator = root, args, ui_q, tr_q, translator
        self.paused = paused
        self.summarizer = summarizer
        self.on_close = on_close
        self.transcript = Transcript(args.out_dir)
        self.windows: list = []  # summary and history windows: they follow "Always on top"
        self.history: HistoryWindow | None = None
        self.ask_wins: dict[Path | None, AskWindow] = {}  # Ask windows: None = this meeting, else a past one's file

        # summary
        self.summary_win = None                 # the live meeting's summary window
        self.summary_language: str | None = None  # the language the latest summary was asked for
        self.summary_upto = 0                   # lines with id < this are in the latest summary (or the one being made)
        self.last_line_at = time.monotonic()    # for the automatic summary after a silence
        self.closing = False                    # the window was closed; it goes once the last summary is done
        self.closed = False                     # on_close was called: the window is gone
        # alert words
        self.alert_words = alerts.parse_words(args.alert)
        self.alert_re = alerts.compile_words(self.alert_words)
        self.last_alert_at = float("-inf")      # when the last desktop notification was shown
        # captions
        self.last_status = ("Starting…", "muted")  # restored when resuming from pause
        self.calm_status = self.last_status         # the last one that wasn't an error
        self.ready = False                          # model loaded: the status dot turns green
        self.view_floor = 0  # lines with id < this have been cleared from the screen
        self.drafts: dict[str, tuple[int, str]] = {}  # label -> (seg_id, temporary text)
        self.finalized: dict[str, int] = {}           # label -> next seg_id that may still be shown as a draft
        self.last_speaker: str | None = None          # consecutive lines of one speaker share a header
        self.last_ts: dt.datetime | None = None

        state = load_window_state() if args.geometry is None else {}
        root.title("Live Caption")
        geometry = args.geometry or restore_geometry(state.get("geometry"), root) or DEFAULT_GEOMETRY
        root.geometry(geometry)
        root.minsize(420, 160)
        self._measure_frame_offset(geometry)
        self.theme = theme.setup(root, args.font_size)
        c = self.theme.colors
        root.configure(bg=c["bg"])
        on_top = state.get("always_on_top", True) is not False
        root.attributes("-topmost", on_top)
        try:
            root.attributes("-alpha", args.opacity)
        except tk.TclError:
            pass
        root.protocol("WM_DELETE_WINDOW", self._close)
        mod_label = "Cmd" if IS_MAC else "Ctrl"

        # ---- header: ● status ............ [Pause] [Summary] [⋯]
        header = tk.Frame(root, bg=c["bg"])
        header.pack(fill="x", padx=10, pady=(8, 6))
        self.menu_btn = ttk.Button(header, text="⋯", width=3, command=self._open_menu)
        self.menu_btn.pack(side="right")
        self.summary_btn = ttk.Button(header, text="✦  Summary", style="Accent.TButton", command=self._summarize)
        self.summary_btn.pack(side="right", padx=(0, 6))
        self.pause_btn = ttk.Button(header, text="⏸  Pause", command=self._toggle_pause)
        self.pause_btn.pack(side="right", padx=(0, 6))
        self.dot = tk.Label(header, text="●", bg=c["bg"], fg=c["muted"], font=self.theme.ui)
        self.dot.pack(side="left")
        self.status = tk.Label(header, text="Starting…", anchor="w", bg=c["bg"], fg=c["muted"], font=self.theme.ui)
        self.status.pack(side="left", fill="x", expand=True, padx=(6, 10))

        # ---- the ⋯ menu: everything that isn't needed every minute
        self.tr_var = tk.BooleanVar(value=translator.enabled)
        self.pin_var = tk.BooleanVar(value=on_top)
        auto = args.auto_summary if args.auto_summary is not None else state.get("auto_summary") is True
        self.auto_var = tk.BooleanVar(value=auto)
        self.menu = tk.Menu(root, tearoff=0, bg=c["field"], fg=c["fg"], activebackground=c["hover"],
                            activeforeground=c["fg"], selectcolor=c["fg"], bd=0, relief="flat", font=self.theme.ui)
        self.menu.add_checkbutton(label=f"Translate → {translator.target}", variable=self.tr_var,
                                  command=self._toggle_translate, accelerator=f"{mod_label}+T")
        self.menu.add_checkbutton(label="Always on top", variable=self.pin_var, command=self._toggle_on_top)
        self.menu.add_checkbutton(label=f"Auto summary (after {args.auto_summary_after:g} min of silence)",
                                  variable=self.auto_var, command=self._toggle_auto_summary)
        self.menu.add_command(label="Alert words…", command=self._edit_alert_words)
        self.menu.add_separator()
        self.menu.add_command(label="Ask about the meeting…", command=self._open_ask, accelerator=f"{mod_label}+K")
        self.menu.add_command(label="Search…", command=self._open_search, accelerator=f"{mod_label}+F")
        self.menu.add_command(label="Meeting history…", command=self._open_history, accelerator=f"{mod_label}+H")
        self.menu.add_command(label="Save as…", command=self._save_as, accelerator=f"{mod_label}+S")
        self.menu.add_command(label="Copy transcript for chat", command=self._copy_transcript)
        self.menu.add_command(label="Clear screen", command=self._clear, accelerator=f"{mod_label}+L")

        tk.Frame(root, bg=c["line"], height=1).pack(fill="x")

        # ---- the captions
        body = tk.Frame(root, bg=c["bg"])
        body.pack(fill="both", expand=True)
        self.text = tk.Text(body, wrap="word", bg=c["bg"], fg=c["fg"], insertbackground=c["fg"], relief="flat",
                            padx=14, pady=8, font=self.theme.body, state="disabled", highlightthickness=0,
                            selectbackground=c["press"], cursor="arrow")
        scroll = ttk.Scrollbar(body, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self._configure_tags()
        # Draft area = from the "draft" mark to the end of the text. Final lines are always inserted right before it.
        self.text.mark_set("draft", "end-1c")
        self.search = SearchBar(root, self.text, before=body, theme=self.theme)  # shown with Ctrl+F

        # Keyboard shortcuts (window must be focused). Cmd on macOS, Ctrl elsewhere.
        mod = "Command" if IS_MAC else "Control"
        shortcuts = {"p": self._toggle_pause, "t": self._toggle_translate_key,
                     "s": self._save_as, "l": self._clear, "m": self._summarize, "f": self._open_search,
                     "h": self._open_history, "k": self._open_ask}
        for key, fn in shortcuts.items():
            for k in (key, key.upper()):  # also works with Caps Lock on
                for widget in (root, self.search.entry):  # on the entry too, so its own Ctrl+key editing keys don't run
                    widget.bind(f"<{mod}-{k}>", lambda _e, fn=fn: (fn(), "break")[1])
        root.bind("<F3>", lambda _e: self.search.step(1))
        root.bind("<Shift-F3>", lambda _e: self.search.step(-1))
        root.bind("<Escape>", lambda _e: self.search.close())

        if args._config_path:
            self._note(f"ℹ Settings loaded from {args._config_path}", "info")
        if self.alert_words:
            self._note(f"ℹ Alert words: {', '.join(self.alert_words)}", "info")
        if self.theme.rough_fonts:
            self._note("ℹ Text looks rough because this Python's Tk has no font smoothing (Xft). "
                       "To fix it, run ./setup.sh --recreate-venv (docs/install.md: Python environment).", "info")
        root.after(30, self._poll)
        root.after(10_000, self._auto_summary_check)

    def _configure_tags(self) -> None:
        c, t = self.theme.colors, self.theme
        indent = t.body_font.measure("  ")
        tag = self.text.tag_configure
        tag("Participant", foreground=c["Participant"], font=t.bold, spacing1=10)
        tag("Me", foreground=c["Me"], font=t.bold, spacing1=10)
        tag("time", foreground=c["muted"], font=t.small)
        tag("body", lmargin1=indent, lmargin2=indent, spacing1=2)
        tag("tr", foreground=c["tr"], font=t.italic, lmargin1=indent, lmargin2=indent * 3, spacing1=1)
        tag("err", foreground=c["err"], font=t.small, spacing1=8)
        tag("info", foreground=c["muted"], font=t.small, spacing1=8)
        tag("draft_head", foreground=c["muted"], font=t.bold, spacing1=10)
        tag("draft", foreground=c["muted"], font=t.italic, lmargin1=indent, lmargin2=indent, spacing1=2)
        # lmargincolor: a word at the start of a line would otherwise color the indent in front of it too
        tag("alert", background=c["alert"], foreground="#ffffff", font=t.bold, lmargincolor=c["bg"])
        tag("match", background=c["match"])
        tag("match_cur", background=c["match_cur"], foreground="#ffffff")
        tag("flash", background=c["match_cur"], foreground="#ffffff", lmargincolor=c["bg"])  # shown from the Ask window

    def _open_menu(self) -> None:
        self.menu.update_idletasks()
        btn = self.menu_btn
        x = btn.winfo_rootx() + btn.winfo_width() - self.menu.winfo_reqwidth()  # right-aligned under ⋯
        self.menu.tk_popup(x, btn.winfo_rooty() + btn.winfo_height() + 2)

    # ---- window position and closing
    def _measure_frame_offset(self, geometry: str) -> None:
        """Tk reports where the window *content* is, but places the window *frame* (title bar included)
        at the requested position. Measure that difference so a saved position doesn't creep down each run."""
        self.frame_offset = (0, 0)
        m = re.fullmatch(r"\d+x\d+\+(-?\d+)\+(-?\d+)", geometry)
        if not m:
            return
        asked_x, asked_y = int(m[1]), int(m[2])

        def measure() -> None:
            dx, dy = self.root.winfo_rootx() - asked_x, self.root.winfo_rooty() - asked_y
            if 0 <= dx <= 50 and 0 <= dy <= 100:  # otherwise the window manager placed it elsewhere (e.g. Wayland)
                self.frame_offset = (dx, dy)
        self.root.after(500, measure)

    def _window_geometry(self) -> str:
        r, (dx, dy) = self.root, self.frame_offset
        return f"{r.winfo_width()}x{r.winfo_height()}+{r.winfo_rootx() - dx}+{r.winfo_rooty() - dy}"

    def _close(self) -> None:
        save_window_state({"geometry": self._window_geometry(), "always_on_top": self.pin_var.get(),
                           "auto_summary": self.auto_var.get(), "alert_words": self.alert_words})
        if not self._summary_before_close():
            self._quit()

    def _quit(self) -> None:
        self.closed = True
        self.on_close()  # destroys the window: nothing may touch Tk after this

    # ---- events from other threads
    def _poll(self) -> None:
        try:
            while not self.closed:
                msg = self.ui_q.get_nowait()
                kind = msg[0]
                if kind == "status":
                    self._set_status(msg[1], "muted")
                elif kind == "error":
                    self._set_status(msg[1], "err", always=True)
                    self._note(f"⚠ {msg[1]}", "err")
                elif kind == "info":  # notice only (e.g. recorder fallback): keep the status line as it is
                    self._note(f"ℹ {msg[1]}", "info")
                elif kind == "recovered":  # a recording reconnected: its error is over
                    self._note(f"ℹ {msg[1]}", "info")
                    if self.last_status[1] == "err":
                        self._set_status(*self.calm_status)
                elif kind == "line":
                    self._add_line(*msg[1:])
                elif kind == "draft":
                    self._set_draft(*msg[1:])
                elif kind == "drop":
                    self._finish_draft(msg[1], msg[2])
                    self._render_drafts()
                elif kind == "translation":
                    self._add_translation(msg[1], msg[2])
                elif kind == "summary":
                    self._summary_done(msg[1])
                elif kind == "summary_failed":
                    self._summary_failed(msg[1])
                elif kind in ("answer", "answer_failed"):  # a question from an Ask window
                    self._answer_done(kind, msg[1], msg[2])
                elif kind.startswith("history_summary"):  # a past meeting's summary (history window)
                    if self.history is not None:
                        self.history.on_message(kind, msg[1])
        except queue.Empty:
            pass
        if not self.closed:
            self.root.after(30, self._poll)

    def _set_status(self, text: str, color: str, always: bool = False) -> None:
        """Show a status message. While paused, only errors and replies to the user's own actions
        (always=True) are shown, next to the pause notice; routine status updates wait for Resume."""
        self.last_status = (text, color)
        if color != "err":
            self.calm_status = (text, color)
        if text.startswith("Ready"):
            self.ready = True
        if not self.paused.is_set():
            self.status.configure(text=text, fg=self.theme.colors[color])
            self._update_dot(error=color == "err")
        elif always:
            self.status.configure(text=f"Paused · {text}", fg=self.theme.colors[color])

    def _update_dot(self, error: bool = False) -> None:
        """● red = error, amber = paused, green = listening, grey = still loading."""
        c = self.theme.colors
        if self.paused.is_set():
            color = c["warn"]
        elif error:
            color = c["err"]
        else:
            color = c["ok"] if self.ready else c["muted"]
        self.dot.configure(fg=color)

    # ---- captions
    def _append(self, parts, mark: str | None = None) -> None:
        """Insert final text right above the draft area."""
        at_bottom = self.text.yview()[1] > 0.98
        self.text.configure(state="normal")
        self.text.mark_gravity("draft", "right")  # the draft mark gets pushed down below the new text
        for content, tag in parts:
            if tag:
                self.text.insert("draft", content, tag)
            else:
                self.text.insert("draft", content)
        if mark:
            # Mark at the end of this line (left gravity) = where the translation is inserted later,
            # staying right below its original line even after new lines have come in.
            self.text.mark_set(mark, "draft")
            self.text.mark_gravity(mark, "left")
        self.text.configure(state="disabled")
        if at_bottom:
            self.text.see("end")
        self.search.refresh()

    def _note(self, text: str, tag: str) -> None:
        """A notice line (info/error/pause). The next caption starts a new speaker header after it."""
        self._append([(f"{text}\n", tag)])
        self.last_speaker = None
        if self.drafts:
            self._render_drafts()  # a draft below the notice needs its speaker header back

    def _set_draft(self, label: str, seg_id: int, text: str) -> None:
        """Temporary text while someone is still speaking."""
        if seg_id < self.finalized.get(label, 0):
            return  # this sentence is already final, its draft arrived late
        self.drafts[label] = (seg_id, text)
        self._render_drafts()

    def _finish_draft(self, label: str, seg_id: int) -> None:
        self.finalized[label] = max(self.finalized.get(label, 0), seg_id + 1)
        current = self.drafts.get(label)
        if current and current[0] <= seg_id:
            del self.drafts[label]

    def _render_drafts(self) -> None:
        at_bottom = self.text.yview()[1] > 0.98
        self.text.configure(state="normal")
        self.text.delete("draft", "end-1c")
        self.text.mark_gravity("draft", "left")  # drafts are written after the mark, the mark stays in front of them
        for label, (_, text) in self.drafts.items():
            if label != self.last_speaker:  # the same speaker carrying on needs no new header
                self.text.insert("end", f"{label} …\n", "draft_head")
            self.text.insert("end", f"{text}\n", "draft")
        self.text.configure(state="disabled")
        if at_bottom:
            self.text.see("end")

    def _add_line(self, ts: dt.datetime, label: str, text: str, lang: str, seg_id: int,
                  end: dt.datetime | None = None) -> None:
        line_id = self.transcript.add(ts, label, text, lang, end)
        parts = []
        if label != self.last_speaker or self.last_ts is None or ts - self.last_ts > NEW_SPEAKER_GAP:
            parts += [(label, label), (f"   {ts:%H:%M:%S}\n", "time")]
        spans = alerts.find(self.alert_re, text) if label != "Me" else []  # your own voice saying your name: no alert
        pos = 0
        for start, stop in spans:  # the alert words highlighted inside the line
            parts += [(text[pos:start], "body"), (text[start:stop], ("body", "alert"))]
            pos = stop
        parts.append((f"{text[pos:]}\n", "body"))
        self._append(parts, mark=f"tr{line_id}")
        # Where the text starts, for show_line. Right gravity: the previous line's translation, inserted here
        # later, goes in front of it.
        self.text.mark_set(f"line{line_id}", f"tr{line_id} -1c linestart")
        self.last_speaker, self.last_ts = label, ts
        self.last_line_at = time.monotonic()
        if spans:
            self._alert(ts, label, text, text[spans[0][0]:spans[0][1]])
        self._finish_draft(label, seg_id)
        self._render_drafts()
        if self.translator.enabled:
            self.tr_q.put((line_id, text, lang))
        self._autosave()

    def _add_translation(self, line_id: int, translation: str) -> None:
        if not self.transcript.set_translation(line_id, translation):
            return
        mark = f"tr{line_id}"
        if line_id >= self.view_floor and mark in self.text.mark_names():
            at_bottom = self.text.yview()[1] > 0.98
            self.text.configure(state="normal")
            self.text.mark_gravity("draft", "right")  # translations must not end up in the draft area
            self.text.insert(mark, f"↳ {translation}\n", "tr")
            self.text.mark_unset(mark)
            self.text.configure(state="disabled")
            if at_bottom:  # don't yank the view down while the user is reading older lines
                self.text.see("end")
            self.search.refresh()
        self._autosave()

    def _autosave(self) -> None:
        try:
            self.transcript.save()
        except OSError as e:
            self._set_status(f"Failed to save transcript: {e}", "err", always=True)

    # ---- menu and buttons
    def _open_search(self) -> None:
        self.search.open()

    def _toggle_translate(self) -> None:
        self.translator.enabled = self.tr_var.get()

    def _toggle_translate_key(self) -> None:
        self.tr_var.set(not self.tr_var.get())
        self._toggle_translate()

    def _toggle_on_top(self) -> None:
        self.root.attributes("-topmost", self.pin_var.get())
        self.windows = [w for w in self.windows if w.winfo_exists()]
        for win in self.windows:
            win.attributes("-topmost", self.pin_var.get())

    def _toggle_pause(self) -> None:
        c = self.theme.colors
        mod = "Cmd" if IS_MAC else "Ctrl"
        if self.paused.is_set():
            self.paused.clear()
            self.pause_btn.configure(text="⏸  Pause")
            self.status.configure(text=self.last_status[0], fg=c[self.last_status[1]])
            self._update_dot(error=self.last_status[1] == "err")
            self._note(f"▶ Resumed at {dt.datetime.now():%H:%M:%S}", "info")
        else:
            self.paused.set()
            self.pause_btn.configure(text="▶  Resume")
            self.status.configure(text=f"Paused — nothing is being transcribed ({mod}+P to resume)", fg=c["warn"])
            self._update_dot()
            self._note(f"⏸ Paused at {dt.datetime.now():%H:%M:%S}", "info")

    def _clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.mark_set("draft", "end-1c")
        self.text.configure(state="disabled")
        self.view_floor = self.transcript.next_id  # only the screen is cleared; the transcript file stays intact
        self.last_speaker = None
        self._render_drafts()
        self.search.refresh()

    def _save_as(self) -> None:
        from tkinter import simpledialog
        started = self.transcript.started
        summary = self.transcript.summary
        chosen = theme.ask_save_path(
            self.root, self.transcript.path.parent, self.transcript.path.stem,
            [("Markdown: transcript, summary and questions", "*.md")] + ([export.WORD_WITH_TRANSCRIPT] if summary else [])
            + [("Word: transcript only", "*.docx"), ("Text: transcript only", "*.txt"),
               ("Subtitles (SubRip)", "*.srt"), ("Subtitles (WebVTT)", "*.vtt")])
        if chosen is None:
            return
        path, kind = chosen
        ext = path.suffix.lower()
        note = ""
        if ext in (".srt", ".vtt"):
            answer = simpledialog.askstring(
                "Recording start",
                "What time did the recording start? (HH:MM or HH:MM:SS)\n"
                "Subtitle times are counted from this moment.",
                initialvalue=f"{started:%H:%M:%S}", parent=self.root)
            if answer is None:
                return
            origin = parse_clock(answer, started)
            if origin is None:
                self._set_status(f"Not a valid time: {answer!r}. Use HH:MM or HH:MM:SS", "err", always=True)
                return
            content, count = self.transcript.subtitles(origin, ext[1:])
            note = f" ({count} subtitles)"
        elif kind == export.WORD_WITH_TRANSCRIPT[0]:
            language = self.summary_language
            content = export.to_docx(summary[1], export.title(started, language), self.transcript.export_lines(),
                                     export.transcript_section(language))
        elif ext in (".docx", ".txt"):
            heading = export.title(started, self.transcript.main_language(), transcript=True)
            to = export.transcript_to_docx if ext == ".docx" else export.transcript_to_text
            content = to(self.transcript.export_lines(), heading)
        else:
            content = self.transcript.render()
        try:
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content, encoding="utf-8")
        except OSError as e:
            self._set_status(f"Failed to save: {e}", "err", always=True)
            return
        self._set_status(f"Saved: {path}{note}", "muted", always=True)

    def _copy_transcript(self) -> None:
        if not self.transcript.records:
            self._set_status("Nothing to copy yet", "muted", always=True)
            return
        heading = export.title(self.transcript.started, self.transcript.main_language(), transcript=True)
        chat = export.transcript_to_chat(self.transcript.export_lines(), heading)
        self.root.clipboard_clear()
        self.root.clipboard_append(chat)
        self._set_status(*export.copied_message(chat), always=True)

    def _open_ask(self) -> None:
        self.open_ask(None)

    def open_ask(self, meeting: history.Meeting | None) -> None:
        """The Ask window of this meeting (None) or of a past one (from the history window)."""
        key = None if meeting is None else meeting.path
        win = self.ask_wins.get(key)
        if win is not None and win.alive():
            win.show()
            return
        self.ask_wins[key] = win = AskWindow(self, meeting)
        self.windows.append(win.win)

    def _answer_done(self, kind: str, meeting: Path | None, payload) -> None:
        """An answer is saved in its meeting's transcript file, also when its Ask window was closed meanwhile."""
        saved = None
        if kind == "answer":
            path = self.transcript.path if meeting is None else meeting
            try:
                if meeting is None:
                    self.transcript.add_answer(payload.to_markdown())
                    self.transcript.save()
                else:
                    history.add_answer(meeting, payload.to_markdown())
                saved = f"Saved in {path.name}"
            except OSError as e:
                saved = f"Answer not saved: {e}"
            if meeting is not None and self.history is not None and self.history.alive():
                self.history.reload(keep=meeting)
        win = self.ask_wins.get(meeting)
        if win is not None and win.alive():
            win.on_message(kind, payload, saved)
        elif kind == "answer_failed":
            self._set_status(f"Question not answered: {payload[1]}", "err", always=True)

    def show_line(self, line_id: int) -> bool:
        """Scrolls the captions to a line and highlights it for a moment. False when it's no longer on screen."""
        mark = f"line{line_id}"
        if line_id < self.view_floor or mark not in self.text.mark_names():
            return False
        self.text.tag_remove("flash", "1.0", "end")
        self.text.tag_add("flash", mark, f"{mark} lineend")
        self.text.see(mark)
        self.root.after(2500, lambda: self.text.tag_remove("flash", "1.0", "end"))
        return True

    def _open_history(self) -> None:
        if self.history is not None and self.history.alive():
            self.history.win.lift()
            self.history.win.focus_force()
            return
        self.history = HistoryWindow(self)
        self.windows.append(self.history.win)

    def show_summary_window(self, text: str, started: dt.datetime, language: str | None, transcript: Path):
        """A summary window (summary_window.py) that follows "Always on top" and reports to the status line."""
        win = open_summary_window(self.root, self.theme, text, started, language, transcript,
                                  topmost=self.pin_var.get(),
                                  set_status=lambda message, color: self._set_status(message, color, always=True))
        self.windows.append(win)
        return win

    # ---- alert words
    def _alert(self, ts: dt.datetime, label: str, text: str, word: str) -> None:
        self._set_status(f"⚑ “{word}” said at {ts:%H:%M:%S}", "warn", always=True)
        if self.root.focus_displayof() is not None:
            return  # the window is in front: the highlight is enough
        if time.monotonic() - self.last_alert_at < ALERT_COOLDOWN:
            return
        self.last_alert_at = time.monotonic()
        body = text if len(text) <= 200 else text[:197] + "…"
        alerts.notify(f"“{word}” — {label}, {ts:%H:%M}", body)

    def _edit_alert_words(self) -> None:
        from tkinter import simpledialog
        answer = simpledialog.askstring(
            "Alert words",
            "Words to watch for, separated by commas (e.g. your name).\n"
            "When someone else says one, it is highlighted and a desktop notification appears.\n"
            "Leave it empty to turn alerts off.",
            initialvalue=", ".join(self.alert_words), parent=self.root)
        if answer is None:
            return
        self.alert_words = alerts.parse_words(answer)
        self.alert_re = alerts.compile_words(self.alert_words)
        if self.alert_words:
            self._set_status(f"Alert words: {', '.join(self.alert_words)}", "muted", always=True)
        else:
            self._set_status("Alerts off", "muted", always=True)

    # ---- summary
    def _toggle_auto_summary(self) -> None:
        if self.auto_var.get():
            self._set_status(f"Auto summary on: after {self.args.auto_summary_after:g} min of silence, and on close",
                             "muted", always=True)
        else:
            self._set_status("Auto summary off", "muted", always=True)

    def _summarizing(self) -> bool:
        return str(self.summary_btn.cget("state")) == "disabled"

    def _new_lines(self) -> int:
        """Lines said since the latest summary was asked for."""
        return self.transcript.next_id - self.summary_upto

    def _auto_summary_due(self) -> bool:
        """Auto summary on, enough new lines since the last summary, and nobody spoke for a while.
        Not while paused: a pause is a break, not the end of the meeting."""
        if not self.auto_var.get() or self.closing or self.paused.is_set() or self._summarizing():
            return False
        if self._new_lines() < AUTO_SUMMARY_MIN_LINES:
            return False
        return time.monotonic() - self.last_line_at >= self.args.auto_summary_after * 60

    def _auto_summary_check(self) -> None:
        if self.closed:
            return
        self.root.after(10_000, self._auto_summary_check)
        if self._auto_summary_due():
            self._summarize(auto=True)

    def _summary_before_close(self) -> bool:
        """On closing with auto summary on: summarize the new lines first, then close.
        Returns False when the window can close right away (closing a second time always can)."""
        if self.closing or not self.auto_var.get():
            return False
        if not self._summarizing() and self._new_lines() < AUTO_SUMMARY_MIN_LINES:
            return False
        self.closing = True
        if not self._summarizing():
            self._summarize(auto=True)
        self._set_status("Summarizing before closing… close again to quit now", "warn", always=True)
        return True

    def _summarize(self, auto: bool = False) -> None:
        if self._summarizing():
            return  # a summary is already being made
        lines = self.transcript.next_id
        if not lines:
            self._set_status("Nothing to summarize yet", "muted", always=True)
            return
        language = self.args.summary_lang
        if language == "auto":
            language = self.transcript.main_language()
        self.summary_btn.configure(state="disabled")
        self.summary_upto = lines
        self.summary_language = language
        what = "Auto summary: summarizing" if auto else "Summarizing"
        self._set_status(f"{what} {lines} lines with {self.summarizer.describe()}…", "muted", always=True)
        SummaryWorker(self.summarizer, self.transcript.for_summary(), self.ui_q, language).start()

    def _summary_done(self, text: str) -> None:
        self.transcript.set_summary(text, f"made at {dt.datetime.now():%H:%M}, {self.summarizer.describe()}")
        self.summary_btn.configure(state="normal")
        self._set_status(f"Summary ready — saved in {self.transcript.path.name}", "muted", always=True)
        self._autosave()
        if self.closing:
            self._quit()
            return
        if self.summary_win is not None and self.summary_win.winfo_exists():
            self.summary_win.destroy()
        self.summary_win = self.show_summary_window(text, self.transcript.started, self.summary_language,
                                                    self.transcript.path)

    def _summary_failed(self, error: str) -> None:
        self.summary_btn.configure(state="normal")
        self._set_status(f"Summary failed: {error}", "err", always=True)
        if self.closing:
            print(f"Summary before closing failed: {error}", file=sys.stderr)  # the log, when started from the menu
            self._quit()
