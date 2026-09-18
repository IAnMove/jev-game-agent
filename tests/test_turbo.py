import unittest
from unittest.mock import patch
from turbo import options, request


class TurboTests(unittest.TestCase):
    def test_options_preserve_inputs_and_never_invent_predictions(self):
        plan = [{'buttons': ['right'], 'frames': 1}, {'buttons': ['right', 'a'], 'frames': 9}]
        with patch('turbo.recipes', return_value={'jump': plan}):
            choices = options({})
        result = choices['jump']
        self.assertEqual([s['frames'] for s in result['segments']], [1, 4, 4, 1])
        self.assertEqual(result['outcome']['controls'], plan)
        self.assertIsNone(result['trajectory'])
        self.assertFalse(result['outcome']['prediction_available'])
        self.assertNotIn('end', result['outcome'])
        self.assertNotIn('eligible', result['outcome'])

    def test_request_uses_geometry_and_excludes_equivalent_failed_inputs(self):
        current = {'area': '1-1', 'level': '1-1'}
        with patch('turbo.recipes', return_value={'jump': [{'buttons': ['a'], 'frames': 8}]}):
            choices = options({})
        choices['alias'] = choices['jump']
        with patch('turbo.describe', return_value=current), patch('turbo.observe', return_value={'objects': ['enemy']}), patch('turbo.pipes', return_value=[]):
            payload = request({}, choices, {'banned': {'jump': {}}}, [], [], {}, [], True)
            self.assertNotIn('jump', payload['questions']['maneuver']['criteria'])
            self.assertNotIn('alias', payload['questions']['maneuver']['criteria'])
            self.assertEqual(payload['state']['observation']['objects'], ['enemy'])
            self.assertIn('No emulator lookahead', payload['state']['method'])
            retry = request({}, choices, {'banned': {'jump': {}}}, [], [], {}, [], True, allow_risky=True)
            self.assertIn('jump', retry['questions']['maneuver']['criteria'])
            self.assertTrue(retry['state']['retry_exhausted_options'])
