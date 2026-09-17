import json
from pathlib import Path
import tempfile
import unittest
from live_view import LiveView


class LiveViewTests(unittest.TestCase):
    def test_keys_clear_during_search_and_events_stay_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            view = LiveView(folder)
            view.publish('executing', {'decisions': 1}, buttons=['right', 'a'], event='move')
            self.assertEqual(json.loads((Path(folder)/'live.json').read_text())['buttons'], ['right', 'a'])
            for i in range(70):
                view.publish('simulating', {'decisions': i}, event='search_started')
            live = json.loads((Path(folder)/'live.json').read_text())
            self.assertEqual(live['buttons'], [])
            self.assertEqual(len(live['events']), 60)
            self.assertEqual(live['events'][-1]['decision'], 69)

    def test_secret_is_redacted_in_live_state_and_event_log(self):
        with tempfile.TemporaryDirectory() as folder:
            view = LiveView(folder, 'sensitive-example-value')
            view.publish('ready', {}, event='error', reason='sensitive-example-value')
            for path in Path(folder).iterdir():
                self.assertNotIn('sensitive-example-value', path.read_text())
                self.assertIn('[REDACTED]', path.read_text())

    def test_image_and_frame_are_published_together(self):
        with tempfile.TemporaryDirectory() as folder:
            from PIL import Image
            image = Path(folder)/'screen.png'
            Image.new('RGB', (8, 8), 'red').save(image)
            view = LiveView(folder)
            view.publish('executing', {}, screenshot=image, timeline_frame=44, buttons=['b'])
            live = json.loads((Path(folder)/'live.json').read_text())
            self.assertEqual(live['image_frame'], 44)
            self.assertTrue(live['image'].startswith('data:image/png;base64,'))
            view.publish('waiting_for_jev', {})
            held = json.loads((Path(folder)/'live.json').read_text())
            self.assertEqual(held['image'], live['image'])
            self.assertEqual(held['buttons'], [])
