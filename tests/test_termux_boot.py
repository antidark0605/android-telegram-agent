"""Synthetic, read-only and isolated tests for the optional boot installer."""
import contextlib
import errno
import importlib.util
import io
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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

    def test_hidden_starters_and_old_temporary_hooks_block_install(self):
        termux_dir = self.home / '.termux'
        termux_dir.mkdir(mode=0o700)
        boot_dir = termux_dir / 'boot'
        boot_dir.mkdir(mode=0o700)
        for name in ('.existing-starter', '.start-interrupted'):
            with self.subTest(name=name):
                existing = boot_dir / name
                existing.write_text(boot.BOOT_SCRIPT)
                # Termux:Boot also makes non-executable regular files executable.
                existing.chmod(0o600)
                with self.assertRaisesRegex(boot.SetupError, 'NO_DUPLICATE_STARTER'):
                    boot.install(self.home, self.prefix, installed=True)
                self.assertFalse((boot_dir / boot.SCRIPT_NAME).exists())
                self.assertEqual(existing.read_text(), boot.BOOT_SCRIPT)
                existing.unlink()

    def test_hidden_symlink_is_not_ignored(self):
        termux_dir = self.home / '.termux'
        termux_dir.mkdir(mode=0o700)
        boot_dir = termux_dir / 'boot'
        boot_dir.mkdir(mode=0o700)
        (boot_dir / '.existing-starter').symlink_to(self.prefix)
        with self.assertRaisesRegex(boot.SetupError, 'symlink'):
            boot.install(self.home, self.prefix, installed=True)
        self.assertFalse((boot_dir / boot.SCRIPT_NAME).exists())

    def test_staging_never_appears_in_boot_consumer_directory(self):
        real_publish = boot._publish_no_replace
        observed = []

        def publish(source, target):
            source, target = Path(source), Path(target)
            observed.append(source)
            self.assertEqual(source.parent, self.home / '.termux')
            self.assertEqual(source.read_text(), boot.BOOT_SCRIPT)
            self.assertEqual(list(target.parent.iterdir()), [])
            return real_publish(source, target)

        with patch.object(boot, '_publish_no_replace', side_effect=publish):
            status = boot.install(self.home, self.prefix, installed=True)
        self.assertTrue(all(status.values()))
        self.assertEqual(len(observed), 1)
        self.assertFalse(observed[0].exists())
        self.assertEqual([p.name for p in (self.home / '.termux/boot').iterdir()],
                         [boot.SCRIPT_NAME])

    def test_cli_filesystem_errors_never_print_private_paths(self):
        # Exercise the actual installation failure sites, not only a stubbed
        # install() exception. Each stage must reach the same safe CLI boundary.
        for stage in ('mkstemp', '_publish_no_replace', 'unlink', 'iterdir', 'mkdir'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp) / 'home'
                home.mkdir(mode=0o700)
                termux_dir = home / '.termux'
                termux_dir.mkdir(mode=0o700)
                boot_dir = termux_dir / 'boot'
                if stage != 'mkdir':
                    boot_dir.mkdir(mode=0o700)
                error = PermissionError(errno.EACCES, 'Permission denied',
                                        '/synthetic/private-location/boot')
                target = {'mkstemp': boot.tempfile, '_publish_no_replace': boot,
                          'unlink': boot.os, 'iterdir': boot.Path,
                          'mkdir': boot.Path}[stage]
                output = io.StringIO()
                with (patch.object(boot, 'TERMUX_HOME', home),
                      patch.object(boot, 'TERMUX_PREFIX', self.prefix),
                      patch.object(boot.Path, 'home', return_value=home),
                      patch.dict(os.environ, {'PREFIX': str(self.prefix)}),
                      patch.object(boot, 'boot_app_installed', return_value=True),
                      patch.object(target, stage, side_effect=error),
                      contextlib.redirect_stdout(output)):
                    result = boot.main(['--install'])
                self.assertEqual(result, 1)
                self.assertEqual(output.getvalue(),
                                 'BOOT_SETUP_FAILED: Cannot access boot setup files.\n')
                self.assertNotIn('/synthetic/', output.getvalue())
                self.assertNotIn(str(home), output.getvalue())
                # A cleanup error can occur after atomic publication. The next
                # invocation must still recognize the hook without overwriting it.
                if stage == 'unlink':
                    hook = boot_dir / boot.SCRIPT_NAME
                    inode = hook.stat().st_ino
                    self.assertTrue(all(boot.install(
                        home, self.prefix, installed=True).values()))
                    self.assertEqual(hook.stat().st_ino, inode)

    def test_real_install_without_os_link_is_private_idempotent_and_clean(self):
        original = getattr(os, 'link', None)
        if hasattr(os, 'link'):
            del os.link
        try:
            status = boot.install(self.home, self.prefix, installed=True)
            self.assertTrue(all(status.values()))
            target = self.home / '.termux/boot' / boot.SCRIPT_NAME
            self.assertEqual(target.read_text(), boot.BOOT_SCRIPT)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
            inode = target.stat().st_ino
            self.assertTrue(all(boot.install(self.home, self.prefix, installed=True).values()))
            self.assertEqual(target.stat().st_ino, inode)
            self.assertEqual(list((self.home / '.termux').iterdir()), [target.parent])
        finally:
            if original is not None:
                os.link = original

    def test_rename_never_replaces_existing_file_or_symlink(self):
        for kind in ('file', 'symlink', 'dangling-symlink'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                source, target = base / 'source', base / 'target'
                source.write_text('new hook')
                if kind == 'file':
                    target.write_text('existing hook')
                else:
                    referent = base / 'referent'
                    if kind == 'symlink':
                        referent.write_text('existing hook')
                    target.symlink_to(referent)
                inode = target.lstat().st_ino
                with patch.object(boot.os, 'link', None, create=True):
                    with self.assertRaises(FileExistsError):
                        boot._publish_no_replace(str(source), target)
                self.assertEqual(target.lstat().st_ino, inode)
                self.assertEqual(source.read_text(), 'new hook')
                if kind != 'dangling-symlink':
                    self.assertEqual(target.read_text(), 'existing hook')

    def test_concurrent_hook_is_not_overwritten_and_staging_is_removed(self):
        real_publish = boot._publish_no_replace

        def race(source, target):
            target.write_text('concurrent hook')
            return real_publish(source, target)

        with (patch.object(boot.os, 'link', None, create=True),
              patch.object(boot, '_publish_no_replace', side_effect=race)):
            with self.assertRaisesRegex(boot.SetupError, 'concurrently'):
                boot.install(self.home, self.prefix, installed=True)
        target = self.home / '.termux/boot' / boot.SCRIPT_NAME
        self.assertEqual(target.read_text(), 'concurrent hook')
        self.assertEqual(list((self.home / '.termux').iterdir()), [target.parent])

    def test_missing_libc_symbol_fails_closed_and_cleans_staging(self):
        import ctypes
        with (patch.object(boot.os, 'link', None, create=True),
              patch.object(ctypes, 'CDLL', return_value=object())):
            with self.assertRaisesRegex(boot.SetupError, 'unavailable'):
                boot.install(self.home, self.prefix, installed=True)
        self.assertEqual(list((self.home / '.termux').iterdir()),
                         [self.home / '.termux/boot'])
        self.assertEqual(list((self.home / '.termux/boot').iterdir()), [])

    def test_rename_errno_and_abi_are_checked_without_unsafe_fallback(self):
        import ctypes
        for code in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP,
                     errno.EACCES, errno.EXDEV):
            with self.subTest(code=code):
                rename = Mock(return_value=-1)
                library = Mock(renameat2=rename)
                expected = (boot.SetupError if code in (
                    errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP) else OSError)
                with (patch.object(boot.os, 'link', None, create=True),
                      patch.object(ctypes, 'CDLL', return_value=library) as load,
                      patch.object(ctypes, 'get_errno', return_value=code),
                      patch.object(boot.os, 'rename', side_effect=AssertionError('unsafe')),
                      patch.object(boot.os, 'replace', side_effect=AssertionError('unsafe'))):
                    with self.assertRaises(expected):
                        boot._publish_no_replace('source', Path('target'))
                load.assert_called_once_with(None, use_errno=True)
                rename.assert_called_once_with(-100, b'source', -100, b'target', 1)
                self.assertEqual(rename.argtypes, (ctypes.c_int, ctypes.c_char_p,
                                 ctypes.c_int, ctypes.c_char_p, ctypes.c_uint))
                self.assertIs(rename.restype, ctypes.c_int)

    def test_old_arm64_libc_syscall_uses_verified_abi_and_errno(self):
        import ctypes
        for code in (0, errno.EEXIST, errno.ENOSYS, errno.EACCES):
            with self.subTest(code=code):
                syscall = Mock(return_value=-1 if code else 0)
                library = Mock(renameat2=None, syscall=syscall)
                with (patch.object(boot.os, 'link', None, create=True),
                      patch.object(ctypes, 'CDLL', return_value=library),
                      patch.object(boot.sys, 'platform', 'linux'),
                      patch.object(boot.os, 'uname', return_value=Mock(machine='aarch64')),
                      patch.object(ctypes, 'sizeof', return_value=8),
                      patch.object(ctypes, 'get_errno', return_value=code)):
                    if code:
                        expected = boot.SetupError if code == errno.ENOSYS else OSError
                        with self.assertRaises(expected) as failure:
                            boot._publish_no_replace('source', Path('target'))
                        if code == errno.EEXIST:
                            self.assertIsInstance(failure.exception, FileExistsError)
                    else:
                        boot._publish_no_replace('source', Path('target'))
                syscall.assert_called_once_with(276, -100, b'source', -100, b'target', 1)
                self.assertEqual(syscall.argtypes, (ctypes.c_long, ctypes.c_int,
                                 ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                                 ctypes.c_uint))
                self.assertIs(syscall.restype, ctypes.c_long)

    def test_unknown_syscall_abi_is_rejected_without_calling_syscall(self):
        import ctypes
        for platform, machine, bits in (('linux', 'unknown', 8),
                                        ('linux', 'aarch64', 4),
                                        ('darwin', 'aarch64', 8)):
            with self.subTest(platform=platform, machine=machine, bits=bits):
                syscall = Mock(side_effect=AssertionError('unknown ABI'))
                library = Mock(renameat2=None, syscall=syscall)
                with (patch.object(boot.os, 'link', None, create=True),
                      patch.object(ctypes, 'CDLL', return_value=library),
                      patch.object(boot.sys, 'platform', platform),
                      patch.object(boot.os, 'uname', return_value=Mock(machine=machine)),
                      patch.object(ctypes, 'sizeof', return_value=bits)):
                    with self.assertRaisesRegex(boot.SetupError, 'unavailable'):
                        boot._publish_no_replace('source', Path('target'))
                syscall.assert_not_called()

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
