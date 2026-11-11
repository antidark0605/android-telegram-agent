"""Set up the Termux:Boot hook for an existing runit-managed Bot service.

This tool never installs APKs, starts services, changes config or invokes AGY.
"""
from __future__ import annotations

import argparse
import errno
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

TERMUX_HOME = Path('/data/data/com.termux/files/home')
TERMUX_PREFIX = Path('/data/data/com.termux/files/usr')
SERVICE_NAME = 'android-telegram-agent'
SCRIPT_NAME = '10-start-services'
BOOT_SCRIPT = '''#!/data/data/com.termux/files/usr/bin/sh
# Termux:Boot starts the existing runit supervisor; it does not start a second Bot.
export HOME=/data/data/com.termux/files/home
export PREFIX=/data/data/com.termux/files/usr
export PATH="$PREFIX/bin:/system/bin:/system/xbin"
[ -r "$PREFIX/etc/profile.d/start-services.sh" ] || exit 1
. "$PREFIX/etc/profile.d/start-services.sh"
'''
START_MARKERS = (b'start-services.sh', b'service-daemon start',
                 b'android-telegram-agent', b'bot.py')


class SetupError(ValueError):
    """A prerequisite is missing or an existing user file would be changed."""


def _publish_no_replace(source: str, target: Path) -> None:
    """Publish on the same filesystem without replacing any existing entry."""
    link = getattr(os, 'link', None)
    if callable(link):
        link(source, target)
        return
    # Termux's Python build disables link/linkat. Never emulate this with
    # exists()+rename(): that would overwrite a concurrently created hook.
    try:
        import ctypes
        library = ctypes.CDLL(None, use_errno=True)
    except (ImportError, AttributeError, OSError):
        raise SetupError('Atomic no-replace publication is unavailable.') from None
    arguments = (-100, os.fsencode(source), -100, os.fsencode(target), 1)
    types = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
             ctypes.c_char_p, ctypes.c_uint)
    rename = getattr(library, 'renameat2', None)
    if rename is not None:
        rename.argtypes = types
        rename.restype = ctypes.c_int
    else:
        # Bionic exposes renameat2 only since API 30. For older ARM64
        # Android, Linux UAPI defines __NR_renameat2=276 (asm-generic).
        # Reject unknown ABIs rather than guessing syscall numbers.
        if (sys.platform not in ('linux', 'android')
                or os.uname().machine != 'aarch64'
                or ctypes.sizeof(ctypes.c_void_p) != 8):
            raise SetupError('Atomic no-replace publication is unavailable.')
        rename = getattr(library, 'syscall', None)
        if rename is None:
            raise SetupError('Atomic no-replace publication is unavailable.')
        rename.argtypes = (ctypes.c_long,) + types
        rename.restype = ctypes.c_long
        arguments = (276,) + arguments
    # Linux/Android UAPI: AT_FDCWD=-100, RENAME_NOREPLACE=1.
    if rename(*arguments) != 0:
        code = ctypes.get_errno()
        if code in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP):
            raise SetupError('Atomic no-replace publication is unavailable.') from None
        raise OSError(code, 'Cannot publish boot script.')


