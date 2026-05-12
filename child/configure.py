"""
configure.py — interactive CLI to create or update child/config.toml.

Usage:
    python configure.py
"""

from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

CONFIG_PATH = Path(__file__).parent / "config.toml"


def _prompt(label: str, default: str, secret: bool = False) -> str:
    display_default = "****" if (secret and default and default != "") else default
    suffix = f" [{display_default}]" if display_default else ""
    while True:
        value = input(f"  {label}{suffix}: ").strip()
        if value:
            return value
        if default:
            return default
        print(f"  ! {label} is required.")


def _load_existing() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    return {}


def _write(values: dict) -> None:
    lines = [
        "# Mothership — Child Node Configuration\n",
        "\n",
        f'child_id   = "{values["child_id"]}"\n',
        f'auth_token = "{values["auth_token"]}"\n',
        f'work_start = "{values["work_start"]}"\n',
        f'work_end   = "{values["work_end"]}"\n',
        "\n",
        "[mother]\n",
        f'host    = "{values["mother_host"]}"\n',
        f'ws_port = {values["mother_ws_port"]}\n',
        "\n",
        "[ollama]\n",
        f'model = "{values["ollama_model"]}"\n',
        f'host  = "{values["ollama_host"]}"\n',
    ]
    CONFIG_PATH.write_text("".join(lines), encoding="utf-8")


def main() -> None:
    existing = _load_existing()
    orch = existing.get("mother", {})
    ollama = existing.get("ollama", {})

    if CONFIG_PATH.exists():
        print(
            f"\nFound existing config at {CONFIG_PATH} — press Enter to keep current value.\n"
        )
    else:
        print("\nNo config.toml found — let's create one.\n")

    print("── Child identity ───────────────────────────────────")
    child_id = _prompt(
        "Child ID (unique name for this machine)", existing.get("child_id", "child-001")
    )
    auth_token = _prompt(
        "Auth token (UUID from the mother admin)",
        existing.get("auth_token", ""),
        secret=True,
    )

    print("\n── Work window (24-hour HH:MM, local time) ──────────")
    work_start = _prompt("Work start time", existing.get("work_start", "00:00"))
    work_end = _prompt("Work end time  ", existing.get("work_end", "23:59"))

    print("\n── Mother ───────────────────────────────────────────")
    nebula_ip = _prompt("Mother host (IP or hostname)", orch.get("host", "127.0.0.1"))
    ws_port = _prompt("Mother WebSocket port", str(orch.get("ws_port", 8765)))

    print("\n── Ollama ───────────────────────────────────────────")
    model = _prompt("Model name", ollama.get("model", "gemma4:e2b"))
    host = _prompt("Ollama host", ollama.get("host", "http://localhost:11434"))

    try:
        ws_port_int = int(ws_port)
    except ValueError:
        print("ERROR: WebSocket port must be an integer.")
        sys.exit(1)

    _write(
        {
            "child_id": child_id,
            "auth_token": auth_token,
            "work_start": work_start,
            "work_end": work_end,
            "mother_host": nebula_ip,
            "mother_ws_port": ws_port_int,
            "ollama_model": model,
            "ollama_host": host,
        }
    )

    print(f"\n==> Saved to {CONFIG_PATH}\n")


if __name__ == "__main__":
    main()
