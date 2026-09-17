from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol


BackendStatus = Literal[
    "ok",
    "load_failed",
    "emulator_crashed",
    "bridge_timeout",
    "frozen",
    "timeout",
    "unsupported_extension",
    "missing_firmware",
]


class BackendError(RuntimeError):
    pass


@dataclass(frozen=True)
class LaunchResult:
    status: BackendStatus
    ok: bool
    error: str | None = None
    state: dict[str, Any] | None = None


# Autoplay branching slots (must not collide with legacy pipeline slots 8/9).
AUTOPLAY_BRANCH_SLOT = 1
AUTOPLAY_SAFE_SLOT = 2
LEGACY_PIPELINE_SLOTS = frozenset({8, 9})


class RetroBackend(Protocol):
    system_id: str | None

    def launch(self, rom: Path) -> LaunchResult: ...
    def wait_ready(self, timeout: float | None = None) -> dict[str, Any]: ...
    def press(self, buttons: list[str], hold_frames: int, advance_frames: int, analog: dict[str, int] | None = None) -> dict[str, Any]: ...
    def advance(self, frames: int) -> dict[str, Any]: ...
    def screenshot(self) -> Path | None: ...
    def read_state(self) -> dict[str, Any] | None: ...
    def save_state(self, slot: int) -> dict[str, Any]: ...
    def load_state(self, slot: int) -> dict[str, Any]: ...
    def close(self) -> None: ...
