#!/usr/bin/env python3
"""Backend for the Rift Omarchy plugin.

The UI is the product surface. This helper is deliberately small and emits
JSON so the Quickshell plugin never has to scrape shell output.
"""

from __future__ import annotations

import argparse
import configparser
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterator


CONFIG_ROOT = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "rift"
RIFTS_ROOT = CONFIG_ROOT / "rifts"
STATE_ROOT = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "rift"
RUNTIME_FILE = STATE_ROOT / "runtime.json"
SETTINGS_FILE = CONFIG_ROOT / "settings.json"

IGNORED_CLASSES = {
    "omarchy-shell",
    "quickshell",
    "org.quickshell",
    "walker",
    "wofi",
    "fuzzel",
    "rofi",
}
GLOBAL_CLASSES = {
    "spotify",
    "signal",
    "signal-beta",
    "discord",
    "vesktop",
    "slack",
    "1password",
    "bitwarden",
}
TERMINALS = {
    "com.mitchellh.ghostty": "ghostty",
    "ghostty": "ghostty",
    "kitty": "kitty",
    "alacritty": "alacritty",
    "foot": "foot",
    "org.wezfurlong.wezterm": "wezterm",
    "wezterm": "wezterm",
}


def ensure_dirs() -> None:
    RIFTS_ROOT.mkdir(parents=True, exist_ok=True)
    STATE_ROOT.mkdir(parents=True, exist_ok=True)


def startup_lock_path() -> Path:
    runtime_root = os.environ.get("XDG_RUNTIME_DIR")
    directory = Path(runtime_root) / "rift" if runtime_root else STATE_ROOT / "locks"
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = directory.stat(follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError("Rift lock directory is not private and user-owned")
    directory.chmod(0o700)
    return directory / "startup.lock"


@contextmanager
def startup_lock() -> Iterator[Any]:
    path = startup_lock_path()
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        os.close(descriptor)
        raise RuntimeError("Rift startup lock is not a user-owned regular file")
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "r+") as lock:
        yield lock


def atomic_json(path: Path, value: Any) -> None:
    ensure_dirs()
    descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temp.unlink(missing_ok=True)


def read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return fallback


def hypr_json(subject: str) -> Any:
    result = subprocess.run(
        ["hyprctl", "-j", subject], capture_output=True, text=True, timeout=3, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"hyprctl {subject} failed")
    return json.loads(result.stdout)


def lua_dispatch(dispatcher: str, argument: str) -> str:
    """Render a dispatcher call for Hyprland >= 0.56, whose hyprctl dispatch takes Lua."""
    literal = argument if re.fullmatch(r"[+-]?\d+", argument) else json.dumps(argument)
    if dispatcher == "workspace":
        return f"hl.dsp.focus({{ workspace = {literal} }})"
    return f"hl.dispatch({json.dumps(dispatcher)}, {literal})"


def _hyprctl_dispatch(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["hyprctl", "dispatch", *args], capture_output=True, text=True, timeout=3, check=False)


def hypr_dispatch(dispatcher: str, argument: str) -> None:
    # Hyprland 0.56 turned `hyprctl dispatch` into a Lua call; older releases
    # still take `dispatch <name> <arg>`. Try the new form, fall back to legacy.
    result = _hyprctl_dispatch([lua_dispatch(dispatcher, argument)])
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0 or output.lower().startswith("error") or "invalid dispatcher" in output.lower():
        legacy = _hyprctl_dispatch([dispatcher, argument])
        legacy_output = (legacy.stdout + legacy.stderr).strip()
        if legacy.returncode != 0 or legacy_output.lower().startswith("error"):
            raise RuntimeError(output or legacy_output or "Hyprland dispatch failed")


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not slug:
        raise ValueError("Give this Rift a name")
    return slug


def process_info(pid: int) -> tuple[str, list[str], str]:
    if pid <= 0:
        return "", [], ""
    proc = Path("/proc") / str(pid)
    try:
        executable = str((proc / "exe").resolve())
    except OSError:
        executable = ""
    try:
        raw = (proc / "cmdline").read_bytes()
        argv = [part.decode(errors="replace") for part in raw.split(b"\0") if part]
    except OSError:
        argv = []
    try:
        cwd = str((proc / "cwd").resolve())
    except OSError:
        cwd = ""
    return executable, argv, cwd


