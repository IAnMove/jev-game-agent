import unittest
from unittest.mock import patch
from platform_skills import targets, PlatformSkill, revealed_blocks, landed_on


def observation(x=100, feet=208, vx=0, grounded=True):
    return {'mario': {'x': x, 'feet_y': feet, 'vx_raw': vx, 'grounded': grounded},
            'visible_x': [0, 255], 'hidden_blocks_from_ram': [], 'solid_rectangles_xyxy': []}


class PlatformTests(unittest.TestCase):
    def test_hidden_block_is_revealed_before_it_is_offered_as_a_step(self):
        obs = observation()
        rect = [144, 144, 160, 160]
        obs['hidden_blocks_from_ram'] = [{'rect': rect}]
        with patch('platform_skills.observe', return_value=obs):
            self.assertEqual(list(targets({})), ['reveal_block_144_144'])
        obs['hidden_blocks_from_ram'] = []
        obs['solid_rectangles_xyxy'] = [rect, [144, 160, 160, 176]]
        with patch('platform_skills.observe', return_value=obs):
            choices = targets({})
        self.assertIn('land_on_144_144_from_left', choices)
        self.assertFalse(any('144_160' in name for name in choices))

    def test_reveal_aligns_and_cancels_inertia_before_jump(self):
        skill = PlatformSkill({'kind': 'reveal', 'rect': [144,144,160,160]})
        for elapsed, x, vx in [(0,100,0),(10,144,24)]:
            with patch('platform_skills.observe', return_value=observation(x,vx=vx)):
                self.assertNotIn('a', skill.controls({},elapsed)['buttons'])
        with patch('platform_skills.observe', return_value=observation(144)):
            self.assertIn('a', skill.controls({},20)['buttons'])

    def test_tall_step_uses_runup_while_direct_hop_releases_jump(self):
        skill = PlatformSkill({'kind':'land','rect':[144,144,160,160],
                              'staging_x':80,'launch_x':104,'side':'left'})
        with patch('platform_skills.observe', return_value=observation(80)):
            self.assertNotIn('a',skill.controls({},0)['buttons'])
            self.assertEqual(skill.controls({},1)['buttons'],['right','b'])
        with patch('platform_skills.observe', return_value=observation(104,vx=24)):
            self.assertIn('a',skill.controls({},20)['buttons'])
        skill = PlatformSkill({'kind':'land','rect':[144,96,176,112],
                              'staging_x':112,'direct':True})
        with patch('platform_skills.observe', return_value=observation(112,feet=144)):
            self.assertNotIn('a',skill.controls({},0)['buttons'])
            self.assertIn('a',skill.controls({},1)['buttons'])

    def test_edge_landing_requires_actual_grounded_support_at_the_right_height(self):
        target={'rect':[144,144,160,160]}
        with patch('platform_skills.observe', return_value=observation(133,feet=144)):
            self.assertTrue(landed_on({},target))
        for obs in [observation(133,feet=146),observation(128,feet=144),observation(133,feet=144,grounded=False)]:
            with patch('platform_skills.observe',return_value=obs):
                self.assertFalse(landed_on({},target))

    def test_camera_scroll_is_not_reported_as_block_activation(self):
        before=observation();before['hidden_blocks_from_ram']=[{'rect':[32,144,48,160]}]
        after=observation()
        with patch('platform_skills.observe',side_effect=[after,before]):
            self.assertEqual(revealed_blocks({},{}),[[32,144,48,160]])
        after['visible_x']=[64,319]
        with patch('platform_skills.observe',side_effect=[after,before]):
            self.assertEqual(revealed_blocks({},{}),[])
