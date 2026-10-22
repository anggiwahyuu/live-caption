"""Colors, fonts and ttk styles shared by every window (main, summary, history)."""

from __future__ import annotations

from dataclasses import dataclass

from .common import IS_LINUX

COLORS = {"bg": "#0f1216", "fg": "#e6e8eb", "muted": "#8b949e", "line": "#232931",
          "field": "#1b2027", "hover": "#252c35", "press": "#2f3742",
          "Participant": "#7dd3fc", "Me": "#86efac", "tr": "#fcd34d", "err": "#f87171",
          "ok": "#4ade80", "warn": "#fbbf24", "accent": "#1e3a4c", "accent_fg": "#bae6fd",
          "match": "#4a3f12", "match_cur": "#b45309", "alert": "#7c2d12"}
# First installed family wins; otherwise Tk's default UI font is used.
UI_FONTS = ["Inter", "Noto Sans", "Cantarell", "Ubuntu", "Segoe UI", "SF Pro Text", "Helvetica Neue", "DejaVu Sans"]


@dataclass
class Theme:
    colors: dict
    body: tuple
    bold: tuple
    italic: tuple
    small: tuple
    ui: tuple
    body_font: object      # tkinter.font.Font of `body`, for measuring text
    rough_fonts: bool      # Tk without font smoothing (uv's own Python on Linux)

    def entry(self, parent, **kw):
        """The text field look shared by the search bars."""
        import tkinter as tk
        c = self.colors
        return tk.Entry(parent, bg=c["field"], fg=c["fg"], insertbackground=c["fg"], relief="flat",
                        highlightthickness=1, highlightbackground=c["line"], highlightcolor=c["Participant"],
                        font=self.ui, **kw)


def setup(root, font_size: int) -> Theme:
    """Picks the fonts and styles the ttk widgets; call once, with the main window."""
    import tkinter as tk
    from tkinter import font as tkfont
    from tkinter import ttk

    c = COLORS
    try:  # uv's standalone Python on Linux ships a Tk without Xft: only a bitmap "fixed" font
        rough = IS_LINUX and root.tk.call("::tk::pkgconfig", "get", "fontsystem") != "xft"
    except tk.TclError:
        rough = False
    families = set(tkfont.families(root))
    family = next((f for f in UI_FONTS if f in families), tkfont.nametofont("TkDefaultFont").actual("family"))
    theme = Theme(colors=c, body=(family, font_size), bold=(family, font_size, "bold"),
                  italic=(family, font_size, "italic"), small=(family, max(8, font_size - 3)),
                  ui=(family, max(9, font_size - 2)), body_font=tkfont.Font(root, family=family, size=font_size),
                  rough_fonts=rough)

    style = ttk.Style(root)
    style.theme_use("clam")  # the only built-in theme whose colors can all be changed
    style.configure("TButton", background=c["field"], foreground=c["fg"], bordercolor=c["field"],
                    lightcolor=c["field"], darkcolor=c["field"], focuscolor=c["field"], borderwidth=0,
                    padding=(10, 4), font=theme.ui)
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
    # The meeting list of the history window
    style.configure("History.Treeview", background=c["bg"], fieldbackground=c["bg"], foreground=c["fg"],
                    bordercolor=c["line"], lightcolor=c["bg"], darkcolor=c["bg"], font=theme.ui,
                    rowheight=theme.body_font.metrics("linespace") + 8)
    style.map("History.Treeview", background=[("selected", c["accent"])], foreground=[("selected", c["accent_fg"])])
    style.configure("History.Treeview.Heading", background=c["field"], foreground=c["muted"], bordercolor=c["line"],
                    lightcolor=c["field"], darkcolor=c["field"], font=theme.ui, relief="flat")
    style.map("History.Treeview.Heading", background=[("active", c["hover"])])
    return theme
