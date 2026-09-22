#!/usr/bin/python3 -IS
"""SSH configuration discovery and isolated tunnel control. Standard library only."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import fcntl
import glob
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid


class TunnelError(Exception):
    pass


SSH_EXECUTABLE = "/usr/bin/ssh"
ENVIRONMENT_KEYS = ("HOME", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR", "LANG",
                    "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "SSH_AUTH_SOCK")


def runtime_environment():
    # This is also enforced for direct CLI calls. Never forward loader,
    # interpreter, shell startup, or arbitrary session variables to SSH.
    environment = {key: os.environ[key] for key in ENVIRONMENT_KEYS if os.environ.get(key)}
    environment["PATH"] = "/usr/bin"
    return environment


def trusted_executable(filename):
    """Resolve a system executable only through root-owned, non-writable paths."""
    path = Path(filename)
    if not path.is_absolute():
        raise TunnelError("SSH executable must have an absolute path.")
    try:
        resolved = path.resolve(strict=True)
        for candidate in {path, resolved, *path.parents, *resolved.parents}:
            info = candidate.stat()
            if info.st_uid != 0 or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                raise TunnelError(f"Untrusted SSH executable path: {candidate}")
        if not stat.S_ISREG(resolved.stat().st_mode) or not os.access(resolved, os.X_OK):
            raise TunnelError(f"SSH executable is not an executable file: {resolved}")
    except OSError as error:
        raise TunnelError(f"Cannot validate system OpenSSH at {path}: {error}") from error
    return str(resolved)


def private_dir(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise TunnelError(f"Not a private directory owned by you: {path}")
    path.chmod(0o700)
    return path


def read_json(path, fallback):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return fallback
    except (ValueError, OSError) as error:
        raise TunnelError(f"Cannot read {path.name}: {error}") from error


def write_json(path, value):
    # Replace atomically, so a shell reload or concurrent monitor never sees
    # half a document. Callers hold the runtime lock while changing state.
    # A user may keep this file in dotfiles. Replace its resolved target rather
    # than the link itself, and reject broken links instead of losing settings.
    if path.is_symlink():
        path = path.resolve(strict=True)
    fd, name = tempfile.mkstemp(prefix=".tunnels-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def valid_alias(alias):
    return (isinstance(alias, str) and bool(alias) and len(alias) <= 255
            and not alias.startswith(("-", "!"))
            and not any(c.isspace() or c in "*?[]/\\\"'`$;|&<>\x00" for c in alias))


def directive(line):
    match = re.match(r"^\s*([A-Za-z]+)(?:\s*=\s*|\s+)(.*)$", line)
    if not match:
        return "", []
    try:
        return match[1].lower(), shlex.split(match[2], comments=True)
    except ValueError:
        # OpenSSH will report malformed configuration during resolution.
        return "", []


class Backend:
    def __init__(self, ssh_config=None, config_dir=None, data_dir=None, runtime_dir=None, system_config=None):
        self.ssh = trusted_executable(SSH_EXECUTABLE)
        self.environment = runtime_environment()
        home = Path.home()
        self.ssh_config = Path(ssh_config or home / ".ssh/config").expanduser()
        self.config_dir = Path(config_dir or home / ".ssh/config.d").expanduser()
        self.system_config = Path(system_config or "/etc/ssh/ssh_config")
        self.data_dir = private_dir(Path(data_dir or Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "omasshtunnels"))
        runtime_base = Path(os.environ.get("XDG_RUNTIME_DIR", tempfile.gettempdir()))
        self.runtime = private_dir(Path(runtime_dir or runtime_base / f"omasshtunnels-{os.getuid()}"))
        self.data_file = self.data_dir / "tunnels.json"
        self.registry_file = self.runtime / "active.json"
        self.cache_file = self.runtime / "catalog.json"
        self.wrapper = self.runtime / "ssh-config"

    @contextmanager
    def locked(self):
        with (self.runtime / "lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def data(self):
        value = read_json(self.data_file, {"version": 1, "tunnels": [], "favorites": []})
        if (not isinstance(value, dict) or value.get("version") != 1
                or not isinstance(value.get("tunnels"), list)
                or not isinstance(value.get("favorites"), list)):
            raise TunnelError("Unsupported tunnels.json format; restore a valid version 1 file.")
        return value

    def sources(self):
        aliases, files, warnings = {}, {}, []
        home = str(Path.home())

        def visit(path, depth=0, system=False):
            path = path.resolve()
            if path in files or not path.is_file():
                return
            if depth > 32:
                warnings.append("SSH Include nesting exceeds 32 levels.")
                return
            try:
                content = path.read_text()
            except (OSError, UnicodeError) as error:
                warnings.append(f"Cannot read {path.name}: {error}")
                return
            files[path] = content
            for line in content.splitlines():
                key, values = directive(line)
                if key == "host":
                    for alias in values:
                        if valid_alias(alias):
                            aliases.setdefault(alias, str(path))
                elif key == "include":
                    for pattern in values:
                        pattern = os.path.expandvars(pattern.replace("%d", home))
                        pattern = os.path.expanduser(pattern)
                        if not os.path.isabs(pattern):
                            pattern = str((self.system_config.parent if system else self.ssh_config.parent) / pattern)
                        for included in sorted(glob.glob(pattern)):
                            visit(Path(included), depth + 1, system)

        visit(self.ssh_config)
        # Files already reached through Include retain their original position
        # and conditions. Unreferenced drop-ins are appended as extra config.
        extras = []
        if self.config_dir.is_dir():
            for path in sorted(self.config_dir.iterdir()):
                if path.is_file() and not path.name.startswith(".") and path.suffix in ("", ".conf"):
                    if path.resolve() not in files:
                        extras.append(path)
                        visit(path)
        visit(self.system_config, system=True)
        return aliases, files, extras, warnings

    def config_args(self, extras):
        if not extras and self.ssh_config == Path.home() / ".ssh/config":
            return []
        paths = ([self.ssh_config] if self.ssh_config.is_file() else []) + extras
        if self.system_config.is_file():
            paths.append(self.system_config)
        # -F omits the system config, so explicitly include it last. Reset Host
        # before each independent file; do not accidentally inherit a last block.
        text = "".join('Host *\nInclude "' + str(p).replace("\\", "\\\\").replace('"', '\\"') + '"\n' for p in paths)
        if not self.wrapper.exists() or self.wrapper.read_text() != text:
            self.wrapper.write_text(text)
            self.wrapper.chmod(0o600)
        return ["-F", str(self.wrapper)]

    def run(self, args, timeout=5):
        try:
            return subprocess.run([self.ssh, *args], env=self.environment, stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise TunnelError("SSH timed out. Check the host, network, and authentication.") from error
        except OSError as error:
            raise TunnelError(f"Could not run OpenSSH: {error}") from error

    def resolve(self, alias, args, source):
        row = {"alias": alias, "source": source, "forwards": [], "error": ""}
        try:
            result = self.run([*args, "-G", "-o", "BatchMode=yes", "--", alias])
            if result.returncode:
                raise TunnelError(result.stderr.strip()[-1500:] or "SSH configuration could not be resolved.")
            for line in result.stdout.splitlines():
                key, _, value = line.partition(" ")
                if key in ("hostname", "user", "port"):
                    row[key] = value
                elif key in ("localforward", "remoteforward", "dynamicforward") and value != "none":
                    row["forwards"].append({"kind": key, "value": value})
                elif key == "clearallforwardings" and value == "yes":
                    row["clearForwards"] = True
            if row.get("clearForwards"):
                row["forwards"] = []
        except TunnelError as error:
            row["error"] = str(error)
        return row

    def catalog(self, force=False):
        aliases, files, extras, warnings = self.sources()
        args = self.config_args(extras)
        fingerprint = hashlib.sha256(json.dumps(
            [args, [(str(k), v) for k, v in files.items()], [str(p) for p in extras]],
            sort_keys=True).encode()).hexdigest()
        cached = read_json(self.cache_file, {})
        if not force and cached.get("fingerprint") == fingerprint and time.time() - cached.get("time", 0) < 60:
            return cached["aliases"], args, warnings
        with ThreadPoolExecutor(max_workers=6) as pool:
            rows = list(pool.map(lambda a: self.resolve(a, args, aliases[a]), sorted(aliases, key=str.casefold)))
        write_json(self.cache_file, {"fingerprint": fingerprint, "time": time.time(), "aliases": rows})
        return rows, args, warnings

    def socket(self, tunnel_id):
        name = hashlib.sha256(tunnel_id.encode()).hexdigest()[:24]
        path = self.runtime / (name + ".sock")
        if len(os.fsencode(path)) > 100:
            raise TunnelError("Runtime directory is too long for an SSH control socket.")
        return path

    def control(self, tunnel_id, operation, forward=None):
        # Never evaluate user SSH config on a stop/check request. In particular,
        # an unrelated ControlPath must not target a normal terminal session.
        args = ["-F", "/dev/null", "-S", str(self.socket(tunnel_id)), "-O", operation]
        if forward:
            args += ["-L", forward]
        return self.run([*args, "--", "localhost"])

    def active(self, tunnel_id):
        return self.socket(tunnel_id).exists() and self.control(tunnel_id, "check").returncode == 0

    def snapshot(self, force=False):
        aliases, _, warnings = self.catalog(force)
        data = self.data()
        rows = [dict(t, configured=False) for t in data["tunnels"]]
        for alias in aliases:
            if alias["forwards"]:
                rows.append({"id": "config:" + alias["alias"], "name": alias["alias"],
                             "alias": alias["alias"], "configured": True,
                             "forwards": alias["forwards"]})
        registry = read_json(self.registry_file, {})
        known = {t["id"] for t in rows}
        for tunnel_id, record in registry.items():
            if tunnel_id not in known and self.active(tunnel_id):
                rows.append(dict(record, missing=True))
        current = {a["alias"]: a for a in aliases}
        for row in rows:
            row["active"] = self.active(row["id"])
            row["starred"] = row["id"] in data["favorites"]
            alias = current.get(row["alias"])
            row["error"] = alias.get("error", "") if alias else "SSH alias no longer exists."
            row["missing"] = row.get("missing", False) or alias is None
            if row["active"] and row["id"] in registry:
                # Display the forwarding actually running, even after config edits.
                row["runningDefinition"] = registry[row["id"]]
        rows.sort(key=lambda row: (not row["starred"], row["name"].casefold()))
        return {"ok": True, "aliases": aliases, "tunnels": rows, "warnings": warnings}

    def save(self, value):
        aliases, _, _ = self.catalog()
        alias = value.get("alias", "")
        if not valid_alias(alias) or alias not in {a["alias"] for a in aliases}:
            raise TunnelError("Choose an alias from your SSH config.")
        try:
            local_port, remote_port = int(value["localPort"]), int(value["remotePort"])
            if not (1 <= local_port <= 65535 and 1 <= remote_port <= 65535):
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise TunnelError("Ports must be whole numbers from 1 to 65535.") from None
        host = str(value.get("remoteHost", "localhost")).strip()
        try:
            ipaddress.ip_address(host.strip("[]"))
            host = host.strip("[]")
        except ValueError:
            if not re.fullmatch(r"[A-Za-z0-9_](?:[A-Za-z0-9_.-]{0,251}[A-Za-z0-9_])?", host):
                raise TunnelError("Enter a destination hostname or IP address.") from None
        name = str(value.get("name", "")).strip() or f"{alias} :{local_port}"
        if len(name) > 100 or any(ord(c) < 32 for c in name):
            raise TunnelError("Name must be at most 100 characters, without control characters.")
        data = self.data()
        tunnel_id = value.get("id") or "saved:" + uuid.uuid4().hex
        if value.get("id") and tunnel_id not in {t["id"] for t in data["tunnels"]}:
            raise TunnelError("Saved tunnel no longer exists.")
        if self.active(tunnel_id):
            raise TunnelError("Stop the tunnel before editing it.")
        row = {"id": tunnel_id, "name": name, "alias": alias,
               "localPort": local_port, "remoteHost": host, "remotePort": remote_port}
        for existing in data["tunnels"]:
            if existing["id"] != tunnel_id and all(existing.get(k) == row[k] for k in ("alias", "localPort", "remoteHost", "remotePort")):
                raise TunnelError("This tunnel is already saved.")
        data["tunnels"] = [t for t in data["tunnels"] if t["id"] != tunnel_id] + [row]
        if not value.get("id"):
            data["favorites"].append(tunnel_id)
        write_json(self.data_file, data)

    def favorite(self, tunnel_id, enabled):
        if tunnel_id not in {t["id"] for t in self.snapshot()["tunnels"]}:
            raise TunnelError("Tunnel no longer exists.")
        data = self.data()
        data["favorites"] = [item for item in data["favorites"] if item != tunnel_id]
        if enabled:
            data["favorites"].append(tunnel_id)
        write_json(self.data_file, data)

    def remove(self, tunnel_id):
        if self.active(tunnel_id):
            raise TunnelError("Stop the tunnel before removing it.")
        data = self.data()
        if tunnel_id not in {t["id"] for t in data["tunnels"]}:
            raise TunnelError("Only saved tunnels can be removed here. SSH config is never edited.")
        data["tunnels"] = [t for t in data["tunnels"] if t["id"] != tunnel_id]
        data["favorites"] = [item for item in data["favorites"] if item != tunnel_id]
        write_json(self.data_file, data)
        registry = read_json(self.registry_file, {})
        registry.pop(tunnel_id, None)
        write_json(self.registry_file, registry)

    def stop(self, tunnel_id):
        if self.active(tunnel_id):
            result = self.control(tunnel_id, "exit")
            if result.returncode:
                raise TunnelError(result.stderr.strip() or "Could not stop the tunnel.")
            for _ in range(20):
                if not self.active(tunnel_id):
                    break
                time.sleep(0.05)
            else:
                raise TunnelError("SSH has not stopped yet. Refresh and try again.")
        registry = read_json(self.registry_file, {})
        registry.pop(tunnel_id, None)
        write_json(self.registry_file, registry)

    def start(self, tunnel_id):
        if self.active(tunnel_id):
            return
        snapshot = self.snapshot(force=True)
        target = next((t for t in snapshot["tunnels"] if t["id"] == tunnel_id), None)
        if target is None or target.get("missing"):
            raise TunnelError("Tunnel or SSH alias no longer exists.")
        if target.get("error"):
            raise TunnelError(target["error"])
        _, args, _ = self.catalog()
        socket = self.socket(tunnel_id)
        if socket.exists():
            socket.unlink()  # An unresponsive stale socket in our private directory.
        args += ["-M", "-S", str(socket), "-N", "-T", "-f",
                 "-o", "ControlMaster=yes", "-o", "ControlPersist=no",
                 "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                 "-o", "ExitOnForwardFailure=yes", "-o", "ConnectTimeout=10",
                 "-o", "ConnectionAttempts=1", "-o", "ServerAliveInterval=30",
                 "-o", "ServerAliveCountMax=3", "-o", "PermitLocalCommand=no",
                 "-o", "RemoteCommand=none", "-o", "SessionType=none",
                 "-o", "ForwardAgent=no", "-o", "ForwardX11=no"]
        if not target["configured"]:
            # First open a master without config forwards, then request exactly
            # this saved forward through its socket. ClearAllForwardings also
            # clears command-line -L, so using both on one command would fail.
            args += ["-o", "ClearAllForwardings=yes", "-o", "GatewayPorts=no"]
        args += ["--", target["alias"]]
        record = {k: v for k, v in target.items() if k not in ("active", "starred", "error", "runningDefinition")}
        registry = read_json(self.registry_file, {})
        registry[tunnel_id] = record
        write_json(self.registry_file, registry)
        log = self.runtime / (socket.stem + ".log")
        try:
            # A background master inherits stderr. A file avoids keeping the
            # helper's output pipe open for the entire lifetime of the tunnel.
            with log.open("w") as stderr:
                process = subprocess.Popen([self.ssh, *args], env=self.environment, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.DEVNULL, stderr=stderr,
                                           start_new_session=True)
                try:
                    code = process.wait(timeout=25)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=5)
                    raise TunnelError("Connection timed out after 25 seconds.") from None
            if code != 0:
                raise TunnelError(log.read_text().strip()[-1800:] or "SSH connection failed.")
            if not self.active(tunnel_id):
                raise TunnelError("SSH exited before its control socket became available.")
            if not target["configured"]:
                host = target["remoteHost"]
                if ":" in host:
                    host = "[" + host + "]"
                spec = f"127.0.0.1:{target['localPort']}:{host}:{target['remotePort']}"
                result = self.control(tunnel_id, "forward", spec)
                if result.returncode:
                    raise TunnelError(result.stderr.strip() or "Could not bind the local port.")
        except (TunnelError, OSError) as error:
            self.stop(tunnel_id)
            raise TunnelError(str(error)) from error


def main():
    environment = runtime_environment()
    os.environ.clear()
    os.environ.update(environment)
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("ssh-config", "config-dir", "data-dir", "runtime-dir"):
        parser.add_argument("--" + option)
    parser.add_argument("action", choices=["list", "refresh", "save", "favorite", "remove", "start", "stop"])
    parser.add_argument("payload", nargs="?", default="{}", help="JSON action arguments")
    args = parser.parse_args()
    try:
        backend = Backend(args.ssh_config, args.config_dir, args.data_dir, args.runtime_dir)
        payload = json.loads(args.payload)
        if not isinstance(payload, dict):
            raise TunnelError("Action arguments must be a JSON object.")
        with backend.locked():
            if args.action == "save":
                backend.save(payload)
            elif args.action == "favorite":
                backend.favorite(payload["id"], payload.get("enabled") is True)
            elif args.action in ("remove", "start", "stop"):
                getattr(backend, args.action)(payload["id"])
            print(json.dumps(backend.snapshot(force=args.action == "refresh")))
        return 0
    except (TunnelError, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
