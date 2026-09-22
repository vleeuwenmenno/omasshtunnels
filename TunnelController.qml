import QtQuick
import Quickshell.Io

Item {
    id: root
    visible: false

    property var aliases: []
    property var tunnels: []
    property var warnings: []
    property string error: ""
    property bool loaded: false
    property bool stale: false
    property bool autoLoad: true
    property string action: ""
    property string actionId: ""
    readonly property bool busy: worker.running
    readonly property int activeCount: tunnels.filter(function(t) { return t.active; }).length
    readonly property string helper: decodeURIComponent(Qt.resolvedUrl("bin/tunnels.py").toString().replace(/^file:\/\//, ""))

    signal saved()

    function request(operation, payload) {
        if (worker.running) return;
        action = operation;
        actionId = payload && payload.id ? payload.id : "";
        if (operation !== "list") error = "";
        worker.command = ["python3", root.helper, operation, JSON.stringify(payload || {})];
        worker.running = true;
    }

    function refresh(force) { request(force ? "refresh" : "list", {}); }

    Component.onCompleted: if (autoLoad) refresh(false)

    Timer {
        interval: 5000
        running: root.autoLoad
        repeat: true
        onTriggered: if (!root.busy) root.refresh(false)
    }

    Process {
        id: worker
        stdout: StdioCollector { id: output; waitForEnd: true }
        stderr: StdioCollector { id: errors; waitForEnd: true }
        onExited: function(exitCode) {
            var operation = root.action;
            try {
                var result = JSON.parse(output.text);
                if (!result.ok || exitCode !== 0) {
                    root.error = result.error || errors.text || "Tunnel operation failed.";
                    if (operation === "list" || operation === "refresh") root.stale = true;
                } else {
                    root.aliases = result.aliases || [];
                    root.tunnels = result.tunnels || [];
                    root.warnings = result.warnings || [];
                    root.loaded = true;
                    root.stale = false;
                    if (operation === "save") root.saved();
                }
            } catch (error) {
                root.error = errors.text || "Could not read tunnel status. Check that Python 3 and OpenSSH are installed.";
                root.stale = true;
            }
            root.action = "";
            root.actionId = "";
        }
    }
}
