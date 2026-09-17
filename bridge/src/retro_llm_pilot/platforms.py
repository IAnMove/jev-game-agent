from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PLATFORM_BY_EXTENSION: dict[str, str] = {
    ".nes": "NES",
    ".sfc": "SNES",
    ".smc": "SNES",
    ".n64": "N64",
    ".z64": "N64",
    ".v64": "N64",
    ".gb": "GB",
    ".gbc": "GB",
    ".gba": "GBA",
    ".md": "GEN",
    ".gen": "GEN",
    ".bin": "GEN",
}

FIRMWARE_REQUIRED: set[str] = set()

# BizHawk's emu.getdisplaytype() distinguishes PAL from NTSC, while the
# platform table intentionally keeps one normal/default rate per system.
# PAL NES/SNES timing observed in native dumps is 322445/6448 Hz.
PAL_FPS_BY_PLATFORM: dict[str, float] = {
    "NES": 322445 / 6448,
    "SNES": 322445 / 6448,
    "N64": 50.0,
    "GEN": 50.0,
}


@dataclass(frozen=True)
class PlatformSupport:
    platform: str | None
    ok: bool
    reason: str | None = None
    source: str | None = None


def package_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_platform_buttons(path: Path | None = None) -> dict[str, Any]:
    data_path = path or package_root() / "data" / "platform_buttons.json"
    return json.loads(data_path.read_text(encoding="utf-8"))


def detect_platform(rom_path: str | Path) -> PlatformSupport:
    path = Path(rom_path)
    suffix = path.suffix.lower()
    if suffix == ".zip":
        return _detect_zip_platform(path)
    platform = PLATFORM_BY_EXTENSION.get(suffix)
    if platform is None:
        return PlatformSupport(platform=None, ok=False, reason="unsupported_extension", source=suffix)
    return PlatformSupport(platform=platform, ok=True, source=suffix)


def check_supported(rom_path: str | Path, firmware_dir: str | Path | None = None) -> PlatformSupport:
    detected = detect_platform(rom_path)
    if not detected.ok or detected.platform is None:
        return detected
    if detected.platform in FIRMWARE_REQUIRED and not _has_firmware(firmware_dir):
        return PlatformSupport(platform=detected.platform, ok=False, reason="missing_firmware", source=detected.source)
    return detected


def resolve_button(platform_or_system: str, button: str, mapping: dict[str, Any] | None = None) -> str:
    mapping = mapping or load_platform_buttons()
    normalized = button.strip().lower()
    platform_data = mapping.get(platform_or_system)
    if platform_data is None:
        for candidate, data in mapping.items():
            if platform_or_system in data.get("system_ids", []):
                platform_data = data
                break
    if platform_data is None:
        raise KeyError(f"Unsupported platform/system id: {platform_or_system}")
    aliases = platform_data.get("aliases", {})
    if normalized not in aliases:
        raise KeyError(f"Unsupported button {button!r} for {platform_or_system}")
    return str(aliases[normalized])


def platform_fps(
    platform_or_system: str,
    mapping: dict[str, Any] | None = None,
    *,
    display_type: str | None = None,
) -> float:
    mapping = mapping or load_platform_buttons()
    platform = platform_or_system if platform_or_system in mapping else None
    if platform is None:
        for candidate, data in mapping.items():
            if platform_or_system in data.get("system_ids", []):
                platform = candidate
                break
    normalized_display = str(display_type or "").strip().upper()
    if platform and (normalized_display.startswith("PAL") or normalized_display == "DENDY"):
        pal_fps = PAL_FPS_BY_PLATFORM.get(platform)
        if pal_fps is not None:
            return pal_fps
    if platform_or_system in mapping:
        return float(mapping[platform_or_system].get("fps", 60.0))
    for data in mapping.values():
        if platform_or_system in data.get("system_ids", []):
            return float(data.get("fps", 60.0))
    return 60.0


def _detect_zip_platform(path: Path) -> PlatformSupport:
    try:
        with zipfile.ZipFile(path) as archive:
            candidates = [name for name in archive.namelist() if not name.endswith("/")]
    except (OSError, zipfile.BadZipFile):
        return PlatformSupport(platform=None, ok=False, reason="invalid_zip", source=".zip")
    for name in candidates:
        platform = PLATFORM_BY_EXTENSION.get(Path(name).suffix.lower())
        if platform:
            return PlatformSupport(platform=platform, ok=True, source=name)
    return PlatformSupport(platform=None, ok=False, reason="unsupported_extension", source=".zip")


def _has_firmware(firmware_dir: str | Path | None) -> bool:
    if firmware_dir is None:
        return False
    path = Path(firmware_dir)
    return path.exists() and any(item.is_file() for item in path.iterdir())
