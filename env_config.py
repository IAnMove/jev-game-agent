"""Small literal .env reader, shared with the dependency-free launcher.

No interpolation, shell evaluation or escape decoding; Windows paths stay intact.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KEYS = {'TYPESAFE_API_KEY', 'JEV_ROM', 'JEV_BIZHAWK'}


def load_env(path=None):
    path = Path(path) if path else ROOT/'.env'
    if not path.exists():
        return
    for number, raw in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        name, sep, value = line.partition('=')
        name, value = name.strip(), value.strip()
        if not sep or name not in KEYS:
            raise ValueError(f'Invalid .env setting at line {number}; use .env.example')
        if value[:1] in ('"', "'"):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f'Unclosed .env quote at line {number}')
            value = value[1:-1]
        if name in ('JEV_ROM', 'JEV_BIZHAWK') and value:
            value = str((path.resolve().parent/Path(value).expanduser()).resolve())
        if value:
            os.environ.setdefault(name, value)
