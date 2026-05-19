#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
manage.py - Cross-platform control CLI for Mothership.

Usage:
    python manage.py <component> <action> [options]

Components:
    mother      — The orchestrator / lighthouse
    child       — A worker node (local dev instance)
    nebula      — Overlay network setup
    bundle      — Child bundle management
    token       — Auth token management

Run `python manage.py <component> --help` for details.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MOTHER_DIR = ROOT / "mother"
CHILD_DIR = ROOT / "child"
NEBULA_DIR = ROOT / "nebula"

IS_WIN = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _find_uv() -> str:
    uv = shutil.which("uv")
    if uv:
        return uv
    # Common install locations per platform
    candidates = [
        Path.home() / ".local" / "bin" / "uv",  # Linux/macOS (curl installer)
        Path.home() / ".cargo" / "bin" / "uv",  # cargo install
    ]
    if IS_WIN:
        local_app = Path(os.environ.get("LOCALAPPDATA", ""))
        candidates += [
            local_app / "Programs" / "uv" / "uv.exe",  # Windows installer default
            Path.home() / ".local" / "bin" / "uv.exe",
            Path.home() / ".cargo" / "bin" / "uv.exe",
        ]
    for c in candidates:
        if c.exists():
            return str(c)
    print("ERROR: uv not found.")
    print("  Install: https://docs.astral.sh/uv/getting-started/installation/")
    if IS_WIN:
        print(
            '  Windows: winget install --id=astral-sh.uv  OR  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"'
        )
    else:
        print("  macOS/Linux: curl -LsSf https://astral.sh/uv/install.sh | sh")
    sys.exit(1)


def _bash_or_die(label: str) -> str:
    """Return path to bash, or print a helpful error and exit on Windows."""
    bash = shutil.which("bash")
    if bash:
        return bash
    if IS_WIN:
        print(f"ERROR: '{label}' requires bash.")
        print(
            "  On Windows, use WSL: https://learn.microsoft.com/en-us/windows/wsl/install"
        )
        print("  Then run this command inside the WSL terminal.")
        sys.exit(1)
    print(f"ERROR: bash not found (required for {label})")
    sys.exit(1)


def _venv_python(component_dir: Path) -> str:
    venv = component_dir / ".venv"
    if IS_WIN:
        return str(venv / "Scripts" / "python.exe")
    return str(venv / "bin" / "python3")


def _ensure_venv(component_dir: Path) -> None:
    venv = component_dir / ".venv"
    if venv.exists():
        return
    uv = _find_uv()
    print(f"==> Creating venv in {venv}...")
    subprocess.run([uv, "venv", str(venv), "--python", "3.12", "--seed"], check=True)
    python = _venv_python(component_dir)
    req = component_dir / "requirements.txt"
    if req.exists():
        print(f"==> Installing dependencies...")
        subprocess.run(
            [python, "-m", "pip", "install", "-r", str(req), "-q"], check=True
        )


def _run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> int:
    merged_env = {**os.environ, **(env or {})}
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=merged_env)
        return proc.returncode
    except KeyboardInterrupt:
        return 130


def _load_mother_env() -> dict[str, str]:
    env_file = MOTHER_DIR / ".env"
    if not env_file.exists():
        return {}
    result = {}
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            result[k.strip()] = v.strip()
    return result


_NAMES_FILE = MOTHER_DIR / "child-names.json"


def _load_names() -> dict[str, str]:
    if not _NAMES_FILE.exists():
        return {}
    import json

    try:
        return json.loads(_NAMES_FILE.read_text())
    except Exception:
        return {}


def _save_names(names: dict[str, str]) -> None:
    import json

    _NAMES_FILE.write_text(json.dumps(names, indent=2) + "\n")


# ── Mother commands ───────────────────────────────────────────────────────────


_MOTHER_PID = MOTHER_DIR / ".mother.pid"
_CHILD_PID = CHILD_DIR / ".child.pid"


