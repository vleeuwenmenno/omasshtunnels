#!/usr/bin/env python3
"""Install the local plugin and optionally enable it in the running Omarchy shell."""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


ROOT = Path(__file__).resolve().parent
FILES = ("manifest.json", "BarWidget.qml", "TunnelPanel.qml", "TunnelController.qml",
         "Model.js", "bin/tunnels.py", "README.md", "LICENSE")


def shell_command(args, **kwargs):
    # Larger existing shells can spend several seconds rebuilding widgets.
    environment = dict(os.environ, OMARCHY_SHELL_IPC_TIMEOUT="15s")
    return subprocess.run(args, env=environment, timeout=25, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--destination", type=Path, help="Override the installation directory (for packaging checks)")
    parser.add_argument("--replace-id", help="Disable and archive a previous local plugin ID")
    args = parser.parse_args()
    plugin_id = json.loads((ROOT / "manifest.json").read_text())["id"]
    destination = args.destination or Path.home() / ".config/omarchy/plugins" / plugin_id
    if args.replace_id:
        if args.replace_id == plugin_id or "/" in args.replace_id or ".." in args.replace_id:
            raise SystemExit("Invalid previous plugin ID.")
        previous = destination.parent / args.replace_id
        if previous.exists():
            manifest = previous / "manifest.json"
            if (previous.is_symlink() or not manifest.is_file() or (previous / ".git").exists()
                    or json.loads(manifest.read_text()).get("id") != args.replace_id):
                raise SystemExit("Refusing to replace an unrelated plugin directory.")
            shell_command(["omarchy", "plugin", "disable", args.replace_id], check=True)
            backup = destination.parent.parent / "plugin-backups" / (args.replace_id + "-renamed-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
            backup.parent.mkdir(parents=True, exist_ok=True)
            previous.rename(backup)
            print(f"Archived old plugin ID to {backup}")
    if destination.is_symlink():
        raise SystemExit("Refusing to replace a symlinked plugin directory.")
    subprocess.run(["omarchy", "plugin", "validate", str(ROOT)], check=True)
    if destination.exists():
        manifest = destination / "manifest.json"
        if not manifest.is_file() or json.loads(manifest.read_text()).get("id") != plugin_id:
            raise SystemExit(f"Refusing to overwrite an unrelated directory: {destination}")
        if (destination / ".git").exists():
            raise SystemExit("This plugin is a git checkout. Update it with git instead of the local installer.")
        backup = destination.parent.parent / "plugin-backups" / (plugin_id + "-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        shutil.copytree(destination, backup)
        print(f"Previous version backed up to {backup}")
    destination.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        target = destination / name
        if target.is_symlink() or any(parent.is_symlink() for parent in target.parents if parent != destination.parent):
            raise SystemExit(f"Refusing to write through a symlink: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() == (ROOT / name).read_bytes():
            continue
        fd, temporary = tempfile.mkstemp(prefix=".install-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write((ROOT / name).read_bytes())
            os.chmod(temporary, 0o644)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    subprocess.run(["omarchy", "plugin", "validate", str(destination)], check=True)
    print(f"Installed {plugin_id} to {destination}")
    if args.enable:
        shell_command(["omarchy-shell", "shell", "rescanPlugins"], check=True)
        # Rescan starts an asynchronous process; its IPC reply is not a signal
        # that the new manifest is already in the registry.
        for _ in range(40):
            result = shell_command(["omarchy", "plugin", "list", "--json"], capture_output=True, text=True)
            if result.returncode == 0 and any(row["id"] == plugin_id for row in json.loads(result.stdout)):
                break
            time.sleep(0.25)
        else:
            raise SystemExit("Files installed, but the shell has not discovered the plugin. Try omarchy-shell shell rescanPlugins.")
        result = shell_command(["omarchy", "plugin", "enable", plugin_id], capture_output=True, text=True)
        # Enabling writes shell.json before rebuilding the bar. If rebuilding
        # outlives the IPC request, verify persisted enabled state before failing.
        if result.returncode:
            status = shell_command(["omarchy", "plugin", "list", "--json"], capture_output=True, text=True)
            if status.returncode or not any(row["id"] == plugin_id and row["enabled"] for row in json.loads(status.stdout)):
                raise SystemExit(result.stderr.strip() or "Could not enable the plugin.")
        else:
            print(result.stdout.strip())
        if args.replace_id and not args.destination:
            # A queued shell config write during plugin reload can restore an
            # entry after disable acknowledged it. Remove only that old ID from
            # the latest document, retaining a backup of the original contents.
            config_path = Path.home() / ".config/omarchy/shell.json"
            contents = config_path.read_bytes()
            config = json.loads(contents)
            changed = False
            for section, entries in config.get("bar", {}).get("layout", {}).items():
                filtered = [entry for entry in entries if entry.get("id") != args.replace_id]
                if len(filtered) != len(entries):
                    config["bar"]["layout"][section] = filtered
                    changed = True
            if changed:
                if config_path.read_bytes() != contents:
                    raise SystemExit("Shell config changed during cleanup; rerun installation to remove the old bar entry.")
                backup = config_path.with_name("shell.json.before-plugin-rename-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
                shutil.copy2(config_path, backup)
                fd, temporary = tempfile.mkstemp(prefix=".shell-", dir=config_path.parent)
                with os.fdopen(fd, "w") as stream:
                    json.dump(config, stream, indent=2)
                    stream.write("\n")
                os.replace(temporary, config_path)
                shell_command(["omarchy-shell", "shell", "reloadConfig"], check=True)
        print("Enabled SSH Tunnels in the bar. No tunnels have been started.")


if __name__ == "__main__":
    main()
