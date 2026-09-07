## Agent

All commits on this repo land as GitHub user `nixfred`. Say who you are (one):

- [ ] Larry (Claude Code)
- [ ] Codex
- [ ] Grok
- [ ] Fred (human)

## Freeze check

- [ ] This PR does **not** implement a frozen issue (#18, #22, #23, #24, #34). See `AGENTS.md`.
- [ ] If it *is* unfreeze work (#19 named workspaces, #17 recipe editor, #21 drift), say so here:

## What was wrong

## Why it matters

## Fix

## Verification

- [ ] `python3 -W error::ResourceWarning -m unittest discover -s tests -v`
- [ ] `omarchy plugin validate .`
- [ ] `git diff --check`
- [ ] Live test on vic if `Panel.qml`/`BarWidget.qml` changed — **not** by checking out this branch in the installed plugin dir
- [ ] Tests never touch the live compositor (assert inside the `hypr_json` patch)

Closes #
