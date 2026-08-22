import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "nixfred.rift"

  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false
  readonly property string helperPath: Qt.resolvedUrl("rift_helper.py").toString().replace(/^file:\/\//, "")

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
    if ("helperPath" in target) target.helperPath = root.helperPath
  }

  function open() { if (panelLoader.item) panelLoader.item.open() }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  function togglePanel() { if (panelLoader.item) panelLoader.item.toggle() }
  function closeForPopoutSwitch() { if (panelLoader.item) panelLoader.item.closeForPopoutSwitch() }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Component.onCompleted: {
    console.debug("rift: widget loaded, helper:", root.helperPath)
    startupProcess.running = true
  }

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  Process {
    id: startupProcess
    command: ["python3", root.helperPath, "startup-open"]
    stdout: SplitParser { onRead: function(line) { console.debug("rift: startup-open:", line) } }
    stderr: SplitParser { onRead: function(line) { console.warn("rift: startup-open stderr:", line) } }
    onExited: function(exitCode) { if (exitCode !== 0) console.warn("rift: startup-open exit", exitCode) }
  }

  IpcHandler {
    target: "nixfred.rift"
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.togglePanel() }
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰦛"
    fontSize: Style.font.icon
    fixedWidth: root.vertical ? root.barSize : Style.bar.statusSlot
    active: root.opened
    tooltipText: "Rifts v" + (panelLoader.item && panelLoader.item.riftVersion ? panelLoader.item.riftVersion : "")
    onPressed: function(b) {
      if (b === Qt.LeftButton) root.togglePanel()
    }
  }
}
