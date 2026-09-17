"""Local asset locations. Importing modules never opens a ROM or starts an emulator."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / 'bridge'
ROM = Path(os.environ.get('JEV_ROM', str(ROOT/'roms/local-game.nes'))).expanduser().resolve()
EMULATOR = Path(os.environ.get('JEV_BIZHAWK', str(ROOT/'emulators/BizHawk/EmuHawk.exe'))).expanduser().resolve()
