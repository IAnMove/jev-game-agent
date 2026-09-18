import unittest
from death_memory import death_episode, recent_deaths, prompt_deaths


def state(x, phase='grounded', timer=100):
    return {'level':'8-4','area':'castle','x':x,'feet_y':208 if phase=='grounded' else 295,
            'phase':phase,'vx_px_frame':2,'vy_px_frame':0 if phase=='grounded' else 5,'timer':timer}


def move(before, buttons, frames, after, action='jump', controller='Jev'):
    return {'before_state':before, 'action':action, 'controller':controller,
            'segments':[{'buttons':buttons,'frames':frames}],
            'trajectory':[{'state':after}]}


class DeathMemoryTests(unittest.TestCase):
    def test_prompt_limits_control_changes_and_labels_omissions_without_changing_log(self):
        route = [move(state(x), ['a'] if x % 2 else ['right'], 1, state(x+1)) for x in range(50)]
        episode = death_episode(route, state(50), 'death_routine')
        prompt = prompt_deaths([{'death_episode': episode}], state(45))[0]
        self.assertEqual(len(prompt['controls']), 24)
        self.assertEqual(prompt['earlier_controls_omitted_from_prompt'], 26)
        self.assertEqual(len(episode['controls']), 50)
        self.assertEqual(prompt['controls'][0][:3], [26, 1, ['right']])
        self.assertEqual(prompt['controls'][0][3][:2], [26, 208])

    def test_includes_approach_and_executed_jump_without_inventing_unexecuted_tail(self):
        route=[move(state(90),['b','right'],8,state(106),'run'),
               move(state(106),['right','b'],4,state(114),'approach'),
               move(state(114),['right','a','b'],4,state(122,'descending'))]
        route[-1]['segments'].append({'buttons':['right'],'frames':40})
        episode=death_episode(route,state(122,'descending'),'death_music_flag','room1')
        self.assertEqual(episode['frames_recorded'],16)
        self.assertEqual([x['frames'] for x in episode['controls']],[12,4])
        self.assertEqual(episode['controls'][0]['actions'],['run','approach'])
        self.assertEqual(episode['last_grounded']['state']['x'],114)
        self.assertEqual(episode['observation'],'fell_below_visible_playfield')
        self.assertNotIn('lava',str(episode))

    def test_neutral_transition_frames_are_labelled_as_automatic(self):
        route=[move(state(100),['a'],4,state(105,'descending')),
               move(state(105,'descending'),[],4,state(105,'descending'),controller='automatic_transition')]
        episode=death_episode(route,state(105,'descending'),'death_routine')
        self.assertEqual(episode['controls'][-1]['buttons'],[])
        self.assertEqual(episode['controls'][-1]['controller'],'automatic_transition')

    def test_bounds_are_whole_observed_segments_and_non_deaths_are_ignored(self):
        route=[move(state(x),['right'],4,state(x+8)) for x in range(0,80,8)]
        episode=death_episode(route,state(80),'death_routine',max_frames=12)
        self.assertEqual(episode['frames_recorded'],12)
        self.assertEqual(episode['start']['x'],56)
        self.assertTrue(episode['earlier_history_omitted'])
        self.assertIsNone(death_episode(route,state(80),'transition_stalled'))
        self.assertIsNone(death_episode([],state(80),'death_routine'))

    def test_signal_does_not_invent_collision_and_timeout_is_distinguished(self):
        route=[move(state(10),[],4,state(10))]
        episode=death_episode(route,state(10),'death_routine')
        self.assertEqual(episode['observation'],'death_detected_exact_collision_unknown')
        self.assertEqual(death_episode(route,state(10,timer=0),'death_routine')['observation'],'game_timer_expired')

    def test_relevant_episodes_survive_restore_and_identical_repeats_are_counted(self):
        route=[move(state(100),['right'],4,state(108))]
        episode=death_episode(route,state(108),'death_routine','room1')
        failures=[{'death_episode':episode},{'death_episode':episode}]
        before_retry=state(90)
        episodes=recent_deaths(failures,before_retry,'room1')
        self.assertEqual(episodes[0]['observations'],2)
        self.assertEqual(recent_deaths(failures,before_retry,'room2'),[])
        self.assertEqual(recent_deaths(failures,state(900),'room1'),[])
