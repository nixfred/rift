# Changelog

All notable changes to Rift. The version shown in the panel header (`RIFTS vX.Y.Z`) and bar tooltip is the one in `manifest.json`; tests keep them in lockstep.

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
