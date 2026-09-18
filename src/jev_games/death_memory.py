"""Bounded, factual controller history preceding an observed death."""
import hashlib
import json

DEATH_SIGNALS = {'death_routine', 'death_music_flag', 'life_lost',
                 'fell_below_playfield', 'game_over'}
FIELDS = ('level', 'area', 'area_type', 'x', 'feet_y', 'phase',
          'vx_px_frame', 'vy_px_frame', 'routine', 'timer')


def compact(state):
    return {k: state[k] for k in FIELDS if k in state} if state else None


def death_episode(route, outcome, signal, room=None, max_frames=240):
    if signal not in DEATH_SIGNALS or not route:
        return None
    steps, frames, truncated = [], 0, False
    for move_index in range(len(route)-1, -1, -1):
        move = route[move_index]
        trace = move.get('trajectory', [])
        # Only inputs paired with an actual recorded state count as executed.
        executed = list(zip(move.get('segments', []), trace))
        for i in range(len(executed)-1, -1, -1):
            segment, sample = executed[i]
            after = sample['state']
            if after.get('level') != outcome.get('level'):
                truncated = True
                break
            if frames+segment['frames'] > max_frames or len(steps) >= 120:
                truncated = True
                break
            before = trace[i-1]['state'] if i else move.get('before_state')
            if before is None and move_index and route[move_index-1].get('trajectory'):
                before = route[move_index-1]['trajectory'][-1]['state']
            steps.append({'buttons': sorted(segment['buttons']), 'frames': segment['frames'],
                          'before': compact(before), 'after': compact(after),
                          'controller': move.get('controller'), 'action': move.get('action')})
            frames += segment['frames']
        if truncated:
            break
    steps.reverse()
    if not steps:
        return None
    timeline, offset = [], 0
    last_grounded = None
    for step in steps:
        if step['before'] and step['before'].get('phase') == 'grounded':
            last_grounded = {'frame': offset, 'state': step['before']}
        if (timeline and timeline[-1]['buttons'] == step['buttons']
                and timeline[-1]['controller'] == step['controller']):
            timeline[-1]['frames'] += step['frames']
            timeline[-1]['after'] = step['after']
            if step['action'] not in timeline[-1]['actions']:
                timeline[-1]['actions'].append(step['action'])
        else:
            timeline.append({'frame': offset, 'frames': step['frames'], 'buttons': step['buttons'],
                             'before': step['before'], 'after': step['after'],
                             'controller': step['controller'], 'actions': [step['action']]})
        offset += step['frames']
    observation = ('game_timer_expired' if outcome.get('timer') == 0 else
                   'fell_below_visible_playfield' if outcome.get('feet_y', 0) >= 288 or signal == 'fell_below_playfield'
                   else 'death_detected_exact_collision_unknown')
    signature = {'start': steps[0]['before'], 'controls': [(s['buttons'], s['frames']) for s in timeline],
                 'signal': signal, 'end': compact(outcome), 'room': room}
    return {'id': hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:16],
            'evidence': 'Actual executed inputs and recorded RAM states, not an imagined explanation.',
            'room': room, 'death_signal': signal, 'observation': observation,
            'start': steps[0]['before'], 'end': compact(outcome), 'frames_recorded': frames,
            'earlier_history_omitted': truncated, 'last_grounded': last_grounded,
            'controls': timeline}


def recent_deaths(failures, current, room=None, limit=3):
    selected = {}
    for failure in reversed(failures):
        episode = failure.get('death_episode')
        if not episode or episode['end'].get('level') != current.get('level'):
            continue
        if room not in (None, 'unknown') and episode.get('room') not in (None, 'unknown', room):
            continue
        positions = [s.get('x') for s in (episode.get('start') or {}, episode['end']) if 'x' in s]
        if positions and current.get('x') is not None and min(abs(current['x']-x) for x in positions) > 320:
            continue
        key = episode['id']
        if key in selected:
            selected[key]['observations'] += 1
        elif len(selected) < limit:
            selected[key] = {**episode, 'observations': 1}
    return list(selected.values())


def prompt_deaths(failures, current, room=None):
    """Bound model context; full episodes remain in the local failure log."""
    result = []
    coordinates = ('x', 'feet_y', 'vx_px_frame', 'vy_px_frame', 'phase')
    for episode in recent_deaths(failures, current, room, limit=2):
        controls = episode['controls'][-24:]
        item = {k: v for k, v in episode.items() if k != 'controls'}
        item['control_columns'] = ['frame', 'frames', 'buttons', 'before', 'after', 'controller']
        item['position_columns'] = list(coordinates)
        item['controls'] = [[step['frame'], step['frames'], step['buttons'],
                             [(step.get('before') or {}).get(k) for k in coordinates],
                             [step['after'].get(k) for k in coordinates], step['controller']]
                            for step in controls]
        item['earlier_controls_omitted_from_prompt'] = len(episode['controls'])-len(controls)
        result.append(item)
    return result
