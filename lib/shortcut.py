"""Start-menu shortcut for setup.sh / setup.ps1, so the app opens without a terminal.

Linux: a .desktop entry + icon in ~/.local/share (application menu of KDE, GNOME, XFCE, …).
Windows: a Start Menu shortcut that runs pythonw (no console window).
macOS: a small "Live Caption.app" in ~/Applications that runs run.sh.

Run with: .venv/bin/python -m lib.shortcut [--remove]
"""

from __future__ import annotations

import argparse
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

from .common import IS_LINUX, IS_MAC, IS_WINDOWS
from .config import log_path

PROJECT = Path(__file__).resolve().parent.parent
ASSETS = PROJECT / "assets"
APP_ID = "live-caption"  # also the Tk class name (lib/main.py), so the taskbar matches the window to its entry
WM_CLASS = "Live-caption"  # what Tk makes of that class name (first letter capitalized)
NAME = "Live Caption"
COMMENT = "Real-time captions and translation for online meetings"


# ---- Linux ----

def _xdg_data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def desktop_exec_quote(path: Path | str) -> str:
    """An argument quoted for the Exec= key (Desktop Entry spec: quote, backslash-escape \" ` $ \\, double %)."""
    escaped = "".join("\\" + c if c in '"`$\\' else c for c in str(path))
    return '"' + escaped.replace("%", "%%") + '"'


def desktop_entry(project: Path) -> str:
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        f"Name={NAME}",
        "GenericName=Meeting captions",
        f"Comment={COMMENT}",
        # run.sh sends the messages to this log, as there is no terminal to show them
        f"Exec=env {desktop_exec_quote(f'LIVE_CAPTION_LOG={log_path()}')} {desktop_exec_quote(project / 'run.sh')}",
        f"Path={project}",
        f"Icon={APP_ID}",
        "Terminal=false",
        "Categories=Utility;Accessibility;",
        "Keywords=caption;subtitle;transcript;translate;meeting;whisper;",
        f"StartupWMClass={WM_CLASS}",
        "",
    ])


def linux_paths() -> list[Path]:
    data = _xdg_data_home()
    return [data / "applications" / f"{APP_ID}.desktop",
            data / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg",
            data / "icons" / "hicolor" / "256x256" / "apps" / f"{APP_ID}.png"]


def _refresh_linux_menus() -> None:
    """Most menus notice a new .desktop file on their own; these caches make it immediate where they exist."""
    data = _xdg_data_home()
    for command in (["update-desktop-database", str(data / "applications")],
                    ["gtk-update-icon-cache", "-q", "-t", str(data / "icons" / "hicolor")],
                    ["kbuildsycoca6"], ["kbuildsycoca5"]):
        if shutil.which(command[0]):
            subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def install_linux() -> list[Path]:
    desktop, svg, png = linux_paths()
    for path in (desktop, svg, png):
        path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ASSETS / f"{APP_ID}.svg", svg)
    shutil.copyfile(ASSETS / f"{APP_ID}.png", png)
    desktop.write_text(desktop_entry(PROJECT), encoding="utf-8")
    desktop.chmod(0o755)  # some desktops (GNOME, XFCE) only trust launchers that are executable
    _refresh_linux_menus()
    return [desktop]


# ---- macOS ----

def mac_app() -> Path:
    return Path.home() / "Applications" / f"{NAME}.app"


def icns_from_png(png: bytes) -> bytes:
    """An .icns holding one 256x256 PNG ('ic08'), which macOS scales for the Dock and Finder."""
    entry = b"ic08" + struct.pack(">I", 8 + len(png)) + png
    return b"icns" + struct.pack(">I", 8 + len(entry)) + entry


def info_plist() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>{NAME}</string>
  <key>CFBundleDisplayName</key><string>{NAME}</string>
  <key>CFBundleIdentifier</key><string>io.github.live-caption</string>
  <key>CFBundleExecutable</key><string>{APP_ID}</string>
  <key>CFBundleIconFile</key><string>{APP_ID}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>NSMicrophoneUsageDescription</key>
  <string>Live Caption listens to the meeting audio (through BlackHole) and your microphone to write captions.</string>
</dict>
</plist>
"""


def install_mac() -> list[Path]:
    app = mac_app()
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True, exist_ok=True)
    (contents / "Resources").mkdir(parents=True, exist_ok=True)
    (contents / "Info.plist").write_text(info_plist(), encoding="utf-8")
    (contents / "Resources" / f"{APP_ID}.icns").write_bytes(icns_from_png((ASSETS / f"{APP_ID}.png").read_bytes()))
    launcher = contents / "MacOS" / APP_ID
    launcher.write_text(f'#!/bin/bash\n# Started from Finder or the Dock: no terminal, so run.sh sends messages to a log.\n'
                        f'export LIVE_CAPTION_LOG="{log_path()}"\nexec "{PROJECT}/run.sh"\n', encoding="utf-8")
    launcher.chmod(0o755)
    return [app]


# ---- Windows ----

def windows_shortcut() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{NAME}.lnk"


def install_windows() -> list[Path]:
    lnk = windows_shortcut()
    # pythonw: no console window. The values go in through the environment, so no path needs quoting in the script.
    env = {**os.environ, "LC_LNK": str(lnk), "LC_TARGET": str(PROJECT / ".venv" / "Scripts" / "pythonw.exe"),
           "LC_ARGS": f'"{PROJECT / "live_caption.py"}"', "LC_DIR": str(PROJECT),
           "LC_ICON": str(ASSETS / f"{APP_ID}.ico"), "LC_DESC": COMMENT}
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LC_LNK); "
              "$s.TargetPath = $env:LC_TARGET; $s.Arguments = $env:LC_ARGS; $s.WorkingDirectory = $env:LC_DIR; "
              "$s.IconLocation = $env:LC_ICON; $s.Description = $env:LC_DESC; $s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env=env, check=True)
    return [lnk]


# ----

def installed_paths() -> list[Path]:
    if IS_WINDOWS:
        return [windows_shortcut()]
    if IS_MAC:
        return [mac_app()]
    return linux_paths()


def install() -> list[Path]:
    if IS_WINDOWS:
        return install_windows()
    if IS_MAC:
        return install_mac()
    if IS_LINUX:
        return install_linux()
    raise OSError(f"no shortcut support for {sys.platform}")


def remove() -> list[Path]:
    removed = []
    for path in installed_paths():
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        else:
            continue
        removed.append(path)
    if IS_LINUX and removed:
        _refresh_linux_menus()
    return removed


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m lib.shortcut", description="Add Live Caption to the application menu.")
    p.add_argument("--remove", action="store_true", help="remove the shortcut again")
    args = p.parse_args(argv)
    if args.remove:
        removed = remove()
        for path in removed:
            print(f"    Removed {path}")
        if not removed:
            print("    No shortcut to remove")
        return 0
    try:
        paths = install()
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"    ! Couldn't create the shortcut: {e}", file=sys.stderr)
        return 1
    where = "the Start menu" if IS_WINDOWS else "Applications (Launchpad, Spotlight)" if IS_MAC else "the application menu"
    print(f"    \"{NAME}\" added to {where}: {paths[0]}")
    print("    It starts with your config.toml settings (README: \"Saving your settings\").")
    return 0


if __name__ == "__main__":
    sys.exit(main())
