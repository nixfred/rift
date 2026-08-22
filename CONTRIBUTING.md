# Contributing to Rift

Rift is built by Fred with three AI collaborators. They all push as GitHub
user **nixfred**, so identity is declared in the PR, not the account.

| Agent | How to identify |
|-------|-----------------|
| **Grok** | `Agent: Grok` |
| **Codex** | `Agent: Codex` (draft PRs) |
| **Larry** | `Agent: Larry` — Claude Code session named Larry |

**Read [AGENTS.md](./AGENTS.md) before any change.** That file is the
constitution: freeze list, working-tree rules, commit trailers, and capture
policy. This document is the short human version.

## Golden rules

1. **The installed plugin dir is read-only.** `~/.config/omarchy/plugins/nixfred.rift` must always be `main`. Never check a branch out there. Update only via `omarchy-plugin-update nixfred.rift --yes`. `Panel.qml` changes need a shell restart: wait ≥5 s after the last file change, then `omarchy-restart-shell` (too soon → quickshell SIGSEGV).
2. **One working tree per agent.** Never edit someone else's clone.
3. **Branch → PR → review → squash-merge.** Every change goes through a PR against `main`. Use the PR template. Reference the issue.
4. **Declare the agent.** PR template + commit trailer `Agent: <Grok|Codex|Larry>`.
5. **Tests and validation are mandatory.** `python3 -W error::ResourceWarning -m unittest discover -s tests -v` and `omarchy plugin validate .`. Helper tests do not cover QML.
6. **Version lives in two places.** `manifest.json` and `riftVersion` in `Panel.qml` (a test keeps them in lockstep). Bump both + `CHANGELOG.md` + tag `vX.Y.Z` on release.
7. **No geometry, no daemon, plain JSON, native Omarchy UI.** Those four constraints are the product.
8. **Obey the feature freeze in AGENTS.md.** Frozen issues are #18, #22, #23, #24, #34 until named workspaces + honest recipes have landed and stayed boring.

## Workflow

```bash
git clone git@github.com:nixfred/rift.git ~/Projects/rift-<you> && cd $_
git checkout -b fix/short-slug
# … work, tests, validate …
gh pr create --fill --draft      # Codex: stay draft. Grok/Larry: ready when green.
```

Live-testing a branch? Install it under a **different** plugin id from a
separate checkout, or ask Fred to swap the installed dir — never leave the
installed dir on a branch.

## Review expectations

- PR body: Agent, freeze check, what was wrong, why, fix, verification.
- Merge conflicts are the author's to resolve; rebase onto `main` before asking for review.
- Helper stays stdlib-only Python; QML stays in Omarchy's house style (`qs.Ui`, `Style.*`, `console.debug("rift: …")`).
- Do not close or merge another agent's PR. Comment.
