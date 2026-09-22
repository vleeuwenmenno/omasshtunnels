import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("tunnels", Path(__file__).parents[1] / "bin/tunnels.py")
tunnels = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tunnels)


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tunnel-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.config = self.base / "config"
        self.dropins = self.base / "config.d"
        self.dropins.mkdir()
        self.system = self.base / "system"
        self.system.write_text("Host *\n  ServerAliveInterval 45\n")
        self.config.write_text("Host dev\n  HostName 127.0.0.1\n  User test\n")
        self.backend = tunnels.Backend(self.config, self.dropins, self.base / "data", self.base / "run", self.system)

    def save(self, **changes):
        value = dict(alias="dev", name="Database", localPort="15432", remoteHost="localhost", remotePort="5432")
        value.update(changes)
        self.backend.save(value)
        return self.backend.data()["tunnels"][-1]

    def test_discovers_includes_aliases_and_orphan_dropins(self):
        included = self.dropins / "included.conf"
        included.write_text("Host db db-short !excluded *.wild\n  HostName 127.0.0.2\n  LocalForward 5433 localhost:5432\n")
        (self.dropins / "orphan.conf").write_text("Host extra\n  HostName 127.0.0.3\n  DynamicForward 1080\n")
        (self.dropins / "ignored.bak").write_text("Host not-a-host\n")
        self.config.write_text(f'Include "{included}"\nHost dev\n  HostName 127.0.0.1\n')
        snapshot = self.backend.snapshot()
        self.assertEqual({a["alias"] for a in snapshot["aliases"]}, {"db", "db-short", "dev", "extra"})
        self.assertTrue(all(not a["error"] for a in snapshot["aliases"]))
        self.assertEqual(len(snapshot["tunnels"]), 3)
        self.assertEqual(len(next(a for a in snapshot["aliases"] if a["alias"] == "db")["forwards"]), 1)

    def test_conditional_include_does_not_become_unconditional(self):
        included = self.dropins / "conditional.conf"
        included.write_text("LocalForward 9876 localhost:9877\n")
        self.config.write_text(f'Host dev\n  HostName 127.0.0.1\n  Include "{included}"\nHost other\n  HostName 127.0.0.1\n')
        snapshot = self.backend.snapshot()
        by_alias = {a["alias"]: a for a in snapshot["aliases"]}
        self.assertEqual(len(by_alias["dev"]["forwards"]), 1)
        self.assertEqual(by_alias["other"]["forwards"], [])

    def test_wildcards_and_match_are_resolved_by_openssh(self):
        self.config.write_text("Host dev\n  HostName 127.0.0.1\nHost d*\n  User matched\n  LocalForward 15432 localhost:5432\nMatch originalhost dev\n  Port 2222\n")
        alias = self.backend.snapshot()["aliases"][0]
        self.assertEqual(alias["user"], "matched")
        self.assertEqual(alias["port"], "2222")
        self.assertEqual(len(alias["forwards"]), 1)

    def test_save_star_reload_unstar_edit_remove(self):
        original = self.config.read_bytes()
        row = self.save()
        self.assertTrue(self.backend.snapshot()["tunnels"][0]["starred"])
        backend2 = tunnels.Backend(self.config, self.dropins, self.base / "data", self.base / "run", self.system)
        self.assertEqual(backend2.data()["tunnels"][0], row)
        backend2.favorite(row["id"], False)
        self.assertFalse(backend2.snapshot()["tunnels"][0]["starred"])
        backend2.save(dict(row, name="Renamed", remotePort=5433))
        self.assertEqual(len(backend2.data()["tunnels"]), 1)
        backend2.remove(row["id"])
        self.assertEqual(backend2.data()["tunnels"], [])
        self.assertEqual(self.config.read_bytes(), original)

    def test_symlinked_config_preserves_gui_edits_in_target(self):
        target = self.base / "dotfiles" / "tunnels.json"
        target.parent.mkdir()
        target.write_text('{"version": 1, "tunnels": [], "favorites": []}')
        link = self.backend.data_file
        link.symlink_to("../dotfiles/tunnels.json")
        row = self.save()
        self.assertTrue(link.is_symlink())
        self.assertEqual(json.loads(target.read_text()), self.backend.data())
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.backend.favorite(row["id"], False)
        self.assertTrue(link.is_symlink())
        self.assertEqual(json.loads(target.read_text())["favorites"], [])
        self.backend.save(dict(row, name="Updated"))
        self.assertTrue(link.is_symlink())
        self.assertEqual(json.loads(target.read_text())["tunnels"][0]["name"], "Updated")
        self.backend.remove(row["id"])
        self.assertTrue(link.is_symlink())
        self.assertEqual(json.loads(target.read_text())["tunnels"], [])

    def test_broken_config_symlink_is_not_replaced(self):
        link = self.backend.data_file
        target = self.base / "missing.json"
        link.symlink_to(target)
        with self.assertRaises(FileNotFoundError):
            tunnels.write_json(link, {"version": 1, "tunnels": [], "favorites": []})
        self.assertTrue(link.is_symlink())
        self.assertFalse(target.exists())

    def test_duplicate_and_invalid_definitions_are_rejected(self):
        self.save()
        for changes in ({}, {"localPort": "0"}, {"remotePort": "65536"}, {"localPort": "12.5"},
                        {"remoteHost": "x;touch /tmp/injected"}, {"alias": "-oProxyCommand=evil"}):
            with self.subTest(changes=changes), self.assertRaises(tunnels.TunnelError):
                self.save(**changes)

    def test_ipv6_destination_and_multiple_tunnels_per_alias(self):
        self.save()
        second = self.save(localPort=15433, remoteHost="[::1]")
        self.assertEqual(second["remoteHost"], "::1")
        self.assertEqual(len(self.backend.snapshot()["tunnels"]), 2)

    def test_config_changes_invalidate_cache(self):
        self.assertEqual(len(self.backend.snapshot()["aliases"]), 1)
        self.config.write_text(self.config.read_text() + "Host extra\n HostName 127.0.0.2\n")
        self.assertEqual(len(self.backend.snapshot()["aliases"]), 2)

    def test_corrupt_saved_state_is_not_overwritten(self):
        self.backend.data_file.write_text("broken {")
        with self.assertRaises(tunnels.TunnelError):
            self.save()
        self.assertEqual(self.backend.data_file.read_text(), "broken {")

    def test_missing_saved_alias_remains_visible(self):
        row = self.save()
        self.config.write_text("")
        state = self.backend.snapshot()["tunnels"][0]
        self.assertTrue(state["missing"])
        with self.assertRaises(tunnels.TunnelError):
            self.backend.start(row["id"])
        self.backend.remove(row["id"])

    def test_running_removed_config_remains_stoppable(self):
        record = dict(id="config:gone", alias="gone", name="Gone", configured=True, forwards=[])
        tunnels.write_json(self.backend.registry_file, {record["id"]: record})
        with patch.object(self.backend, "active", return_value=True):
            snapshot = self.backend.snapshot()
        self.assertTrue(snapshot["tunnels"][0]["missing"])
        self.assertTrue(snapshot["tunnels"][0]["active"])

    def test_control_commands_never_load_user_ssh_config(self):
        with patch.object(self.backend, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            self.backend.control("saved:a", "exit")
        args = run.call_args.args[0]
        self.assertEqual(args[:2], ["-F", "/dev/null"])
        self.assertIn(str(self.backend.socket("saved:a")), args)
        self.assertNotEqual(self.backend.socket("saved:a"), self.backend.socket("saved:b"))

    def test_active_saved_tunnels_cannot_be_edited_or_removed(self):
        row = self.save()
        with patch.object(self.backend, "active", return_value=True):
            with self.assertRaises(tunnels.TunnelError):
                self.backend.save(row)
            with self.assertRaises(tunnels.TunnelError):
                self.backend.remove(row["id"])

    def test_private_data_permissions(self):
        self.save()
        self.assertEqual(self.backend.data_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.backend.runtime.stat().st_mode & 0o777, 0o700)

    def test_shadow_ssh_and_unrelated_environment_are_ignored(self):
        shadow = self.base / "shadow"
        shadow.mkdir()
        marker = self.base / "shadow-ran"
        (shadow / "ssh").write_text(f"#!/bin/sh\nprintf shadow > '{marker}'\nexit 97\n")
        (shadow / "ssh").chmod(0o755)
        with patch.dict(os.environ, {"PATH": str(shadow), "PYTHONPATH": str(shadow),
                                    "LD_PRELOAD": "/nonexistent-test-loader.so", "BASH_ENV": str(shadow),
                                    "SESSION_SECRET": "do-not-inherit", "SSH_AUTH_SOCK": "/test/agent.sock"}):
            backend = tunnels.Backend(self.config, self.dropins, self.base / "data", self.base / "run", self.system)
            result = backend.run(["-V"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("OpenSSH", result.stderr)
        self.assertFalse(marker.exists())
        self.assertEqual(backend.environment["PATH"], "/usr/bin")
        self.assertEqual(backend.environment["SSH_AUTH_SOCK"], "/test/agent.sock")
        self.assertLessEqual(set(backend.environment), set(tunnels.ENVIRONMENT_KEYS) | {"PATH"})
        for key in ("PYTHONPATH", "LD_PRELOAD", "BASH_ENV", "SESSION_SECRET"):
            self.assertNotIn(key, backend.environment)

    def test_start_uses_pinned_ssh_and_explicit_environment(self):
        row = dict(self.save(), configured=False)
        with patch.object(self.backend, "active", side_effect=[False, True]), \
                patch.object(self.backend, "snapshot", return_value={"tunnels": [row]}), \
                patch.object(self.backend, "catalog", return_value=([], [], [])), \
                patch.object(self.backend, "control", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                patch.object(tunnels.subprocess, "Popen") as popen:
            popen.return_value.wait.return_value = 0
            self.backend.start(row["id"])
        self.assertEqual(popen.call_args.args[0][0], str(Path("/usr/bin/ssh").resolve()))
        self.assertEqual(popen.call_args.kwargs["env"], self.backend.environment)

    def test_discovery_and_control_use_pinned_ssh_and_explicit_environment(self):
        with patch.object(tunnels.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            self.backend.resolve("dev", [], str(self.config))
            self.backend.control("saved:test", "check")
            self.backend.control("saved:test", "exit")
        self.assertEqual(len(run.call_args_list), 3)
        for call in run.call_args_list:
            self.assertEqual(call.args[0][0], str(Path("/usr/bin/ssh").resolve()))
            self.assertEqual(call.kwargs["env"], self.backend.environment)

    def test_untrusted_ssh_is_rejected_without_path_fallback(self):
        executable = self.base / "ssh"
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o755)
        for candidate in ("ssh", str(executable), str(self.base / "missing-ssh")):
            with self.subTest(candidate=candidate), self.assertRaises(tunnels.TunnelError):
                tunnels.trusted_executable(candidate)
        with patch.object(tunnels, "SSH_EXECUTABLE", str(executable)), self.assertRaises(tunnels.TunnelError):
            tunnels.Backend(self.config, self.dropins, self.base / "data", self.base / "run", self.system)

    def test_writable_or_non_root_system_paths_are_rejected(self):
        for owner, mode in ((1000, stat.S_IFREG | 0o755), (0, stat.S_IFREG | 0o775), (0, stat.S_IFREG | 0o757)):
            with self.subTest(owner=owner, mode=mode), \
                    patch.object(Path, "stat", return_value=SimpleNamespace(st_uid=owner, st_mode=mode)), \
                    self.assertRaises(tunnels.TunnelError):
                tunnels.trusted_executable("/usr/bin/ssh")


if __name__ == "__main__":
    unittest.main()
