"""Inspect the proposed Git source package, never print matching secret values."""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {'.py', '.md', '.txt', '.json', '.lua', '.ini', '.ps1', '.sh', '.html', '.yml', '.yaml'}
SPECIAL = {'.gitignore', '.gitattributes', '.dockerignore', '.env.example', 'Dockerfile', 'LICENSE'}
SECRET = re.compile(rb'(?:apikey_[A-Za-z0-9_]{24,}|gh[pousr]_[A-Za-z0-9]{24,}|github_pat_[A-Za-z0-9_]{24,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')


def inspect(path):
    problems = []
    if path.is_symlink():
        return ['symbolic link is not allowed in the source release']
    if path.name not in SPECIAL and path.suffix.lower() not in ALLOWED:
        problems.append('file type not in source allowlist')
    if (path.name.startswith('.env') and path.name != '.env.example') or path.name == 'config.local.json':
        problems.append('local settings or environment file')
    data = path.read_bytes()
    if path.name == '.env.example':
        for line in data.decode('utf-8').splitlines():
            if line.strip() and not line.lstrip().startswith('#'):
                key, sep, value = line.partition('=')
                if not sep or key not in {'TYPESAFE_API_KEY', 'JEV_ROM', 'JEV_BIZHAWK'} or value.strip():
                    problems.append('environment template must contain only empty settings')
    if len(data) > 2_000_000:
        problems.append('unexpected large source file')
    if data[:4] in (b'NES\x1a', b'PK\x03\x04', b'\x7fELF') or data[:2] == b'MZ':
        problems.append('ROM, archive or executable magic')
    if b'\x00' in data:
        problems.append('binary content')
    if SECRET.search(data):
        problems.append('possible credential (value withheld)')
    if re.search(rb'[A-Za-z]:[\\/]Users[\\/]', data):
        problems.append('personal absolute user path')
    return problems


def main():
    names = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT).decode().split('\0')
    failures = []
    for name in sorted(set(filter(None, names))):
        path = ROOT/name
        if not path.is_file():
            failures.append((name, ['tracked file missing']))
            continue
        problems = inspect(path)
        if problems:
            failures.append((name, problems))
    for name, problems in failures:
        print(name+': '+', '.join(problems))
    count = len(set(filter(None, names)))
    print(f'Checked {count} tracked/unignored files; {len(failures)} rejected. Ignored local runs/assets are excluded.')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
