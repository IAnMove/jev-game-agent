import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
from campaign_media import media_timeout
import campaign_replay


class MediaTimeoutTests(unittest.TestCase):
    def test_full_campaign_has_a_larger_budget_than_short_chapters(self):
        self.assertEqual(media_timeout(20), 180)
        self.assertGreater(media_timeout(1598), 1598)
        self.assertLess(media_timeout(1598), media_timeout(3200))

    def test_video_failure_preserves_successful_replay_checks_and_returns_failure(self):
        initial = {'timeline_frame': 1}
        final = {'timeline_frame': 5}
        route = {'initial_state': 'unused.State', 'initial_ram': 'unused.json', 'moves': [{
            'segments': [{'buttons': [], 'frames': 4}], 'trajectory': [{'ram_sha256': 'digest'}],
            'before_ram_sha256': 'digest', 'after_ram_sha256': 'digest',
            'controller': 'Jev', 'action': 'wait'}]}
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)/'replay'
            backend = Mock(states_dir=Path(temp))
            backend.launch.return_value.ok = True
            backend.load_state_named.return_value = initial
            backend.advance.return_value = final
            backend.screenshot.return_value = None
            with patch('sys.argv', ['replay', '--out', str(out), '--legacy-runs', temp]), \
                 patch.object(campaign_replay, 'legacy_route', return_value=route), \
                 patch.object(campaign_replay, 'backend_at', return_value=backend), \
                 patch.object(campaign_replay.shutil, 'copyfile'), \
                 patch.object(campaign_replay, 'read', return_value=initial), \
                 patch.object(campaign_replay, 'fingerprint', return_value='digest'), \
                 patch.object(campaign_replay, 'values', return_value={'world': 0, 'level': 0}), \
                 patch.object(campaign_replay, 'finalize', side_effect=TimeoutError('encoding exceeded budget')):
                self.assertEqual(campaign_replay.main(), 2)
            result = json.loads((out/'result.json').read_text())
            self.assertEqual(result['status'], 'verified_deterministic_replay')
            self.assertEqual(result['checks'], 1)
            self.assertEqual(result['video_status'], 'failed')
            self.assertIn('TimeoutError', result['video_error'])
