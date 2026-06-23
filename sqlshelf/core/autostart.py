from __future__ import annotations

import sys
from pathlib import Path

_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APP_NAME = "SQLShelf"


def _get_launch_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pythonw = Path(sys.executable).parent / "pythonw.exe"
    main = Path(__file__).parent.parent.parent / "main.py"
    return f'"{pythonw}" "{main}"'


def enable_autostart() -> None:
    """Register SQLShelf to run at Windows startup via HKCU Run key."""
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, _REG_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, _APP_NAME, 0, winreg.REG_SZ, _get_launch_command())


def disable_autostart() -> None:
    """Remove SQLShelf from the Windows startup Run key."""
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _REG_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, _APP_NAME)
    except FileNotFoundError:
        pass


def is_autostart_enabled() -> bool:
    """Return True if the SQLShelf Run key exists in HKCU."""
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _REG_KEY, 0, winreg.KEY_READ
        ) as key:
            winreg.QueryValueEx(key, _APP_NAME)
            return True
    except (FileNotFoundError, OSError):
        return False
