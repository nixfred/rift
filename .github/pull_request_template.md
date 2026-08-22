## Agent

All commits on this repo land as GitHub user `nixfred`. Say who you are (one):

- [ ] Larry (Claude Code)
- [ ] Codex
- [ ] Grok
- [ ] Fred (human)

## What was wrong

## Why it matters

## Fix

## Verification

- [ ] `python3 -W error::ResourceWarning -m unittest discover -s tests -v`
- [ ] `omarchy plugin validate .`
- [ ] Live test on vic if `Panel.qml`/`BarWidget.qml` changed — **not** by checking out this branch in the installed plugin dir
- [ ] Tests never touch the live compositor (assert inside the `hypr_json` patch)

Closes #
