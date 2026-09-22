"""Run the actual QML controller against a diagnostic helper, without the desktop."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    with tempfile.TemporaryDirectory(prefix="tunnel-qml-test-") as directory:
        base = Path(directory)
        (base / "bin").mkdir()
        (base / "shadow").mkdir()
        (base / "runtime").mkdir(mode=0o700)
        shutil.copyfile(Path(__file__).parents[1] / "TunnelController.qml", base / "TunnelController.qml")
        marker = base / "untrusted-code-ran"
        for executable in ("python3", "ssh"):
            file = base / "shadow" / executable
            file.write_text(f"#!/bin/sh\nprintf shadow > '{marker}'\nexit 97\n")
            file.chmod(0o755)
        (base / "shadow/sitecustomize.py").write_text(f"open({str(marker)!r}, 'w').write('sitecustomize')\n")
        (base / "bin/tunnels.py").write_text('''import json, os, sys
print(json.dumps({"ok": True, "tunnels": [], "aliases": [dict(os.environ),
    {"isolated": sys.flags.isolated, "no_site": sys.flags.no_site, "pid": os.getpid()}]}))
''')
        (base / "shell.qml").write_text('''import QtQuick
import Quickshell

ShellRoot {
    id: root
    property int firstPid: 0
    TunnelController { id: controller }

    function verify(condition, message) {
        if (!condition) {
            console.error(message);
            Qt.exit(1);
            throw new Error(message);
        }
    }

    function checkEnvironment() {
        var environment = controller.aliases[0];
        var allowed = ["PATH", "HOME", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR", "LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "SSH_AUTH_SOCK"];
        verify(environment.PATH === "/usr/bin", "Untrusted PATH");
        verify(environment.SSH_AUTH_SOCK === "/test/ssh-agent.sock", "Agent socket lost");
        verify(!!environment.HOME, "HOME lost");
        verify(!!environment.XDG_CONFIG_HOME, "Config path lost");
        verify(!!environment.XDG_RUNTIME_DIR, "Runtime path lost");
        Object.keys(environment).forEach(function(key) { verify(allowed.indexOf(key) >= 0, "Unexpected variable: " + key); });
        verify(controller.aliases[1].isolated === 1, "Python is not isolated");
        verify(controller.aliases[1].no_site === 1, "Python site initialization enabled");
        verify(controller.error === "", controller.error);
    }

    Timer {
        interval: 100; running: true; repeat: true
        onTriggered: {
            root.verify(controller.error === "", controller.error);
            if (!controller.loaded) return;
            root.checkEnvironment();
            if (root.firstPid === 0) root.firstPid = controller.aliases[1].pid;
            else if (controller.aliases[1].pid !== root.firstPid) {
                console.log("RUNTIME_ENVIRONMENT_PASSED");
                Qt.quit();
            }
        }
    }
    Timer {
        interval: 11000; running: true
        onTriggered: { console.error("Timed out waiting for startup and polling"); Qt.exit(1); }
    }
}
''')
        environment = dict(os.environ, PATH=str(base / "shadow") + ":/usr/bin",
                           PYTHONPATH=str(base / "shadow"), PYTHONHOME=str(base / "invalid-python-home"),
                           BASH_ENV=str(base / "shadow"), LD_LIBRARY_PATH=str(base / "shadow"),
                           SESSION_SECRET="do-not-inherit", SSH_AUTH_SOCK="/test/ssh-agent.sock",
                           XDG_CONFIG_HOME=str(base / "config"), XDG_RUNTIME_DIR=str(base / "runtime"),
                           QT_QPA_PLATFORM="offscreen", QT_QPA_PLATFORMTHEME="generic", QT_QUICK_CONTROLS_STYLE="Basic")
        runner = Path("/usr/bin/quickshell")
        if not runner.is_file():
            raise SystemExit("Quickshell is required for the controller regression test.")
        result = subprocess.run([str(runner), "-p", str(base / "shell.qml")], env=environment,
                                capture_output=True, text=True, timeout=20)
        if result.returncode or "RUNTIME_ENVIRONMENT_PASSED" not in result.stdout + result.stderr:
            raise AssertionError(result.stdout + result.stderr)
        assert not marker.exists(), "A shadow executable or Python startup hook ran"
        print("PASS: QML startup and polling use isolated system Python with only the allowed environment.")


if __name__ == "__main__":
    main()
