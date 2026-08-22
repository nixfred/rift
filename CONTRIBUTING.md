# Contributing to Rift

Rift is built by Fred with three AI collaborators (Larry/Claude, Codex, Grok). These rules keep us from stepping on each other.

## Golden rules

1. **The installed plugin dir is read-only.** `~/.config/omarchy/plugins/nixfred.rift` must always be `main`; it is what the bar actually runs. Never check a branch out there, never edit it. It is updated only via `omarchy-plugin-update nixfred.rift --yes` (hot-reload; `Panel.qml` changes additionally need a shell restart — wait ≥5 s after the last file change before `omarchy-restart-shell`, or quickshell can segfault).
2. **One working tree per agent.** Each collaborator works in their own clone (`~/Projects/rift`, `~/Projects/rift-codex`, …). Never edit someone else's tree.
3. **Branch → PR → review → squash-merge.** Every change, however small, goes through a PR against `main`. Reference the issue (`Closes #N`). Keep PRs narrow.
4. **Tests and validation are mandatory.** `python3 -W error::ResourceWarning -m unittest discover -s tests -v` and `omarchy plugin validate .` must pass. New behaviour needs a test.
5. **Version lives in two places.** `manifest.json` and `riftVersion` in `Panel.qml` (a test keeps them in lockstep). Bump both + `CHANGELOG.md` + tag `vX.Y.Z` on release.
6. **No geometry, no daemon, plain JSON, native Omarchy UI.** Those four constraints are the product.

## Workflow

```bash
git clone git@github.com:nixfred/rift.git ~/Projects/rift-<you> && cd $_
git checkout -b fix/short-slug
# … work, tests, validate …
gh pr create --fill --draft      # draft until verified; mark ready when green
```

Live-testing a branch? Install it under a **different** plugin id from a separate checkout, or ask the maintainer to swap the installed dir temporarily — never leave the installed dir on a branch.

## Review expectations

- Say *what* was wrong, *why*, *how* it's fixed, and *how you verified it* (the four-section PR body Codex uses is the template).
- Merge conflicts are the author's to resolve; rebase/merge `main` before asking for review.
- Helper stays stdlib-only Python; QML stays in Omarchy's house style (`qs.Ui` components, `Style.*`, `console.debug("rift: …")` tracing).
