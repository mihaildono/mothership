"""
config.py — loads hub/config.toml into a typed dataclass.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass
class OrchestratorConfig:
    nebula_ip: str
    ws_port: int

    @property
    def ws_url(self) -> str:
        return f"ws://{self.nebula_ip}:{self.ws_port}/ws/hub"


@dataclass
class OllamaConfig:
    model: str
    host: str


@dataclass
class HubConfig:
    hub_id: str
    auth_token: str
    work_start: str  # "HH:MM"
    work_end: str  # "HH:MM"
    orchestrator: OrchestratorConfig
    ollama: OllamaConfig


def load(path: Path | None = None) -> HubConfig:
    config_path = path or Path(__file__).parent / "config.toml"
    with open(config_path, "rb") as f:
        raw = tomllib.load(f)

    return HubConfig(
        hub_id=raw["hub_id"],
        auth_token=raw["auth_token"],
        work_start=raw["work_start"],
        work_end=raw["work_end"],
        orchestrator=OrchestratorConfig(
            nebula_ip=raw["orchestrator"]["nebula_ip"],
            ws_port=raw["orchestrator"]["ws_port"],
        ),
        ollama=OllamaConfig(
            model=raw["ollama"]["model"],
            host=raw["ollama"]["host"],
        ),
    )
