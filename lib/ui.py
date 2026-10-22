"""The Tkinter window: captions, drafts, translations, search, summary and transcript saving."""

from __future__ import annotations

import collections
import datetime as dt
import queue
import re
import sys
import threading
import time
from pathlib import Path

from . import alerts
from .common import IS_LINUX, IS_MAC
from .config import DEFAULT_GEOMETRY, load_window_state, restore_geometry, save_window_state
from .subtitles import parse_clock, subtitle_cues, to_srt, to_vtt
from .summary import Summarizer, SummaryWorker
from .translation import Translator

# First installed family wins; otherwise Tk's default UI font is used.
UI_FONTS = ["Inter", "Noto Sans", "Cantarell", "Ubuntu", "Segoe UI", "SF Pro Text", "Helvetica Neue", "DejaVu Sans"]
NEW_SPEAKER_GAP = dt.timedelta(minutes=2)  # same speaker after this long gets a new header (with its time)
AUTO_SUMMARY_MIN_LINES = 5  # fewer new lines than this aren't worth an automatic summary
ALERT_COOLDOWN = 20  # seconds: at most one desktop notification in this time, however often the word comes


class App:
    COLORS = {"bg": "#0f1216", "fg": "#e6e8eb", "muted": "#8b949e", "line": "#232931",
              "field": "#1b2027", "hover": "#252c35", "press": "#2f3742",
              "Participant": "#7dd3fc", "Me": "#86efac", "tr": "#fcd34d", "err": "#f87171",
              "ok": "#4ade80", "warn": "#fbbf24", "accent": "#1e3a4c", "accent_fg": "#bae6fd",
              "match": "#4a3f12", "match_cur": "#b45309", "alert": "#7c2d12"}

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
        self.summary_upto = 0                   # lines with id < this are in the latest summary (or the one being made)
        self.last_line_at = time.monotonic()    # for the automatic summary after a silence
        self.closing = False                    # the window was closed; it goes once the last summary is done
        self.on_close = on_close
        self.alert_words = alerts.parse_words(args.alert)
        self.alert_re = alerts.compile_words(self.alert_words)
        self.last_alert_at = float("-inf")      # when the last desktop notification was shown
        self.last_status = ("Starting…", "muted")  # restored when resuming from pause
        self.ready = False                          # model loaded: the status dot turns green
        self.records: dict[int, dict] = {}
        self.next_id = 0
        self.view_floor = 0  # lines with id < this have been cleared from the screen
        self.drafts: dict[str, tuple[int, str]] = {}  # label -> (seg_id, temporary text)
        self.finalized: dict[str, int] = {}           # label -> next seg_id that may still be shown as a draft
        self.last_speaker: str | None = None          # consecutive lines of one speaker share a header
        self.last_ts: dt.datetime | None = None

        out_dir = Path(args.out_dir).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        self.started = dt.datetime.now()
        self.transcript_path = out_dir / f"meeting-{self.started:%Y-%m-%d_%H%M}.md"

        c = self.COLORS
        state = load_window_state() if args.geometry is None else {}
        root.title("Live Caption")
        geometry = args.geometry or restore_geometry(state.get("geometry"), root) or DEFAULT_GEOMETRY
        root.geometry(geometry)
        root.minsize(420, 160)
        self._measure_frame_offset(geometry)
        root.configure(bg=c["bg"])
        on_top = state.get("always_on_top", True) is not False
        root.attributes("-topmost", on_top)
        try:
            root.attributes("-alpha", args.opacity)
        except tk.TclError:
            pass

        def close() -> None:
            save_window_state({"geometry": self._window_geometry(), "always_on_top": self.pin_var.get(),
                               "auto_summary": self.auto_var.get(), "alert_words": self.alert_words})
            if not self._summary_before_close():
                on_close()
        root.protocol("WM_DELETE_WINDOW", close)

        self._setup_fonts_and_style(tk, ttk)
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
        self.dot = tk.Label(header, text="●", bg=c["bg"], fg=c["muted"], font=self.f_ui)
        self.dot.pack(side="left")
        self.status = tk.Label(header, text="Starting…", anchor="w", bg=c["bg"], fg=c["muted"], font=self.f_ui)
        self.status.pack(side="left", fill="x", expand=True, padx=(6, 10))

        # ---- the ⋯ menu: everything that isn't needed every minute
        self.tr_var = tk.BooleanVar(value=translator.enabled)
        self.pin_var = tk.BooleanVar(value=on_top)
        auto = args.auto_summary if args.auto_summary is not None else state.get("auto_summary") is True
        self.auto_var = tk.BooleanVar(value=auto)
        self.menu = tk.Menu(root, tearoff=0, bg=c["field"], fg=c["fg"], activebackground=c["hover"],
                            activeforeground=c["fg"], selectcolor=c["fg"], bd=0, relief="flat", font=self.f_ui)
        self.menu.add_checkbutton(label=f"Translate → {translator.target}", variable=self.tr_var,
                                  command=self._toggle_translate, accelerator=f"{mod_label}+T")
        self.menu.add_checkbutton(label="Always on top", variable=self.pin_var, command=self._toggle_on_top)
        self.menu.add_checkbutton(label=f"Auto summary (after {args.auto_summary_after:g} min of silence)",
                                  variable=self.auto_var, command=self._toggle_auto_summary)
        self.menu.add_command(label="Alert words…", command=self._edit_alert_words)
        self.menu.add_separator()
        self.menu.add_command(label="Search…", command=self._open_search, accelerator=f"{mod_label}+F")
        self.menu.add_command(label="Save as…", command=self._save_as, accelerator=f"{mod_label}+S")
        self.menu.add_command(label="Clear screen", command=self._clear, accelerator=f"{mod_label}+L")

        self.divider = tk.Frame(root, bg=c["line"], height=1)
        self.divider.pack(fill="x")
        self._build_search_bar(tk, ttk)  # shown with Ctrl+F, between the divider and the text

        body = self.body = tk.Frame(root, bg=c["bg"])
        body.pack(fill="both", expand=True)
        self.text = tk.Text(body, wrap="word", bg=c["bg"], fg=c["fg"], insertbackground=c["fg"], relief="flat",
                            padx=14, pady=8, font=self.f_body, state="disabled", highlightthickness=0,
                            selectbackground=c["press"], cursor="arrow")
        scroll = ttk.Scrollbar(body, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)

        indent = self.f_body_obj.measure("  ")
        self.text.tag_configure("Participant", foreground=c["Participant"], font=self.f_bold, spacing1=10)
        self.text.tag_configure("Me", foreground=c["Me"], font=self.f_bold, spacing1=10)
        self.text.tag_configure("time", foreground=c["muted"], font=self.f_small)
        self.text.tag_configure("body", lmargin1=indent, lmargin2=indent, spacing1=2)
        self.text.tag_configure("tr", foreground=c["tr"], font=self.f_italic, lmargin1=indent, lmargin2=indent * 3,
                                spacing1=1)
        self.text.tag_configure("err", foreground=c["err"], font=self.f_small, spacing1=8)
        self.text.tag_configure("info", foreground=c["muted"], font=self.f_small, spacing1=8)
        self.text.tag_configure("draft_head", foreground=c["muted"], font=self.f_bold, spacing1=10)
        self.text.tag_configure("draft", foreground=c["muted"], font=self.f_italic, lmargin1=indent, lmargin2=indent,
                                spacing1=2)
        # lmargincolor: a word at the start of a line would otherwise color the indent in front of it too
        self.text.tag_configure("alert", background=c["alert"], foreground="#ffffff", font=self.f_bold,
                                lmargincolor=c["bg"])
        self.text.tag_configure("match", background=c["match"])
        self.text.tag_configure("match_cur", background=c["match_cur"], foreground="#ffffff")
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
            self._note(f"ℹ Settings loaded from {args._config_path}", "info")
        if self.alert_words:
            self._note(f"ℹ Alert words: {', '.join(self.alert_words)}", "info")
        if self.rough_fonts:
            self._note("ℹ Text looks rough because this Python's Tk has no font smoothing (Xft). "
                       "To fix it, recreate .venv with your distribution's Python: see README, step 1.3.", "info")
        root.after(30, self._poll)
        root.after(10_000, self._auto_summary_check)

    def _setup_fonts_and_style(self, tk, ttk) -> None:
        from tkinter import font as tkfont
        c = self.COLORS
        try:  # uv's standalone Python on Linux ships a Tk without Xft: only a bitmap "fixed" font
            self.rough_fonts = IS_LINUX and self.root.tk.call("::tk::pkgconfig", "get", "fontsystem") != "xft"
        except tk.TclError:
            self.rough_fonts = False
        families = set(tkfont.families(self.root))
        family = next((f for f in UI_FONTS if f in families), tkfont.nametofont("TkDefaultFont").actual("family"))
        size = self.args.font_size
        self.f_body_obj = tkfont.Font(self.root, family=family, size=size)
        self.f_body = (family, size)
        self.f_bold = (family, size, "bold")
        self.f_italic = (family, size, "italic")
        self.f_small = (family, max(8, size - 3))
        self.f_ui = (family, max(9, size - 2))

        style = ttk.Style(self.root)
        style.theme_use("clam")  # the only built-in theme whose colors can all be changed
        style.configure("TButton", background=c["field"], foreground=c["fg"], bordercolor=c["field"],
                        lightcolor=c["field"], darkcolor=c["field"], focuscolor=c["field"], borderwidth=0,
                        padding=(10, 4), font=self.f_ui)
        style.map("TButton",
                  background=[("disabled", c["bg"]), ("pressed", c["press"]), ("active", c["hover"])],
                  lightcolor=[("pressed", c["press"]), ("active", c["hover"])],
                  darkcolor=[("pressed", c["press"]), ("active", c["hover"])],
                  foreground=[("disabled", c["muted"])])
        style.configure("Accent.TButton", background=c["accent"], foreground=c["accent_fg"],
                        bordercolor=c["accent"], lightcolor=c["accent"], darkcolor=c["accent"])
        style.map("Accent.TButton", background=[("disabled", c["bg"]), ("active", "#24485e")],
                  lightcolor=[("active", "#24485e")], darkcolor=[("active", "#24485e")])
        style.configure("Small.TButton", padding=(6, 2))
        style.configure("Vertical.TScrollbar", background=c["field"], troughcolor=c["bg"], bordercolor=c["bg"],
                        lightcolor=c["field"], darkcolor=c["field"], arrowcolor=c["muted"], gripcount=0)
        style.map("Vertical.TScrollbar", background=[("active", c["hover"])])

    def _open_menu(self) -> None:
        self.menu.update_idletasks()
        btn = self.menu_btn
        x = btn.winfo_rootx() + btn.winfo_width() - self.menu.winfo_reqwidth()  # right-aligned under ⋯
        self.menu.tk_popup(x, btn.winfo_rooty() + btn.winfo_height() + 2)

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
                    self._note(f"⚠ {msg[1]}", "err")
                elif kind == "info":  # notice only (e.g. recorder fallback): keep the status line as it is
                    self._note(f"ℹ {msg[1]}", "info")
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
                    if self.closing:
                        print(f"Summary before closing failed: {msg[1]}", file=sys.stderr)  # the log, from the menu
                        self.on_close()
                        return
        except queue.Empty:
            pass
        self.root.after(30, self._poll)

    def _set_status(self, text: str, color: str, always: bool = False) -> None:
        """Show a status message. While paused, only errors and replies to the user's own actions
        (always=True) are shown, next to the pause notice; routine status updates wait for Resume."""
        self.last_status = (text, color)
        if text.startswith("Ready"):
            self.ready = True
        if not self.paused.is_set():
            self.status.configure(text=text, fg=self.COLORS[color])
            self._update_dot(error=color == "err")
        elif always:
            self.status.configure(text=f"Paused · {text}", fg=self.COLORS[color])

    def _update_dot(self, error: bool = False) -> None:
        """● red = error, amber = paused, green = listening, grey = still loading."""
        c = self.COLORS
        if self.paused.is_set():
            color = c["warn"]
        elif error:
            color = c["err"]
        else:
            color = c["ok"] if self.ready else c["muted"]
        self.dot.configure(fg=color)

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

    def _note(self, text: str, tag: str) -> None:
        """A notice line (info/error/pause). The next caption starts a new speaker header after it."""
        self._append([(f"{text}\n", tag)])
        self.last_speaker = None
        if self.drafts:
            self._render_drafts()  # a draft below the notice needs its speaker header back

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
            if label != self.last_speaker:  # the same speaker carrying on needs no new header
                self.text.insert("end", f"{label} …\n", "draft_head")
            self.text.insert("end", f"{text}\n", "draft")
        self.text.configure(state="disabled")
        if at_bottom:
            self.text.see("end")

    def _add_line(self, ts: dt.datetime, label: str, text: str, lang: str, seg_id: int,
                  end: dt.datetime | None = None) -> None:
        line_id = self.next_id
        self.next_id += 1
        self.records[line_id] = {"ts": ts, "end": end or ts, "label": label, "text": text, "lang": lang, "tr": None}
        parts = []
        if label != self.last_speaker or self.last_ts is None or ts - self.last_ts > NEW_SPEAKER_GAP:
            parts += [(label, label), (f"   {ts:%H:%M:%S}\n", "time")]
        spans = alerts.find(self.alert_re, text) if label != "Me" else []  # your own voice saying your name: no alert
        pos = 0
        for start, end in spans:  # the alert words highlighted inside the line
            parts += [(text[pos:start], "body"), (text[start:end], ("body", "alert"))]
            pos = end
        parts.append((f"{text[pos:]}\n", "body"))
        self._append(parts, mark=f"tr{line_id}")
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
        rec = self.records.get(line_id)
        if rec is None:
            return
        rec["tr"] = translation
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
            self._search_refresh()
        self._autosave()

    # ---- search (Ctrl+F)
    def _build_search_bar(self, tk, ttk) -> None:
        c = self.COLORS
        self.search_bar = tk.Frame(self.root, bg=c["bg"])
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(self.search_bar, textvariable=self.search_var, bg=c["field"], fg=c["fg"],
                                     insertbackground=c["fg"], relief="flat", highlightthickness=1,
                                     highlightbackground=c["line"], highlightcolor=c["Participant"], font=self.f_ui)
        self.search_entry.pack(side="left", fill="x", expand=True, ipady=3)
        self.search_count = tk.Label(self.search_bar, text="", width=11, bg=c["bg"], fg=c["muted"], font=self.f_ui)
        self.search_count.pack(side="left", padx=6)
        for text, cmd in (("↑", lambda: self._search_step(-1)), ("↓", lambda: self._search_step(1)),
                          ("✕", self._close_search)):
            ttk.Button(self.search_bar, text=text, width=2, style="Small.TButton", command=cmd).pack(side="left",
                                                                                                    padx=(0, 3))
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
            self.search_bar.pack(fill="x", padx=10, pady=(8, 0), before=self.body)
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

    def _toggle_on_top(self) -> None:
        self.root.attributes("-topmost", self.pin_var.get())
        if self.summary_win is not None and self.summary_win.winfo_exists():
            self.summary_win.attributes("-topmost", self.pin_var.get())

    # ---- alert words
    def _alert(self, ts: dt.datetime, label: str, text: str, word: str) -> None:
        self._set_status(f"⚑ \u201c{word}\u201d said at {ts:%H:%M:%S}", "warn", always=True)
        if self.root.focus_displayof() is not None:
            return  # the window is in front: the highlight is enough
        if time.monotonic() - self.last_alert_at < ALERT_COOLDOWN:
            return
        self.last_alert_at = time.monotonic()
        body = text if len(text) <= 200 else text[:197] + "…"
        alerts.notify(f"\u201c{word}\u201d \u2014 {label}, {ts:%H:%M}", body)

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

    def _toggle_auto_summary(self) -> None:
        if self.auto_var.get():
            self._set_status(f"Auto summary on: after {self.args.auto_summary_after:g} min of silence, and on close",
                             "muted", always=True)
        else:
            self._set_status("Auto summary off", "muted", always=True)

    def _toggle_pause(self) -> None:
        mod = "Cmd" if IS_MAC else "Ctrl"
        if self.paused.is_set():
            self.paused.clear()
            self.pause_btn.configure(text="⏸  Pause")
            self.status.configure(text=self.last_status[0], fg=self.COLORS[self.last_status[1]])
            self._update_dot(error=self.last_status[1] == "err")
            self._note(f"▶ Resumed at {dt.datetime.now():%H:%M:%S}", "info")
        else:
            self.paused.set()
            self.pause_btn.configure(text="▶  Resume")
            self.status.configure(text=f"Paused — nothing is being transcribed ({mod}+P to resume)",
                                  fg=self.COLORS["warn"])
            self._update_dot()
            self._note(f"⏸ Paused at {dt.datetime.now():%H:%M:%S}", "info")

    def _clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.mark_set("draft", "end-1c")
        self.text.configure(state="disabled")
        self.view_floor = self.next_id  # only the screen is cleared; the transcript file stays intact
        self.last_speaker = None
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
    def _summarizing(self) -> bool:
        return str(self.summary_btn.cget("state")) == "disabled"

    def _auto_summary_due(self) -> bool:
        """Auto summary on, enough new lines since the last summary, and nobody spoke for a while.
        Not while paused: a pause is a break, not the end of the meeting."""
        if not self.auto_var.get() or self.closing or self.paused.is_set() or self._summarizing():
            return False
        if self.next_id - self.summary_upto < AUTO_SUMMARY_MIN_LINES:
            return False
        return time.monotonic() - self.last_line_at >= self.args.auto_summary_after * 60

    def _auto_summary_check(self) -> None:
        self.root.after(10_000, self._auto_summary_check)
        if self._auto_summary_due():
            self._summarize(auto=True)

    def _summary_before_close(self) -> bool:
        """On closing with auto summary on: summarize the new lines first, then close.
        Returns False when the window can close right away (closing a second time always can)."""
        if self.closing or not self.auto_var.get():
            return False
        if not self._summarizing() and self.next_id - self.summary_upto < AUTO_SUMMARY_MIN_LINES:
            return False
        self.closing = True
        if not self._summarizing():
            self._summarize(auto=True)
        self._set_status("Summarizing before closing… close again to quit now", "warn", always=True)
        return True

    def _summarize(self, auto: bool = False) -> None:
        if self._summarizing():
            return  # a summary is already being made
        lines = [f"[{r['ts']:%H:%M:%S}] {r['label']}: {r['text']}" for r in self.records.values()]
        if not lines:
            self._set_status("Nothing to summarize yet", "muted", always=True)
            return
        language = self.args.summary_lang
        if language == "auto":  # the meeting's main language, weighted by how much was said in each
            spoken = collections.Counter()
            for r in self.records.values():
                spoken[r["lang"]] += len(r["text"])
            language = spoken.most_common(1)[0][0] if spoken else None
        self.summary_btn.configure(state="disabled")
        self.summary_upto = self.next_id
        what = "Auto summary: summarizing" if auto else "Summarizing"
        self._set_status(f"{what} {len(lines)} lines with {self.summarizer.describe()}…", "muted", always=True)
        SummaryWorker(self.summarizer, "\n".join(lines), self.ui_q, language).start()

    def _show_summary(self, text: str) -> None:
        tk = self.tk
        from tkinter import ttk
        c = self.COLORS
        self.summary = (dt.datetime.now(), text)
        self.summary_btn.configure(state="normal")
        self._set_status(f"Summary ready — saved in {self.transcript_path.name}", "muted", always=True)
        self._autosave()
        if self.closing:
            self.on_close()
            return

        if self.summary_win is not None and self.summary_win.winfo_exists():
            self.summary_win.destroy()
        win = self.summary_win = tk.Toplevel(self.root)
        win.title("Meeting summary")
        win.geometry("640x480")
        win.configure(bg=c["bg"])
        win.attributes("-topmost", self.pin_var.get())

        def copy() -> None:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)  # the Markdown as written, ready to paste into chat or a doc
            self._set_status("Summary copied to the clipboard", "muted", always=True)

        buttons = tk.Frame(win, bg=c["bg"])
        buttons.pack(side="bottom", fill="x", padx=12, pady=10)
        ttk.Button(buttons, text="Close", command=win.destroy).pack(side="right")
        ttk.Button(buttons, text="Copy", style="Accent.TButton", command=copy).pack(side="right", padx=(0, 6))
        tk.Frame(win, bg=c["line"], height=1).pack(side="bottom", fill="x")

        box = tk.Text(win, wrap="word", bg=c["bg"], fg=c["fg"], relief="flat", padx=18, pady=12,
                      font=self.f_body, highlightthickness=0, selectbackground=c["press"])
        box.tag_configure("heading", font=self.f_bold, foreground=c["Participant"], spacing1=12, spacing3=4)
        box.tag_configure("bullet", lmargin1=8, lmargin2=24, spacing1=2)
        box.tag_configure("none", lmargin1=8, foreground=c["muted"])
        for line in text.splitlines():  # light Markdown: headings and bullets; the rest as is
            if not line.strip():
                continue  # headings already have space above them
            if m := re.match(r"#{1,6}\s+(.*)", line):
                box.insert("end", m[1] + "\n", "heading")
            elif m := re.match(r"\s*[-*]\s+(.*)", line):
                box.insert("end", f"•  {m[1]}\n", "bullet")
            elif re.fullmatch(r"\s*[-*]\s*", line):  # the prompt writes "-" for an empty section
                box.insert("end", "—\n", "none")
            else:
                box.insert("end", line + "\n")
        box.configure(state="disabled")  # still selectable, so parts can be copied
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