def _start_background(cmd: list[str], cwd: Path, env: dict, pid_file: Path) -> int:
    """Start a process detached from the terminal and write its PID."""
    import subprocess

    if IS_WIN:
        # CREATE_NEW_PROCESS_GROUP + DETACHED_PROCESS
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            creationflags=0x00000008
            | 0x00000200,  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            stdout=open(cwd / "server.log", "a"),
            stderr=subprocess.STDOUT,
        )
    else:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdout=open(cwd / "server.log", "a"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    pid_file.write_text(str(proc.pid))
    return proc.pid


def _stop_by_pid(pid_file: Path, label: str) -> int:
    """Kill the process recorded in pid_file."""
    if not pid_file.exists():
        print(f"{label} is not running (no PID file).")
        return 0
    try:
        pid = int(pid_file.read_text().strip())
        if IS_WIN:
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            os.kill(pid, signal.SIGTERM)
        pid_file.unlink(missing_ok=True)
        print(f"{label} stopped (PID {pid}).")
        return 0
    except (ValueError, ProcessLookupError):
        pid_file.unlink(missing_ok=True)
        print(f"{label} was not running (stale PID file removed).")
        return 0
    except Exception as e:
        print(f"ERROR stopping {label}: {e}")
        return 1


def mother_start(args: argparse.Namespace) -> int:
    _ensure_venv(MOTHER_DIR)
    python = _venv_python(MOTHER_DIR)
    env = {**os.environ, **_load_mother_env()}
    if not env.get("MOTHER_API_KEY"):
        print("ERROR: mother/.env not found or MOTHER_API_KEY not set.")
        print("       Run: python3 manage.py nebula setup")
        return 1
    if _MOTHER_PID.exists():
        print(
            "Mother appears already running (PID file exists). Run 'mother stop' first."
        )
        return 1
    pid = _start_background(
        [python, "main.py"], cwd=MOTHER_DIR, env=env, pid_file=_MOTHER_PID
    )
    print(f"==> Mother started (PID {pid}) on port 8765.")
    print(f"    Logs: {MOTHER_DIR / 'server.log'}")
    return 0


def mother_stop(args: argparse.Namespace) -> int:
    return _stop_by_pid(_MOTHER_PID, "Mother")


def mother_status(args: argparse.Namespace) -> int:
    env = _load_mother_env()
    api_key = env.get("MOTHER_API_KEY", "")
    if not api_key:
        print("Mother not configured (no .env)")
        return 1
    import urllib.request
    import urllib.error

    url = "http://localhost:8765/children"
    req = urllib.request.Request(url, headers={"X-API-Key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            import json

            children = json.loads(resp.read())
            print(f"Mother is RUNNING — {len(children)} child(ren) connected:")
            for c in children:
                model = c.get("model", "?")
                print(
                    f"  • {c['child_id']}  status={c['status']}  model={model}  ping={c.get('last_ping_ms', '?')}ms"
                )
            return 0
    except urllib.error.URLError:
        print("Mother is NOT running (port 8765 not responding)")
        return 1


# ── Child commands ────────────────────────────────────────────────────────────


def child_start(args: argparse.Namespace) -> int:
    _ensure_venv(CHILD_DIR)
    python = _venv_python(CHILD_DIR)
    config = CHILD_DIR / "config.toml"
    if not config.exists():
        print("ERROR: child/config.toml not found.")
        print("       For local dev, copy from a bundle or create manually.")
        return 1
    if _CHILD_PID.exists():
        print(
            "Child appears already running (PID file exists). Run 'child stop' first."
        )
        return 1
    pid = _start_background(
        [python, "main.py"], cwd=CHILD_DIR, env=dict(os.environ), pid_file=_CHILD_PID
    )
    print(f"==> Child started (PID {pid}).")
    print(f"    Logs: {CHILD_DIR / 'server.log'}")
    return 0


def child_stop(args: argparse.Namespace) -> int:
    return _stop_by_pid(_CHILD_PID, "Child")


def child_logs(args: argparse.Namespace) -> int:
    log = Path.home() / "mothership-child" / "child.log"
    if not log.exists():
        log = CHILD_DIR / "child.log"
    if not log.exists():
        print("No log file found. Run the child first.")
        return 1
    lines = args.lines if hasattr(args, "lines") else 50
    content = log.read_text().splitlines()
    for line in content[-lines:]:
        print(line)
    return 0


def child_detect_model(args: argparse.Namespace) -> int:
    """Run whichllm detection without starting the agent."""
    _ensure_venv(CHILD_DIR)
    python = _venv_python(CHILD_DIR)
    code = """
import model_detector, ollama_runner
result = model_detector.detect(ollama_bin=ollama_runner._OLLAMA_BIN)
if result:
    print(f"Recommended: {result.ollama_name}")
    print(f"  HuggingFace: {result.hf_model_id}")
    print(f"  Score: {result.score:.1f}")
    print(f"  VRAM: ~{result.vram_gb:.1f} GB")
else:
    print("Detection failed — whichllm not available or no mappable model found.")
"""
    return _run([python, "-c", code], cwd=CHILD_DIR)


def child_list(args: argparse.Namespace) -> int:
    """List all named children from child-names.json."""
    names = _load_names()
    if not names:
        print(
            "No children named yet. Use: python3 manage.py child name <child_id> <display_name>"
        )
        return 0
    print(f"Named children ({len(names)}):")
    for child_id, name in sorted(names.items()):
        print(f"  {name:<24} {child_id}")
    return 0


def child_name(args: argparse.Namespace) -> int:
    """Set or update the display name for a child."""
    names = _load_names()
    old = names.get(args.child_id)
    names[args.child_id] = args.display_name
    _save_names(names)
    if old:
        print(f"Renamed '{old}' -> '{args.display_name}' ({args.child_id})")
    else:
        print(f"Named '{args.child_id}' as '{args.display_name}'")
    print("Restart the mother for the new name to appear in status.")
    return 0


def child_remove(args: argparse.Namespace) -> int:
    """Remove a child: kick active connection, revoke token, delete name."""
    # 1. Kick the active WS connection if mother is running
    env = _load_mother_env()
    api_key = env.get("MOTHER_API_KEY", "")
    if api_key:
        import urllib.request
        import urllib.error

        req = urllib.request.Request(
            f"http://localhost:8765/children/{args.child_id}",
            headers={"X-API-Key": api_key},
            method="DELETE",
        )
        try:
            urllib.request.urlopen(req, timeout=3)
            print(f"Child '{args.child_id}' disconnected from mother.")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"Child '{args.child_id}' was not connected (already offline).")
            else:
                print(f"Warning: kick returned {e.code} — proceeding anyway.")
        except urllib.error.URLError:
            print(
                "Warning: mother not reachable — token will be revoked but child may stay connected until it reconnects."
            )

    # 2. Remove from names file
    names = _load_names()
    removed_name = names.pop(args.child_id, None)
    if removed_name:
        _save_names(names)
        print(f"Removed name entry: '{removed_name}'")

    # 3. Revoke token + bundle (child will be rejected on next reconnect)
    rc = _manage_child_sh("revoke", args.child_id)

    print(
        f"Child '{args.child_id}' removed. Token revoked — reconnection will be rejected."
    )
    return rc


# ── Nebula / setup commands ───────────────────────────────────────────────────


def do_setup(args: argparse.Namespace) -> int:
    """Install uv and create venvs for mother and child."""
    # Install uv if missing
    if not shutil.which("uv"):
        print("==> Installing uv...")
        if IS_WIN:
            rc = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "irm https://astral.sh/uv/install.ps1 | iex",
                ],
                shell=False,
            ).returncode
        else:
            rc = subprocess.run(
                "curl -LsSf https://astral.sh/uv/install.sh | sh",
                shell=True,
            ).returncode
        if rc != 0:
            print(
                "ERROR: uv installation failed. Install manually: https://docs.astral.sh/uv/"
            )
            return rc
        # Re-scan PATH after install
        path_additions = [
            str(Path.home() / ".local" / "bin"),
            str(Path.home() / ".cargo" / "bin"),
        ]
        os.environ["PATH"] = (
            os.pathsep.join(path_additions) + os.pathsep + os.environ.get("PATH", "")
        )

    uv = _find_uv()
    print(f"==> uv found: {uv}")

    for label, d in (("mother", MOTHER_DIR), ("child", CHILD_DIR)):
        venv = d / ".venv"
        if venv.exists():
            print(f"    {label}/.venv already exists — skipping")
            continue
        print(f"==> Creating {label}/.venv (Python 3.12)...")
        subprocess.run(
            [uv, "venv", str(venv), "--python", "3.12", "--seed"], check=True
        )
        python = _venv_python(d)
        req = d / "requirements.txt"
        if req.exists():
            print(f"==> Installing {label} dependencies...")
            subprocess.run(
                [python, "-m", "pip", "install", "-r", str(req), "-q"], check=True
            )

    print("\nSetup complete.")
    if not IS_WIN:
        print(
            "Next: run  python3 manage.py nebula setup  to generate keys and bundles."
        )
    else:
        print(
            "Next: run nebula/setup-mother.sh inside WSL to generate keys and bundles."
        )
    return 0


