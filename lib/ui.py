"""The Tkinter window: captions, drafts, translations, search, summary and transcript saving."""

from __future__ import annotations

import datetime as dt
import queue
import re
import threading
from pathlib import Path

from .common import IS_MAC
from .config import DEFAULT_GEOMETRY, load_window_state, restore_geometry, save_window_state
from .subtitles import parse_clock, subtitle_cues, to_srt, to_vtt
from .summary import Summarizer, SummaryWorker
from .translation import Translator


class App:
    COLORS = {"bg": "#111418", "fg": "#e8eaed", "muted": "#8a919a",
              "Participant": "#7dd3fc", "Me": "#86efac", "tr": "#fbbf24", "err": "#f87171",
              "field": "#1f242b", "match": "#4a3f12", "match_cur": "#b45309"}

    def __init__(self, root, args, ui_q: queue.Queue, tr_q: queue.Queue, translator: Translator,
                 paused: threading.Event, summarizer: Summarizer, on_close):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.root, self.args, self.ui_q, self.tr_q, self.translator = root, args, ui_q, tr_q, translator
        self.paused = paused
        self.summarizer = summarizer
        self.summary: tuple[dt.datetime, str] | None = None  # (when, Markdown) of the latest summary
        self.summary_win = None
        self.last_status = ("Starting…", "muted")  # restored when resuming from pause
        self.records: dict[int, dict] = {}
        self.next_id = 0
        self.view_floor = 0  # lines with id < this have been cleared from the screen
        self.drafts: dict[str, tuple[int, str]] = {}  # label -> (seg_id, temporary text)
        self.finalized: dict[str, int] = {}           # label -> next seg_id that may still be shown as a draft

        out_dir = Path(args.out_dir).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        self.started = dt.datetime.now()
        self.transcript_path = out_dir / f"meeting-{self.started:%Y-%m-%d_%H%M}.md"

        state = load_window_state() if args.geometry is None else {}
        root.title("Live Caption")
        geometry = args.geometry or restore_geometry(state.get("geometry"), root) or DEFAULT_GEOMETRY
        root.geometry(geometry)
        self._measure_frame_offset(geometry)
        root.configure(bg=self.COLORS["bg"])
        on_top = state.get("always_on_top", True) is not False
        root.attributes("-topmost", on_top)
        try:
            root.attributes("-alpha", args.opacity)
        except tk.TclError:
            pass

        def close() -> None:
            save_window_state({"geometry": self._window_geometry(), "always_on_top": self.pin_var.get()})
            on_close()
        root.protocol("WM_DELETE_WINDOW", close)

        bar = tk.Frame(root, bg=self.COLORS["bg"])
        bar.pack(fill="x", padx=8, pady=(6, 0))

        self.status = tk.Label(bar, text="Starting…", anchor="w", bg=self.COLORS["bg"], fg=self.COLORS["muted"])
        self.status.pack(side="left", fill="x", expand=True)

        self.pause_btn = ttk.Button(bar, text="Pause", command=self._toggle_pause)
        self.pause_btn.pack(side="left", padx=3)

        self.tr_var = tk.BooleanVar(value=translator.enabled)
        tk.Checkbutton(bar, text=f"Translate → {translator.target}", variable=self.tr_var,
                       command=self._toggle_translate, bg=self.COLORS["bg"], fg=self.COLORS["fg"],
                       selectcolor=self.COLORS["bg"], activebackground=self.COLORS["bg"],
                       activeforeground=self.COLORS["fg"], highlightthickness=0).pack(side="left", padx=6)

        self.pin_var = tk.BooleanVar(value=on_top)
        tk.Checkbutton(bar, text="Always on top", variable=self.pin_var,
                       command=lambda: root.attributes("-topmost", self.pin_var.get()),
                       bg=self.COLORS["bg"], fg=self.COLORS["fg"], selectcolor=self.COLORS["bg"],
                       activebackground=self.COLORS["bg"], activeforeground=self.COLORS["fg"],
                       highlightthickness=0).pack(side="left", padx=6)

        self.summary_btn = ttk.Button(bar, text="Summary", command=self._summarize)
        self.summary_btn.pack(side="left", padx=3)
        ttk.Button(bar, text="Save as…", command=self._save_as).pack(side="left", padx=3)
        ttk.Button(bar, text="Clear", command=self._clear).pack(side="left", padx=3)

        self._build_search_bar(tk, ttk)  # shown with Ctrl+F

        body = self.body = tk.Frame(root, bg=self.COLORS["bg"])
        body.pack(fill="both", expand=True, padx=8, pady=6)
        self.text = tk.Text(body, wrap="word", bg=self.COLORS["bg"], fg=self.COLORS["fg"],
                            insertbackground=self.COLORS["fg"], relief="flat", padx=8, pady=6,
                            font=("Sans", args.font_size), state="disabled", highlightthickness=0)
        scroll = ttk.Scrollbar(body, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)

        small = ("Sans", max(8, args.font_size - 3))
        self.text.tag_configure("time", foreground=self.COLORS["muted"], font=small)
        self.text.tag_configure("Participant", foreground=self.COLORS["Participant"], font=("Sans", args.font_size, "bold"))
        self.text.tag_configure("Me", foreground=self.COLORS["Me"], font=("Sans", args.font_size, "bold"))
        self.text.tag_configure("tr", foreground=self.COLORS["tr"], font=("Sans", args.font_size, "italic"))
        self.text.tag_configure("err", foreground=self.COLORS["err"])
        self.text.tag_configure("info", foreground=self.COLORS["muted"], font=small)
        self.text.tag_configure("draft", foreground=self.COLORS["muted"], font=("Sans", args.font_size, "italic"))
        self.text.tag_configure("match", background=self.COLORS["match"])
        self.text.tag_configure("match_cur", background=self.COLORS["match_cur"], foreground="#ffffff")
        # Draft area = from the "draft" mark to the end of the text. Final lines are always inserted right before it.
        self.text.mark_set("draft", "end-1c")

        # Keyboard shortcuts (window must be focused). Cmd on macOS, Ctrl elsewhere.
        mod = "Command" if IS_MAC else "Control"
        shortcuts = {"p": self._toggle_pause, "t": self._toggle_translate_key,
                     "s": self._save_as, "l": self._clear, "m": self._summarize, "f": self._open_search}
        for key, fn in shortcuts.items():
            for k in (key, key.upper()):  # also works with Caps Lock on
                for widget in (root, self.search_entry):  # on the entry too, so its own Ctrl+key editing keys don't run
                    widget.bind(f"<{mod}-{k}>", lambda _e, fn=fn: (fn(), "break")[1])
        root.bind("<F3>", lambda _e: self._search_step(1))
        root.bind("<Shift-F3>", lambda _e: self._search_step(-1))
        root.bind("<Escape>", lambda _e: self._close_search())

        if args._config_path:
            self._append([(f"ℹ Settings loaded from {args._config_path}\n", "info")])
        root.after(30, self._poll)

    # ---- window position
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

    # ---- events from other threads
    def _poll(self) -> None:
        try:
            while True:
                msg = self.ui_q.get_nowait()
                kind = msg[0]
                if kind == "status":
                    self._set_status(msg[1], "muted")
                elif kind == "error":
                    self._set_status(msg[1], "err", always=True)
                    self._append([(f"⚠ {msg[1]}\n", "err")])
                elif kind == "info":  # notice only (e.g. recorder fallback): keep the status line as it is
                    self._append([(f"ℹ {msg[1]}\n", "info")])
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
                    self._show_summary(msg[1])
                elif kind == "summary_failed":
                    self.summary_btn.configure(state="normal")
                    self._set_status(f"Summary failed: {msg[1]}", "err", always=True)
        except queue.Empty:
            pass
        self.root.after(30, self._poll)

    def _set_status(self, text: str, color: str, always: bool = False) -> None:
        """Show a status message. While paused, only errors and replies to the user's own actions
        (always=True) are shown, next to the pause notice; routine status updates wait for Resume."""
        self.last_status = (text, color)
        if not self.paused.is_set():
            self.status.configure(text=text, fg=self.COLORS[color])
        elif always:
            self.status.configure(text=f"⏸ Paused · {text}", fg=self.COLORS[color])

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
        self._search_refresh()

    # ---- drafts (temporary text while someone is still speaking)
    def _set_draft(self, label: str, seg_id: int, text: str) -> None:
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
            self.text.insert("end", f"… {label}: {text}\n", "draft")
        self.text.configure(state="disabled")
        if at_bottom:
            self.text.see("end")

    def _add_line(self, ts: dt.datetime, label: str, text: str, lang: str, seg_id: int,
                  end: dt.datetime | None = None) -> None:
        line_id = self.next_id
        self.next_id += 1
        self.records[line_id] = {"ts": ts, "end": end or ts, "label": label, "text": text, "lang": lang, "tr": None}
        self._append([(f"[{ts:%H:%M:%S}] ", "time"), (f"{label}: ", label), (f"{text}\n", None)],
                     mark=f"tr{line_id}")
        self._finish_draft(label, seg_id)
        self._render_drafts()
        if self.translator.enabled:
            self.tr_q.put((line_id, text, lang))
        self._autosave()

    def _add_translation(self, line_id: int, translation: str) -> None:
        rec = self.records.get(line_id)
        if rec is None:
            return
        rec["tr"] = translation
        mark = f"tr{line_id}"
        if line_id >= self.view_floor and mark in self.text.mark_names():
            at_bottom = self.text.yview()[1] > 0.98
            self.text.configure(state="normal")
            self.text.mark_gravity("draft", "right")  # translations must not end up in the draft area
            self.text.insert(mark, f"      ↳ {translation}\n", "tr")
            self.text.mark_unset(mark)
            self.text.configure(state="disabled")
            if at_bottom:  # don't yank the view down while the user is reading older lines
                self.text.see("end")
            self._search_refresh()
        self._autosave()

    # ---- search (Ctrl+F)
    def _build_search_bar(self, tk, ttk) -> None:
        c = self.COLORS
        self.search_bar = tk.Frame(self.root, bg=c["bg"])
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(self.search_bar, textvariable=self.search_var, bg=c["field"], fg=c["fg"],
                                     insertbackground=c["fg"], relief="flat", highlightthickness=1,
                                     highlightbackground=c["muted"], highlightcolor=c["Participant"])
        self.search_entry.pack(side="left", fill="x", expand=True, ipady=2)
        self.search_count = tk.Label(self.search_bar, text="", width=11, bg=c["bg"], fg=c["muted"])
        self.search_count.pack(side="left", padx=6)
        ttk.Button(self.search_bar, text="↑", width=3, command=lambda: self._search_step(-1)).pack(side="left")
        ttk.Button(self.search_bar, text="↓", width=3, command=lambda: self._search_step(1)).pack(side="left", padx=3)
        ttk.Button(self.search_bar, text="✕", width=3, command=self._close_search).pack(side="left")
        self.search_entry.bind("<Return>", lambda _e: self._search_step(1))
        self.search_entry.bind("<Shift-Return>", lambda _e: self._search_step(-1))
        self.search_entry.bind("<Escape>", lambda _e: (self._close_search(), "break")[1])
        self.search_var.trace_add("write", lambda *_: self._search_refresh(jump=True))
        self.matches: list[tuple[str, str]] = []  # (start, end) text indices of every match
        self.match_idx = -1
        self.search_visible = False  # not winfo_ismapped(): that only turns true after Tk redraws

    def _search_open(self) -> bool:
        return self.search_visible

    def _open_search(self) -> None:
        if not self.search_visible:
            self.search_visible = True
            self.search_bar.pack(fill="x", padx=8, pady=(6, 0), before=self.body)
        self.search_entry.focus_set()
        self.search_entry.select_range(0, "end")
        self._search_refresh(jump=True)

    def _close_search(self) -> None:
        if not self._search_open():
            return
        self.search_visible = False
        self.search_bar.pack_forget()
        self.text.tag_remove("match", "1.0", "end")
        self.text.tag_remove("match_cur", "1.0", "end")
        self.matches, self.match_idx = [], -1
        if "search_cur" in self.text.mark_names():
            self.text.mark_unset("search_cur")
        self.root.focus_set()
        self.text.see("end")  # back to the live captions

    def _search_refresh(self, jump: bool = False) -> None:
        """Highlight every match of the query in the final text (drafts are still changing, so they're skipped).
        jump=True (the query changed): select the newest match and scroll to it. Otherwise (new lines arrived):
        keep the current match selected and the view where it is."""
        if not self._search_open():
            return
        current = "search_cur" in self.text.mark_names() and 0 <= self.match_idx < len(self.matches)
        self.text.tag_remove("match", "1.0", "end")
        self.text.tag_remove("match_cur", "1.0", "end")
        query = self.search_var.get()
        self.matches = []
        if query:
            count, idx = self.tk.IntVar(), "1.0"
            while pos := self.text.search(query, idx, stopindex="draft", nocase=True, count=count):
                end = f"{pos}+{count.get()}c"
                self.matches.append((pos, end))
                self.text.tag_add("match", pos, end)
                idx = end
        if not self.matches:
            self.match_idx = -1
        elif jump or not current:
            self.match_idx = len(self.matches) - 1
        else:
            cur = self.text.index("search_cur")
            self.match_idx = next((i for i, (a, _) in enumerate(self.matches) if self.text.compare(a, "==", cur)),
                                  min(self.match_idx, len(self.matches) - 1))
        self._show_match(scroll=jump)

    def _search_step(self, step: int):
        if not self._search_open():
            return None
        if self.matches:
            self.match_idx = (self.match_idx + step) % len(self.matches)
            self._show_match(scroll=True)
        return "break"

    def _show_match(self, scroll: bool) -> None:
        self.text.tag_remove("match_cur", "1.0", "end")
        if self.match_idx < 0:
            text = "No matches" if self.search_var.get() else ""
            self.search_count.configure(text=text, fg=self.COLORS["err" if text else "muted"])
            return
        start, end = self.matches[self.match_idx]
        self.text.tag_add("match_cur", start, end)
        # A mark moves with the text when lines or translations are inserted above it, so the same match
        # stays selected after the next refresh (plain indices like "4.29" would then point elsewhere).
        self.text.mark_set("search_cur", start)
        self.search_count.configure(text=f"{self.match_idx + 1} / {len(self.matches)}", fg=self.COLORS["muted"])
        if scroll:
            self.text.see(start)

    # ---- buttons
    def _toggle_translate(self) -> None:
        self.translator.enabled = self.tr_var.get()

    def _toggle_translate_key(self) -> None:
        self.tr_var.set(not self.tr_var.get())
        self._toggle_translate()

    def _toggle_pause(self) -> None:
        mod = "Cmd" if IS_MAC else "Ctrl"
        if self.paused.is_set():
            self.paused.clear()
            self.pause_btn.configure(text="Pause")
            self.status.configure(text=self.last_status[0], fg=self.COLORS[self.last_status[1]])
            self._append([(f"▶ Resumed at {dt.datetime.now():%H:%M:%S}\n", "info")])
        else:
            self.paused.set()
            self.pause_btn.configure(text="Resume")
            self.status.configure(text=f"⏸ Paused — nothing is being transcribed ({mod}+P to resume)",
                                  fg=self.COLORS["tr"])
            self._append([(f"⏸ Paused at {dt.datetime.now():%H:%M:%S}\n", "info")])

    def _clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.mark_set("draft", "end-1c")
        self.text.configure(state="disabled")
        self.view_floor = self.next_id  # only the screen is cleared; the transcript file stays intact
        self._render_drafts()
        self._search_refresh()

    def _save_as(self) -> None:
        from tkinter import filedialog, simpledialog
        path = filedialog.asksaveasfilename(
            defaultextension=".md", initialfile=self.transcript_path.name,
            filetypes=[("Markdown", "*.md"), ("Text", "*.txt"),
                       ("Subtitles (SubRip)", "*.srt"), ("Subtitles (WebVTT)", "*.vtt")])
        if not path:
            return
        ext = Path(path).suffix.lower()
        if ext in (".srt", ".vtt"):
            answer = simpledialog.askstring(
                "Recording start",
                "What time did the recording start? (HH:MM or HH:MM:SS)\n"
                "Subtitle times are counted from this moment.",
                initialvalue=f"{self.started:%H:%M:%S}", parent=self.root)
            if answer is None:
                return
            origin = parse_clock(answer, self.started)
            if origin is None:
                self._set_status(f"Not a valid time: {answer!r}. Use HH:MM or HH:MM:SS", "err", always=True)
                return
            cues = subtitle_cues(self.records.values(), origin)
            content = to_srt(cues) if ext == ".srt" else to_vtt(cues)
        else:
            cues, content = None, self._render()
        try:
            Path(path).write_text(content, encoding="utf-8")
        except OSError as e:
            self._set_status(f"Failed to save: {e}", "err", always=True)
            return
        note = f" ({len(cues)} subtitles)" if cues is not None else ""
        self._set_status(f"Saved: {path}{note}", "muted", always=True)

    # ---- summary
    def _summarize(self) -> None:
        if str(self.summary_btn.cget("state")) == "disabled":
            return  # a summary is already being made
        lines = [f"[{r['ts']:%H:%M:%S}] {r['label']}: {r['text']}" for r in self.records.values()]
        if not lines:
            self._set_status("Nothing to summarize yet", "muted", always=True)
            return
        self.summary_btn.configure(state="disabled")
        self._set_status(f"Summarizing {len(lines)} lines with {self.summarizer.describe()}…", "muted", always=True)
        SummaryWorker(self.summarizer, "\n".join(lines), self.ui_q).start()

    def _show_summary(self, text: str) -> None:
        tk = self.tk
        self.summary = (dt.datetime.now(), text)
        self.summary_btn.configure(state="normal")
        self._set_status(f"Summary ready — saved in {self.transcript_path.name}", "muted", always=True)
        self._autosave()

        if self.summary_win is not None and self.summary_win.winfo_exists():
            self.summary_win.destroy()
        win = self.summary_win = tk.Toplevel(self.root)
        win.title("Meeting summary")
        win.geometry("640x480")
        win.configure(bg=self.COLORS["bg"])
        win.attributes("-topmost", self.pin_var.get())
        box = tk.Text(win, wrap="word", bg=self.COLORS["bg"], fg=self.COLORS["fg"], relief="flat",
                      padx=10, pady=8, font=("Sans", self.args.font_size), highlightthickness=0)
        box.insert("1.0", text)
        box.configure(state="disabled")  # still selectable, so parts can be copied

        def copy() -> None:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self._set_status("Summary copied to the clipboard", "muted", always=True)

        from tkinter import ttk
        ttk.Button(win, text="Copy", command=copy).pack(side="bottom", anchor="e", padx=8, pady=6)
        box.pack(fill="both", expand=True)

    # ---- transcript
    def _render(self) -> str:
        # Month names are hard-coded so the header doesn't depend on the system locale.
        months = ["January", "February", "March", "April", "May", "June", "July",
                  "August", "September", "October", "November", "December"]
        d = self.started
        lines = [f"# Meeting transcript — {months[d.month - 1]} {d.day}, {d.year}, {d:%H:%M}", ""]
        if self.summary:
            when, text = self.summary
            lines += [f"## Summary (made at {when:%H:%M}, {self.summarizer.describe()})", "", text, "",
                      "## Transcript", ""]
        for rec in self.records.values():
            lines.append(f"- **[{rec['ts']:%H:%M:%S}] {rec['label']}:** {rec['text']}")
            if rec["tr"]:
                lines.append(f"  - _{rec['tr']}_")
        return "\n".join(lines) + "\n"

    def _autosave(self) -> None:
        try:
            self.transcript_path.write_text(self._render(), encoding="utf-8")
        except OSError as e:
            self._set_status(f"Failed to save transcript: {e}", "err", always=True)
