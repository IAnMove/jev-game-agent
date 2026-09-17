"""Run from a source checkout: python jev.py --help."""
import argparse
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import threading
import webbrowser
import platform
from env_config import load_env
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT/'src/jev_games'))


def viewer_handler(directory):
    import time
    from manual_control import validate_command
    from run import write
    lock = threading.Lock()
    class RunHandler(SimpleHTTPRequestHandler):
        def do_POST(self):
            # JSON and exact loopback origin prevent cross-site form requests
            # and DNS rebinding. Never enable CORS for this control endpoint.
            host = self.headers.get('Host', '')
            origin = self.headers.get('Origin', '')
            if (urlsplit(self.path).path != '/control' or
                    urlsplit('http://'+host).hostname not in ('127.0.0.1', 'localhost', '::1') or
                    origin != 'http://'+host or
                    self.headers.get_content_type() != 'application/json'):
                self.send_error(403, 'Local same-origin JSON only')
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 1024:
                    raise ValueError('Invalid body size')
                command = validate_command(json.loads(self.rfile.read(size)))
                live = json.loads((directory/'live.json').read_text(encoding='utf-8'))
                if live['phase'] == 'stopped' or time.time()-(directory/'live.json').stat().st_mtime > 120:
                    self.send_error(409, 'Runner is stopped or unavailable')
                    return
                with lock:
                    write(directory/'control.json', command)
                self.send_response(204)
                self.end_headers()
            except (OSError, ValueError, KeyError, TypeError):
                self.send_error(400, 'Invalid control request or run unavailable')

        def log_message(self, format, *args):
            if len(args) > 1 and str(args[1]) == '200':
                return
            super().log_message(format, *args)

        def do_GET(self):
            route = urlsplit(self.path).path
            if route in ('/', '/watch.html') and not (directory/'watch.html').exists():
                data = (ROOT/'src/jev_games/campaign_watch.html').read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if route == '/videos.json' or route.startswith('/_history/'):
                try:
                    videos = json.loads((directory/'videos.json').read_text(encoding='utf-8'))
                    if route == '/videos.json':
                        for index, video in enumerate(videos):
                            path = Path(video['file']).resolve()
                            if not path.is_relative_to(directory):
                                video['url'] = f'/_history/{index}.mp4'
                        data = json.dumps(videos).encode()
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.send_header('Content-Length', str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                        return
                    index = int(route.removeprefix('/_history/').removesuffix('.mp4'))
                    if index < 0:
                        raise ValueError('Invalid video index')
                    path = Path(videos[index]['file']).resolve()
                    if path.suffix.lower() != '.mp4' or not path.is_file():
                        raise ValueError('Missing recorded video')
                    self.send_response(200)
                    self.send_header('Content-Type', 'video/mp4')
                    self.send_header('Content-Length', str(path.stat().st_size))
                    self.end_headers()
                    with path.open('rb') as stream:
                        shutil.copyfileobj(stream, self.wfile)
                    return
                except (OSError, ValueError, IndexError, KeyError):
                    self.send_error(404, 'Recorded video unavailable')
                    return
            super().do_GET()
    return functools.partial(RunHandler, directory=str(directory))


def configure(args):
    load_env()
    settings = {}
    if args.config:
        config_path = Path(args.config).resolve()
        settings = json.loads(config_path.read_text(encoding='utf-8-sig'))
        if set(settings) - {'rom', 'emulator'}:
            raise ValueError('Config accepts only rom and emulator paths; keep API keys in the environment.')
        settings = {key: str((config_path.parent/Path(value)).resolve()) for key, value in settings.items()}
    for argument, variable in (('rom', 'JEV_ROM'), ('emulator', 'JEV_BIZHAWK')):
        value = getattr(args, argument) or settings.get(argument) or os.environ.get(variable)
        if value:
            os.environ[variable] = str(Path(value).expanduser().resolve())


def checks(require_key=False):
    from runtime import ROM, EMULATOR, ENGINE, engine_files
    results = []
    def check(label, ok):
        results.append(bool(ok))
        print(f"{'OK' if ok else 'MISSING/INVALID'}: {label}")
    check('Windows or Linux x86_64 runtime (macOS: use python3 start.py --container)',
          sys.platform in ('win32', 'linux') and platform.machine().lower() in ('amd64', 'x86_64'))
    check('Python 3.11+', sys.version_info >= (3, 11))
    try:
        import httpx
        import PIL
        check(f'Python dependencies: httpx {httpx.__version__}, Pillow {PIL.__version__}', True)
    except ImportError:
        check('Python dependencies; run python -m pip install -r requirements.txt', False)
    expected_launcher = 'EmuHawk.exe' if os.name == 'nt' else 'EmuHawkMono.sh'
    check(f'{expected_launcher} path (BizHawk 2.11.1 expected)', EMULATOR.is_file() and EMULATOR.name == expected_launcher)
    for name, path in engine_files().items():
        check(f'Emulator dependency {name}', path.is_file())
    if sys.platform == 'linux':
        for binary in ('mono', 'sh', 'lsb_release'):
            check(binary+' on PATH', shutil.which(binary))
        check('X display (desktop or xvfb-run)', bool(os.environ.get('DISPLAY')))
    header = b''
    if ROM.is_file():
        with ROM.open('rb') as stream:
            header = stream.read(16)
    check('Local iNES file (the adapter expects Super Mario Bros. PAL)', ROM.suffix.lower() == '.nes' and header[:4] == b'NES\x1a')
    for path in ('scripts/bizhawk_bridge.lua', 'data/bizhawk_search_config_template.ini', 'data/platform_buttons.json'):
        check(f'Bridge resource {path}', (ENGINE/path).is_file())
    for binary in ('ffmpeg', 'ffprobe'):
        check(binary+' on PATH', shutil.which(binary))
    if shutil.which('ffmpeg'):
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        filters = subprocess.run(['ffmpeg', '-hide_banner', '-filters'], capture_output=True, text=True, creationflags=flags)
        check('FFmpeg ASS subtitle filter', filters.returncode == 0 and ' ass ' in filters.stdout)
    if require_key:
        check('TYPESAFE_API_KEY in environment (value never displayed)', bool(os.environ.get('TYPESAFE_API_KEY')))
    return all(results)


def smoke(output):
    """Exercise the real bridge and recording with zero API calls."""
    from runtime import ROM
    from campaign import campaign_backend
    from campaign_model import configure as configure_ram
    from campaign_media import finalize
    from lookahead import fingerprint
    from run import write
    configure_ram()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    backend = campaign_backend(output, record=True)
    result = {'status': 'starting', 'api_calls': 0}
    start = end = 0
    try:
        launched = backend.launch(ROM)
        if not launched.ok:
            raise RuntimeError(launched.error)
        origin = backend.read_state()
        start = origin['timeline_frame']
        backend.save_state_named('smoke')
        first = backend.press(['start'], 1, 1)
        first = backend.advance(19)
        restored = backend.load_state_named('smoke', neutralize_framebuffer=False)
        if fingerprint(restored) != fingerprint(origin):
            raise RuntimeError('Checkpoint restore RAM mismatch')
        repeated = backend.press(['start'], 1, 1)
        repeated = backend.advance(19)
        if fingerprint(repeated) != fingerprint(first):
            raise RuntimeError('Repeated inputs produced different RAM')
        end = repeated['timeline_frame']
        write(output/'final.json', repeated)
        result.update(status='verified_smoke', restore_verified=True, repeated_inputs_verified=True, frames=end-start)
    finally:
        backend.close()
    events = [{'timeline_start': start, 'timeline_end': end, 'controller': 'Smoke test (no Jev)',
               'action': 'start, restore, repeat', 'frames': end-start, 'rewinds': 1}]
    result['video'] = finalize(output, events, start, end)
    write(output/'result.json', result)
    print(json.dumps(result, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description='Jev + RAM + emulator lookahead. Current game adapter: SMB PAL.')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('doctor', 'smoke', 'play', 'replay'):
        p = sub.add_parser(name)
        p.add_argument('--config', help='Local JSON with rom and emulator paths; no API key')
        p.add_argument('--rom', help='Path to your own .nes ROM; never copied into the repo')
        p.add_argument('--emulator', help='Path to EmuHawk.exe (Windows) or EmuHawkMono.sh (Linux)')
        if name == 'smoke':
            p.add_argument('--out', type=Path, required=True)
        if name == 'play':
            p.add_argument('--out', type=Path, required=True)
            p.add_argument('--watch', action='store_true', help='Start the local dashboard and keep it open after play stops')
            p.add_argument('--port', type=int, default=8768)
            p.add_argument('--no-open', action='store_true', help='Print the viewer URL without opening a browser')
    sub.add_parser('test', help='Unit tests without ROM, emulator or API')
    p = sub.add_parser('watch', help='Serve one local run on localhost only')
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--port', type=int, default=8768)
    args, rest = parser.parse_known_args()
    if args.command not in ('play', 'replay') and rest:
        parser.error('Unknown arguments: '+' '.join(rest))
    if args.command == 'test':
        suite = unittest.defaultTestLoader.discover(str(ROOT/'tests'))
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    if args.command == 'watch':
        directory = args.run.resolve()
        if not (directory/'watch.html').is_file():
            parser.error('Run has no watch.html')
        handler = viewer_handler(directory)
        print(f'Viewer: http://127.0.0.1:{args.port}/watch.html', flush=True)
        with ThreadingHTTPServer(('127.0.0.1', args.port), handler) as server:
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
        return 0
    configure(args)
    if not checks(require_key=args.command == 'play'):
        return 1
    if args.command == 'doctor':
        print('Static checks passed. Run smoke to verify the emulator, RAM and recording.')
        return 0
    if args.command == 'smoke':
        return smoke(args.out)
    sys.argv = [args.command]+rest
    if args.command == 'play':
        sys.argv.extend(['--out', str(args.out)])
        from campaign import main as run
    else:
        from campaign_replay import main as run
    if args.command != 'play' or not args.watch:
        return run()
    if args.out.exists():
        parser.error('Output directory must be new')
    # Docker publishes this port exclusively on host loopback; native stays loopback.
    bind = '0.0.0.0' if os.environ.get('JEV_CONTAINER') == '1' else '127.0.0.1'
    with ThreadingHTTPServer((bind, args.port), viewer_handler(args.out.resolve())) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{args.port}/watch.html'
        print('Live dashboard: '+url, flush=True)
        if not args.no_open:
            webbrowser.open(url)
        try:
            result = run()
            print('Play stopped. Dashboard remains available; press Ctrl+C to close it.', flush=True)
            thread.join()
            return result
        except KeyboardInterrupt:
            return 130
        finally:
            server.shutdown()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, RuntimeError) as exc:
        message = str(exc)
        key = os.environ.get('TYPESAFE_API_KEY')
        print('Error: '+(message.replace(key, '[REDACTED]') if key else message), file=sys.stderr)
        raise SystemExit(1)
