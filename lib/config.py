"""Command-line options, the config.toml settings file and the remembered window state."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from .common import IS_WINDOWS
from .transcriber import load_vocab, make_transcribe_fn


DEFAULT_GEOMETRY = "760x280"


def config_dir() -> Path:
    """Linux/macOS: $XDG_CONFIG_HOME/live-caption (~/.config/live-caption); Windows: %APPDATA%\\live-caption."""
    if IS_WINDOWS:
        base = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "live-caption"


# Options that only make sense on the command line, never in config.toml.
def log_path() -> Path:
    """Where messages go when the app was started from the menu shortcut (no terminal): see run.sh and main.py."""
    return config_dir() / "live-caption.log"


CLI_ONLY = {"help", "config", "init_config"}


class ConfigError(Exception):
    pass


def config_to_argv(parser: argparse.ArgumentParser, config: dict) -> list[str]:
    """Turn config.toml keys (named like the command-line options) into command-line arguments,
    so argparse checks types and choices exactly as it does for real flags."""
    actions = {a.dest: a for a in parser._actions if a.option_strings and a.dest not in CLI_ONLY}
    argv = []
    for key, value in config.items():
        action = actions.get(key.replace("-", "_"))
        if action is None:
            raise ConfigError(f"unknown setting '{key}'")
        if value == "":
            continue  # empty = not set (the template writes options without a default as "")
        flag = action.option_strings[0]
        if isinstance(action, argparse.BooleanOptionalAction):
            if not isinstance(value, bool):
                raise ConfigError(f"'{key}' must be true or false")
            argv.append(flag if value else f"--no-{flag[2:]}")
        elif key.replace("-", "_") in ("vocab", "alert") and isinstance(value, list):
            argv.append(f"{flag}={', '.join(str(v) for v in value)}")  # vocab = ["Anggi", "OKR"] also works
        elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
            argv.append(f"{flag}={value}")
        else:
            raise ConfigError(f"'{key}' has an unsupported value: {value!r}")
    return argv


def load_config(parser: argparse.ArgumentParser, path: Path) -> list[str]:
    import tomllib
    try:
        with open(path, "rb") as f:
            config = tomllib.load(f)
        argv = config_to_argv(parser, config)

        def fail(message: str):
            raise ConfigError(message)
        parser.error = fail  # check the values now, so the error names config.toml instead of the command line
        try:
            parser.parse_args(argv)
        finally:
            del parser.error
    except (OSError, tomllib.TOMLDecodeError, ConfigError) as e:
        sys.exit(f"Error in {path}: {e}")
    return argv


def config_template(parser: argparse.ArgumentParser) -> str:
    """config.toml with every option commented out at its default value."""
    lines = ["# Live Caption settings. Uncomment a line and change its value.",
             "# Keys are the command-line options without the leading --. Command-line options win over this file.",
             ""]
    for a in parser._actions:
        if not a.option_strings or a.dest in CLI_ONLY:
            continue
        key = a.option_strings[0][2:]
        if a.help:
            lines.append(f"# {a.help}")
        if a.choices:
            lines.append(f"# Choices: {', '.join(a.choices)}")
        if isinstance(a.default, bool):
            value = "true" if a.default else "false"
        elif a.default is None:
            value = '""'
        elif isinstance(a.default, str):
            value = json.dumps(a.default)  # a JSON string is a valid TOML basic string
        else:
            value = str(a.default)
        lines += [f"# {key} = {value}", ""]
    return "\n".join(lines)


def load_window_state() -> dict:
    try:
        state = json.loads((config_dir() / "window.json").read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def save_window_state(state: dict) -> None:
    try:
        config_dir().mkdir(parents=True, exist_ok=True)
        (config_dir() / "window.json").write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass  # only a convenience: never block closing the app


def restore_geometry(geometry, root) -> str | None:
    """Last window size/position, without the position if it would be off-screen (e.g. a monitor was unplugged)."""
    m = re.fullmatch(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", geometry or "")  # as Tk reports it, e.g. 760x280+-20+40
    if not m:
        return None
    w, h, x, y = int(m[1]), int(m[2]), int(m[3]), int(m[4])
    if w < 200 or h < 100:
        return None
    if -w + 50 < x < root.winfo_screenwidth() - 50 and 0 <= y < root.winfo_screenheight() - 50:
        return geometry
    return f"{w}x{h}"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Live captions + optional translation for online meetings.")
    p.add_argument("--config", default=None,
                   help=f"Settings file (default: {config_dir() / 'config.toml'}, used when it exists)")
    p.add_argument("--init-config", action="store_true",
                   help="Write a config.toml with every option and its default value, then exit")
    p.add_argument("--model", default="small",
                   help="tiny | base | small | medium | large-v3-turbo | distil-large-v3 (default: small)")
    p.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    p.add_argument("--compute-type", default="int8",
                   help="Compute type on the GPU. int8 suits GTX 10xx (Pascal) best. (default: int8)")
    p.add_argument("--lang", default="auto", help="Speaker language code: id, en, ... or auto (default)")
    p.add_argument("--source", default="system", choices=["system", "mic", "both"],
                   help="system = other participants, mic = your voice, both = both (use a headset)")
    p.add_argument("--monitor", default=None,
                   help="Record a specific output instead of the default one. Linux: monitor source name "
                        "(pactl list short sources); Windows: part of the speaker/headset name; "
                        "macOS: part of the virtual input name (default: BlackHole)")
    p.add_argument("--translate", action=argparse.BooleanOptionalAction, default=False,
                   help="Enable translation from the start")
    p.add_argument("--translate-to", default="id", help="Translation target language (default: id)")
    p.add_argument("--translator", default="google", choices=["google", "argos"],
                   help="google = online & lightweight, argos = offline (large dependencies)")
    p.add_argument("--threshold", type=float, default=0.008,
                   help="Speech volume threshold (0..1). Raise it if noise gets transcribed.")
    p.add_argument("--silence-ms", type=int, default=700, help="Pause (ms) treated as the end of a sentence")
    p.add_argument("--max-seconds", type=float, default=10.0, help="Maximum length of one sentence before it is locked")
    p.add_argument("--draft-interval", type=float, default=0.25,
                   help="How often the temporary text updates while someone speaks (seconds). 0 = off")
    p.add_argument("--draft-model", default="base",
                   help="Small model used only for draft text, to keep captions live (tiny | base | ...). "
                        "'same' = use the main model (default: base)")
    p.add_argument("--beam-size", type=int, default=1, help="1 = fastest; 3-5 = more accurate but slower")
    p.add_argument("--vocab", default=None,
                   help='Comma-separated names/terms Whisper should recognize, e.g. "Anggi, Kubernetes, OKR"')
    p.add_argument("--vocab-file", default=None,
                   help="Text file with one name/term per line (# starts a comment). Combined with --vocab")
    p.add_argument("--alert", default=None,
                   help='Comma-separated words to watch for, e.g. "Anggi, backend team". When someone else says one, '
                        "it is highlighted and a desktop notification appears (default: as set in the window menu)")
    p.add_argument("--summarizer", default="ollama", choices=["ollama", "claude"],
                   help="Engine for the Summary button. ollama = local & private (default), "
                        "claude = Claude API, better quality but the transcript is sent to Anthropic")
    p.add_argument("--summary-model", default=None,
                   help="Model for summaries (default: qwen2.5:7b for ollama, claude-opus-5-5 for claude)")
    p.add_argument("--summary-lang", default="auto",
                   help="Language of the summary: auto = the meeting's main language (default), or a code like id, en")
    p.add_argument("--auto-summary", action=argparse.BooleanOptionalAction, default=None,
                   help="Make the summary by itself after --auto-summary-after minutes without speech, and when the "
                        "window is closed (default: the tick in the window menu, as you last left it; off at first)")
    p.add_argument("--auto-summary-after", type=float, default=10,
                   help="Minutes without speech before the automatic summary (default: 10). Pause stops the clock")
    p.add_argument("--ollama-url", default="http://localhost:11434", help="Address of the Ollama server")
    p.add_argument("--font-size", type=int, default=13)
    p.add_argument("--opacity", type=float, default=0.93)
    p.add_argument("--geometry", default=None,
                   help=f"Window size/position, e.g. 760x280 or 760x280+0+800 "
                        f"(default: as the window was last closed, else {DEFAULT_GEOMETRY})")
    p.add_argument("--out-dir", default="~/LiveCaption", help="Folder for auto-saved transcripts")
    return p


def parse_args(argv: list[str] | None = None):
    argv = sys.argv[1:] if argv is None else argv
    p = build_parser()
    first, _ = p.parse_known_args(argv)  # only to find --config / --init-config
    path = Path(first.config).expanduser() if first.config else config_dir() / "config.toml"

    if first.init_config:
        if path.exists():
            sys.exit(f"{path} already exists. Edit it, or delete it first to start over.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(config_template(p), encoding="utf-8")
        print(f"Wrote {path}")
        sys.exit(0)

    if path.exists():
        config_argv = load_config(p, path)
    elif first.config:
        sys.exit(f"Config file not found: {path}")
    else:
        config_argv, path = [], None

    args = p.parse_args(config_argv + argv)  # command-line options come last, so they win
    args._config_path = path
    if args.alert is None:  # the words set in the window last time (they are also hotwords for Whisper below)
        args.alert = ", ".join(w for w in load_window_state().get("alert_words") or [] if isinstance(w, str))
    args._whisper_transcribe = make_transcribe_fn(args.beam_size, load_vocab(args))
    return args
