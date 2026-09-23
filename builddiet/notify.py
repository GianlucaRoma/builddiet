"""Ask the user one yes/no question, with a native dialog when there is a desktop.

No dependencies: a Windows Forms message box through PowerShell, `osascript`
on macOS, `zenity` on Linux. Returns True/False, or None when no dialog could
be shown (the caller then falls back to the terminal or just logs).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Optional

TIMEOUT = 3600  # an unanswered dialog is a "no"


def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def ask(title: str, message: str, button: str = "Reclaim") -> Optional[bool]:
    if os.environ.get("BUILDDIET_NO_DIALOG"):
        return None
    try:
        if os.name == "nt":
            script = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                f"[System.Windows.Forms.MessageBox]::Show({_ps_quote(message + chr(10) + chr(10) + button + '?')}, "
                f"{_ps_quote(title)}, 'YesNo', 'Warning')"
            )
            out = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                                 capture_output=True, text=True, timeout=TIMEOUT)
            return out.stdout.strip() == "Yes" if out.returncode == 0 else None
        if sys.platform == "darwin" and shutil.which("osascript"):
            script = (f'display dialog {json_str(message)} with title {json_str(title)} '
                      f'buttons {{"Not now", {json_str(button)}}} default button "Not now"')
            out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=TIMEOUT)
            return button in out.stdout if out.returncode in (0, 1) else None
        if shutil.which("zenity") and os.environ.get("DISPLAY"):
            out = subprocess.run(["zenity", "--question", f"--title={title}", f"--text={message}",
                                  f"--ok-label={button}", "--cancel-label=Not now"], timeout=TIMEOUT)
            return out.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def json_str(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
