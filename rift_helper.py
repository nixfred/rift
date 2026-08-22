#!/usr/bin/env python3
"""Backend for the Rift Omarchy plugin.

The UI is the product surface. This helper is deliberately small and emits
JSON so the Quickshell plugin never has to scrape shell output.
"""

from __future__ import annotations

import argparse
import configparser
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any


CONFIG_ROOT = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "rift"
RIFTS_ROOT = CONFIG_ROOT / "rifts"
STATE_ROOT = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "rift"
RUNTIME_FILE = STATE_ROOT / "runtime.json"
STARTUP_LOCK = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / f"rift-startup-{os.getuid()}.lock"

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
            exec_token = exec_line.split()[0] if exec_line else ""
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


def terminal_recipe(binary: str, cwd: str) -> list[str]:
    if binary == "ghostty":
        return [binary, f"--working-directory={cwd}"] if cwd else [binary]
    if binary == "kitty":
        return [binary, "--directory", cwd] if cwd else [binary]
    if binary == "alacritty":
        return [binary, "--working-directory", cwd] if cwd else [binary]
    if binary == "foot":
        return [binary, "--working-directory", cwd] if cwd else [binary]
    if binary == "wezterm":
        return [binary, "start", "--cwd", cwd] if cwd else [binary]
    return [binary]


def app_from_client(client: dict[str, Any], entries: list[dict[str, str]]) -> dict[str, Any] | None:
    app_class = str(client.get("class") or "").strip()
    initial_class = str(client.get("initialClass") or "").strip()
    if not app_class and not initial_class:
        return None
    lower_classes = {app_class.casefold(), initial_class.casefold()}
    if lower_classes & IGNORED_CLASSES or any("omarchy" in value for value in lower_classes):
        return None

    pid = int(client.get("pid") or 0)
    executable, _argv, cwd = process_info(pid)
    terminal = next((TERMINALS[value] for value in lower_classes if value in TERMINALS), "")
    desktop = find_desktop_entry([app_class, initial_class], executable, entries)

    if terminal and shutil.which(terminal):
        launch = terminal_recipe(terminal, cwd)
        app_id = f"terminal:{terminal}:{cwd or 'home'}"
        name = terminal.title()
        kind = "terminal"
    elif desktop:
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
    }


def current_workspace() -> dict[str, Any]:
    return hypr_json("activeworkspace")


def current_apps() -> list[dict[str, Any]]:
    workspace = current_workspace()
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


def load_rifts() -> list[dict[str, Any]]:
    ensure_dirs()
    result = []
    for path in sorted(RIFTS_ROOT.glob("*.json")):
        value = read_json(path, None)
        if isinstance(value, dict) and value.get("slug"):
            result.append(value)
    result.sort(key=lambda item: str(item.get("name", "")).casefold())
    return result


def rift_path(slug: str) -> Path:
    return RIFTS_ROOT / f"{slugify(slug)}.json"


def load_rift(slug: str) -> dict[str, Any]:
    value = read_json(rift_path(slug), None)
    if not isinstance(value, dict):
        raise ValueError(f"Rift not found: {slug}")
    return value


def runtime_state() -> dict[str, Any]:
    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
    state = read_json(RUNTIME_FILE, {"signature": signature, "open": {}})
    if state.get("signature") != signature:
        state = {"signature": signature, "open": {}}
    try:
        live_ids = {int(item.get("id", 0)) for item in hypr_json("workspaces")}
    except Exception:
        return state
    state["open"] = {
        slug: item for slug, item in (state.get("open") or {}).items()
        if int((item or {}).get("workspace_id", 0)) in live_ids
    }
    return state


def save_runtime(state: dict[str, Any]) -> None:
    atomic_json(RUNTIME_FILE, state)


def state_payload() -> dict[str, Any]:
    workspace = current_workspace()
    apps = current_apps()
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
    return {
        "workspace": {"id": workspace.get("id", 0), "name": workspace.get("name", "")},
        "apps": apps,
        "rifts": rifts,
        "currentRift": current_slug,
        "changed": bool(current_slug and current_saved and saved_ids != current_ids),
    }


