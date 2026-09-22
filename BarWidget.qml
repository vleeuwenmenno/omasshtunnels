import QtQuick
import qs.Ui as Ui

Ui.BarWidget {
    id: root
    moduleName: "vleeuwenmenno.sshtunnels"

    readonly property bool opened: panel.opened
    readonly property bool popoutSwitchClosing: panel.popoutSwitchClosing
    readonly property real openPanelIndicatorWidth: button.labelWidth

    function open() { panel.open(); }
    function close() { panel.close(); }
    function toggle() { panel.toggle(); }
    function closeForPopoutSwitch() { panel.closeForPopoutSwitch(); }

    implicitWidth: button.implicitWidth
    implicitHeight: button.implicitHeight

    TunnelController { id: tunnelBackend }

    TunnelPanel {
        id: panel
        bar: root.bar
        anchorItem: button
        hostWidget: root
        backend: tunnelBackend
    }

    Ui.WidgetButton {
        id: button
        anchors.fill: parent
        bar: root.bar
        text: String.fromCodePoint(0xf048d) + (!root.vertical && tunnelBackend.activeCount > 0 ? " " + tunnelBackend.activeCount : "")
        tooltipText: tunnelBackend.stale ? "SSH tunnels: status unavailable"
            : "SSH tunnels · " + tunnelBackend.activeCount + " active"
        onPressed: function(mouseButton) {
            if (mouseButton === Qt.RightButton) tunnelBackend.refresh(true);
            else if (mouseButton === Qt.LeftButton) root.toggle();
        }
    }
}
