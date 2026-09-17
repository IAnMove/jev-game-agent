import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from manual_control import ManualControl, ManualRequested, validate_command
from run import write
from jev import viewer_handler


class ManualControlTests(unittest.TestCase):
    def test_lost_browser_releases_buttons_without_resuming_jev(self):
        with tempfile.TemporaryDirectory() as tmp:
            control = ManualControl(tmp)
            with patch('manual_control.time.time', return_value=100):
                write(control.path, validate_command({'mode': 'human', 'buttons': ['a', 'right']}))
                self.assertEqual(control.read(), ('human', ['a', 'right'], True))
                self.assertRaises(ManualRequested, control.check)
            with patch('manual_control.time.time', return_value=102):
                self.assertEqual(control.read(), ('human', [], False))
            write(control.path, validate_command({'mode': 'jev', 'buttons': ['a']}))
            self.assertEqual(control.read()[0:2], ('jev', []))

    def test_only_legal_controller_input_is_accepted(self):
        for command in ({'mode': 'human', 'buttons': ['quit']}, {'mode': 'human', 'buttons': 'a'},
                        {'mode': 'human', 'buttons': [['a']]}, {'mode': 'shell', 'buttons': []},
                        {'mode': 'human', 'buttons': [], 'path': '../control.json'}):
            with self.assertRaises(ValueError):
                validate_command(command)

    def test_endpoint_rejects_cross_origin_and_stopped_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            write(folder/'live.json', {'phase': 'ready'})
            server = ThreadingHTTPServer(('127.0.0.1', 0), viewer_handler(folder))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            def post(origin):
                return urlopen(Request(base+'/control', data=b'{"mode":"human","buttons":["right"]}',
                                      headers={'Content-Type': 'application/json', 'Origin': origin}), timeout=3)
            try:
                with self.assertRaises(HTTPError) as error:
                    post('https://another-site.example')
                self.assertEqual(error.exception.code, 403)
                self.assertFalse((folder/'control.json').exists())
                with post(base) as response:
                    self.assertEqual(response.status, 204)
                self.assertEqual(ManualControl(folder).read()[1], ['right'])
                write(folder/'live.json', {'phase': 'stopped'})
                with self.assertRaises(HTTPError) as error:
                    post(base)
                self.assertEqual(error.exception.code, 409)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
