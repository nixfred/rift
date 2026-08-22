# Changelog

All notable changes to Rift. The version shown in the panel header (`RIFTS vX.Y.Z`) and bar tooltip is the one in `manifest.json`; tests keep them in lockstep.

## v0.3.2 — 2026-08-21 · Capture & save hardening (Codex/Grok PRs #35–#44)

- Foreground program captured via the terminal's **tpgid** (process group), not `/proc` child order — background jobs can't masquerade; `kitty -e claude` (no shell) captured too (#42)
- Editor folder args resolved against the **editor's** cwd (`code .` no longer records the plugin dir) (#44)
- A recorded cwd that no longer exists → explicit failed/retryable result, never a silent launch in `$HOME` (#43)
- Saving refuses to overwrite an existing Rift unless it is an explicit update (#39); refuses empty Rifts (#40)
- Runtime lock no longer held across workspace switch + launches (#41)
- Ensure-policy apps only launch after a *confirmed* absence; unknown state is retryable, never a duplicate (#36, closes #14)
- Save form watches the workspace while you name a new Rift — apps opened meanwhile appear and tick themselves (#35)
- Keyboard: selection stays visible in long lists; Tab/Shift+Tab hand off to neighbouring popouts correctly (#38)

## v0.3.1 — 2026-08-21 · Deep terminal capture

- Terminals now record the **shell's real cwd** (walks terminal → shell → foreground program via `/proc`), not the terminal's launch dir
- **Claude Code sessions come back**: `claude … --continue` in that directory (your wrapper flags preserved); Codex: `codex resume --last`; common TUIs (`nvim`, `btop`, `lazygit`, `ssh`, …) replayed as-is; anything else is recorded but not replayed
- Terminals launch with `--hold` so the window survives the program exiting
- Editors keep their project folder (`code /path`) instead of a bare `.desktop` launch
- Entry view shows each app's directory and the command it will run
- README: "What a Rift actually saves"

## v0.3.0 — 2026-08-21 · Entries, help, delete

- `＋` is always top-right and opens an explicit **New Rift** chooser: *save what's on this workspace* or *start on a fresh, empty workspace*
- Click a Rift → its **entry**: app list, where it's open, and **Open / Go to workspace · Update · Revert · Open at login · Delete** (two-step confirm). Update lives *only* here and only when you're standing on that Rift's workspace
- **Help mode** for new users: a five-step walkthrough at the top of the panel, auto-retires after the first saved Rift, `󰋖`/`H` toggles it back on (`help on|off` CLI)
- `rename <slug> <name>` CLI (file, slug, and association follow)
- State now reports each Rift's open workspace

## v0.2.1 — 2026-08-21 · Never update the wrong Rift

Fixes a real incident: the panel was opened on a Rift's workspace, the user moved to a fresh workspace, pressed `󰑐`, and the *other* Rift was overwritten with the empty workspace.

- Panel tracks the focused Hyprland workspace live; any change re-reads state and **every write control is disabled until the model matches the focused workspace** ("Reading workspace N…")
- Helper `save` takes `--expect-workspace` and `--update-of`; it **refuses** if the focused workspace differs from what the panel showed, or if the Rift being updated is bound to another workspace — with a plain-English error
- An association only counts when its workspace **has windows**: Hyprland reuses numeric ids, so an empty workspace can never inherit a stale Rift
- Recipe **history** (last 5) replaces the single `previous` slot; Revert walks back step by step and stays revertible
- "Save this workspace as a different Rift" is always offered when standing on a Rift (`N`), so you are never stuck

## v0.2.0 — 2026-08-21 · Reliability

- Hyprland ≥ 0.56 Lua dispatch (`hl.dsp.focus({ workspace = N })`) with legacy fallback — open/focus works again
- Context-aware header: `＋` saves *this* workspace as a new Rift; `󰑐` one-click re-records the current Rift; **Revert** restores the previous recipe (revert is revertible)
- Runtime state transactions (flock) — no lost updates across concurrent helper calls (#8)
- Verified workspace transitions instead of a fixed sleep; save aborts if focus moves (#9)
- Panel refresh queue — stale state responses are discarded (#10)
- Startup lock hardened: private dir, `O_NOFOLLOW`, mode 0600 (#11)
- Rift definitions and runtime state validated; malformed entries skipped, not fatal (#12)
- Per-app launch outcomes: opened / partial / failed; failed apps retried on next open; startup marker withheld until clean (#13)
- Desktop Entry `Exec` parsed properly (quotes, escapes, `env` wrappers) (#15)
- Unique Rift↔workspace association; hyprctl failure no longer wipes state; atomic writes via `mkstemp`+fsync; unlaunchable apps skipped; accurate focus/open notifications
- `rift:`-prefixed debug tracing in the shell log
- Version visible in the panel header and bar tooltip

## v0.1.0 — 2026-08-21

- Initial release: save the apps on a workspace as a named Rift, reopen or focus, open at login, terminals relaunch in their cwd.
