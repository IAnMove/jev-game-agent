import unittest
from unittest.mock import patch
from gap_jump import visible_gap, GapJump, has_useful_option


def obs(x=100, grounded=True, vx=0):
    return {'mario': {'x': x, 'feet_y': 208, 'grounded': grounded, 'vx_raw': vx},
            'solid_rectangles_xyxy': [[16,208,128,224], [224,208,320,224]]}


class GapJumpTests(unittest.TestCase):
    def test_geometry_moves_with_the_world_and_rejects_airborne(self):
        for offset in (0, 700):
            o = obs(100+offset)
            o['solid_rectangles_xyxy'] = [[a+offset,b,c+offset,d] for a,b,c,d in o['solid_rectangles_xyxy']]
            with patch('gap_jump.observe', return_value=o):
                gap = visible_gap({})
            self.assertEqual(gap['width'],96)
            self.assertEqual(gap['edge_x'],128+offset)
        with patch('gap_jump.observe', return_value=obs(grounded=False)):
            self.assertIsNone(visible_gap({}))
        o = obs()
        o['solid_rectangles_xyxy'] = [[16,208,320,224]]
        with patch('gap_jump.observe', return_value=o):
            self.assertIsNone(visible_gap({}))

    def test_complete_runup_releases_jump_until_the_edge(self):
        skill = GapJump({'runway_left':16, 'edge_x':128},48)
        for x, elapsed, buttons in [(100,0,['left','b']), (80,12,['right','b']),
                                     (116,30,['right','b']), (120,32,['right','b','a']),
                                     (230,68,['right','b'])]:
            with patch('gap_jump.observe', return_value=obs(x,vx=48)):
                result = skill.controls({},elapsed)
            self.assertEqual(result['buttons'],buttons)
            if x == 116:
                self.assertEqual(result['frames'],1)
        self.assertEqual(skill.launch_speed,3)

    def test_brakes_and_waits_do_not_prevent_search_expansion(self):
        forecasts = {name: {'outcome': outcome} for name,outcome in {
            'brake': {'progress_px':-6}, 'wait':{'progress_px':0},
            'cross': {'progress_px':120}, 'pipe':{'pipe_entry_started':True},
            'up': {'end':{'feet_y':176}}}.items()}
        with patch('gap_jump.observe', return_value=obs()):
            self.assertFalse(has_useful_option({},forecasts,['brake','wait']))
            for name in ('cross','pipe','up'):
                self.assertTrue(has_useful_option({},forecasts,[name]))

    def test_landing_brake_only_starts_after_clearing_the_gap(self):
        skill = GapJump({'runway_left':16, 'edge_x':128, 'landing_x':224},0,brake_landing=True)
        with patch('gap_jump.observe', return_value=obs(120,vx=48)):
            skill.controls({},0)
        with patch('gap_jump.observe', return_value=obs(210,grounded=False,vx=48)):
            self.assertEqual(skill.controls({},36)['buttons'],['right','b'])
        with patch('gap_jump.observe', return_value=obs(218,grounded=False,vx=48)):
            self.assertEqual(skill.controls({},40)['buttons'],['left'])
        with patch('gap_jump.observe', return_value=obs(250,grounded=False,vx=4)):
            self.assertEqual(skill.controls({},44)['buttons'],[])
