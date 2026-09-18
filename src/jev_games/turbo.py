"""Direct RAM decisions: no simulated outcomes or safety claims."""
import json
from campaign_model import describe, recipes, pipes, guide_context
from run import observe
from death_memory import prompt_deaths
from navigation import navigation_context


def options(state):
    result = {}
    library = recipes(state)
    library['retreat'] = [{'buttons': ['left'], 'frames': 24}]
    library['wait'] = [{'buttons': [], 'frames': 16}]
    for name, plan in library.items():
        segments = []
        for part in plan:
            remaining = part['frames']
            while remaining:
                frames = min(4, remaining)
                segments.append({'buttons': list(part['buttons']), 'frames': frames})
                remaining -= frames
        result[name] = {'segments': segments, 'trajectory': None, 'outcome': {
            'prediction_available': False, 'frames': sum(s['frames'] for s in segments),
            'controls': plan, 'stop_rule': 'Stop on death, loss of game control, or first landing after 12 frames; otherwise execute the listed inputs.'}}
    return result


def request(state, choices, node, failures, history, visited, completed, allow_warps=False, guide=None, allow_risky=False):
    current = describe(state)
    navigation = navigation_context(history, current, guide)
    def signature(item):
        return json.dumps(item['segments'], sort_keys=True)
    banned = {signature(choices[name]) for name in node['banned'] if name in choices}
    criteria = {name: json.dumps(item['outcome'], separators=(',', ':'))
                for name, item in choices.items()
                if allow_risky or (name not in node['banned'] and signature(item) not in banned)}
    return {'model': 'jev-latest', 'state': {
        'mode': 'turbo_direct_ram',
        'objective': 'Rescue the princess in 8-4. '+('Warps allowed.' if allow_warps else 'Complete every level in order; avoid warps.'),
        'method': 'No emulator lookahead. These are control recipes, NOT predicted outcomes. No candidate is certified safe. Your chosen recipe executes in the recorded emulator.',
        'coordinates': 'World pixels: x increases right, y increases down. Rectangles are [left,top,right,bottom]. Missing ground is a pit. Mario is 16 pixels wide.',
        'controls': 'Arrows move. B runs or fires. A jumps; release A before a new jump. Holding A sustains height. Momentum persists. Down enters a pipe from its top; Right enters side pipes. Up climbs. In water pulse A to rise.',
        'current': current, 'observation': observe(state),
        'visible_enterable_pipes': pipes(state),
        'external_walkthrough': guide_context(current, guide, history),
        'navigation_memory': navigation,
        'completed_levels': completed, 'recent_path': history[-4:],
        'previous_failed_attempts': [{k: v for k, v in f.items() if k != 'death_episode'}
                                     for f in failures if f['from']['area'] == current['area']][-8:],
        'banned_at_this_exact_checkpoint': {name: {k: v for k, v in f.items() if k != 'death_episode'}
                                          for name, f in node['banned'].items()},
        'recent_death_episodes': prompt_deaths(failures, current, navigation['current_room']),
        'retry_exhausted_options': bool(allow_risky),
        'recovery': 'Actual death restores a checkpoint. Review recent_death_episodes: actual buttons and game frames before death, with position and velocity. Vary approach speed, takeoff timing, jump hold or braking, not just the final dying input. Do not infer a collision cause when unknown. Maze repeats are navigation feedback, not death. Avoid standing still until the timer expires.',
    }, 'questions': {'maneuver': {'type': 'choice',
        'instructions': 'Choose the next maneuver to survive and finish this area. Use current geometry, enemies, velocity and failures; outcomes have not been simulated.',
        'criteria': criteria}}}
