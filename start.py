"""Portable launcher. Native Windows/Linux; Linux container on macOS."""
import argparse
from datetime import datetime
import getpass
import os
from pathlib import Path
import shutil
import subprocess
import sys
import webbrowser

from env_config import ROOT, load_env


def docker_command(rom, emulator, runs, command, port):
    return ['docker', 'run', '--rm', '--init', '--platform', 'linux/amd64',
            '-p', f'127.0.0.1:{port}:{port}',
            '--env', 'TYPESAFE_API_KEY', '--env', 'JEV_CONTAINER=1',
            '--env', 'JEV_ROM=/game/game.nes', '--env', 'JEV_BIZHAWK=/emulator/EmuHawkMono.sh',
            '--mount', f'type=bind,source={rom},target=/game/game.nes,readonly',
            '--mount', f'type=bind,source={emulator.parent},target=/emulator',
            '--mount', f'type=bind,source={runs},target=/app/runs',
            'jev-game-agent:local', *command]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container', action='store_true', help='Run the Linux x64 emulator through Docker')
    parser.add_argument('--fast', action='store_true', help='Reduce initial lookahead work; may choose different moves')
    parser.add_argument('--smoke', action='store_true', help='Check real emulator and recording without API calls')
    parser.add_argument('--port', type=int, default=8768)
    parser.add_argument('--out', help='New folder name within runs/')
    parser.add_argument('--rom')
    parser.add_argument('--emulator')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Port must be 1..65535')
    load_env()
    container = args.container or sys.platform == 'darwin'
    for override, variable, prompt in (
        (args.rom, 'JEV_ROM', 'Path to your local Super Mario Bros. PAL .nes'),
        (args.emulator, 'JEV_BIZHAWK', 'Path to EmuHawkMono.sh (Linux build)' if container or os.name != 'nt' else 'Path to EmuHawk.exe')):
        value = override or os.environ.get(variable) or input(prompt+': ').strip()
        if not value:
            parser.error('Fill in .env or provide both asset paths')
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            parser.error(f'Missing {variable} file; check .env')
        os.environ[variable] = str(path)
    if not args.smoke and not os.environ.get('TYPESAFE_API_KEY'):
        os.environ['TYPESAFE_API_KEY'] = getpass.getpass('TypeSafe API key (hidden; not saved): ')
        if not os.environ['TYPESAFE_API_KEY']:
            parser.error('An API key is required to play')
    name = args.out or ('smoke-' if args.smoke else 'play-')+datetime.now().strftime('%Y%m%d-%H%M%S')
    if name in ('.', '..') or '/' in name or '\\' in name or ':' in name:
        parser.error('--out is a new folder name inside runs/, not a path')
    runs = ROOT/'runs'
    runs.mkdir(exist_ok=True)
    if (runs/name).exists():
        parser.error('Output directory must be new')
    command = ['smoke' if args.smoke else 'play', '--out', f'runs/{name}']
    if not args.smoke:
        if args.fast:
            command += ['--fast']
        command += ['--watch', '--port', str(args.port), '--allow-warps', '--wall-seconds', '600',
                    '--max-decisions', '100', '--max-rewinds', '100', '--max-input-tokens', '500000']
    if container:
        emulator = Path(os.environ['JEV_BIZHAWK'])
        if emulator.name != 'EmuHawkMono.sh':
            parser.error('Docker requires the complete Linux x64 BizHawk build; set JEV_BIZHAWK to its EmuHawkMono.sh')
        if not shutil.which('docker'):
            parser.error('Install/start Docker Desktop (or Docker Engine on Linux) first')
        subprocess.run(['docker', 'info'], stdout=subprocess.DEVNULL, check=True)
        subprocess.run(['docker', 'build', '--platform', 'linux/amd64', '-t', 'jev-game-agent:local', '.'], cwd=ROOT, check=True)
        if not args.smoke:
            command += ['--no-open']
            url = f'http://127.0.0.1:{args.port}/watch.html'
            print('Dashboard (available after startup): '+url, flush=True)
            webbrowser.open(url)
        return subprocess.call(docker_command(Path(os.environ['JEV_ROM']), emulator, runs, command, args.port), cwd=ROOT)
    python = ROOT/('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python')
    if not python.exists():
        subprocess.run([sys.executable, '-m', 'venv', str(ROOT/'.venv')], check=True)
    subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(ROOT/'requirements.txt')], check=True)
    return subprocess.call([str(python), str(ROOT/'jev.py'), *command], cwd=ROOT)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        message = str(exc)
        key = os.environ.get('TYPESAFE_API_KEY')
        print(message.replace(key, '[REDACTED]') if key else message, file=sys.stderr)
        raise SystemExit(1)
