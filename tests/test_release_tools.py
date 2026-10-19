"""Synthetic release-tool tests; no real credentials, network, service or AGY."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class ReleaseToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir(mode=0o700)
        self.env = dict(os.environ, HOME=str(self.home))

    def run_tool(self, name, *args, check=True):
        return subprocess.run(
            [str(ROOT / "scripts" / name), *map(str, args)],
            cwd=ROOT, env=self.env, text=True, capture_output=True,
            timeout=60, check=check)

    def fake_repo(self):
        repo = self.home / "source"
        repo.mkdir()
        (repo / "tests").mkdir()
        (repo / "bot.py").write_text("VALUE = 1\n", encoding="utf8")
        (repo / "config.py").write_text("VALUE = 1\n", encoding="utf8")
        (repo / "telegram_api.py").write_text("VALUE = 1\n", encoding="utf8")
        (repo / "tests" / "test_smoke.py").write_text(
            "import unittest\nfrom pathlib import Path\nclass T(unittest.TestCase):\n"
            " def test_ok(self):\n"
            "  self.assertFalse((Path(__file__).parents[1] / '.installed-commit').exists())\n",
            encoding="utf8")
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        commit_env = dict(self.env, GIT_AUTHOR_NAME="Synthetic Owner",
                          GIT_AUTHOR_EMAIL="owner@example.invalid",
                          GIT_COMMITTER_NAME="Synthetic Owner",
                          GIT_COMMITTER_EMAIL="owner@example.invalid")
        subprocess.run(["git", "commit", "-qm", "initial"], cwd=repo,
                       env=commit_env, check=True)
        return repo, commit_env

    def test_versioned_install_upgrade_and_rollback(self):
        repo, commit_env = self.fake_repo()
        install_root = self.home / "opt" / "android-telegram-agent"
        first = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                        cwd=repo, text=True).strip()
        result = self.run_tool("install-termux.sh", "--source", repo,
                               "--ref", first, "--install-root", install_root)
        self.assertEqual(result.stdout.strip(), "INSTALL_OK " + first)
        self.assertEqual(os.readlink(install_root / "current"), "releases/" + first)
        self.assertEqual((install_root / "releases" / first /
                          ".installed-commit").read_text().strip(), first)

        (repo / "bot.py").write_text("VALUE = 2\n", encoding="utf8")
        subprocess.run(["git", "add", "bot.py"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "second"], cwd=repo,
                       env=commit_env, check=True)
        second = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                         cwd=repo, text=True).strip()
        self.run_tool("upgrade-termux.sh", "--source", repo, "--ref", second,
                      "--install-root", install_root)
        self.assertEqual(os.readlink(install_root / "current"), "releases/" + second)
        self.assertEqual(os.readlink(install_root / "previous"), "releases/" + first)

        (repo / "tests" / "test_smoke.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n"
            " def test_failure(self): self.fail('synthetic')\n", encoding="utf8")
        subprocess.run(["git", "add", "tests/test_smoke.py"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "failing"], cwd=repo,
                       env=commit_env, check=True)
        failing = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                          cwd=repo, text=True).strip()
        rejected = self.run_tool("install-termux.sh", "--source", repo,
                                 "--ref", failing, "--install-root", install_root,
                                 check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(os.readlink(install_root / "current"), "releases/" + second)
        self.assertFalse((install_root / "releases" / failing).exists())

        self.run_tool("rollback-termux.sh", "--install-root", install_root)
        self.assertEqual(os.readlink(install_root / "current"), "releases/" + first)
        self.assertEqual(os.readlink(install_root / "previous"), "releases/" + second)

    def test_install_refuses_outside_home(self):
        repo, _ = self.fake_repo()
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                         cwd=repo, text=True).strip()
        bad = self.run_tool("install-termux.sh", "--source", repo, "--ref", commit,
                            "--install-root", Path(self.tmp.name) / "outside",
                            check=False)
        self.assertNotEqual(bad.returncode, 0)
        self.assertNotIn(str(self.home), bad.stdout + bad.stderr)

    def test_doctor_is_fixed_output_and_does_not_run_agy(self):
        private = self.home / ".config" / "android-telegram-agent"
        private.mkdir(parents=True, mode=0o700)
        workspace = self.home / "workspace"
        workspace.mkdir(mode=0o700)
        agy = self.home / "agy-synthetic"
        agy.write_text("#!/bin/sh\nexit 99\n", encoding="utf8")
        agy.chmod(0o700)
        fake_bin = self.home / "bin"
        fake_bin.mkdir()
        sv = fake_bin / "sv"
        sv.write_text("#!/bin/sh\nprintf 'run: synthetic service\\n'\n", encoding="utf8")
        sv.chmod(0o700)
        self.env["PATH"] = str(fake_bin) + os.pathsep + self.env["PATH"]
        config = private / "config.json"
        config.write_text(json.dumps({
            "bot_token": "100000000:SyntheticTokenOnlyForTests",
            "allowed_pairs": [{"user_id": 101, "chat_id": 202}],
            "agy_command": str(agy), "workspace": str(workspace),
            "task_timeout_seconds": None,
        }), encoding="utf8")
        config.chmod(0o600)
        result = subprocess.run(
            ["python", str(ROOT / "scripts" / "doctor.py"),
             "--checkout", str(ROOT), "--config", str(config),
             "--service", "android-telegram-agent"],
            cwd=ROOT, env=self.env, text=True, capture_output=True,
            timeout=20, check=True)
        self.assertEqual(result.stdout.splitlines(), [
            "NON_ROOT_OK", "PYTHON_OK", "CODE_OK", "CONFIG_OK", "WORKSPACE_OK",
            "AGY_EXECUTABLE_OK", "INSTALL_VERSION_OK", "SERVICE_OK", "DOCTOR_OK",
        ])
        self.assertNotIn(str(self.home), result.stdout + result.stderr)

    def test_shell_syntax_and_help(self):
        for name in ("install-termux.sh", "upgrade-termux.sh",
                     "rollback-termux.sh"):
            subprocess.run(["bash", "-n", str(ROOT / "scripts" / name)], check=True)
            result = self.run_tool(name, "--help")
            self.assertIn("Usage:", result.stdout)

        invalid = self.run_tool("install-termux.sh", "--source", ROOT,
                                "--ref", "HEAD", check=False)
        self.assertEqual(invalid.returncode, 2)


if __name__ == "__main__":
    unittest.main()
