import unittest
from navigation import pipe_destination, confirmed_arrival, navigation_context
from campaign_model import guide_context


class NavigationTests(unittest.TestCase):
    def test_destination_is_read_only_during_real_entry_and_masks_pointer_flags(self):
        def state(routine, pointer):
            return {'ram': {k: {'value':v} for k,v in
                    {'routine':routine,'area_pointer':pointer,'entrance_page':7}.items()}}
        self.assertIsNone(pipe_destination(state(8,229)))
        self.assertIsNone(pipe_destination({'ram':{'routine':{'value':3}}}))
        self.assertEqual(pipe_destination(state(3,229)),pipe_destination(state(3,101)))
        self.assertEqual(pipe_destination(state(3,229))['key'],'3:5:7')
        self.assertEqual(pipe_destination(state(2,229))['key'],'3:5:7')

    def test_arrival_requires_control_correct_area_and_destination_page(self):
        dest={'area_type':3,'entry_page':7}
        after={'routine':8,'area_type':3,'x':1848}
        self.assertTrue(confirmed_arrival(dest,after))
        for changes in ({'routine':7},{'area_type':0},{'x':312}):
            self.assertFalse(confirmed_arrival(dest,after|changes))

    def test_room_follows_completed_pipes_and_rollback_not_x_wrap_or_attempts(self):
        guide={'id':'test','sources':[], 'levels':{'8-4':{'initial_room':'first','rooms':{},'phases':[
            {'room':'first','goal':'first exit'},{'room':'second','goal':'elevated exit'}]}}}
        current={'level':'8-4','x':280,'feet_y':128}
        def step(destination=None,confirmed=True):
            s={'from':current|{'x':1296},'to':current,'action':'pipe'}
            if destination:s['pipe_transition']={'confirmed':confirmed,'destination':{'key':destination}}
            return s
        history=[step(),step('second',False)]
        self.assertEqual(navigation_context(history,current,guide)['current_room'],'first')
        history.append(step('second'))
        self.assertEqual(guide_context(current,guide,history)['current_navigation_goal']['goal'],'elevated exit')
        self.assertEqual(navigation_context(history[:-1],current,guide)['current_room'],'first')
        history.append(step('first'))
        nav=navigation_context(history,current,guide)
        self.assertEqual(nav['current_room'],'first')
        self.assertEqual(nav['total_crossings_on_current_route'],2)
