import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Hyprland
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "nixfred.rift"
  ipcTarget: "nixfred.rift"
  manageIpc: false

  readonly property string riftVersion: "0.3.0"  // keep in lockstep with manifest.json (tests enforce)
  property var anchorItem: null
  property var hostWidget: null
  property string helperPath: ""
  property var stateData: ({ workspace: { id: 0, name: "" }, apps: [], rifts: [], currentRift: "", changed: false })
  property string mode: "browse"          // browse | new | detail | save
  property string detailSlug: ""
  property bool confirmDelete: false
  property int selectedIndex: 0
  property var includedApps: ({})
  property string statusText: ""
  property string errorText: ""
  property string pendingAction: ""
  property string stateOutput: ""
  property string actionOutput: ""
  property bool refreshPending: false
  // The panel's model is only trustworthy when it describes the workspace that
  // is focused RIGHT NOW. Anything that writes (save/update/revert) is disabled
  // while stale, and the helper double-checks with --expect-workspace anyway.
  property bool stateStale: true
  readonly property int liveWorkspaceId: Hyprland.focusedWorkspace ? Hyprland.focusedWorkspace.id : 0
  readonly property bool stale: stateStale || stateProcess.running
    || (stateData.workspace && stateData.workspace.id !== liveWorkspaceId)
  readonly property bool canWrite: !stale && !actionProcess.running
  onLiveWorkspaceIdChanged: if (opened) { stateStale = true; refresh() }

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
  readonly property var detailRift: {
    for (var i = 0; i < rifts.length; i++)
      if (rifts[i].slug === detailSlug) return rifts[i]
    return null
  }
  readonly property bool helpOn: stateData.help === true
  // Update is only offered from inside an entry, and only when that Rift is
  // the one open on the workspace you are standing on right now.
  readonly property bool detailIsHere: detailRift !== null && detailRift.openWorkspace === liveWorkspaceId && liveWorkspaceId > 0

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
    stateStale = true
    refresh()
    controller.show()
  }

  function close() {
    controller.hide()
    mode = "browse"
    confirmDelete = false
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
    if (selectedAppIds().length === 0) {
      errorText = "Open at least one application before saving this Rift"
      return
    }
    runAction("save", ["save", name, "--apps", selectedAppIds().join("\x1f"),
                       "--expect-workspace", String(stateData.workspace.id)])
  }

  function openRift(slug) { runAction("open", ["open", slug]) }

  // ＋ always opens the New Rift chooser: save what is here, or start fresh.
  function beginNew() {
    if (mode === "new") { backToBrowse(); return }
    confirmDelete = false
    errorText = ""
    mode = "new"
  }

  function createFreshWorkspace() {
    if (actionProcess.running) return
    runAction("new", ["new-workspace"])
  }

  function openDetail(slug) {
    detailSlug = slug
    confirmDelete = false
    errorText = ""
    mode = "detail"
  }

  function backToBrowse() {
    mode = "browse"
    confirmDelete = false
    errorText = ""
  }

  function updateRift(rift) {
    if (!rift || !canWrite || rift.openWorkspace !== liveWorkspaceId) return
    runAction("update", ["save", rift.name,
                         "--expect-workspace", String(stateData.workspace.id),
                         "--update-of", rift.slug])
  }

  function revertRift(rift) {
    if (rift && rift.previous && canWrite) runAction("revert", ["revert", rift.slug])
  }

  function deleteRift(rift) {
    if (!rift || actionProcess.running) return
    if (!confirmDelete) { confirmDelete = true; return }
    runAction("delete", ["delete", rift.slug])
  }

  function toggleHelp() { runAction("help", ["help", helpOn ? "off" : "on"]) }

  function toggleStartup(rift) {
    runAction("startup", ["startup", rift.slug, rift.startup ? "off" : "on"])
  }



  function moveSelection(delta) {
    if (mode !== "browse" || rifts.length === 0) return
    selectedIndex = Math.max(0, Math.min(rifts.length - 1, selectedIndex + delta))
  }

  function activateSelection() {
    if (mode === "browse" && rifts.length > 0) openDetail(rifts[selectedIndex].slug)
    else if (mode === "detail" && detailRift) openRift(detailRift.slug)
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
        root.stateStale = false
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
        } else if (root.pendingAction === "new") {
          root.notify("Fresh workspace " + response.data.workspace.id, "Open what belongs here, then click 󰦛 and save it as a Rift.")
          root.close()
        } else if (root.pendingAction === "delete") {
          root.mode = "browse"
          root.confirmDelete = false
          root.statusText = "Deleted " + response.data.deleted
          root.refresh()
        } else if (root.pendingAction === "update") {
          root.statusText = "Updated " + response.data.name + " · " + response.data.apps.length + " application" + (response.data.apps.length === 1 ? "" : "s")
          root.notify("Rift updated", response.data.name + " now has " + response.data.apps.length + " application" + (response.data.apps.length === 1 ? "" : "s"))
          root.refresh()
        } else if (root.pendingAction === "revert") {
          root.statusText = "Reverted " + response.data.name + " to its previous recipe"
          root.notify("Rift reverted", response.data.name)
          root.refresh()
        } else if (root.pendingAction === "help") {
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
      onCloseRequested: { if (root.mode === "detail" || root.mode === "new") root.backToBrowse(); else root.close() }
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(text) {
        var key = String(text).toLowerCase()
        if (key === "h") { root.toggleHelp(); return }
        if (key === "n") { root.beginNew(); return }
        if (root.mode === "browse") {
          if (key === "s" && !root.currentRift && root.canWrite) root.beginSave("")
        } else if (root.mode === "new") {
          if (key === "s" && root.canWrite && root.apps.length > 0) root.beginSave("")
          else if (key === "f") root.createFreshWorkspace()
          else if (key === "b") root.backToBrowse()
        } else if (root.mode === "detail" && root.detailRift) {
          if (key === "o") root.openRift(root.detailRift.slug)
          else if (key === "u") root.updateRift(root.detailRift)
          else if (key === "r") root.revertRift(root.detailRift)
          else if (key === "l") root.toggleStartup(root.detailRift)
          else if (key === "d") root.deleteRift(root.detailRift)
          else if (key === "b") root.backToBrowse()
        }
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
              width: parent.width - parent.children[0].width - helpButton.width - freshButton.width - parent.spacing * 3
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(2)

              Row {
                spacing: Style.space(6)
                Text {
                  text: "RIFTS"
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.title
                  font.bold: true
                  font.letterSpacing: 1.2
                }
                Text {
                  text: "v" + root.riftVersion
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  anchors.baseline: parent.children[0].baseline
                }
              }

              Text {
                text: root.stale
                  ? ("Reading workspace " + root.liveWorkspaceId + "…")
                  : root.currentRift
                    ? (root.currentRift.name + " · workspace " + root.stateData.workspace.id)
                    : ("Workspace " + root.stateData.workspace.id + " · unsaved")
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }
            }

            Button {
              id: helpButton
              iconText: "󰋖"
              tooltipText: root.helpOn ? "Turn help off · H" : "Turn help on · H"
              foreground: root.foreground
              active: root.helpOn
              focusable: true
              enabled: !actionProcess.running
              onClicked: root.toggleHelp()
            }

            Button {
              id: freshButton
              iconText: "＋"
              tooltipText: "New Rift · N"
              foreground: root.foreground
              active: root.mode === "new"
              focusable: true
              enabled: !actionProcess.running
              onClicked: root.beginNew()
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

            // ---- Help walkthrough (new users; toggle with 󰋖 / H)
            Rectangle {
              visible: root.helpOn
              width: parent.width
              height: helpColumn.implicitHeight + Style.space(20)
              radius: Style.space(8)
              color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.07)
              border.color: root.dim
              border.width: 1

              Column {
                id: helpColumn
                anchors.fill: parent
                anchors.margins: Style.space(10)
                spacing: Style.space(4)

                Text {
                  text: "HOW RIFT WORKS"
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  font.bold: true
                  font.letterSpacing: 1.1
                }
                Repeater {
                  model: [
                    { n: "1", done: root.rifts.length > 0 || root.apps.length > 0, text: "Click ＋ (top right). Choose “start on a fresh workspace”." },
                    { n: "2", done: root.rifts.length > 0 || root.apps.length > 0, text: "Open the apps that belong together there." },
                    { n: "3", done: root.rifts.length > 0, text: "Click 󰦛 → “Save this workspace as a Rift” and name it." },
                    { n: "4", done: false, text: "Click a Rift to open, update, revert, or delete it." },
                    { n: "5", done: false, text: "Flip 󰐥 in the entry so it opens at login. 󰋖 hides this help." }
                  ]
                  Row {
                    spacing: Style.space(8)
                    width: helpColumn.width
                    Text {
                      text: modelData.done ? "󰄬" : modelData.n
                      color: modelData.done ? root.foreground : root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                      width: Style.space(14)
                    }
                    Text {
                      width: parent.width - Style.space(22)
                      text: modelData.text
                      wrapMode: Text.WordWrap
                      color: modelData.done ? root.dim : root.foreground
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                    }
                  }
                }
              }
            }

            Button {
              visible: !root.currentRift
              width: parent.width
              text: root.apps.length > 0 ? "Save this workspace as a Rift" : "Open some apps here, then save this workspace as a Rift"
              iconText: "󰆓"
              leftAlign: true
              foreground: root.foreground
              focusable: true
              enabled: root.canWrite && root.apps.length > 0
              tooltipText: "S"
              onClicked: root.beginSave("")
            }

            Text {
              visible: root.currentRift ? true : false
              width: parent.width
              text: "You're on " + (root.currentRift ? root.currentRift.name : "") + (root.stateData.changed ? " — it has changed. Open its entry to update it." : ".")
              wrapMode: Text.WordWrap
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }

            Text {
              visible: root.rifts.length === 0 && !root.helpOn
              width: parent.width
              text: "No Rifts yet. Click ＋ for a fresh workspace, open what belongs there, then save it."
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
                    root.openDetail(modelData.slug)
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
                        + (modelData.openWorkspace > 0 ? " · on workspace " + modelData.openWorkspace : "")
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
            visible: root.mode === "new"
            width: parent.width
            spacing: Style.space(8)

            Text {
              text: "NEW RIFT"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.1
            }

            Button {
              width: parent.width
              text: root.apps.length > 0
                ? "Save what's here (" + root.apps.length + " app" + (root.apps.length === 1 ? "" : "s") + ") as a new Rift"
                : "Save what's here — nothing is open yet"
              iconText: "󰆓"
              leftAlign: true
              foreground: root.foreground
              focusable: true
              enabled: root.canWrite && root.apps.length > 0
              tooltipText: "S"
              onClicked: root.beginSave("")
            }

            Button {
              width: parent.width
              text: "Start on a fresh, empty workspace"
              iconText: "󰐊"
              leftAlign: true
              foreground: root.foreground
              focusable: true
              enabled: !actionProcess.running
              tooltipText: "F · Rift jumps you there; come back and save when it's ready"
              onClicked: root.createFreshWorkspace()
            }

            Button {
              text: "Back"
              iconText: "󰁍"
              foreground: root.foreground
              focusable: true
              tooltipText: "Esc"
              onClicked: root.backToBrowse()
            }
          }

          Column {
            visible: root.mode === "detail" && root.detailRift !== null
            width: parent.width
            spacing: Style.space(8)

            Row {
              width: parent.width
              spacing: Style.space(8)
              Button {
                iconText: "󰁍"
                tooltipText: "Back · Esc"
                foreground: root.foreground
                focusable: true
                onClicked: root.backToBrowse()
              }
              Column {
                anchors.verticalCenter: parent.verticalCenter
                width: parent.width - parent.children[0].width - parent.spacing
                spacing: Style.space(2)
                Text {
                  width: parent.width
                  text: root.detailRift ? root.detailRift.name : ""
                  elide: Text.ElideRight
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.title
                  font.bold: true
                }
                Text {
                  width: parent.width
                  text: root.detailRift
                    ? (root.detailRift.apps.length + " application" + (root.detailRift.apps.length === 1 ? "" : "s")
                       + (root.detailRift.openWorkspace > 0 ? " · open on workspace " + root.detailRift.openWorkspace : " · not open")
                       + (root.detailRift.startup ? " · opens at login" : ""))
                    : ""
                  elide: Text.ElideRight
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }
              }
            }

            Repeater {
              model: root.detailRift ? root.detailRift.apps : []
              Text {
                width: parent.width
                text: "  " + (modelData.kind === "terminal" ? "󰆍 " : "󰘔 ") + modelData.name + (modelData.cwd ? "  " + modelData.cwd : "")
                elide: Text.ElideMiddle
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }
            }

            Rectangle { width: parent.width; height: 1; color: root.dim; opacity: 0.28 }

            Button {
              width: parent.width
              text: root.detailRift && root.detailRift.openWorkspace > 0
                ? "Go to workspace " + root.detailRift.openWorkspace
                : "Open on a fresh workspace"
              iconText: root.detailRift && root.detailRift.openWorkspace > 0 ? "󰁔" : "󰐊"
              leftAlign: true
              foreground: root.foreground
              active: true
              focusable: true
              enabled: !actionProcess.running
              tooltipText: "O · Enter"
              onClicked: if (root.detailRift) root.openRift(root.detailRift.slug)
            }

            Button {
              width: parent.width
              text: root.detailIsHere
                ? (root.stateData.changed ? "Update with this workspace (it changed)" : "Update with this workspace")
                : (root.detailRift && root.detailRift.openWorkspace > 0
                    ? "Update — go to workspace " + root.detailRift.openWorkspace + " first"
                    : "Update — open this Rift first")
              iconText: "󰑐"
              leftAlign: true
              foreground: root.foreground
              focusable: true
              enabled: root.detailIsHere && root.canWrite
              tooltipText: "U · re-records the apps on this workspace into this Rift"
              onClicked: root.updateRift(root.detailRift)
            }

            Button {
              visible: root.detailRift && root.detailRift.previous ? true : false
              width: parent.width
              text: "Revert to the previous recipe"
              iconText: "󰕌"
              leftAlign: true
              foreground: root.foreground
              focusable: true
              enabled: root.canWrite
              tooltipText: "R"
              onClicked: root.revertRift(root.detailRift)
            }

            Button {
              width: parent.width
              text: root.detailRift && root.detailRift.startup ? "Opens at login — turn off" : "Open at login"
              iconText: root.detailRift && root.detailRift.startup ? "󰐥" : "󰒲"
              leftAlign: true
              foreground: root.foreground
              focusable: true
              enabled: !actionProcess.running
              tooltipText: "L"
              onClicked: root.toggleStartup(root.detailRift)
            }

            Row {
              width: parent.width
              spacing: Style.space(8)
              Button {
                text: root.confirmDelete ? "Yes, delete " + (root.detailRift ? root.detailRift.name : "") : "Delete this Rift"
                iconText: "󰆴"
                foreground: root.confirmDelete ? root.urgent : root.foreground
                active: root.confirmDelete
                focusable: true
                enabled: !actionProcess.running
                tooltipText: "D (twice)"
                onClicked: root.deleteRift(root.detailRift)
              }
              Button {
                visible: root.confirmDelete
                text: "Cancel"
                foreground: root.foreground
                focusable: true
                onClicked: root.confirmDelete = false
              }
            }
          }

          Column {
            visible: root.mode === "save"
            width: parent.width
            spacing: Style.space(10)

            Text {
              text: "SAVE THIS WORKSPACE"
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
                enabled: root.canWrite && root.apps.length > 0
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
