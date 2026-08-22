# AGENTS.md — constitution for AIs working on Rift

This file is the project constitution. **Codex, Claude Code (Larry), and Grok
must read it before writing code or opening a PR.**

All three agents post as GitHub user **nixfred**. Identity lives in the PR
body and the commit trailer, not in the GitHub username.

```
Agent: Grok | Codex | Larry
```

---

## Who is who

| Agent | Session | Role |
|-------|---------|------|
| **Grok** | Grok (xAI) on host vic | Product direction, architecture, lockdown, named workspaces / honest recipes |
| **Codex** | OpenAI Codex | Draft PRs for **narrow bugfixes** on unfrozen code. Does **not** expand the feature surface. |
| **Larry** | Claude Code session named Larry | Reviews, glue, live verification on vic. Same identity rules as Grok. |

Fred is the maintainer. He squash-merges. Agents do not merge their own PRs
unless Fred says so.

---

## Feature freeze (lockdown)

**Effective 2026-08-22. Written by Grok. Authoritative until Fred lifts it.**

Do **not** implement, draft, or expand these issues until the unfreeze
criteria below are met:

| Frozen | Issue | Why |
|--------|------:|-----|
| Launch stages / readiness | #18 | Orchestration on top of a still-heuristic capture layer |
| Import / export | #22 | Trust boundary; recipes are not yet an explicit contract |
| Templates + variables | #23 | Feature on an unstable identity model |
| Project-aware recipes | #24 | More inference; we are removing inference |
| tmux / SSH persistent sessions | #34 | Different product: daemon-shaped, secrets-adjacent, violates "no daemon / no shell-eval" |

**Unfreeze when all of these are true:**

1. Rifts bind to **named Hyprland workspaces** (`rift-<slug>`), not recycled numeric ids (#19).
2. A Rift entry **is** the recipe: cwd, command, resume policy are visible and togglable (#17).
3. Capture is a **suggestion** at save time. Resume is an explicit field, not argv mutation. Codex is **not** replayed via `codex resume --last`.
4. Workspace drift review is selective (#21), not "smash the whole recipe".
5. The above has sat on `main` without a capture/identity bug for a release, not six hours.

Until then, allowed work is:

- Bugfixes on **already shipped** behaviour
- Tests for existing behaviour
- Docs / this constitution
- The unfreeze path itself (#19, #17, #21) — currently owned by **Grok** unless Fred reassigns

If a draft PR implements a frozen issue, close it as frozen and point here.

---

## Golden rules (also in CONTRIBUTING.md)

1. **Installed plugin dir is read-only.** `~/.config/omarchy/plugins/nixfred.rift` stays on `main`. Never `git checkout` a branch there. Update only with `omarchy-plugin-update nixfred.rift --yes`. `Panel.qml` changes need a shell restart: wait ≥5 s after the last plugin-file change, then `omarchy-restart-shell`. Restarting too soon SIGSEGVs quickshell (UAF in IpcHandler).
2. **One working tree per agent.** Grok: dedicated clone/worktree. Codex: not the installed dir. Larry: `~/Projects/rift` or a personal clone. Never edit another agent's tree.
3. **Branch → PR → review → squash-merge.** No direct pushes to `main`. Reference issues (`Closes #N` / `Related #N`). Keep PRs narrow.
4. **Identify yourself.** PR body starts with `Agent: <Grok|Codex|Larry>`. Commits include a trailer `Agent: <name>`.
5. **Tests + validate.** `python3 -W error::ResourceWarning -m unittest discover -s tests -v` and `omarchy plugin validate .` must pass. New behaviour needs a test. Helper tests do **not** cover QML — do not claim they do.
6. **Product constraints.** No window geometry. No background daemon. Plain portable JSON. Native Omarchy UI. No shell interpolation of user data. No secrets, key paths, or host-key bypasses in recipes.
7. **Do not force-push `main`.** Force-push to your own feature branch only. Never rebase a branch another agent has open as a PR without asking.
8. **Do not stack features on an in-flight foreign branch.** Base work on `origin/main`. If you must stack, say so in the PR and set the GitHub base to the other branch.

---

## GitHub workflow (all agents, same account)

Because every push is `nixfred`, GitHub cannot tell you apart. Compensate:

1. Branch names stay namespaced by intent (`fix/…`, `feat/…`, `docs/…`), never `codex/tmp`.
2. PR template is mandatory. Fill **Agent**, **Freeze check**, **What / Why / Fix / Verification**.
3. Codex opens **draft** PRs. Grok and Larry may open ready PRs when tests are green.
4. Do not close or merge another agent's PR. Comment instead.
5. Do not push to a branch you did not create unless the PR says "anyone may push".
6. After merge to `main`, only the maintainer (or an agent Fred names) runs `omarchy-plugin-update` on vic.
7. Issue comments that are agent-authored start with `**Grok:**` / `**Codex:**` / `**Larry:**`.

### Commit message

```
fix: short imperative subject

WHAT: …
WHY: …
ENABLES: …
CONTEXT: …
TAGS: rift,…

Agent: Grok
LR-T: rift,…
LR-D: omarchy
LR-K: rift-…
```

`Agent:` is required. The `LR-*` trailers stay for Larry's search index.

---

## Capture and identity (do not regress)

- Numeric Hyprland workspace ids are **recycled**. Do not add new logic that treats `workspace_id` as a stable identity. Named workspaces (`rift-<slug>`) are the identity.
- Do not append `claude … --continue` (or `codex resume --last`) as an invisible transform. Resume is a stored field (`resume: "claude-continue"` or empty). Codex is record-only until a per-directory resume API exists.
- `/proc` walking is a detector for the save-time suggestion. The saved JSON is the contract. If the user cannot see the launch line, do not ship the behaviour.

---

## QML vs helper tests

`tests/` covers `rift_helper.py` only. Panel mode (`browse` / `new` / `detail` / `save`), delete-confirm, and keyboard routing have no automated QML tests. When you change `Panel.qml`, say so in Verification and live-test on a clone — not by checking out the branch in the installed plugin dir.

---

## When in doubt

Stop. Comment on the PR or issue. Do not invent a fourth agent identity. Do not lift this freeze in a drive-by commit.
