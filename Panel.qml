import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "nixfred.rift"
  ipcTarget: "nixfred.rift"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  property string helperPath: ""
  property var stateData: ({ workspace: { id: 0, name: "" }, apps: [], rifts: [], currentRift: "", changed: false })
  property string mode: "browse"
  property int selectedIndex: 0
  property var includedApps: ({})
  property string statusText: ""
  property string errorText: ""
  property string pendingAction: ""
  property string stateOutput: ""
  property string actionOutput: ""
  property bool refreshPending: false

  readonly property var barIdentity: hostWidget || root
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property var rifts: stateData.rifts || []
  readonly property var apps: stateData.apps || []
  readonly property var currentRift: {
    for (var i = 0; i < rifts.length; i++)
      if (rifts[i].slug === stateData.currentRift) return rifts[i]
    return null
  }

  function helperCommand(args) {
    var command = ["python3", helperPath]
    for (var i = 0; i < args.length; i++) command.push(String(args[i]))
    return command
  }

  function notify(title, body) {
    Quickshell.execDetached(["notify-send", "-a", "Rift", title, body])
  }

  function refresh() {
    if (!helperPath) return
    if (stateProcess.running) {
      refreshPending = true
      return
    }
    refreshPending = false
    stateOutput = ""
    stateProcess.command = helperCommand(["state"])
    console.debug("rift: run", JSON.stringify(stateProcess.command))
    stateProcess.running = true
  }

  function runAction(action, args) {
    if (!helperPath || actionProcess.running) return
    pendingAction = action
    actionOutput = ""
    errorText = ""
    actionProcess.command = helperCommand(args)
    console.debug("rift: run", JSON.stringify(actionProcess.command))
    actionProcess.running = true
  }

  function open() {
    mode = "browse"
    statusText = ""
    errorText = ""
    refresh()
    controller.show()
  }

  function close() {
    controller.hide()
    mode = "browse"
  }

  function toggle() { opened ? close() : open() }

  function closeForPopoutSwitch() {
    popoutSwitchClosing = true
    close()
    Qt.callLater(function() { popoutSwitchClosing = false })
  }

  function beginSave(suggestedName) {
    mode = "save"
    var next = ({})
    for (var i = 0; i < apps.length; i++) next[apps[i].id] = apps[i].selected !== false
    includedApps = next
    Qt.callLater(function() {
      nameField.text = suggestedName || ""
      nameField.selectAll()
      nameField.forceActiveFocus()
    })
  }

  function toggleIncluded(appId) {
    var next = ({})
    for (var key in includedApps) next[key] = includedApps[key]
    next[appId] = !next[appId]
    includedApps = next
  }

  function selectedAppIds() {
    var ids = []
    for (var i = 0; i < apps.length; i++) if (includedApps[apps[i].id]) ids.push(apps[i].id)
    return ids
  }

  function saveCurrent() {
    var name = String(nameField.text || "").trim()
    if (name === "") {
      errorText = "Give this Rift a name"
      nameField.forceActiveFocus()
      return
    }
    runAction("save", ["save", name, "--apps", selectedAppIds().join("\x1f")])
  }

  function openRift(slug) { runAction("open", ["open", slug]) }

  function beginNewRift() { beginSave("") }

  function headerAction() {
    if (currentRift) updateCurrent()
    else beginNewRift()
  }

  function revertCurrent() {
    if (currentRift && currentRift.previous) runAction("revert", ["revert", currentRift.slug])
  }

  function toggleStartup(rift) {
    runAction("startup", ["startup", rift.slug, rift.startup ? "off" : "on"])
  }

  // One-click: re-record whatever is on this workspace under the current Rift's name.
  function updateCurrent() {
    if (currentRift) runAction("update", ["save", currentRift.name])
  }

  function moveSelection(delta) {
    if (mode !== "browse" || rifts.length === 0) return
    selectedIndex = Math.max(0, Math.min(rifts.length - 1, selectedIndex + delta))
  }

  function activateSelection() {
    if (mode === "browse" && rifts.length > 0) openRift(rifts[selectedIndex].slug)
  }

  Process {
    id: stateProcess
    stdout: SplitParser { onRead: function(line) { root.stateOutput += line } }
    stderr: SplitParser { onRead: function(line) { console.warn("rift: helper stderr:", line) } }
    onExited: function(exitCode) {
      console.debug("rift: state exited", exitCode, "bytes:", root.stateOutput.length)
      if (root.refreshPending) {
        root.refreshPending = false
        console.debug("rift: discarding stale state response; queued refresh follows")
        Qt.callLater(root.refresh)
        return
      }
      try {
        var response = JSON.parse(root.stateOutput)
        if (!response.ok) throw new Error(response.error || "Could not inspect this workspace")
        root.stateData = response.data
        console.debug("rift: state workspace", response.data.workspace.id, "apps:", response.data.apps.length, "rifts:", response.data.rifts.length, "current:", response.data.currentRift || "-")
        if (root.selectedIndex >= root.rifts.length) root.selectedIndex = Math.max(0, root.rifts.length - 1)
      } catch (error) {
        console.warn("rift: state failed:", String(error.message || error), "raw:", root.stateOutput.slice(0, 300))
        root.errorText = String(error.message || error)
      }
    }
  }

  Process {
    id: actionProcess
    stdout: SplitParser { onRead: function(line) { root.actionOutput += line } }
    stderr: SplitParser { onRead: function(line) { console.warn("rift: helper stderr:", line) } }
    onExited: function(exitCode) {
      console.debug("rift: action", root.pendingAction, "exited", exitCode, "raw:", root.actionOutput.slice(0, 300))
      try {
        var response = JSON.parse(root.actionOutput)
        if (!response.ok) throw new Error(response.error || "Rift action failed")
        if (root.pendingAction === "open") {
          if (response.data.action === "failed") {
            root.errorText = "No applications could be launched. Fix the saved recipes or try again."
            root.notify("Rift could not open", response.data.failed + " application" + (response.data.failed === 1 ? " needs" : "s need") + " attention.")
            root.refresh()
          } else if (response.data.action === "focused") {
            root.notify("Rift focused", "Switched to workspace " + response.data.workspace + ".")
            root.close()
          } else if (response.data.action === "partial") {
            root.notify("Rift partially opened", response.data.launched + " launched · " + response.data.failed + " failed on workspace " + response.data.workspace + ".")
            root.close()
          } else {
            root.notify("Rift opened", response.data.launched + " application" + (response.data.launched === 1 ? " is" : "s are") + " launching on workspace " + response.data.workspace + ".")
            root.close()
          }
        } else if (root.pendingAction === "update") {
          root.mode = "browse"
          root.statusText = "Updated " + response.data.name + " · " + response.data.apps.length + " application" + (response.data.apps.length === 1 ? "" : "s")
          root.notify("Rift updated", response.data.name + " now has " + response.data.apps.length + " application" + (response.data.apps.length === 1 ? "" : "s"))
          root.refresh()
        } else if (root.pendingAction === "revert") {
          root.statusText = "Reverted " + response.data.name + " to its previous recipe"
          root.notify("Rift reverted", response.data.name)
          root.refresh()
        } else if (root.pendingAction === "save") {
          root.mode = "browse"
          root.statusText = "Saved " + response.data.name
          root.notify("Rift saved", response.data.apps.length + " application" + (response.data.apps.length === 1 ? "" : "s"))
          root.refresh()
        } else {
          root.refresh()
        }
      } catch (error) {
        console.warn("rift: action", root.pendingAction, "failed:", String(error.message || error))
        root.errorText = String(error.message || error)
      }
      root.pendingAction = ""
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(430))
    contentHeight: panel.fittedContentHeight(contentColumn.implicitHeight, Style.space(650))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: root.mode === "save"
      onMoveRequested: function(dx, dy) { if (dy !== 0) root.moveSelection(dy) }
      onActivateRequested: root.activateSelection()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(text) {
        if (text === "n" || text === "N") root.beginNewRift()
        else if (text === "s" || text === "S") root.beginSave(root.currentRift ? root.currentRift.name : "")
        else if ((text === "u" || text === "U") && root.currentRift) root.updateCurrent()
        else if ((text === "r" || text === "R") && root.currentRift && root.currentRift.previous) root.revertCurrent()
      }

      Flickable {
        id: scroll
        anchors.fill: parent
        contentWidth: width
        contentHeight: contentColumn.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        Column {
          id: contentColumn
          width: scroll.width
          spacing: Style.space(10)

          Row {
            width: parent.width
            spacing: Style.space(10)

            Text {
              text: "󰦛"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.display
            }

            Column {
              width: parent.width - parent.children[0].width - freshButton.width - parent.spacing * 2
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(2)

              Text {
                text: "RIFTS"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.title
                font.bold: true
                font.letterSpacing: 1.2
              }

              Text {
                text: root.currentRift
                  ? (root.currentRift.name + " · workspace " + root.stateData.workspace.id)
                  : ("Workspace " + root.stateData.workspace.id + " · unsaved")
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }
            }

            Button {
              id: freshButton
              iconText: root.currentRift ? "󰑐" : "＋"
              tooltipText: root.currentRift
                ? ("Update " + root.currentRift.name + " with this workspace · U")
                : "Save this workspace as a new Rift · N"
              foreground: root.foreground
              active: root.currentRift ? root.stateData.changed === true : false
              focusable: true
              enabled: !actionProcess.running
              onClicked: root.headerAction()
            }
          }

          Rectangle {
            width: parent.width
            height: 1
            color: root.dim
            opacity: 0.28
          }

          Column {
            visible: root.mode === "browse"
            width: parent.width
            spacing: Style.space(8)

            Button {
              width: parent.width
              text: root.currentRift
                ? (root.stateData.changed ? "Update " + root.currentRift.name + " with this workspace" : root.currentRift.name + " is up to date")
                : "Save this workspace as a Rift"
              iconText: root.currentRift ? (root.stateData.changed ? "󰑐" : "󰄬") : "󰆓"
              leftAlign: true
              foreground: root.foreground
              active: root.currentRift && !root.stateData.changed
              focusable: true
              enabled: !actionProcess.running
              onClicked: {
                if (root.currentRift && root.stateData.changed) root.updateCurrent()
                else if (!root.currentRift) root.beginNewRift()
              }
            }

            Button {
              visible: root.currentRift && root.currentRift.previous ? true : false
              width: parent.width
              text: "Revert " + (root.currentRift ? root.currentRift.name : "") + " to its previous recipe"
              iconText: "󰕌"
              leftAlign: true
              foreground: root.foreground
              focusable: true
              enabled: !actionProcess.running
              onClicked: root.revertCurrent()
            }

            Text {
              visible: root.rifts.length === 0
              width: parent.width
              text: "No Rifts yet. Stand on a workspace, open what belongs there, and save it."
              wrapMode: Text.WordWrap
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
            }

            Repeater {
              model: root.rifts

              CursorSurface {
                required property var modelData
                required property int index
                width: parent.width
                height: Style.space(58)
                hasCursor: root.selectedIndex === index
                current: root.stateData.currentRift === modelData.slug
                foreground: root.foreground

                MouseArea {
                  anchors.fill: parent
                  acceptedButtons: Qt.LeftButton
                  onClicked: {
                    root.selectedIndex = index
                    root.openRift(modelData.slug)
                  }
                }

                Row {
                  anchors.fill: parent
                  anchors.leftMargin: Style.space(12)
                  anchors.rightMargin: Style.space(8)
                  spacing: Style.space(10)

                  Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: root.stateData.currentRift === modelData.slug ? "󰦛" : "󰆍"
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.icon
                  }

                  Column {
                    anchors.verticalCenter: parent.verticalCenter
                    width: parent.width - parent.children[0].width - startupButton.width - parent.spacing * 2
                    spacing: Style.space(2)

                    Text {
                      width: parent.width
                      text: modelData.name
                      elide: Text.ElideRight
                      color: root.foreground
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.body
                      font.bold: true
                    }

                    Text {
                      width: parent.width
                      text: modelData.apps.length + " application" + (modelData.apps.length === 1 ? "" : "s")
                        + (modelData.startup ? " · opens at login" : "")
                      elide: Text.ElideRight
                      color: root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                    }
                  }

                  PanelActionButton {
                    id: startupButton
                    anchors.verticalCenter: parent.verticalCenter
                    iconText: modelData.startup ? "󰐥" : "󰒲"
                    tooltipText: modelData.startup ? "Disable startup" : "Open at login"
                    foreground: root.foreground
                    hoverColor: root.foreground
                    onClicked: root.toggleStartup(modelData)
                  }
                }

              }
            }
          }

          Column {
            visible: root.mode === "save"
            width: parent.width
            spacing: Style.space(10)

            Text {
              text: root.currentRift ? "UPDATE THIS RIFT" : "SAVE THIS WORKSPACE"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.1
            }

            TextField {
              id: nameField
              width: parent.width
              placeholderText: "Name this Rift"
              foreground: root.foreground
              onAccepted: root.saveCurrent()
              Keys.onEscapePressed: {
                root.mode = "browse"
                keyCatcher.forceActiveFocus()
              }
            }

            Text {
              width: parent.width
              visible: root.apps.length === 0
              text: "This workspace has no savable application windows yet. Open some apps and try again."
              wrapMode: Text.WordWrap
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
            }

            Repeater {
              model: root.apps

              Button {
                required property var modelData
                width: parent.width
                iconText: root.includedApps[modelData.id] ? "󰄬" : "󰅖"
                text: modelData.name + (modelData.cwd ? "  ·  " + modelData.cwd : "")
                selected: root.includedApps[modelData.id] === true
                leftAlign: true
                foreground: root.foreground
                focusable: true
                onClicked: root.toggleIncluded(modelData.id)
              }
            }

            Row {
              width: parent.width
              spacing: Style.space(8)

              Button {
                text: "Cancel"
                foreground: root.foreground
                focusable: true
                onClicked: {
                  root.mode = "browse"
                  keyCatcher.forceActiveFocus()
                }
              }

              Item { width: Math.max(0, parent.width - parent.children[0].width - parent.children[2].width - parent.spacing * 2); height: 1 }

              Button {
                text: actionProcess.running ? "Saving…" : "Save Rift"
                iconText: "󰆓"
                foreground: root.foreground
                active: true
                enabled: !actionProcess.running && root.apps.length > 0
                focusable: true
                onClicked: root.saveCurrent()
              }
            }
          }

          Text {
            visible: root.statusText !== "" || root.errorText !== ""
            width: parent.width
            text: root.errorText !== "" ? root.errorText : root.statusText
            wrapMode: Text.WordWrap
            color: root.errorText !== "" ? root.urgent : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }

          Text {
            visible: actionProcess.running || stateProcess.running
            width: parent.width
            text: actionProcess.running ? "Opening the Rift…" : "Reading this workspace…"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            horizontalAlignment: Text.AlignHCenter
          }
        }
      }
    }
  }
}
