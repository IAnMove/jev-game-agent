import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('launcher_test', ROOT/'start.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def test_default_fast_and_explicit_modes_reach_native_player(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('game.nes', 'emulator'):
                (root/name).touch()
            python = root/('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python')
            python.parent.mkdir(parents=True)
            python.touch()
            environment = {'JEV_ROM':str(root/'game.nes'), 'JEV_BIZHAWK':str(root/'emulator'),
                           'TYPESAFE_API_KEY':'test-placeholder'}
            for mode, expected in (([], '--fast'), (['--fast'], '--fast'),
                                   (['--full'], '--full'), (['--turbo'], '--turbo')):
                with self.subTest(mode=mode), patch.object(launcher,'ROOT',root), \
                     patch.object(launcher,'load_env'), patch.dict(os.environ,environment), \
                     patch.object(sys,'platform','win32' if os.name == 'nt' else 'linux'), \
                     patch.object(sys,'argv',['start.py','--port','8771',*mode]), \
                     patch.object(launcher.subprocess,'run'), \
                     patch.object(launcher.subprocess,'call',return_value=0) as call:
                    self.assertEqual(launcher.main(),0)
                    command = call.call_args.args[0]
                    self.assertIn(expected,command)
                    self.assertEqual(sum(flag in command for flag in ('--fast','--full','--turbo')),1)
                    self.assertEqual(command[command.index('--port')+1],'8771')

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell launcher')
    def test_powershell_does_not_bind_gnu_fast_as_rom(self):
        shell = shutil.which('pwsh') or shutil.which('powershell')
        for arguments, expected in [
            ('--fast -Port 8771',['--fast','--port',8771]),
            ('-Port 8771',['--port',8771]),
            ('-Full -Rom "test folder/game.nes"',['--full','--rom','test folder/game.nes']),
            ('-Turbo',['--turbo'])]:
            with self.subTest(arguments=arguments):
                script = 'function py { $args | ConvertTo-Json -Compress; $global:LASTEXITCODE=0 }; & ./start.ps1 '+arguments
                result = subprocess.run([shell,'-NoProfile','-NonInteractive','-Command',script],
                                        cwd=ROOT,check=True,capture_output=True,text=True)
                self.assertEqual(json.loads(result.stdout),['-3','start.py',*expected])
