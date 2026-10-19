#!/usr/bin/env python3
"""Safe installation checks without printing secrets or private identifiers."""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def report(label: str, passed: bool) -> bool:
    print(f"{label}_{'OK' if passed else 'FAILED'}")
    return passed


def executable(command: str) -> bool:
    if "/" in command:
        path = Path(command).expanduser()
        return path.is_file() and os.access(path, os.X_OK)
    return shutil.which(command) is not None


def main() -> int:
    parser = argparse.ArgumentParser(description="Check an android-telegram-agent install")
    parser.add_argument("--config", required=True)
    parser.add_argument("--checkout", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--network", action="store_true",
                        help="also verify Bot identity and command menu; never polls updates")
    parser.add_argument("--service", help="optional termux-services name to check")
    args = parser.parse_args()

    checkout = Path(args.checkout).expanduser().resolve()
    sys.path.insert(0, str(checkout))
    passed = report("NON_ROOT", os.getuid() != 0)
    passed &= report("PYTHON", sys.version_info >= (3, 10))
    code_ok = all((checkout / name).is_file()
                  for name in ("bot.py", "config.py", "telegram_api.py",
                               "command_menu.py"))
    passed &= report("CODE", code_ok)

    settings = None
    if code_ok:
        try:
            from config import load_settings
            settings = load_settings(args.config)
        except (ImportError, OSError, ValueError):
            pass
    passed &= report("CONFIG", settings is not None)
    passed &= report("WORKSPACE", bool(settings and settings.workspace.is_dir()))
    passed &= report("AGY_EXECUTABLE", bool(settings and executable(settings.agy_command)))

    marker = checkout / ".installed-commit"
    version_ok = True
    if marker.exists():
        try:
            value = marker.read_text(encoding="ascii").strip()
            version_ok = bool(re.fullmatch(r"[0-9a-f]{40}", value)
                              and checkout.name == value)
        except OSError:
            version_ok = False
    passed &= report("INSTALL_VERSION", version_ok)

    if args.network:
        identity_ok = menu_ok = False
        if settings is not None:
            try:
                from command_menu import INHERITED_COMMAND_SCOPES, telegram_commands
                from telegram_api import TelegramAPI
                api = TelegramAPI(settings.bot_token)
                identity = api.get_me()
                identity_ok = (isinstance(identity, dict)
                               and identity.get("is_bot") is True)
                menu_ok = (api.get_my_commands() == telegram_commands()
                           and all(not api.get_my_commands(scope)
                                   for scope in INHERITED_COMMAND_SCOPES))
            except (ImportError, OSError, RuntimeError, ValueError):
                pass
        passed &= report("BOT_IDENTITY", identity_ok)
        passed &= report("COMMAND_MENU", menu_ok)

    if args.service:
        service_ok = False
        if (re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", args.service)
                and shutil.which("sv")):
            try:
                result = subprocess.run(
                    ["sv", "status", args.service], stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    timeout=5, check=False, text=True)
                service_ok = (result.returncode == 0
                              and result.stdout.startswith("run:"))
            except (OSError, subprocess.TimeoutExpired):
                pass
        passed &= report("SERVICE", service_ok)

    report("DOCTOR", bool(passed))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