# ---------------------------------------------------------------------------
# Deep terminal capture: what is really going on inside that terminal window?
# Hyprland gives us the terminal's pid. Its cwd is where the terminal was
# *started* (usually $HOME) — useless. The shell underneath knows the real
# directory, and the program in the foreground (claude, codex, nvim, btop…)
# is what the user actually wants back.
# ---------------------------------------------------------------------------

SHELLS = {"bash", "zsh", "fish", "sh", "dash", "nu", "nushell", "elvish", "xonsh", "tcsh", "ksh"}
TERMINAL_HELPERS = {"kitten", "ghostty", "alacritty", "foot", "wezterm-gui", "wezterm", "kitty"}
# Programs that know how to pick their own session back up. The recipe we
# replay is argv with the resume flag appended, so Fred's wrapper flags survive.
RESUMABLE = {"claude", "codex"}
# Plain TUI programs that are safe and useful to relaunch exactly as seen.
REPLAYABLE = {
    "nvim", "vim", "vi", "hx", "helix", "nano", "micro", "emacs",
    "btop", "htop", "top", "lazygit", "lazydocker", "tmux", "zellij",
    "yazi", "ranger", "nnn", "lf", "ssh", "mosh", "tail", "journalctl", "watch",
}
# GUI apps whose argv carries the project folder we want to keep (gtk-launch would lose it).
GUI_ARGV_APPS = {"code", "code-oss", "codium", "cursor", "zed", "zeditor", "windsurf"}


def child_pids(pid: int) -> list[int]:
    try:
        text = (Path("/proc") / str(pid) / "task" / str(pid) / "children").read_text()
        return [int(part) for part in text.split()]
    except (OSError, ValueError):
        pass
    result = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat_text = (entry / "stat").read_text()
            parent = int(stat_text.rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        if parent == pid:
            result.append(int(entry.name))
    return result


def proc_comm(pid: int) -> str:
    try:
        return (Path("/proc") / str(pid) / "comm").read_text().strip()
    except OSError:
        return ""


def proc_ids(pid: int) -> tuple[int, int, int] | None:
    """Return (ppid, pgrp, tpgid) from /proc/<pid>/stat, or None."""
    try:
        text = (Path("/proc") / str(pid) / "stat").read_text()
        rest = text.rsplit(")", 1)[1].split()
        return int(rest[1]), int(rest[2]), int(rest[5])
    except (OSError, ValueError, IndexError):
        return None


def capture_program(pid: int, fallback_cwd: str) -> dict[str, Any] | None:
    _executable, argv, cwd = process_info(pid)
    comm = proc_comm(pid)
    if not argv or comm in SHELLS or comm in TERMINAL_HELPERS:
        return None
    program = Path(argv[0]).name
    if program in SHELLS or program in TERMINAL_HELPERS:
        return None
    return {"command": argv, "program": program, "cwd": cwd or fallback_cwd}


def terminal_session(pid: int) -> dict[str, Any]:
    """Find the shell under a terminal pid and whatever runs in its foreground.

    The foreground program is the process group named by the shell's (or the
    terminal's) tpgid — not "whatever child /proc listed last". Background
    jobs are children too; using list order captured `sleep 100 &` instead of
    the Claude session in front of it. Direct `kitty -e claude` has no shell;
    tpgid on the terminal pid still finds claude.

    Returns {"cwd": shell cwd, "command": argv or [], "program": basename or ""}.
    """
    session: dict[str, Any] = {"cwd": "", "command": [], "program": ""}
    queue = [pid]
    seen: set[int] = set()
    shell_pid = 0
    # Breadth-first through helpers (kitten etc.) until we hit a real shell.
    while queue and not shell_pid:
        current = queue.pop(0)
        for child in child_pids(current):
            if child in seen:
                continue
            seen.add(child)
            comm = proc_comm(child)
            if comm in SHELLS:
                shell_pid = child
                break
            if comm in TERMINAL_HELPERS or not comm:
                queue.append(child)
    target = shell_pid or pid
    _exe, _argv, target_cwd = process_info(target)
    session["cwd"] = target_cwd
    ids = proc_ids(target)
    if not ids:
        return session
    _ppid, _pgrp, tpgid = ids
    if tpgid <= 0 or tpgid == target:
        return session
    captured = capture_program(tpgid, target_cwd)
    if captured:
        session.update(captured)
    return session


def resume_command(session: dict[str, Any]) -> list[str]:
    """Turn a captured foreground program into something worth replaying, or []."""
    argv = list(session.get("command") or [])
    program = str(session.get("program") or "")
    if not argv or not program:
        return []
    if program == "claude":
        # --continue picks up the most recent conversation in this directory,
        # so a Claude Code session really does come back as that session.
        if not any(flag in argv for flag in ("--continue", "-c", "--resume", "-r")):
            argv = argv + ["--continue"]
        return argv
    if program == "codex":
        return ["codex", "resume", "--last"]
    if program in REPLAYABLE:
        return argv
    return []


def desktop_exec_binary(exec_line: str) -> str:
    """Return the executable token from a Desktop Entry Exec value."""
    try:
        tokens = shlex.split(exec_line, posix=True)
    except ValueError:
        return ""
    if not tokens:
        return ""
    index = 0
    if Path(tokens[0]).name == "env":
        index = 1
        while index < len(tokens):
            token = tokens[index]
            if token == "--":
                index += 1
                break
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token):
                index += 1
                continue
            if token in {"-u", "--unset"}:
                index += 2
                continue
            if token in {"-i", "--ignore-environment", "-0", "--null", "--debug"}:
                index += 1
                continue
            if token.startswith("--unset=") or token.startswith("--chdir="):
                index += 1
                continue
            if token.startswith("-"):
                return ""
            break
    if index >= len(tokens) or tokens[index].startswith("%"):
        return ""
    return tokens[index]


