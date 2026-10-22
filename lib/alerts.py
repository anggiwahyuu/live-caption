"""Alert words: notice when someone says your name (or another word you're waiting for).

The words are matched whole and case-insensitively in other people's final lines; a match is highlighted
in the window and shown as a desktop notification (notify-send on Linux, Notification Center on macOS,
a toast on Windows).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys


def parse_words(text: str | None) -> list[str]:
    """ "Anggi, tim backend" -> ["Anggi", "tim backend"] (empties and duplicates dropped, order kept)."""
    words = (" ".join(w.split()) for w in (text or "").split(","))
    return list(dict.fromkeys(w for w in words if w))


def compile_words(words: list[str]) -> re.Pattern | None:
    """One pattern for all words: whole words only ("Anggi" matches "Anggi," but not "Anggia"),
    any case, and any spacing inside a phrase. Longer words first, so a phrase wins over its first word."""
    if not words:
        return None
    alternatives = sorted((r"\s+".join(map(re.escape, w.split())) for w in words), key=len, reverse=True)
    return re.compile(rf"(?<!\w)(?:{'|'.join(alternatives)})(?!\w)", re.IGNORECASE)


def find(pattern: re.Pattern | None, text: str) -> list[tuple[int, int]]:
    return [m.span() for m in pattern.finditer(text)] if pattern else []


# A toast from Windows PowerShell 5.1 (built into Windows 10/11), with PowerShell's own app id: an app needs a
# registered id to show toasts, and this one always exists. The texts come in through the environment.
WINDOWS_TOAST = (
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; "
    "$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
    "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
    "$texts = $xml.GetElementsByTagName('text'); "
    "$texts.Item(0).AppendChild($xml.CreateTextNode($env:LC_TITLE)) > $null; "
    "$texts.Item(1).AppendChild($xml.CreateTextNode($env:LC_BODY)) > $null; "
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
    "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe')"
    ".Show([Windows.UI.Notifications.ToastNotification]::new($xml))"
)


def notify_command(title: str, body: str, platform: str = sys.platform) -> tuple[list[str], dict] | None:
    """The command (and extra environment) that shows a desktop notification here, or None if there's none."""
    if platform == "win32":
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", WINDOWS_TOAST], \
            {"LC_TITLE": title, "LC_BODY": body}
    if platform == "darwin":  # the texts as arguments, so nothing in them needs AppleScript quoting
        return ["osascript", "-e", "on run argv", "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
                "-e", "end run", title, body], {}
    if shutil.which("notify-send"):
        # Icon: the one the menu shortcut installs (lib/shortcut.py); desktops fall back to a generic one without it.
        return ["notify-send", "--app-name=Live Caption", "--icon=live-caption", title, body], {}
    if shutil.which("gdbus"):  # the same notification without libnotify's command
        return ["gdbus", "call", "--session", "--dest", "org.freedesktop.Notifications",
                "--object-path", "/org/freedesktop/Notifications", "--method", "org.freedesktop.Notifications.Notify",
                "Live Caption", "0", "live-caption", title, body, "[]", "{}", "8000"], {}
    return None


def notify(title: str, body: str) -> bool:
    """Shows a desktop notification without waiting for it. False when this system has no way to show one."""
    found = notify_command(title, body)
    if found is None:
        return False
    command, env = found
    try:
        subprocess.Popen(command, env={**os.environ, **env}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # no console flash on Windows
    except OSError:
        return False
    return True
