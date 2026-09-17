import argparse
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import urlopen
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cli = load('jev_cli_test', ROOT/'jev.py')
release = load('release_test', ROOT/'tools/check_release.py')


class PortabilityTests(unittest.TestCase):
    def test_viewer_serves_inherited_video_without_exposing_parent_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            run = root/'continued'
            run.mkdir()
            inherited = root/'previous.mp4'
            inherited.write_bytes(b'test-video-content')
            (run/'videos.json').write_text(json.dumps([{'file': str(inherited), 'url': '../previous.mp4'}]))
            with ThreadingHTTPServer(('127.0.0.1', 0), cli.viewer_handler(run)) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    base = f'http://127.0.0.1:{server.server_port}'
                    with urlopen(base+'/videos.json') as response:
                        videos = json.load(response)
                    self.assertEqual(videos[0]['url'], '/_history/0.mp4')
                    with urlopen(base+videos[0]['url']) as response:
                        self.assertEqual(response.read(), inherited.read_bytes())
                    from urllib.error import HTTPError
                    with self.assertRaises(HTTPError):
                        urlopen(base+'/previous.mp4')
                finally:
                    server.shutdown()
                    thread.join()

    def test_relative_assets_resolve_from_config_directory(self):
        with tempfile.TemporaryDirectory(prefix='jev paths ') as folder, patch.dict(os.environ, {}, clear=True):
            path = Path(folder)/'config.json'
            path.write_text(json.dumps({'rom': 'private/my game.nes', 'emulator': 'tools/EmuHawk.exe'}))
            cli.configure(argparse.Namespace(config=str(path), rom=None, emulator=None))
            self.assertEqual(Path(os.environ['JEV_ROM']), (Path(folder)/'private/my game.nes').resolve())
            self.assertEqual(Path(os.environ['JEV_BIZHAWK']), (Path(folder)/'tools/EmuHawk.exe').resolve())

    def test_cli_overrides_config_without_storing_secret(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            path = Path(folder)/'config.json'
            path.write_text(json.dumps({'rom': 'old.nes'}))
            rom = Path(folder)/'another game.nes'
            cli.configure(argparse.Namespace(config=str(path), rom=str(rom), emulator=None))
            self.assertEqual(Path(os.environ['JEV_ROM']), rom.resolve())

    def test_config_rejects_keys_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'config.json'
            path.write_text(json.dumps({'api_key': 'should-not-be-saved'}))
            with self.assertRaises(ValueError):
                cli.configure(argparse.Namespace(config=str(path), rom=None, emulator=None))

    def test_release_rejects_disguised_rom_executable_and_secret(self):
        with tempfile.TemporaryDirectory() as folder:
            for content in (b'NES'+bytes([26])+b'fake', b'MZfake', b'api'+b'key_'+b'a'*32):
                path = Path(folder)/'innocent.txt'
                path.write_bytes(content)
                self.assertTrue(release.inspect(path))

    def test_release_rejects_local_config_even_if_force_added(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'config.local.json'
            path.write_text('{}')
            self.assertTrue(release.inspect(path))

    def test_release_accepts_plain_source(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'hello.py'
            path.write_text("print('hello')\n")
            self.assertEqual(release.inspect(path), [])
