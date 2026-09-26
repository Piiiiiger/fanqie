import QtQuick
import Quickshell
import Quickshell.Io
import qs.Common
import qs.Widgets
import qs.Modules.Plugins

PluginComponent {
    id: root

    property bool timerActive: false
    property string task: ""
    property string timeText: ""
    property string scriptPath: pluginService && pluginId ? pluginService.getPluginPath(pluginId) + "/status.py" : ""

    pillClickAction: () => Quickshell.execDetached(["fanqie"])

    onTimerActiveChanged: setVisibilityOverride(timerActive)

    Component.onCompleted: {
        setVisibilityOverride(false);
        Qt.callLater(poll);
    }

    function poll() {
        if (scriptPath && !statusProcess.running) {
            statusProcess.running = true;
        }
    }

    function applyStatus(text) {
        const status = JSON.parse(text);
        timerActive = status.active === true;
        if (timerActive) {
            task = status.task || "Countdown";
            timeText = status.time || "00:00";
        }
    }

    onScriptPathChanged: Qt.callLater(poll)

    Timer {
        interval: 1000
        repeat: true
        running: true
        onTriggered: root.poll()
    }

    Process {
        id: statusProcess
        command: root.scriptPath ? [root.scriptPath] : []

        stdout: StdioCollector {
            id: statusOutput
        }

        onExited: exitCode => {
            if (exitCode !== 0) {
                root.timerActive = false;
                return;
            }
            try {
                root.applyStatus(statusOutput.text.trim());
            } catch (error) {
                root.timerActive = false;
                console.warn("fanqie: invalid status", error);
            }
        }
    }

    horizontalBarPill: Component {
        StyledText {
            width: Math.min(implicitWidth, 280)
            text: root.task + " · " + root.timeText
            color: Theme.surfaceText
            font.pixelSize: Theme.fontSizeSmall
            font.weight: Font.Medium
            elide: Text.ElideRight
            maximumLineCount: 1
            anchors.verticalCenter: parent.verticalCenter
        }
    }

    verticalBarPill: Component {
        StyledText {
            text: root.timeText
            color: Theme.surfaceText
            font.pixelSize: Theme.fontSizeSmall
            anchors.horizontalCenter: parent.horizontalCenter
        }
    }
}