def desktop_entries() -> list[dict[str, str]]:
    roots = [Path.home() / ".local/share/applications", Path("/usr/share/applications")]
    entries: list[dict[str, str]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.desktop"):
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                parser.read(path, encoding="utf-8")
                section = parser["Desktop Entry"]
            except (OSError, KeyError, configparser.Error):
                continue
            if section.get("Type", "Application") != "Application" or section.getboolean("NoDisplay", fallback=False):
                continue
            exec_line = section.get("Exec", "").strip()
            exec_token = desktop_exec_binary(exec_line)
            entries.append(
                {
                    "id": path.name.removesuffix(".desktop"),
                    "name": section.get("Name", path.stem),
                    "startup_class": section.get("StartupWMClass", ""),
                    "exec": Path(exec_token).name,
                }
            )
    return entries


def find_desktop_entry(classes: list[str], executable: str, entries: list[dict[str, str]]) -> dict[str, str] | None:
    keys = {value.casefold() for value in classes if value}
    exe_name = Path(executable).name.casefold() if executable else ""
    for entry in entries:
        if entry["startup_class"].casefold() in keys and entry["startup_class"]:
            return entry
    for entry in entries:
        if entry["id"].casefold() in keys:
            return entry
    for entry in entries:
        if exe_name and entry["exec"].casefold() == exe_name:
            return entry
    return None


def terminal_recipe(binary: str, cwd: str, command: list[str] | None = None) -> list[str]:
    """Launch a terminal in cwd, optionally running command inside it."""
    # --hold & friends: when the program exits you get a shell prompt instead
    # of the window vanishing (claude /exit should not take the terminal with it).
    command = list(command or [])
    if binary == "ghostty":
        recipe = [binary, f"--working-directory={cwd}"] if cwd else [binary]
        return recipe + ["--wait-after-command=true", "-e", *command] if command else recipe
    if binary == "kitty":
        recipe = [binary, "--directory", cwd] if cwd else [binary]
        return recipe + ["--hold", *command] if command else recipe
    if binary == "alacritty":
        recipe = [binary, "--working-directory", cwd] if cwd else [binary]
        return recipe + ["--hold", "-e", *command] if command else recipe
    if binary == "foot":
        recipe = [binary, "--working-directory", cwd] if cwd else [binary]
        return recipe + ["--hold", *command] if command else recipe
    if binary == "wezterm":
        recipe = [binary, "start", "--cwd", cwd] if cwd else [binary, "start"]
        return recipe + ["--", *command] if command else recipe
    return [binary] + command


def app_from_client(client: dict[str, Any], entries: list[dict[str, str]]) -> dict[str, Any] | None:
    app_class = str(client.get("class") or "").strip()
    initial_class = str(client.get("initialClass") or "").strip()
    if not app_class and not initial_class:
        return None
    lower_classes = {app_class.casefold(), initial_class.casefold()}
    if lower_classes & IGNORED_CLASSES or any("omarchy" in value for value in lower_classes):
        return None

    pid = int(client.get("pid") or 0)
    executable, argv, cwd = process_info(pid)
    terminal = next((TERMINALS[value] for value in lower_classes if value in TERMINALS), "")
    desktop = find_desktop_entry([app_class, initial_class], executable, entries)
    command: list[str] = []
    program = ""

    if terminal and shutil.which(terminal):
        session = terminal_session(pid)
        cwd = session.get("cwd") or cwd
        program = str(session.get("program") or "")
        command = resume_command(session)
        launch = terminal_recipe(terminal, cwd, command)
        app_id = f"terminal:{terminal}:{cwd or 'home'}" + (f":{program}" if program else "")
        name = terminal.title() + (f" · {program}" if program else "")
        kind = "terminal"
    elif desktop:
        exe_name = Path(executable).name if executable else ""
        folder_args = [arg for arg in argv[1:] if arg and not arg.startswith("-") and Path(arg).is_dir()]
        if exe_name in GUI_ARGV_APPS and folder_args:
            # Keep the project folder the editor was opened on.
            launch = [executable, *folder_args]
            app_id = f"desktop:{desktop['id']}:{folder_args[0]}"
        else:
            launch = ["gtk-launch", desktop["id"]]
            app_id = f"desktop:{desktop['id']}"
        name = desktop["name"]
        kind = "application"
    elif executable and os.access(executable, os.X_OK):
        launch = [executable]
        app_id = f"exec:{Path(executable).name}"
        name = app_class or initial_class or Path(executable).name
        kind = "application"
    else:
        return None

    global_app = bool(lower_classes & GLOBAL_CLASSES)
    return {
        "id": app_id,
        "name": name,
        "class": app_class or initial_class,
        "kind": kind,
        "cwd": cwd if kind == "terminal" else "",
        "launch": launch,
        "policy": "ensure" if global_app else "launch",
        "selected": not global_app,
        "program": program,
        "command": command,
        "title": str(client.get("title") or ""),
    }


def current_workspace() -> dict[str, Any]:
    return hypr_json("activeworkspace")


def current_apps(workspace: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    workspace = workspace or current_workspace()
    workspace_id = int(workspace.get("id", 0))
    entries = desktop_entries()
    apps: list[dict[str, Any]] = []
    seen: set[str] = set()
    for client in hypr_json("clients"):
        client_workspace = client.get("workspace") or {}
        if int(client_workspace.get("id", 0)) != workspace_id:
            continue
        app = app_from_client(client, entries)
        if not app or app["id"] in seen:
            continue
        seen.add(app["id"])
        apps.append(app)
    apps.sort(key=lambda item: (item["kind"] != "terminal", item["name"].casefold()))
    return apps


def valid_app_recipe(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    launch = value.get("launch")
    return bool(
        isinstance(launch, list)
        and launch
        and all(isinstance(argument, str) and "\0" not in argument for argument in launch)
    )


def validated_rift(value: Any, expected_slug: str | None = None) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        return None
    slug = value.get("slug")
    name = value.get("name")
    apps = value.get("apps")
    if not isinstance(slug, str) or not slug:
        return None
    try:
        if slugify(slug) != slug:
            return None
    except ValueError:
        return None
    if expected_slug is not None and slug != expected_slug:
        return None
    if not isinstance(name, str) or not name.strip() or not isinstance(apps, list):
        return None
    result = dict(value)
    result["apps"] = [app for app in apps if valid_app_recipe(app)]
    result["validationErrors"] = [
        f"Skipped invalid app recipe at index {index}"
        for index, app in enumerate(apps)
        if not valid_app_recipe(app)
    ]
    result["startup"] = value.get("startup") is True
    return result


def load_rifts() -> list[dict[str, Any]]:
    ensure_dirs()
    result = []
    for path in sorted(RIFTS_ROOT.glob("*.json")):
        value = validated_rift(read_json(path, None), path.stem)
        if value is not None:
            result.append(value)
    result.sort(key=lambda item: str(item.get("name", "")).casefold())
    return result


def rift_path(slug: str) -> Path:
    return RIFTS_ROOT / f"{slugify(slug)}.json"


def load_rift(slug: str) -> dict[str, Any]:
    expected_slug = slugify(slug)
    value = validated_rift(read_json(rift_path(expected_slug), None), expected_slug)
    if value is None:
        raise ValueError(f"Rift not found or invalid: {slug}")
    return value


def normalized_runtime_state(value: Any, signature: str) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("signature") != signature:
        return {"signature": signature, "open": {}}
    associations = value.get("open")
    if not isinstance(associations, dict):
        associations = {}
    valid = {}
    for slug, association in associations.items():
        if not isinstance(slug, str) or not isinstance(association, dict):
            continue
        try:
            workspace_id = int(association.get("workspace_id", 0))
        except (TypeError, ValueError):
            continue
        if workspace_id <= 0:
            continue
        valid[slug] = {
            "workspace_id": workspace_id,
            "workspace_name": str(association.get("workspace_name", "")),
        }
    return {"signature": signature, "open": valid}


def runtime_state() -> dict[str, Any]:
    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
    state = normalized_runtime_state(read_json(RUNTIME_FILE, None), signature)
    try:
        # A workspace that exists but holds no windows is nobody's Rift: Hyprland
        # reuses numeric ids, so an empty workspace must never inherit a stale
        # association (that is how an update once clobbered the wrong Rift).
        live_ids = {
            int(item.get("id", 0))
            for item in hypr_json("workspaces")
            if int(item.get("windows", 0) or 0) > 0
        }
    except Exception:
        return state
    state["open"] = {
        slug: item for slug, item in (state.get("open") or {}).items()
        if int((item or {}).get("workspace_id", 0)) in live_ids
    }
    return state


def save_runtime(state: dict[str, Any]) -> None:
    atomic_json(RUNTIME_FILE, state)


@contextmanager
def runtime_transaction() -> Iterator[dict[str, Any]]:
    """Serialize a complete runtime-state read, mutation, and replacement."""
    ensure_dirs()
    with (STATE_ROOT / "runtime.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state = runtime_state()
        yield state
        save_runtime(state)


def load_settings() -> dict[str, Any]:
    value = read_json(SETTINGS_FILE, {})
    return value if isinstance(value, dict) else {}


def save_settings(settings: dict[str, Any]) -> None:
    atomic_json(SETTINGS_FILE, settings)


def help_enabled(settings: dict[str, Any] | None = None, rifts: list[dict[str, Any]] | None = None) -> bool:
    """Help mode is on for brand-new users until they save their first Rift, or
    whenever they switch it on themselves."""
    settings = load_settings() if settings is None else settings
    explicit = settings.get("help")
    if isinstance(explicit, bool):
        return explicit
    return not bool(settings.get("everSaved")) and not (rifts if rifts is not None else load_rifts())


def set_help(enabled: bool) -> dict[str, Any]:
    settings = load_settings()
    settings["help"] = enabled
    save_settings(settings)
    return {"help": enabled}


def rename_rift(slug: str, new_name: str) -> dict[str, Any]:
    rift = load_rift(slug)
    new_slug = slugify(new_name)
    if new_slug != rift["slug"] and rift_path(new_slug).exists():
        raise ValueError(f"A Rift named {new_name.strip()} already exists")
    old_slug = rift["slug"]
    rift["name"] = new_name.strip()
    rift["slug"] = new_slug
    persist_rift(rift)
    if new_slug != old_slug:
        rift_path(old_slug).unlink(missing_ok=True)
        with runtime_transaction() as runtime:
            association = runtime.get("open", {}).pop(old_slug, None)
            if association:
                runtime["open"][new_slug] = association
    return rift


def state_payload() -> dict[str, Any]:
    workspace = current_workspace()
    apps = current_apps(workspace)
    runtime = runtime_state()
    current_slug = ""
    for slug, association in runtime.get("open", {}).items():
        if int(association.get("workspace_id", 0)) == int(workspace.get("id", 0)):
            current_slug = slug
            break
    rifts = load_rifts()
    current_saved = next((item for item in rifts if item.get("slug") == current_slug), None)
    saved_ids = {app.get("id") for app in (current_saved or {}).get("apps", [])}
    current_ids = {app.get("id") for app in apps if app.get("selected", True)}
    settings = load_settings()
    # Tell the panel where every open Rift lives so the entry view can say
    # "on workspace 7" and only offer Update when you are standing there.
    open_map = {slug: int(item.get("workspace_id", 0)) for slug, item in runtime.get("open", {}).items()}
    for rift in rifts:
        rift["openWorkspace"] = open_map.get(rift.get("slug"), 0)
    return {
        "workspace": {"id": workspace.get("id", 0), "name": workspace.get("name", "")},
        "apps": apps,
        "rifts": rifts,
        "currentRift": current_slug,
        "changed": bool(current_slug and current_saved and saved_ids != current_ids),
        "help": help_enabled(settings, rifts),
        "everSaved": bool(settings.get("everSaved")),
    }


def save_rift(
    name: str,
    include_ids: list[str] | None = None,
    expect_workspace: int | None = None,
    update_of: str | None = None,
) -> dict[str, Any]:
    slug = slugify(name)
    workspace = current_workspace()
    workspace_id = int(workspace.get("id", 0))
    # The panel tells us which workspace it believed it was looking at. If the
    # user moved in the meantime, refuse rather than record the wrong workspace.
    if expect_workspace is not None and expect_workspace != workspace_id:
        raise RuntimeError(
            f"You are on workspace {workspace_id} but the panel showed workspace {expect_workspace}. "
            "Reopen Rift and try again."
        )
    if update_of is not None:
        association = runtime_state().get("open", {}).get(slugify(update_of))
        bound_to = int((association or {}).get("workspace_id", 0))
        if bound_to != workspace_id:
            where = f"workspace {bound_to}" if bound_to else "no open workspace"
            raise RuntimeError(
                f"{name} belongs to {where}, not workspace {workspace_id}. "
                "Save this workspace as a new Rift instead."
            )
    if rift_path(slug).exists() and (update_of is None or slugify(update_of) != slug):
        raise RuntimeError(
            f"A Rift named {name.strip()} already exists. "
            "Update it from its own workspace, or choose a different name."
        )
    apps = current_apps(workspace)
    if include_ids is None:
        apps = [app for app in apps if app.get("selected", True)]
    else:
        wanted = set(include_ids)
        apps = [app for app in apps if app["id"] in wanted]
    focused = current_workspace()
    if int(focused.get("id", 0)) != workspace_id:
        raise RuntimeError("Workspace changed while saving; try again")
    existing = read_json(rift_path(slug), {})
    value = {
        "schemaVersion": 1,
        "slug": slug,
        "name": name.strip(),
        "startup": bool(existing.get("startup", False)),
        "apps": apps,
        "savedAt": int(time.time()),
    }
    # Keep the recipes we are replacing (newest first, max 5) so the panel can
    # offer revert — and revert again if the first revert was wrong too.
    history = list(existing.get("history") or [])
    if existing.get("previous") and not history:  # migrate v0.2.0 single-slot format
        history = [existing["previous"]]
    if isinstance(existing.get("apps"), list) and existing.get("apps") != apps:
        history.insert(0, {"apps": existing["apps"], "savedAt": existing.get("savedAt", 0)})
    history = history[:5]
    if history:
        value["history"] = history
        value["previous"] = history[0]
    atomic_json(rift_path(slug), value)
    settings = load_settings()
    if not settings.get("everSaved"):
        settings["everSaved"] = True  # help mode retires itself after the first real Rift
        save_settings(settings)
    with runtime_transaction() as runtime:
        runtime["open"] = {
            open_slug: association
            for open_slug, association in runtime.get("open", {}).items()
            if open_slug == slug or int(association.get("workspace_id", 0)) != workspace_id
        }
        runtime.setdefault("open", {})[slug] = {
            "workspace_id": workspace_id,
            "workspace_name": str(workspace.get("name", "")),
        }
    return value


def app_is_running(app: dict[str, Any]) -> bool:
    wanted = str(app.get("class", "")).casefold()
    if not wanted:
        return False
    try:
        return any(
            wanted in {str(client.get("class", "")).casefold(), str(client.get("initialClass", "")).casefold()}
            for client in hypr_json("clients")
        )
    except Exception:
        return False


def launch_app_result(app: dict[str, Any], rift: dict[str, Any]) -> dict[str, str]:
    identity = str(app.get("id") or app.get("name") or "unknown")
    if app.get("policy") == "ensure" and app_is_running(app):
        return {"app": identity, "status": "already-running"}
    argv = app.get("launch") or []
    if not isinstance(argv, list) or not argv:
        return {"app": identity, "status": "failed", "error": "Invalid launch recipe"}
    recorded = str(app.get("cwd") or "")
    if recorded:
        if not Path(recorded).is_dir():
            return {
                "app": identity,
                "status": "failed",
                "error": f"Working directory no longer exists: {recorded}",
            }
        cwd = recorded
    else:
        cwd = str(Path.home())
    env = os.environ.copy()
    env["RIFT_NAME"] = str(rift.get("name", ""))
    env["RIFT_SLUG"] = str(rift.get("slug", ""))
    try:
        subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, TypeError, ValueError) as error:
        return {"app": identity, "status": "failed", "error": str(error)}
    return {"app": identity, "status": "launched"}


def launch_app(app: dict[str, Any], rift: dict[str, Any]) -> bool:
    return launch_app_result(app, rift)["status"] == "launched"


def wait_for_workspace_change(previous_id: int, timeout: float = 2.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        workspace = current_workspace()
        workspace_id = int(workspace.get("id", 0))
        if workspace_id > 0 and workspace_id != previous_id:
            return workspace
        if time.monotonic() >= deadline:
            raise RuntimeError("Timed out waiting for a fresh workspace")
        time.sleep(0.05)


def open_rift(slug: str) -> dict[str, Any]:
    rift = load_rift(slug)
    runtime = runtime_state()
    association = (runtime.get("open") or {}).get(rift["slug"])
    if association:
        hypr_dispatch("workspace", str(association["workspace_id"]))
        pending = set(association.get("failed_apps", []))
        if pending:
            retry_apps = [app for app in rift.get("apps", []) if str(app.get("id", "")) in pending]
            results = [launch_app_result(app, rift) for app in retry_apps]
            remaining = [result["app"] for result in results if result["status"] == "failed"]
            launched = sum(result["status"] == "launched" for result in results)
            with runtime_transaction() as locked:
                current = (locked.get("open") or {}).get(rift["slug"])
                if current is not None:
                    if remaining:
                        current["failed_apps"] = remaining
                    else:
                        current.pop("failed_apps", None)
            return {
                "action": "partial" if remaining else "opened",
                "rift": rift["slug"],
                "workspace": association["workspace_id"],
                "launched": launched,
                "failed": len(remaining),
                "results": results,
            }
        return {"action": "focused", "rift": rift["slug"], "workspace": association["workspace_id"]}

    previous_id = int(current_workspace().get("id", 0))
    hypr_dispatch("workspace", "emptyn")
    workspace = wait_for_workspace_change(previous_id)
    workspace_id = int(workspace.get("id", 0))
    apps = rift.get("apps", [])
    results = [launch_app_result(app, rift) for app in apps]
    launched = sum(result["status"] == "launched" for result in results)
    satisfied = sum(result["status"] in {"launched", "already-running"} for result in results)
    failed = len(results) - satisfied
    if apps and satisfied == 0:
        return {
            "action": "failed",
            "rift": rift["slug"],
            "workspace": workspace_id,
            "launched": 0,
            "failed": failed,
            "results": results,
        }
    with runtime_transaction() as locked:
        locked.setdefault("open", {})[rift["slug"]] = {
            "workspace_id": workspace_id,
            "workspace_name": str(workspace.get("name", "")),
        }
        if failed:
            locked["open"][rift["slug"]]["failed_apps"] = [
                result["app"] for result in results if result["status"] == "failed"
            ]
    return {
        "action": "partial" if failed else "opened",
        "rift": rift["slug"],
        "workspace": workspace_id,
        "launched": launched,
        "failed": failed,
        "results": results,
        "validationErrors": rift.get("validationErrors", []),
    }


def new_workspace() -> dict[str, Any]:
    previous_id = int(current_workspace().get("id", 0))
    hypr_dispatch("workspace", "emptyn")
    workspace = wait_for_workspace_change(previous_id)
    return {"workspace": {"id": workspace.get("id", 0), "name": workspace.get("name", "")}}


def persist_rift(rift: dict[str, Any]) -> dict[str, Any]:
    """Write a Rift definition, dropping derived fields that must not be stored."""
    stored = {key: value for key, value in rift.items() if key not in ("validationErrors", "openWorkspace")}
    atomic_json(rift_path(stored["slug"]), stored)
    return rift


def set_startup(slug: str, enabled: bool) -> dict[str, Any]:
    rift = load_rift(slug)
    rift["startup"] = enabled
    return persist_rift(rift)


def revert_rift(slug: str) -> dict[str, Any]:
    """Swap the current recipe with the previous one, so revert is itself revertible."""
    rift = load_rift(slug)
    history = list(rift.get("history") or ([rift["previous"]] if rift.get("previous") else []))
    if not history or not isinstance(history[0], dict) or not isinstance(history[0].get("apps"), list):
        raise ValueError(f"Nothing to revert for {rift.get('name', slug)}")
    restored = history.pop(0)
    # The recipe we are leaving goes to the back of the stack so revert is itself revertible.
    history.append({"apps": rift.get("apps", []), "savedAt": rift.get("savedAt", 0)})
    history = history[:5]
    rift["apps"] = restored["apps"]
    rift["savedAt"] = int(time.time())
    rift["history"] = history
    rift["previous"] = history[0]
    return persist_rift(rift)


def delete_rift(slug: str) -> None:
    path = rift_path(slug)
    if not path.exists():
        raise ValueError(f"Rift not found: {slug}")
    path.unlink()
    with runtime_transaction() as runtime:
        runtime.get("open", {}).pop(slugify(slug), None)


def startup_open() -> dict[str, Any]:
    ensure_dirs()
    with startup_lock() as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"action": "already-running"}
        signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
        marker = STATE_ROOT / f"startup-{re.sub(r'[^a-zA-Z0-9_.-]', '_', signature)}"
        if marker.exists():
            return {"action": "already-opened"}
        opened = []
        needs_retry = []
        for rift in load_rifts():
            if rift.get("startup"):
                result = open_rift(rift["slug"])
                opened.append(result)
                if result.get("action") in {"failed", "partial"}:
                    needs_retry.append(rift["slug"])
                time.sleep(0.25)
        if needs_retry:
            # Leave the marker unset so the next startup-open retries the stragglers,
            # but every other startup Rift has already been given its chance.
            raise RuntimeError("Startup Rift needs retry: " + ", ".join(needs_retry))
        marker.touch()
        return {"action": "startup", "opened": opened}


def emit(value: Any) -> None:
    print(json.dumps({"ok": True, "data": value}, separators=(",", ":")))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="rift-helper")
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("state")
    save = sub.add_parser("save")
    save.add_argument("name")
    save.add_argument("--apps", default=None)
    save.add_argument("--expect-workspace", type=int, default=None)
    save.add_argument("--update-of", default=None)
    open_command = sub.add_parser("open")
    open_command.add_argument("slug")
    startup = sub.add_parser("startup")
    startup.add_argument("slug")
    startup.add_argument("enabled", choices=["on", "off"])
    delete = sub.add_parser("delete")
    delete.add_argument("slug")
    revert = sub.add_parser("revert")
    revert.add_argument("slug")
    rename = sub.add_parser("rename")
    rename.add_argument("slug")
    rename.add_argument("name")
    help_command = sub.add_parser("help")
    help_command.add_argument("enabled", choices=["on", "off"])
    sub.add_parser("new-workspace")
    sub.add_parser("startup-open")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "state":
            emit(state_payload())
        elif args.command == "save":
            include = None if args.apps is None else (args.apps.split("\x1f") if args.apps else [])
            emit(save_rift(args.name, include, args.expect_workspace, args.update_of))
        elif args.command == "open":
            emit(open_rift(args.slug))
        elif args.command == "startup":
            emit(set_startup(args.slug, args.enabled == "on"))
        elif args.command == "revert":
            emit(revert_rift(args.slug))
        elif args.command == "rename":
            emit(rename_rift(args.slug, args.name))
        elif args.command == "help":
            emit(set_help(args.enabled == "on"))
        elif args.command == "delete":
            delete_rift(args.slug)
            emit({"deleted": args.slug})
        elif args.command == "new-workspace":
            emit(new_workspace())
        elif args.command == "startup-open":
            emit(startup_open())
        return 0
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
