import unittest
from unittest.mock import patch
from recovery import StallWatch
from campaign_model import make_request


class RecoveryTests(unittest.TestCase):
    def test_short_decisions_and_wall_clock_waits_do_not_trigger_restore(self):
        monitor = StallWatch(600)
        state = dict(area='1-1', x=300, feet_y=208)
        for _ in range(6):
            self.assertFalse(monitor.observe(state, state, 4))
        for _ in range(100):
            self.assertFalse(monitor.observe(state, state, 0))
        self.assertFalse(monitor.observe(state, state, 575))
        self.assertTrue(monitor.observe(state, state, 1))

    def test_moving_or_game_maze_loop_is_not_a_physical_stall(self):
        monitor = StallWatch(600)
        previous = dict(area='7-4', x=2000, feet_y=96)
        for i in range(80):
            current = dict(area='7-4', x=2000+(i%8)*64, feet_y=96)
            self.assertFalse(monitor.observe(previous, current, 32))
            previous = current

    def test_disable_and_reset(self):
        state = dict(area='1-1', x=20, feet_y=208)
        self.assertFalse(StallWatch(0).observe(state, state, 10000))
        monitor = StallWatch(600)
        monitor.observe(state, state, 599)
        monitor.reset()
        self.assertFalse(monitor.observe(state, state, 1))

    def test_risk_fallback_discloses_prediction_without_fabricating_safety(self):
        state = dict(area='7-4', x=1000, feet_y=208, maze_correct=0)
        forecasts = {'jump': {'segments': [{'buttons': ['a'], 'frames': 4}],
                            'outcome': {'eligible': False, 'failure': 'death_routine', 'end': state}}}
        with patch('campaign_model.describe', return_value=state):
            args = ({}, forecasts, {'banned': {'jump': {}}}, [], [], {}, [])
            self.assertEqual(make_request(*args)['questions']['maneuver']['criteria'], {})
            request = make_request(*args, allow_risky=True)
        self.assertIn('jump', request['questions']['maneuver']['criteria'])
        self.assertFalse(request['state']['candidate_outcomes']['jump']['eligible'])
        self.assertIn('actually execute', request['state']['risk_fallback'])