def nebula_setup(args: argparse.Namespace) -> int:
    bash = _bash_or_die("nebula setup")
    script = NEBULA_DIR / "setup-mother.sh"
    if not script.exists():
        print(f"ERROR: {script} not found")
        return 1
    print("==> Running nebula/setup-mother.sh...")
    return _run([bash, str(script)], cwd=ROOT)


# ── Bundle commands ───────────────────────────────────────────────────────────


def bundle_list(args: argparse.Namespace) -> int:
    bundles_dir = NEBULA_DIR / "bundles"
    if not bundles_dir.exists():
        print("No bundles directory. Run: python manage.py nebula setup")
        return 1
    tarballs = sorted(bundles_dir.glob("*.tar.gz"))
    tokens = sorted(bundles_dir.glob("*.token"))
    if not tarballs:
        print("No bundles found.")
        return 0
    print(f"Bundles in {bundles_dir}:")
    for tb in tarballs:
        child_id = tb.stem.replace(".tar", "")
        has_token = (bundles_dir / f"{child_id}.token").exists()
        status = "ready (token valid)" if has_token else "used (token consumed)"
        print(f"  • {child_id}.tar.gz  — {status}")
    return 0


# ── Token commands ────────────────────────────────────────────────────────────


def _manage_child_sh(subcmd: str, child_id: str | None = None) -> int:
    bash = _bash_or_die(f"token {subcmd}")
    script = NEBULA_DIR / "manage-child.sh"
    if not script.exists():
        print("ERROR: nebula/manage-child.sh not found")
        return 1
    cmd = [bash, str(script), subcmd]
    if child_id:
        cmd.append(child_id)
    return _run(cmd, cwd=ROOT)


