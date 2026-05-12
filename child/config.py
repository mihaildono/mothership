"""
config.py — loads child/config.toml into a typed dataclass.
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
class MotherConfig:
    host: str
    ws_port: int

    @property
    def ws_url(self) -> str:
        return f"ws://{self.host}:{self.ws_port}/ws/child"


@dataclass
class OllamaConfig:
    model: str
    host: str


@dataclass
class ChildConfig:
    child_id: str
    auth_token: str
    work_start: str  # "HH:MM"
    work_end: str  # "HH:MM"
    mother: MotherConfig
    ollama: OllamaConfig


def load(path: Path | None = None) -> ChildConfig:
    config_path = path or Path(__file__).parent / "config.toml"
    with open(config_path, "rb") as f:
        raw = tomllib.load(f)

    return ChildConfig(
        child_id=raw["child_id"],
        auth_token=raw["auth_token"],
        work_start=raw["work_start"],
        work_end=raw["work_end"],
        mother=MotherConfig(
            host=raw["mother"]["host"],
            ws_port=raw["mother"]["ws_port"],
        ),
        ollama=OllamaConfig(
            model=raw["ollama"]["model"],
            host=raw["ollama"]["host"],
        ),
    )