def boot_app_installed() -> bool:
    """Only ask Android package manager for the package name, not its metadata."""
    try:
        response = subprocess.run(
            ['/system/bin/pm', 'list', 'packages', 'com.termux.boot'],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=8, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return response.returncode == 0 and any(
        line.strip() == 'package:com.termux.boot'
        for line in response.stdout.splitlines())


def _private_directory(path: Path, *, create: bool) -> bool:
    """Check an existing private directory without mutating its permissions."""
    if not path.exists() and not path.is_symlink():
        if not create:
            return False
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
    if path.is_symlink():
        raise SetupError('Refusing a symlink in the Termux boot-script path.')
    try:
        meta = path.stat()
    except OSError:
        raise SetupError('Cannot access the Termux boot-script directory.') from None
    if (not stat.S_ISDIR(meta.st_mode) or meta.st_uid != os.getuid()
            or meta.st_mode & 0o077):
        raise SetupError('Termux boot-script directory must be private and owned by this user.')
    return True


def _script_matches(script: Path) -> bool:
    if script.is_symlink():
        raise SetupError('Refusing a symlink at the managed boot-script path.')
    try:
        meta = script.stat()
    except FileNotFoundError:
        return False
    except OSError:
        raise SetupError('Cannot inspect existing boot script.') from None
    if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid():
        raise SetupError('Existing boot script is not a user-owned regular file.')
    if meta.st_mode & 0o077 or not meta.st_mode & stat.S_IXUSR:
        raise SetupError('Existing boot script is not private and executable.')
    try:
        content = script.read_bytes()
    except OSError:
        raise SetupError('Cannot read existing boot script.') from None
    if content != BOOT_SCRIPT.encode('utf-8'):
        raise SetupError('Boot-script name already exists with different content; not overwritten.')
    return True


def _other_startup_script(boot_dir: Path, managed: Path) -> bool:
    if not boot_dir.is_dir():
        return False
    for path in boot_dir.iterdir():
        # Termux:Boot enumerates hidden regular files too.
        if path == managed:
            continue
        if path.is_symlink():
            raise SetupError('An existing boot script is a symlink; inspect it manually.')
        if not path.is_file():
            continue
        if path.stat().st_size > 65536:
            raise SetupError('An existing boot script is too large to inspect safely.')
        try:
            content = path.read_bytes()
        except OSError:
            raise SetupError('Cannot inspect an existing boot script.') from None
        if any(marker in content for marker in START_MARKERS):
            return True
    return False


def inspect(home: Path, prefix: Path, *, installed: bool) -> dict[str, bool]:
    """Read-only readiness check, safe even when the boot hook is absent."""
    boot_dir = home / '.termux' / 'boot'
    script = boot_dir / SCRIPT_NAME
    status = {
        'BOOT_APP_INSTALLED': installed,
        'START_SERVICES_PRESENT': (prefix / 'etc/profile.d/start-services.sh').is_file(),
        'SERVICE_RUN_READY': os.access(prefix / 'var/service' / SERVICE_NAME / 'run', os.X_OK),
        'SERVICE_ENABLED': not (prefix / 'var/service' / SERVICE_NAME / 'down').exists(),
        'BOOT_SCRIPT_READY': False,
        'NO_DUPLICATE_STARTER': True,
    }
    if boot_dir.exists() or boot_dir.is_symlink():
        _private_directory(home / '.termux', create=False)
        _private_directory(boot_dir, create=False)
        status['BOOT_SCRIPT_READY'] = _script_matches(script)
        status['NO_DUPLICATE_STARTER'] = not _other_startup_script(boot_dir, script)
    return status


def install(home: Path, prefix: Path, *, installed: bool) -> dict[str, bool]:
    """Add one idempotent hook. Never start processes or replace user files."""
    if os.getuid() == 0:
        raise SetupError('Run as the non-root Termux app user.')
    before = inspect(home, prefix, installed=installed)
    for required in ('BOOT_APP_INSTALLED', 'START_SERVICES_PRESENT',
                     'SERVICE_RUN_READY', 'SERVICE_ENABLED', 'NO_DUPLICATE_STARTER'):
        if not before[required]:
            raise SetupError(f'{required} is not ready; no boot script installed.')
    if before['BOOT_SCRIPT_READY']:
        return before
    termux_dir = home / '.termux'
    if not _private_directory(termux_dir, create=True):
        raise SetupError('Cannot prepare Termux directory.')
    boot_dir = termux_dir / 'boot'
    if not _private_directory(boot_dir, create=True):
        raise SetupError('Cannot prepare Termux boot directory.')
    target = boot_dir / SCRIPT_NAME
    # Re-check after creating the directory to avoid overwriting a concurrent
    # installation or a user-managed hook.
    if _script_matches(target):
        return inspect(home, prefix, installed=installed)
    if _other_startup_script(boot_dir, target):
        raise SetupError('Another boot script already starts the Bot or services.')
    # Keep staging outside the boot directory: even hidden files there can
    # execute at boot, including leftovers from an interrupted installation.
    fd, temporary = tempfile.mkstemp(prefix='.start-', dir=termux_dir)
    try:
        os.fchmod(fd, 0o700)
        with os.fdopen(fd, 'wb') as output:
            fd = -1
            output.write(BOOT_SCRIPT.encode('utf-8'))
            output.flush()
            os.fsync(output.fileno())
        try:
            _publish_no_replace(temporary, target)
        except FileExistsError:
            raise SetupError('Boot script appeared concurrently; not overwritten.') from None
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            # renameat2 consumes the staging name on successful publication.
            pass
    return inspect(home, prefix, installed=installed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Inspect or install Termux:Boot service hook')
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--check', action='store_true', help='read-only readiness check')
    action.add_argument('--install', action='store_true', help='install private boot script only')
    args = parser.parse_args(argv)
    if (os.getuid() == 0 or Path.home() != TERMUX_HOME
            or Path(os.environ.get('PREFIX', '')) != TERMUX_PREFIX):
        print('TERMUX_NONROOT_ENV_FAILED')
        return 2
    try:
        statuses = (install if args.install else inspect)(
            TERMUX_HOME, TERMUX_PREFIX, installed=boot_app_installed())
    except SetupError as exc:
        # SetupError messages are controlled, generic conditions.
        print('BOOT_SETUP_FAILED:', exc)
        return 1
    except OSError:
        # Filesystem exceptions can contain private filenames or paths.
        print('BOOT_SETUP_FAILED: Cannot access boot setup files.')
        return 1
    for label, ready in statuses.items():
        print(label + ('_OK' if ready else '_MISSING'))
    return 0 if all(statuses.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
