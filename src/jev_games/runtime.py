"""Local asset locations. Importing modules never opens a ROM or starts an emulator."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / 'bridge'
ROM = Path(os.environ.get('JEV_ROM', str(ROOT/'roms/local-game.nes'))).expanduser().resolve()
EMULATOR = Path(os.environ.get('JEV_BIZHAWK', str(ROOT/'emulators/BizHawk/EmuHawk.exe'))).expanduser().resolve()


def engine_files(emulator=None, system=None):
    directory = Path(emulator or EMULATOR).parent/'dll'
    suffix = '.dll' if (system or sys.platform) == 'win32' else '.so'
    return {name: directory/name for name in (
        'BizHawk.Emulation.Cores.dll', 'BizHawk.Emulation.Common.dll', 'libquicknes'+suffix)}