def save_rift(name: str, include_ids: list[str] | None = None) -> dict[str, Any]:
    slug = slugify(name)
    apps = current_apps()
    if include_ids is None:
        apps = [app for app in apps if app.get("selected", True)]
    else:
        wanted = set(include_ids)
        apps = [app for app in apps if app["id"] in wanted]
    existing = read_json(rift_path(slug), {})
    value = {
        "schemaVersion": 1,
        "slug": slug,
        "name": name.strip(),
        "startup": bool(existing.get("startup", False)),
        "apps": apps,
        "savedAt": int(time.time()),
    }
    # Keep the recipe we are replacing so the panel can offer a one-click revert.
    if isinstance(existing.get("apps"), list) and existing.get("apps") != apps:
        value["previous"] = {"apps": existing["apps"], "savedAt": existing.get("savedAt", 0)}
    elif existing.get("previous"):
        value["previous"] = existing["previous"]
    atomic_json(rift_path(slug), value)
    workspace = current_workspace()
    workspace_id = int(workspace.get("id", 0))
    runtime = runtime_state()
    runtime["open"] = {
        open_slug: association
        for open_slug, association in runtime.get("open", {}).items()
        if open_slug == slug or int(association.get("workspace_id", 0)) != workspace_id
    }
    runtime.setdefault("open", {})[slug] = {
        "workspace_id": workspace_id,
        "workspace_name": str(workspace.get("name", "")),
    }
    save_runtime(runtime)
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


def launch_app(app: dict[str, Any], rift: dict[str, Any]) -> bool:
    if app.get("policy") == "ensure" and app_is_running(app):
        return False
    argv = app.get("launch") or []
    if not isinstance(argv, list) or not argv:
        return False
    cwd = str(app.get("cwd") or "")
    if not cwd or not Path(cwd).is_dir():
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
    except (OSError, TypeError, ValueError):
        return False
    return True


def open_rift(slug: str) -> dict[str, Any]:
    rift = load_rift(slug)
    runtime = runtime_state()
    association = runtime.get("open", {}).get(rift["slug"])
    if association:
        hypr_dispatch("workspace", str(association["workspace_id"]))
        return {"action": "focused", "rift": rift["slug"], "workspace": association["workspace_id"]}

    hypr_dispatch("workspace", "emptyn")
    time.sleep(0.12)
    workspace = current_workspace()
    workspace_id = int(workspace.get("id", 0))
    runtime.setdefault("open", {})[rift["slug"]] = {
        "workspace_id": workspace_id,
        "workspace_name": str(workspace.get("name", "")),
    }
    save_runtime(runtime)
    launched = sum(1 for app in rift.get("apps", []) if launch_app(app, rift))
    return {"action": "opened", "rift": rift["slug"], "workspace": workspace_id, "launched": launched}


def new_workspace() -> dict[str, Any]:
    hypr_dispatch("workspace", "emptyn")
    time.sleep(0.12)
    workspace = current_workspace()
    return {"workspace": {"id": workspace.get("id", 0), "name": workspace.get("name", "")}}


def set_startup(slug: str, enabled: bool) -> dict[str, Any]:
    rift = load_rift(slug)
    rift["startup"] = enabled
    atomic_json(rift_path(slug), rift)
    return rift


def revert_rift(slug: str) -> dict[str, Any]:
    """Swap the current recipe with the previous one, so revert is itself revertible."""
    rift = load_rift(slug)
    previous = rift.get("previous")
    if not isinstance(previous, dict) or not isinstance(previous.get("apps"), list):
        raise ValueError(f"Nothing to revert for {rift.get('name', slug)}")
    rift["previous"] = {"apps": rift.get("apps", []), "savedAt": rift.get("savedAt", 0)}
    rift["apps"] = previous["apps"]
    rift["savedAt"] = int(time.time())
    atomic_json(rift_path(slug), rift)
    return rift


def delete_rift(slug: str) -> None:
    path = rift_path(slug)
    if not path.exists():
        raise ValueError(f"Rift not found: {slug}")
    path.unlink()
    runtime = runtime_state()
    runtime.get("open", {}).pop(slugify(slug), None)
    save_runtime(runtime)


def startup_open() -> dict[str, Any]:
    ensure_dirs()
    with STARTUP_LOCK.open("w") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"action": "already-running"}
        signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
        marker = STATE_ROOT / f"startup-{re.sub(r'[^a-zA-Z0-9_.-]', '_', signature)}"
        if marker.exists():
            return {"action": "already-opened"}
        opened = []
        for rift in load_rifts():
            if rift.get("startup"):
                opened.append(open_rift(rift["slug"]))
                time.sleep(0.25)
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
    open_command = sub.add_parser("open")
    open_command.add_argument("slug")
    startup = sub.add_parser("startup")
    startup.add_argument("slug")
    startup.add_argument("enabled", choices=["on", "off"])
    delete = sub.add_parser("delete")
    delete.add_argument("slug")
    revert = sub.add_parser("revert")
    revert.add_argument("slug")
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
            emit(save_rift(args.name, include))
        elif args.command == "open":
            emit(open_rift(args.slug))
        elif args.command == "startup":
            emit(set_startup(args.slug, args.enabled == "on"))
        elif args.command == "revert":
            emit(revert_rift(args.slug))
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