def token_list(args: argparse.Namespace) -> int:
    return _manage_child_sh("list")


def token_add(args: argparse.Namespace) -> int:
    return _manage_child_sh("add", args.child_id)


def token_revoke(args: argparse.Namespace) -> int:
    return _manage_child_sh("revoke", args.child_id)


def token_retoken(args: argparse.Namespace) -> int:
    return _manage_child_sh("retoken", args.child_id)


# ── Send / test commands ──────────────────────────────────────────────────────


def send_prompt(args: argparse.Namespace) -> int:
    """Send a prompt to a child and poll for result."""
    env = _load_mother_env()
    api_key = env.get("MOTHER_API_KEY", "")
    if not api_key:
        print("ERROR: MOTHER_API_KEY not set. Is mother configured?")
        return 1

    import json
    import urllib.request
    import urllib.error
    import time
    import secrets as sec

    task_id = f"t-{sec.token_hex(4)}"
    host = args.host if hasattr(args, "host") and args.host else "localhost"
    base = f"http://{host}:8765"

    # Send
    payload = json.dumps(
        {
            "child_id": args.child_id,
            "task_id": task_id,
            "prompt": args.prompt,
        }
    ).encode()
    req = urllib.request.Request(
        f"{base}/send",
        data=payload,
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as e:
        print(f"ERROR: {e.code} — {e.read().decode()}")
        return 1
    except urllib.error.URLError as e:
        print(f"ERROR: Cannot reach mother at {base} — {e}")
        return 1

    print(f"Task {task_id} sent to {args.child_id}. Waiting for result...")

    # Poll
    timeout = args.timeout if hasattr(args, "timeout") and args.timeout else 120
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1)
        req = urllib.request.Request(
            f"{base}/result/{task_id}",
            headers={"X-API-Key": api_key},
        )
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())
                if data.get("error"):
                    print(f"\nERROR from {args.child_id}: {data['error']}")
                    return 1
                print(f"\n{data.get('result', '')}")
                return 0
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(".", end="", flush=True)
                continue
            print(f"\nERROR: {e.code}")
            return 1

    print(f"\nTimeout after {timeout}s — result not ready.")
    return 1


