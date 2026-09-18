import unittest
from run import WATCH, observe


class HiddenBlockTests(unittest.TestCase):
    def test_hidden_coin_and_oneup_are_not_landing_surfaces_until_revealed(self):
        state = {'frame': 0, 'ram': {key: {'value': 0} for key in WATCH}}
        for index, value in ((0, 0x5f), (1, 0x60), (2, 0xc4)):
            state['ram'][f'tile{index}']['value'] = value
        observation = observe(state)
        self.assertEqual(observation['solid_rectangles_xyxy'], [[32, 32, 48, 48]])
        self.assertEqual([b['kind'] for b in observation['hidden_blocks_from_ram']],
                         ['hidden_coin', 'hidden_1up'])
        state['ram']['tile0']['value'] = 0xc4
        observation = observe(state)
        self.assertIn([0, 32, 16, 48], observation['solid_rectangles_xyxy'])
        self.assertEqual(len(observation['hidden_blocks_from_ram']), 1)
