## Agent

All commits on this repo are GitHub user `nixfred`. Check exactly one:

- [ ] Grok
- [ ] Codex
- [ ] Larry (Claude Code)

## Freeze check

- [ ] This PR does **not** implement a frozen issue (#18, #22, #23, #24, #34). See `AGENTS.md`.
- [ ] If it *is* unfreeze work (#19 named workspaces, #17 recipe editor, #21 drift), say so here:

## Bug / request

## What was wrong

## Why it matters

## Fix

## Verification

- [ ] `python3 -W error::ResourceWarning -m unittest discover -s tests -v`
- [ ] `omarchy plugin validate .`
- [ ] `git diff --check`
- [ ] Live test (if `Panel.qml` changed): **not** by checking out this branch in the installed plugin dir

Closes #
