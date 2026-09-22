import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
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


if __name__ == "__main__":
    unittest.main()
