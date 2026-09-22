"""Exercise real OpenSSH forwarding against a temporary loopback-only SSH server."""

import getpass
from pathlib import Path
import shutil
import socket
import socketserver
import subprocess
import tempfile
import threading
import time

from test_backend import tunnels


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class Echo(socketserver.BaseRequestHandler):
    def handle(self):
        data = self.request.recv(100)
        self.request.sendall(data)


def traffic(port):
    with socket.create_connection(("127.0.0.1", port), timeout=3) as client:
        client.sendall(b"tunnel-verified")
        assert client.recv(100) == b"tunnel-verified"


def main():
    with tempfile.TemporaryDirectory(prefix="ssh-integration-") as directory:
        base = Path(directory)
        for name in ("host_key", "client_key"):
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(base / name)], check=True)
        ssh_port = free_port()
        echo = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Echo)
        thread = threading.Thread(target=echo.serve_forever, daemon=True)
        thread.start()
        destination_port = echo.server_address[1]
        server_config = base / "sshd_config"
        server_config.write_text(f"""Port {ssh_port}
ListenAddress 127.0.0.1
HostKey {base / 'host_key'}
PidFile {base / 'sshd.pid'}
AuthorizedKeysFile {base / 'client_key.pub'}
StrictModes no
PasswordAuthentication no
KbdInteractiveAuthentication no
UsePAM no
AllowTcpForwarding yes
AllowUsers {getpass.getuser()}
LogLevel ERROR
""")
        host_key = (base / "host_key.pub").read_text().split()
        (base / "known_hosts").write_text(f"[127.0.0.1]:{ssh_port} {host_key[0]} {host_key[1]}\n")
        inherited_port = free_port()
        client_config = base / "config"
        client_config.write_text(f"""Host integration
  HostName 127.0.0.1
  Port {ssh_port}
  User {getpass.getuser()}
  IdentityFile {base / 'client_key'}
  IdentitiesOnly yes
  UserKnownHostsFile {base / 'known_hosts'}
  ControlPath {base / 'unrelated.sock'}
  LocalForward 127.0.0.1:{inherited_port} 127.0.0.1:{destination_port}
""")
        system = base / "system"
        system.write_text("")
        backend = tunnels.Backend(client_config, base / "config.d", base / "data", base / "run", system)
        started = []
        with (base / "sshd.log").open("w+") as log:
            server = subprocess.Popen([shutil.which("sshd"), "-D", "-e", "-f", str(server_config)], stderr=log)
            try:
                for _ in range(50):
                    if server.poll() is not None:
                        log.seek(0)
                        raise RuntimeError(log.read())
                    try:
                        with socket.create_connection(("127.0.0.1", ssh_port), timeout=0.1):
                            break
                    except OSError:
                        time.sleep(0.05)
                else:
                    raise RuntimeError("Test SSH server did not start")

                # A separate master represents a normal terminal session.
                subprocess.run(["ssh", "-F", str(client_config), "-MNf", "-o", "BatchMode=yes",
                                "-o", "StrictHostKeyChecking=yes", "-o", "ClearAllForwardings=yes",
                                "integration"], check=True)
                ports = [free_port(), free_port()]
                for index, port in enumerate(ports):
                    backend.save(dict(alias="integration", name=f"Test {index}", localPort=port,
                                      remoteHost="127.0.0.1", remotePort=destination_port))
                    tunnel_id = backend.data()["tunnels"][-1]["id"]
                    started.append(tunnel_id)
                    backend.start(tunnel_id)
                    traffic(port)
                assert not backend.socket("config:integration").exists()
                with socket.socket() as probe:
                    assert probe.connect_ex(("127.0.0.1", inherited_port)) != 0, "Saved tunnel loaded unwanted config forwards"

                backend.start("config:integration")
                started.append("config:integration")
                traffic(inherited_port)

                # A conflicting listener must fail without killing the owner.
                backend.save(dict(alias="integration", name="Conflict", localPort=ports[1],
                                  remoteHost="localhost", remotePort=destination_port))
                conflict = backend.data()["tunnels"][-1]["id"]
                started.append(conflict)
                try:
                    backend.start(conflict)
                except tunnels.TunnelError:
                    pass
                else:
                    raise AssertionError("Port conflict was reported as connected")
                assert not backend.active(conflict)
                traffic(ports[1])

                reloaded = tunnels.Backend(client_config, base / "config.d", base / "data", base / "run", system)
                assert sum(row["active"] for row in reloaded.snapshot()["tunnels"]) == 3
                reloaded.stop(started[0])
                assert not reloaded.active(started[0])
                traffic(ports[1])
                result = subprocess.run(["ssh", "-F", "/dev/null", "-S", str(base / "unrelated.sock"),
                                         "-O", "check", "localhost"], capture_output=True)
                assert result.returncode == 0, "Stopping a tunnel killed an unrelated session"
                print("PASS: two independent saved tunnels carry traffic; config forwards work; port conflicts fail; reload preserves status; Stop leaves other sessions running.")
            finally:
                for tunnel_id in started:
                    backend.stop(tunnel_id)
                subprocess.run(["ssh", "-F", "/dev/null", "-S", str(base / "unrelated.sock"),
                                "-O", "exit", "localhost"], capture_output=True)
                server.terminate()
                server.wait(timeout=5)
                echo.shutdown()
                echo.server_close()


if __name__ == "__main__":
    main()
