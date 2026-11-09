"""Synthetic, read-only and isolated tests for the optional boot installer."""
import importlib.util
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parent.parent / 'scripts/setup-termux-boot.py'
spec = importlib.util.spec_from_file_location('setup_termux_boot', SCRIPT)
boot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boot)


class BootSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.home, self.prefix = base / 'home', base / 'prefix'
        self.home.mkdir(mode=0o700)
        profile = self.prefix / 'etc/profile.d/start-services.sh'
        profile.parent.mkdir(parents=True)
        profile.write_text('synthetic fixture')
        svc = self.prefix / 'var/service/android-telegram-agent'
        svc.mkdir(parents=True)
        (svc / 'run').write_text('# synthetic service')
        (svc / 'run').chmod(0o700)

    def _install(self, app=True):
        with patch.object(boot.os, 'getuid', return_value=12345):
            return boot.install(self.home, self.prefix, installed=app)

    def _check(self, app=True):
        with patch.object(boot.os, 'getuid', return_value=12345):
            # stat() uses actual UID; patched getuid must match fixture UID.
            return boot.inspect(self.home, self.prefix, installed=app)

    def test_missing_boot_app_is_not_modified(self):
        with self.assertRaisesRegex(boot.SetupError, 'BOOT_APP_INSTALLED'):
            self._install(app=False)
        self.assertFalse((self.home / '.termux').exists())

    def test_missing_service_or_disabled_service_does_not_install(self):
        svc = self.prefix / 'var/service/android-telegram-agent'
        (svc / 'down').touch()
        with self.assertRaisesRegex(boot.SetupError, 'SERVICE_ENABLED'):
            self._install()
        (svc / 'down').unlink()
        (svc / 'run').chmod(0o600)
        with self.assertRaisesRegex(boot.SetupError, 'SERVICE_RUN_READY'):
            self._install()
        self.assertFalse((self.home / '.termux').exists())

    def test_idempotent_and_private_without_running_a_service(self):
        with patch.object(boot.os, 'getuid', return_value=os.getuid()), patch.object(
                boot.subprocess, 'run', side_effect=AssertionError('must not run')):
            status = boot.install(self.home, self.prefix, installed=True)
            self.assertTrue(all(status.values()))
            target = self.home / '.termux/boot/10-start-services'
            self.assertEqual(target.read_text(), boot.BOOT_SCRIPT)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(target.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(target.parent.parent.stat().st_mode), 0o700)
            before = target.stat().st_ino
            self.assertTrue(all(boot.install(self.home, self.prefix, installed=True).values()))
            self.assertEqual(target.stat().st_ino, before)

    def test_never_overwrite_other_boot_script_or_duplicate_supervisor(self):
        termux_dir = self.home / '.termux'
        termux_dir.mkdir(mode=0o700)
        boot_dir = termux_dir / 'boot'
        boot_dir.mkdir(mode=0o700)
        target = boot_dir / '10-start-services'
        target.write_text('# user script\n')
        target.chmod(0o700)
        with patch.object(boot.os, 'getuid', return_value=os.getuid()):
            with self.assertRaisesRegex(boot.SetupError, 'different content'):
                boot.install(self.home, self.prefix, installed=True)
        self.assertEqual(target.read_text(), '# user script\n')
        target.unlink()
        (boot_dir / '12-other').write_text('#!/bin/sh\n. "$PREFIX/etc/profile.d/start-services.sh"\n')
        with patch.object(boot.os, 'getuid', return_value=os.getuid()):
            with self.assertRaisesRegex(boot.SetupError, 'NO_DUPLICATE_STARTER'):
                boot.install(self.home, self.prefix, installed=True)
        self.assertFalse(target.exists())

    def test_symlinked_parent_or_target_is_rejected(self):
        target = self.home / '.termux'
        target.symlink_to(self.prefix, target_is_directory=True)
        with patch.object(boot.os, 'getuid', return_value=os.getuid()):
            with self.assertRaises(boot.SetupError):
                boot.install(self.home, self.prefix, installed=True)
        target.unlink()
        termux_dir = self.home / '.termux'
        termux_dir.mkdir(mode=0o700)
        boot_dir = termux_dir / 'boot'
        boot_dir.mkdir(mode=0o700)
        (boot_dir / '10-start-services').symlink_to(self.prefix)
        with patch.object(boot.os, 'getuid', return_value=os.getuid()):
            with self.assertRaises(boot.SetupError):
                boot.install(self.home, self.prefix, installed=True)

    def test_read_only_check_does_not_create_dirs(self):
        with patch.object(boot.os, 'getuid', return_value=os.getuid()):
            status = boot.inspect(self.home, self.prefix, installed=True)
        self.assertFalse(status['BOOT_SCRIPT_READY'])
        self.assertFalse((self.home / '.termux').exists())

    def test_package_presence_requires_exact_package_line(self):
        class Result:
            returncode = 0
            stdout = 'package:com.termux.boot.fake\npackage:com.termux\n'
        with patch.object(boot.subprocess, 'run', return_value=Result()) as cmd:
            self.assertFalse(boot.boot_app_installed())
            cmd.assert_called_once()
        Result.stdout = 'package:com.termux.boot\n'
        with patch.object(boot.subprocess, 'run', return_value=Result()):
            self.assertTrue(boot.boot_app_installed())


if __name__ == '__main__':
    unittest.main()
