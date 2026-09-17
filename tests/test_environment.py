import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from env_config import load_env
from start import docker_command
from runtime import engine_files
from test_portability import release


class EnvironmentTests(unittest.TestCase):
    def test_env_quotes_paths_relative_to_file_and_existing_environment_wins(self):
        with tempfile.TemporaryDirectory(prefix='jev paths ') as folder, patch.dict(os.environ, {'TYPESAFE_API_KEY': 'existing'}, clear=True):
            path = Path(folder)/'.env'
            path.write_text('TYPESAFE_API_KEY="file-value"\nJEV_ROM="my games/game.nes"\nJEV_BIZHAWK=tools/EmuHawkMono.sh\n', encoding='utf-8')
            load_env(path)
            self.assertEqual(os.environ['TYPESAFE_API_KEY'], 'existing')
            self.assertEqual(Path(os.environ['JEV_ROM']), (Path(folder)/'my games/game.nes').resolve())
            self.assertEqual(Path(os.environ['JEV_BIZHAWK']), (Path(folder)/'tools/EmuHawkMono.sh').resolve())

    def test_env_never_expands_shell_or_interpolates_secrets(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            path = Path(folder)/'.env'
            path.write_text('TYPESAFE_API_KEY=literal$VALUE`command`#part\n', encoding='utf-8')
            load_env(path)
            self.assertEqual(os.environ['TYPESAFE_API_KEY'], 'literal$VALUE`command`#part')

    def test_errors_do_not_include_secret_values(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'.env'
            for value in ('UNSUPPORTED=private-value', 'TYPESAFE_API_KEY="private-value'):
                path.write_text(value, encoding='utf-8')
                with self.assertRaises(ValueError) as error:
                    load_env(path)
                self.assertNotIn('private-value', str(error.exception))

    def test_release_only_accepts_empty_env_template(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'.env.example'
            path.write_text('TYPESAFE_API_KEY=\nJEV_ROM=\nJEV_BIZHAWK=\n')
            self.assertEqual(release.inspect(path), [])
            path.write_text('TYPESAFE_API_KEY=private-value\n')
            self.assertTrue(release.inspect(path))
            local = Path(folder)/'.env'
            local.write_text('TYPESAFE_API_KEY=\n')
            self.assertTrue(release.inspect(local))

    def test_platform_core_fingerprints_use_correct_shared_library(self):
        self.assertIn('libquicknes.dll', engine_files(Path('/emulator/EmuHawk.exe'), 'win32'))
        self.assertIn('libquicknes.so', engine_files(Path('/emulator/EmuHawkMono.sh'), 'linux'))

    def test_container_command_keeps_secrets_out_of_args_and_only_publishes_loopback(self):
        with patch.dict(os.environ, {'TYPESAFE_API_KEY': 'private-value'}):
            args = docker_command(Path('/games/my game.nes'), Path('/tools/Biz Hawk/EmuHawkMono.sh'), Path('/work/runs'), ['smoke'], 8771)
        self.assertNotIn('private-value', ' '.join(args))
        self.assertIn('127.0.0.1:8771:8771', args)
        self.assertIn(f'type=bind,source={Path("/games/my game.nes")},target=/game/game.nes,readonly', args)
        self.assertIn('linux/amd64', args)
