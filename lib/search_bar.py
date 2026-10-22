"""The Ctrl+F search bar of the main window: highlights every match in the captions."""

from __future__ import annotations


class SearchBar:
    def __init__(self, root, text, before, theme):
        """text: the caption Text widget (final lines end at its "draft" mark).
        before: the widget the bar is shown above."""
        import tkinter as tk
        from tkinter import ttk

        self.root, self.text, self.before, self.theme = root, text, before, theme
        c = theme.colors
        self.frame = tk.Frame(root, bg=c["bg"])
        self.query = tk.StringVar()
        self.entry = theme.entry(self.frame, textvariable=self.query)
        self.entry.pack(side="left", fill="x", expand=True, ipady=3)
        self.count = tk.Label(self.frame, text="", width=11, bg=c["bg"], fg=c["muted"], font=theme.ui)
        self.count.pack(side="left", padx=6)
        for label, cmd in (("↑", lambda: self.step(-1)), ("↓", lambda: self.step(1)), ("✕", self.close)):
            ttk.Button(self.frame, text=label, width=2, style="Small.TButton", command=cmd).pack(side="left",
                                                                                               padx=(0, 3))
        self.entry.bind("<Return>", lambda _e: self.step(1))
        self.entry.bind("<Shift-Return>", lambda _e: self.step(-1))
        self.entry.bind("<Escape>", lambda _e: (self.close(), "break")[1])
        self.query.trace_add("write", lambda *_: self.refresh(jump=True))
        self.matches: list[tuple[str, str]] = []  # (start, end) text indices of every match
        self.index = -1
        self.visible = False  # not winfo_ismapped(): that only turns true after Tk redraws

    def open(self) -> None:
        if not self.visible:
            self.visible = True
            self.frame.pack(fill="x", padx=10, pady=(8, 0), before=self.before)
        self.entry.focus_set()
        self.entry.select_range(0, "end")
        self.refresh(jump=True)

    def close(self) -> None:
        if not self.visible:
            return
        self.visible = False
        self.frame.pack_forget()
        self.text.tag_remove("match", "1.0", "end")
        self.text.tag_remove("match_cur", "1.0", "end")
        self.matches, self.index = [], -1
        if "search_cur" in self.text.mark_names():
            self.text.mark_unset("search_cur")
        self.root.focus_set()
        self.text.see("end")  # back to the live captions

    def refresh(self, jump: bool = False) -> None:
        """Highlight every match of the query in the final text (drafts are still changing, so they're skipped).
        jump=True (the query changed): select the newest match and scroll to it. Otherwise (new lines arrived):
        keep the current match selected and the view where it is."""
        if not self.visible:
            return
        text = self.text
        current = "search_cur" in text.mark_names() and 0 <= self.index < len(self.matches)
        text.tag_remove("match", "1.0", "end")
        text.tag_remove("match_cur", "1.0", "end")
        query = self.query.get()
        self.matches = []
        if query:
            import tkinter as tk
            count, idx = tk.IntVar(), "1.0"
            while pos := text.search(query, idx, stopindex="draft", nocase=True, count=count):
                end = f"{pos}+{count.get()}c"
                self.matches.append((pos, end))
                text.tag_add("match", pos, end)
                idx = end
        if not self.matches:
            self.index = -1
        elif jump or not current:
            self.index = len(self.matches) - 1
        else:
            cur = text.index("search_cur")
            self.index = next((i for i, (a, _) in enumerate(self.matches) if text.compare(a, "==", cur)),
                              min(self.index, len(self.matches) - 1))
        self._show(scroll=jump)

    def step(self, step: int):
        if not self.visible:
            return None
        if self.matches:
            self.index = (self.index + step) % len(self.matches)
            self._show(scroll=True)
        return "break"

    def _show(self, scroll: bool) -> None:
        c = self.theme.colors
        self.text.tag_remove("match_cur", "1.0", "end")
        if self.index < 0:
            message = "No matches" if self.query.get() else ""
            self.count.configure(text=message, fg=c["err" if message else "muted"])
            return
        start, end = self.matches[self.index]
        self.text.tag_add("match_cur", start, end)
        # A mark moves with the text when lines or translations are inserted above it, so the same match
        # stays selected after the next refresh (plain indices like "4.29" would then point elsewhere).
        self.text.mark_set("search_cur", start)
        self.count.configure(text=f"{self.index + 1} / {len(self.matches)}", fg=c["muted"])
        if scroll:
            self.text.see(start)
