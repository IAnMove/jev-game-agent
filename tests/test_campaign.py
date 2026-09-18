import unittest
from unittest.mock import Mock, patch

from campaign_model import death_reason, won, playable, make_request, pipes
from run import WATCH
from campaign_model import guide_context, novelty_cell
from pathlib import Path
import json
from campaign import Campaign
from campaign_media import ass_time


def state(**updates):
    return dict(mode=1, mode_task=3, routine=8, world=0, level=0,
                lives=2, yhigh=1, death_music=0, cloud_override=0,
                msg_primary=0, world_end_timer=0) | updates


class CampaignTests(unittest.TestCase):
    def test_repeated_death_skips_airborne_area_change_to_grounded_takeoff(self):
        c = Campaign.__new__(Campaign)
        c.state, c.summary, c.failures, c.event = {}, {'decisions': 1}, [], Mock()
        def node(i, parent, x, area, phase, via='jump'):
            return {'id': i, 'parent': parent, 'via': via, 'banned': {},
                    'state': {'level': '8-4', 'x': x, 'area': area, 'phase': phase}}
        ground = node('ground', None, 1020, 'old', 'grounded')
        apex = node('apex', 'ground', 1143, 'old', 'apex')
        child = node('child', 'apex', 1114, 'new', 'descending', 'brake')
        c.nodes = {n['id']: n for n in (ground, apex, child)}
        segments = [{'buttons': ['left'], 'frames': 4}]
        with patch('campaign.describe', return_value={'timer': 200}), patch('campaign.values', return_value={}):
            self.assertEqual(c.death_checkpoint(child, 'retreat', 'death_music_flag', segments)[0], child)
            target, reason = c.death_checkpoint(child, 'retreat', 'death_music_flag', segments)
        self.assertEqual(target, ground)
        self.assertIn('jump', ground['banned'])
        self.assertEqual(reason, 'repeated_actual_death_replan_earlier')

    def test_walkthrough_can_distinguish_rooms_with_the_same_x(self):
        guide = {'id':'pipes', 'sources':[], 'levels':{'8-4':{'phases':[
            {'area':'first','goal':'first pipe'}, {'area':'second','goal':'floating pipe'},
            {'swimming':True,'goal':'underwater exit'}]}}}
        for area, swim, goal in [('first',False,'first pipe'),('second',False,'floating pipe'),('water',True,'underwater exit')]:
            current = {'level':'8-4','x':100,'feet_y':160,'area':area,'swimming':swim}
            self.assertEqual(guide_context(current,guide)['current_navigation_goal']['goal'],goal)

    def test_actual_repeated_death_moves_before_fatal_checkpoint(self):
        c = Campaign.__new__(Campaign)
        c.state = {}
        c.summary = {'decisions': 1}
        c.failures = []
        c.event = Mock()
        root = {'id': 'root', 'parent': None, 'state': {'level': '1-1', 'area': 'test', 'x': 100}, 'banned': {}}
        prefix = {'id': 'prefix', 'parent': 'root', 'via': 'jump', 'state': {'level': '1-1', 'area': 'test', 'x': 248}, 'banned': {}}
        child = {'id': 'child', 'parent': 'prefix', 'via': 'jump', 'state': {'level': '1-1', 'area': 'test', 'x': 252}, 'banned': {}}
        c.nodes = {'root': root, 'child': child, 'prefix': prefix}
        segments = [{'buttons': ['right'], 'frames': 4}]
        with patch('campaign.describe', return_value={'timer': 200}), patch('campaign.values', return_value={}):
            self.assertEqual(c.death_checkpoint(child, 'run', 'death_routine', segments)[0]['id'], 'child')
            # A different input is still a fresh attempt, not a decision-count reset.
            other = [{'buttons': ['a'], 'frames': 4}]
            self.assertEqual(c.death_checkpoint(child, 'jump', 'death_routine', other)[0]['id'], 'child')
            target, reason = c.death_checkpoint(child, 'run', 'death_routine', segments)
            self.assertEqual(target['id'], 'root')
            self.assertEqual(reason, 'repeated_actual_death_replan_earlier')

    def test_guided_progress_is_not_penalized_for_unguided_visits(self):
        guide = {'id': 'test-guide', 'levels': {'4-4': {}}}
        with patch('campaign_model.cell', return_value='4-4:4:97:10:3:0'), patch('campaign_model.describe', return_value={'level': '4-4'}):
            self.assertNotEqual(novelty_cell({}, guide), novelty_cell({}))
        with patch('campaign_model.cell', return_value='5-1:0:1:10:3:0'), patch('campaign_model.describe', return_value={'level': '5-1'}):
            self.assertEqual(novelty_cell({}, guide), novelty_cell({}))

    def test_external_walkthrough_is_scoped_and_orders_the_two_corridors(self):
        guide = json.loads((Path(__file__).resolve().parents[1]/'src/jev_games/guide_4_4.json').read_text())
        def context(x, feet, level='4-4'):
            return guide_context({'level': level, 'x': x, 'feet_y': feet}, guide)
        self.assertIsNone(context(332, 96, '7-4'))
        self.assertIsNone(context(332, 96, '5-1'))
        self.assertIsNone(guide_context({'level': '4-4'}, None))
        self.assertEqual(context(332, 96)['current_navigation_goal']['target_feet_y'], 96)
        self.assertEqual(context(1576, 96)['current_navigation_goal']['target_x'], 1576)
        self.assertEqual(context(1576, 160)['current_navigation_goal']['target_x'], 1520)
        self.assertEqual(context(1520, 208)['current_navigation_goal']['target_feet_y'], 208)
        self.assertIn('axe', context(2400, 208)['current_navigation_goal']['goal'])

    def test_death_life_loss_and_counter_underflow(self):
        self.assertEqual(death_reason(state(routine=11)), 'death_routine')
        self.assertEqual(death_reason(state(lives=1), 2), 'life_lost')
        self.assertEqual(death_reason(state(lives=255), 0), 'life_lost')
        self.assertEqual(death_reason(state(mode=3)), 'game_over')

    def test_cloud_exit_and_loading_are_not_deaths(self):
        self.assertIsNone(death_reason(state(yhigh=2, cloud_override=1)))
        self.assertIsNone(death_reason(state(yhigh=2, mode_task=0)))
        self.assertEqual(death_reason(state(yhigh=2)), 'fell_below_playfield')

    def test_final_victory_requires_world_8_4_and_completed_message(self):
        final = state(mode=2, mode_task=4, world=7, level=3, msg_primary=7)
        self.assertTrue(won(final))
        for update in ({'world': 0}, {'level': 2}, {'mode_task': 3}, {'msg_primary': 6}, {'world_end_timer': 1}):
            self.assertFalse(won(final | update))
        self.assertIsNone(death_reason(final | {'lives': 255}, 2))

    def test_control_is_not_assumed_during_flag_or_pipe(self):
        self.assertTrue(playable(state()))
        for routine in (0, 2, 3, 4, 5, 7, 11):
            self.assertFalse(playable(state(routine=routine)))

    def test_exhausted_child_bans_parent_edge_and_restores_parent(self):
        c = Campaign.__new__(Campaign)
        c.current = 'child'
        c.state = {}
        c.nodes = {'root': {'id': 'root', 'state': {'area': '1-1'}, 'banned': {}},
                   'child': {'id': 'child', 'parent': 'root', 'via': 'run'}}
        c.summary = {'decisions': 3}
        c.failures = []
        c.event, c.restore = Mock(), Mock()
        with patch('campaign.describe', return_value={'area': '1-1', 'x': 400}):
            self.assertTrue(c.backtrack('dead_end'))
        self.assertIn('run', c.nodes['root']['banned'])
        self.assertEqual(c.failures[0]['reason'], 'dead_end')
        c.restore.assert_called_once_with('root', 'dead_end')

    def test_failed_choice_removed_and_failure_sent_to_jev(self):
        end = {'area': '1-1', 'x': 100, 'feet_y': 208, 'maze_correct': 0}
        forecasts = {k: {'outcome': {'eligible': True, 'end': end}} for k in ('failed_jump', 'new_jump')}
        failure = {'from': end, 'action': 'failed_jump', 'reason': 'death_routine'}
        with patch('campaign_model.describe', return_value=end):
            request = make_request({}, forecasts, {'banned': {'failed_jump': failure}}, [failure], [], {}, [])
        self.assertEqual(set(request['questions']['maneuver']['criteria']), {'new_jump'})
        self.assertEqual(request['state']['previous_failed_attempts'], [failure])

    def test_subtitle_times(self):
        self.assertEqual(ass_time(61.25), '0:01:01.25')
        self.assertEqual(ass_time(-1), '0:00:00.00')

    def test_renaming_identical_failed_inputs_does_not_bypass_memory(self):
        end = {'area': '1-1', 'x': 100, 'feet_y': 208, 'maze_correct': 0}
        forecasts = {name: {'outcome': {'eligible': True, 'end': end},
                           'segments': [{'buttons': buttons, 'frames': 8}]}
                     for name, buttons in [('bad', ['right']), ('alias', ['right']), ('alternative', ['left'])]}
        with patch('campaign_model.describe', return_value=end):
            request = make_request({}, forecasts, {'banned': {'bad': {}}}, [], [], {}, [])
        self.assertEqual(set(request['questions']['maneuver']['criteria']), {'alternative'})

    def test_pipe_geometry_comes_from_visible_collision_tiles(self):
        ram = {key: {'value': 0} for key in WATCH}
        ram['camera_page']['value'] = 11
        # World x=2912, row 8: paired enterable mouth, not ordinary solid tiles.
        col, row = 2912//16, 8
        for c, tile in ((col, 0x10), (col+1, 0x11)):
            ram[f'tile{((c//16)%2)*208+row*16+c%16}']['value'] = tile
        self.assertEqual(pipes({'ram': ram}), [dict(left=2912, right=2944, top=160, target_player_x=2920)])
        ram['camera_page']['value'] = 10
        self.assertEqual(pipes({'ram': ram}), [])

    def test_plateau_rewinds_spatially_before_the_failed_branch(self):
        c = Campaign.__new__(Campaign)
        c.nodes = {}
        for i, x in enumerate((2600, 2750, 2900, 3000, 3010, 3000)):
            c.nodes[str(i)] = {'id': str(i), 'parent': str(i-1) if i else None,
                'via': 'run', 'state': {'area': '1-2', 'x': x}, 'banned': {}}
        c.current, c.no_novelty = '5', 16
        c.recovery_counts, c.failures, c.summary = {}, [], {'decisions': 20}
        c.event, c.restore = Mock(), Mock()
        self.assertTrue(c.recover_plateau())
        c.restore.assert_called_once_with('1', 'spatial_branch_exhausted')
        self.assertIn('run', c.nodes['1']['banned'])
        self.assertEqual(c.no_novelty, 0)

    def test_warp_goal_is_explicit_and_skips_are_not_claimed_complete(self):
        end = {'area': '1-2', 'x': 100, 'feet_y': 208, 'maze_correct': 0}
        with patch('campaign_model.describe', return_value=end):
            request = make_request({}, {}, {'banned': {}}, [], [], {}, ['1-1'], True)
        self.assertIn('Warp shortcuts are allowed', request['state']['objective'])
        self.assertIn('Skipped levels do not count as completed', request['state']['objective'])
        self.assertEqual(request['state']['completed_levels'], ['1-1'])


if __name__ == '__main__':
    unittest.main()