# ── CLI parser ────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="manage.py",
        description="Mothership — cross-platform control CLI",
    )
    sub = parser.add_subparsers(dest="component", required=True)

    # setup (top-level, no subcommand needed)
    sub.add_parser(
        "setup", help="Install uv + create venvs for mother and child"
    ).set_defaults(func=do_setup)

    # mother
    m = sub.add_parser("mother", help="Manage the mother orchestrator")
    m_sub = m.add_subparsers(dest="action", required=True)
    m_sub.add_parser("start", help="Start the mother server").set_defaults(
        func=mother_start
    )
    m_sub.add_parser("stop", help="Stop the mother server").set_defaults(
        func=mother_stop
    )
    m_sub.add_parser(
        "status", help="Check mother status and connected children"
    ).set_defaults(func=mother_status)

    # child
    c = sub.add_parser("child", help="Manage child nodes")
    c_sub = c.add_subparsers(dest="action", required=True)
    c_sub.add_parser("start", help="Start the local child agent").set_defaults(
        func=child_start
    )
    c_sub.add_parser("stop", help="Stop the local child agent").set_defaults(
        func=child_stop
    )
    c_logs = c_sub.add_parser("logs", help="Tail child logs")
    c_logs.add_argument("-n", "--lines", type=int, default=50, help="Number of lines")
    c_logs.set_defaults(func=child_logs)
    c_sub.add_parser(
        "detect-model", help="Run hardware detection (whichllm)"
    ).set_defaults(func=child_detect_model)
    c_sub.add_parser("list", help="List all named children").set_defaults(
        func=child_list
    )
    c_name = c_sub.add_parser("name", help="Set/update a child's display name")
    c_name.add_argument("child_id", help="e.g. child-001")
    c_name.add_argument("display_name", help="e.g. 'Gaming PC'")
    c_name.set_defaults(func=child_name)
    c_rename = c_sub.add_parser("rename", help="Rename a child (alias for 'name')")
    c_rename.add_argument("child_id")
    c_rename.add_argument("display_name")
    c_rename.set_defaults(func=child_name)
    c_remove = c_sub.add_parser(
        "remove", help="Remove a child (revoke token + delete name)"
    )
    c_remove.add_argument("child_id")
    c_remove.set_defaults(func=child_remove)

    # nebula
    n = sub.add_parser("nebula", help="Nebula overlay network")
    n_sub = n.add_subparsers(dest="action", required=True)
    n_sub.add_parser("setup", help="Run full mother + Nebula setup").set_defaults(
        func=nebula_setup
    )

    # bundle
    b = sub.add_parser("bundle", help="Child bundle management")
    b_sub = b.add_subparsers(dest="action", required=True)
    b_sub.add_parser("list", help="List available bundles").set_defaults(
        func=bundle_list
    )

    # token
    t = sub.add_parser("token", help="Auth token management")
    t_sub = t.add_subparsers(dest="action", required=True)
    t_sub.add_parser("list", help="List all child tokens").set_defaults(func=token_list)
    t_add = t_sub.add_parser("add", help="Add a new child")
    t_add.add_argument("child_id", help="e.g. child-002")
    t_add.set_defaults(func=token_add)
    t_rev = t_sub.add_parser("revoke", help="Revoke a child's access")
    t_rev.add_argument("child_id")
    t_rev.set_defaults(func=token_revoke)
    t_ret = t_sub.add_parser("retoken", help="Regenerate token + download link")
    t_ret.add_argument("child_id")
    t_ret.set_defaults(func=token_retoken)

    # send (top-level convenience)
    s = sub.add_parser("send", help="Send a prompt to a child")
    s.add_argument("child_id", help="Target child ID")
    s.add_argument("prompt", help="The prompt text")
    s.add_argument(
        "--host", default="localhost", help="Mother host (default: localhost)"
    )
    s.add_argument(
        "--timeout", type=int, default=120, help="Seconds to wait for result"
    )
    s.set_defaults(func=send_prompt)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if hasattr(args, "func"):
        return args.func(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
