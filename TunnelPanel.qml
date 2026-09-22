pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import qs.Commons
import qs.Ui as Ui
import "Model.js" as Model

Ui.Panel {
    id: root
    moduleName: "vleeuwenmenno.sshtunnels"
    manageIpc: false

    property var anchorItem: null
    property var hostWidget: null
    required property var backend
    property string tab: "tunnels"
    property string filter: "all"
    property string editingAlias: ""
    property string editingId: ""
    property string removingId: ""
    readonly property color foreground: bar ? bar.foreground : Color.foreground
    readonly property var shownTunnels: Model.tunnels(backend.tunnels, search.text, filter)
    readonly property var shownAliases: Model.aliases(backend.aliases, search.text)

    function open() {
        backend.refresh(false);
        controller.show();
    }

    function close() { controller.hide(); }
    function toggle() { opened ? close() : open(); }

    function edit(alias, row) {
        editingAlias = alias;
        editingId = row ? row.id : "";
        nameField.text = row ? row.name : "";
        localPort.text = row ? String(row.localPort) : "";
        remoteHost.text = row ? row.remoteHost : "localhost";
        remotePort.text = row ? String(row.remotePort) : "";
        Qt.callLater(function() { localPort.forceActiveFocus(); });
    }

    function save() {
        backend.request("save", {id: editingId, alias: editingAlias, name: nameField.text,
            localPort: localPort.text, remoteHost: remoteHost.text, remotePort: remotePort.text});
    }

    Connections {
        target: root.backend
        function onSaved() {
            root.editingAlias = "";
            root.editingId = "";
            root.tab = "tunnels";
            root.filter = "all";
            search.text = "";
        }
    }

    Ui.KeyboardPanel {
        id: popup
        anchorItem: root.anchorItem
        owner: root.hostWidget || root
        bar: root.bar
        open: root.opened
        focusTarget: search
        contentWidth: fittedContentWidth(Style.space(540))
        contentHeight: fittedContentHeight(Style.space(590))

        Item {
            anchors.fill: parent
            Keys.onEscapePressed: root.close()

            ColumnLayout {
                anchors.fill: parent
                spacing: Style.space(10)

                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: "SSH Tunnels"
                        color: root.foreground
                        font.family: Style.font.family
                        font.pixelSize: Style.font.subtitle
                        font.bold: true
                        Layout.fillWidth: true
                    }
                    Text {
                        text: root.backend.activeCount + " active"
                        color: root.backend.activeCount > 0 ? Color.accent : Color.muted
                        font.family: Style.font.family
                        font.pixelSize: Style.font.bodySmall
                    }
                    Ui.Button {
                        text: "Refresh"
                        foreground: root.foreground
                        focusable: true
                        enabled: !root.backend.busy
                        onClicked: root.backend.refresh(true)
                    }
                }

                Ui.TextField {
                    id: search
                    Layout.fillWidth: true
                    foreground: root.foreground
                    placeholderText: "Search aliases, tunnels, or ports…"
                    Keys.onEscapePressed: root.close()
                }

                RowLayout {
                    Layout.fillWidth: true
                    Ui.Button {
                        text: "Tunnels (" + root.backend.tunnels.length + ")"
                        selected: root.tab === "tunnels"
                        foreground: root.foreground
                        focusable: true
                        onClicked: root.tab = "tunnels"
                    }
                    Ui.Button {
                        text: "SSH aliases (" + root.backend.aliases.length + ")"
                        selected: root.tab === "aliases"
                        foreground: root.foreground
                        focusable: true
                        onClicked: root.tab = "aliases"
                    }
                    Item { Layout.fillWidth: true }
                    Text {
                        visible: root.backend.busy
                        text: root.backend.action === "start" ? "Connecting…"
                            : root.backend.action === "stop" ? "Stopping…" : "Loading…"
                        color: Color.muted
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption
                    }
                }

                RowLayout {
                    visible: root.tab === "tunnels" && root.editingAlias === ""
                    Repeater {
                        model: [{key: "all", label: "All"}, {key: "starred", label: "★ Starred"}, {key: "active", label: "Active"}]
                        Ui.Button {
                            required property var modelData
                            text: modelData.label
                            selected: root.filter === modelData.key
                            foreground: root.foreground
                            focusable: true
                            onClicked: root.filter = modelData.key
                        }
                    }
                }

                RowLayout {
                    visible: root.backend.error !== ""
                    Layout.fillWidth: true
                    Text {
                        text: root.backend.error
                        textFormat: Text.PlainText
                        color: Color.urgent
                        font.family: Style.font.family
                        font.pixelSize: Style.font.bodySmall
                        wrapMode: Text.WrapAnywhere
                        maximumLineCount: 5
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                    Ui.Button {
                        text: "Dismiss"
                        foreground: root.foreground
                        focusable: true
                        onClicked: root.backend.error = ""
                    }
                }

                Text {
                    visible: root.backend.warnings.length > 0
                    text: root.backend.warnings.join("\n")
                    textFormat: Text.PlainText
                    color: Color.urgent
                    wrapMode: Text.WrapAnywhere
                    maximumLineCount: 3
                    Layout.fillWidth: true
                    font.family: Style.font.family
                    font.pixelSize: Style.font.bodySmall
                }

                // Saving a forward never rewrites the user's SSH configuration.
                ColumnLayout {
                    visible: root.editingAlias !== ""
                    Layout.fillWidth: true
                    spacing: Style.space(8)
                    Text {
                        text: (root.editingId ? "Edit tunnel via " : "New tunnel via ") + root.editingAlias
                        textFormat: Text.PlainText
                        color: root.foreground
                        font.family: Style.font.family
                        font.pixelSize: Style.font.body
                        font.bold: true
                        elide: Text.ElideMiddle
                        Layout.fillWidth: true
                    }
                    Ui.TextField {
                        id: nameField
                        Layout.fillWidth: true
                        placeholderText: "Name (optional), e.g. Dev database"
                        foreground: root.foreground
                        maximumLength: 100
                    }
                    GridLayout {
                        columns: 2
                        Layout.fillWidth: true
                        columnSpacing: Style.space(12)
                        Text { text: "Local port"; color: root.foreground; font.family: Style.font.family; font.pixelSize: Style.font.bodySmall }
                        Ui.TextField {
                            id: localPort
                            Layout.fillWidth: true
                            placeholderText: "15432"
                            foreground: root.foreground
                            validator: IntValidator { bottom: 1; top: 65535 }
                        }
                        Text { text: "Destination host"; color: root.foreground; font.family: Style.font.family; font.pixelSize: Style.font.bodySmall }
                        Ui.TextField {
                            id: remoteHost
                            Layout.fillWidth: true
                            text: "localhost"
                            foreground: root.foreground
                        }
                        Text { text: "Destination port"; color: root.foreground; font.family: Style.font.family; font.pixelSize: Style.font.bodySmall }
                        Ui.TextField {
                            id: remotePort
                            Layout.fillWidth: true
                            placeholderText: "5432"
                            foreground: root.foreground
                            validator: IntValidator { bottom: 1; top: 65535 }
                            onAccepted: if (saveButton.enabled) root.save()
                        }
                    }
                    Text {
                        text: "Destination is reached from the SSH server. Local listener: 127.0.0.1."
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        color: Color.muted
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption
                    }
                    RowLayout {
                        Ui.Button {
                            id: saveButton
                            text: root.editingId ? "Save changes" : "Save & star"
                            selected: true
                            foreground: root.foreground
                            focusable: true
                            enabled: !root.backend.busy && localPort.acceptableInput && localPort.text !== ""
                                && remotePort.acceptableInput && remotePort.text !== "" && remoteHost.text.trim() !== ""
                            onClicked: root.save()
                        }
                        Ui.Button {
                            text: "Cancel"
                            foreground: root.foreground
                            focusable: true
                            onClicked: root.editingAlias = ""
                        }
                    }
                    Item { Layout.fillHeight: true }
                }

                Controls.ScrollView {
                    id: scroll
                    visible: root.editingAlias === ""
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    contentWidth: availableWidth
                    Controls.ScrollBar.horizontal.policy: Controls.ScrollBar.AlwaysOff

                    Column {
                        width: scroll.availableWidth
                        spacing: Style.space(8)

                        Text {
                            visible: root.tab === "tunnels" ? root.shownTunnels.length === 0 : root.shownAliases.length === 0
                            width: parent.width
                            topPadding: Style.space(18)
                            bottomPadding: Style.space(18)
                            text: !root.backend.loaded ? (root.backend.error ? "Could not load SSH configuration." : "Reading SSH configuration…")
                                : search.text.trim() ? "No matches. Try another alias or port."
                                : root.tab === "aliases" ? "No literal Host aliases found in ~/.ssh/config or ~/.ssh/config.d."
                                : root.filter === "starred" ? "No starred tunnels. Use the star beside a tunnel."
                                : root.filter === "active" ? "No active tunnels."
                                : "No tunnels saved yet. Open SSH aliases, find a host, and choose Add tunnel."
                            textFormat: Text.PlainText
                            color: Color.muted
                            wrapMode: Text.WordWrap
                            font.family: Style.font.family
                            font.pixelSize: Style.font.body
                        }

                        Repeater {
                            model: root.tab === "tunnels" ? root.shownTunnels : []
                            Rectangle {
                                id: tunnelRow
                                required property var modelData
                                width: parent.width
                                height: rowContent.implicitHeight + Style.space(16)
                                color: Style.normalFillFor(root.foreground, Color.accent)
                                radius: Style.cornerRadius

                                ColumnLayout {
                                    id: rowContent
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.top: parent.top
                                    anchors.margins: Style.space(8)
                                    spacing: Style.space(4)
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Ui.Button {
                                            text: tunnelRow.modelData.starred ? "★" : "☆"
                                            tooltipText: tunnelRow.modelData.starred ? "Unstar tunnel" : "Star tunnel"
                                            foreground: tunnelRow.modelData.starred ? Color.accent : root.foreground
                                            enabled: !root.backend.busy
                                            focusable: true
                                            onClicked: root.backend.request("favorite", {id: tunnelRow.modelData.id, enabled: !tunnelRow.modelData.starred})
                                        }
                                        Text {
                                            text: tunnelRow.modelData.name
                                            textFormat: Text.PlainText
                                            color: root.foreground
                                            font.family: Style.font.family
                                            font.pixelSize: Style.font.body
                                            font.bold: true
                                            elide: Text.ElideRight
                                            Layout.fillWidth: true
                                        }
                                        Ui.Button {
                                            text: root.backend.actionId === tunnelRow.modelData.id && root.backend.action === "start" ? "Starting…"
                                                : root.backend.actionId === tunnelRow.modelData.id && root.backend.action === "stop" ? "Stopping…"
                                                : tunnelRow.modelData.active ? "Stop" : "Start"
                                            selected: tunnelRow.modelData.active
                                            foreground: root.foreground
                                            focusable: true
                                            enabled: !root.backend.busy && (tunnelRow.modelData.active
                                                || (!root.backend.stale && !tunnelRow.modelData.error && !tunnelRow.modelData.missing))
                                            onClicked: root.backend.request(tunnelRow.modelData.active ? "stop" : "start", {id: tunnelRow.modelData.id})
                                        }
                                    }
                                    Text {
                                        text: tunnelRow.modelData.alias + " · " + (root.backend.stale ? "Status unavailable" : tunnelRow.modelData.active ? "Active" : "Stopped")
                                            + (tunnelRow.modelData.configured ? " · SSH config" : "")
                                        textFormat: Text.PlainText
                                        color: tunnelRow.modelData.active ? Color.accent : Color.muted
                                        font.family: Style.font.family
                                        font.pixelSize: Style.font.caption
                                        Layout.fillWidth: true
                                        elide: Text.ElideRight
                                    }
                                    Text {
                                        text: Model.details(tunnelRow.modelData)
                                        textFormat: Text.PlainText
                                        color: root.foreground
                                        font.family: Style.font.family
                                        font.pixelSize: Style.font.bodySmall
                                        wrapMode: Text.WrapAnywhere
                                        Layout.fillWidth: true
                                    }
                                    Text {
                                        visible: !!tunnelRow.modelData.error
                                        text: tunnelRow.modelData.error || ""
                                        textFormat: Text.PlainText
                                        color: Color.urgent
                                        font.family: Style.font.family
                                        font.pixelSize: Style.font.caption
                                        wrapMode: Text.WrapAnywhere
                                        Layout.fillWidth: true
                                    }
                                    RowLayout {
                                        visible: !tunnelRow.modelData.configured
                                        Ui.Button {
                                            text: "Edit"
                                            foreground: root.foreground
                                            focusable: true
                                            enabled: !root.backend.busy && !tunnelRow.modelData.active && !tunnelRow.modelData.missing
                                            onClicked: root.edit(tunnelRow.modelData.alias, tunnelRow.modelData)
                                        }
                                        Ui.Button {
                                            text: root.removingId === tunnelRow.modelData.id ? "Confirm remove" : "Remove"
                                            foreground: root.removingId === tunnelRow.modelData.id ? Color.urgent : root.foreground
                                            focusable: true
                                            enabled: !root.backend.busy && !tunnelRow.modelData.active
                                            onClicked: {
                                                if (root.removingId === tunnelRow.modelData.id) {
                                                    root.backend.request("remove", {id: tunnelRow.modelData.id});
                                                    root.removingId = "";
                                                } else root.removingId = tunnelRow.modelData.id;
                                            }
                                        }
                                        Ui.Button {
                                            visible: root.removingId === tunnelRow.modelData.id
                                            text: "Cancel"
                                            foreground: root.foreground
                                            focusable: true
                                            onClicked: root.removingId = ""
                                        }
                                    }
                                }
                            }
                        }

                        Repeater {
                            model: root.tab === "aliases" ? root.shownAliases : []
                            ColumnLayout {
                                id: aliasRow
                                required property var modelData
                                width: parent.width
                                spacing: Style.space(4)
                                RowLayout {
                                    Layout.fillWidth: true
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        Text {
                                            text: aliasRow.modelData.alias
                                            textFormat: Text.PlainText
                                            color: root.foreground
                                            font.family: Style.font.family
                                            font.pixelSize: Style.font.body
                                            font.bold: true
                                            elide: Text.ElideRight
                                            Layout.fillWidth: true
                                        }
                                        Text {
                                            text: aliasRow.modelData.error || (aliasRow.modelData.user + "@" + aliasRow.modelData.hostname + ":" + aliasRow.modelData.port)
                                            textFormat: Text.PlainText
                                            color: aliasRow.modelData.error ? Color.urgent : Color.muted
                                            font.family: Style.font.family
                                            font.pixelSize: Style.font.caption
                                            elide: Text.ElideMiddle
                                            Layout.fillWidth: true
                                        }
                                    }
                                    Ui.Button {
                                        text: "Add tunnel"
                                        foreground: root.foreground
                                        focusable: true
                                        enabled: !root.backend.busy && !root.backend.stale && !aliasRow.modelData.error
                                        onClicked: root.edit(aliasRow.modelData.alias, null)
                                    }
                                }
                                Ui.PanelSeparator { Layout.fillWidth: true; foreground: root.foreground }
                            }
                        }
                    }
                }

                Text {
                    text: "Stars save favorites. Tunnels stay running when this panel closes."
                    color: Color.muted
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
            }
        }
    }
}
