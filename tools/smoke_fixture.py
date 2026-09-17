"""Generate an original minimal NES test program in ignored runs/, never a game.

This tests the Linux bridge/recording in CI without proprietary data or API calls.
The 6502 program disables interrupts/rendering and loops forever; no SMB content.
"""
from pathlib import Path


def make_fixture(path):
    header = b'NES\x1a'+bytes([1, 1, 0, 0, 0, 1])+bytes(6)  # PAL iNES
    program = bytearray([0xea])*16384
    code = bytes.fromhex('78 d8 a2 ff 9a e8 8e 00 20 8e 01 20 4c 0c 80')
    program[:len(code)] = code
    program[-6:] = bytes.fromhex('00 80 00 80 00 80')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header+program+bytes(8192))


if __name__ == '__main__':
    make_fixture(Path(__file__).resolve().parents[1]/'runs/ci-fixture.nes')
