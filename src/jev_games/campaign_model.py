"""SMB campaign state machine and bounded, emulator-backed maneuver search.

RAM symbols verified against smbdis_reference.txt. Optional external hints are explicit.
"""
import json
import shutil
import time

from run import WATCH, values, observe, write
from lookahead import fingerprint, compact, candidates


def warp_control(state):
    # Supplementary telemetry; keep the original 510-watch digest replay-compatible.
    return state.get('ram', {}).get('warp_zone', {}).get('value', 0)


def pipes(state):
    """Visible enterable pipe mouths, decoded from collision RAM, not a level map."""
    r = values(state)
    camera = r['camera_page']*256+r['camera_x']
    result = []
    for row in range(13):
        for col in range(camera//16, (camera+255)//16):
            def tile(c):
                return r[f'tile{((c//16)%2)*208+row*16+c%16}']
            # HandlePipeEntry requires the pair $10/$11 under Mario's feet.
            if tile(col) == 0x10 and tile(col+1) == 0x11:
                result.append({'left': col*16, 'right': col*16+32,
                               'top': row*16+32, 'target_player_x': col*16+8})
    return result


def pipe_controls(state, target, elapsed):
    """Generic feedback skill used only in shadow rollouts; Jev selects the skill."""
    d = describe(state)
    dx = target['target_player_x']-d['x']
    vx = d['vx_px_frame']
    desired = max(-2.0, min(2.0, dx*0.20))
    buttons = []
    if vx < desired-0.15:
        buttons.append('right')
    elif vx > desired+0.15:
        buttons.append('left')
    if abs(dx) <= 6 and abs(d['feet_y']-target['top']) <= 2:
        buttons.append('down')
    elif elapsed > 0 and elapsed <= 36 and d['feet_y'] > target['top']-48:
        buttons.append('a')
    return {'buttons': buttons, 'frames': 1 if abs(dx) < 16 else 4}

EXTRA_WATCH = {
    'area_type': 0x74e, 'area_pointer': 0x750, 'swimming': 0x704,
    'cloud_override': 0x743, 'loop_command': 0x745, 'loop_correct': 0x6d9,
    'loop_pass': 0x6da, 'msg_primary': 0x719, 'msg_secondary': 0x749,
    'world_end_timer': 0x7a1, 'timer_expired': 0x759, 'clock_hundreds': 0x7f8,
    'clock_tens': 0x7f9, 'clock_units': 0x7fa, 'frame_counter': 0x09,
    'injury_timer': 0x79e, 'star_timer': 0x79f, 'entrance': 0x710,
    'alternate_entrance': 0x752, 'scroll_lock': 0x723,
}
EXTRA_WATCH.update({f'prng_{i}': 0x7a7+i for i in range(7)})


def configure():
    WATCH.update(EXTRA_WATCH)


def level_id(r):
    return f"{r['world']+1}-{r['level']+1}"


def rank(r):
    return r['world']*4+r['level']


def area_id(r):
    return f"{level_id(r)}:{r.get('area', 0)}:{r.get('area_pointer', 0)}"


def death_reason(r, prior_lives=None):
    # EndChkBButton at the final victory sets lives=255: victory takes priority.
    if r['mode'] == 2:
        return None
    if r['mode'] == 3:
        return 'game_over'
    if prior_lives is not None and (r['lives'] == 255 or r['lives'] < prior_lives):
        return 'life_lost'
    if r['mode'] != 1 or r['mode_task'] != 3:
        return None
    if r['routine'] in (6, 11):
        return 'death_routine'
    if r.get('death_music'):
        return 'death_music_flag'
    # Falling out of a cloud bonus area is a legitimate exit, not a death.
    if r['yhigh'] >= 2 and not r.get('cloud_override'):
        return 'fell_below_playfield'
    return None


def won(r):
    return (r['world'] == 7 and r['level'] == 3 and r['mode'] == 2
            and r['mode_task'] == 4 and r.get('msg_primary', 0) >= 7
            and r.get('world_end_timer', 1) == 0)


def playable(r):
    return r['mode'] == 1 and r['mode_task'] == 3 and r['routine'] == 8 and not death_reason(r)


def describe(state):
    r = values(state)
    return {**compact(state), 'area': area_id(r), 'area_type': r['area_type'],
            'swimming': bool(r['swimming']), 'mode': r['mode'], 'mode_task': r['mode_task'],
            'power': r['power'], 'timer': 100*r['clock_hundreds']+10*r['clock_tens']+r['clock_units'],
            'maze_correct': r['loop_correct'], 'maze_pass': r['loop_pass'],
            'warp_zone_control': warp_control(state)}


def cell(state):
    d = describe(state)
    return f"{d['area']}:{d['x']//32}:{d['feet_y']//32}:{d['maze_correct']}"


def recipes(state, tier=0):
    r = values(state)
    def s(buttons, frames):
        return {'buttons': buttons, 'frames': frames}
    run = ['right', 'b']
    if r['swimming'] or r['area_type'] == 0:
        library = {
            'swim_right_pulses': [s(run, 1), s(run+['a'], 7)]*4,
            'swim_right_gently': [s(run, 8), s(run+['a'], 4)]*3,
            'swim_up': [s([], 1), s(['a'], 7)]*4,
            'swim_down_right': [s(run, 24)],
            'swim_retreat': [s(['left'], 1), s(['left','a'], 7)]*3,
            'enter_pipe': [s(['down'], 24)],
            'wait': [s([], 12)],
        }
    else:
        library = candidates(observe(state))
        library.update({'enter_pipe': [s(['down'], 24)], 'climb': [s(['up'], 24)]})
    if tier:
        library.update({
            'tiny_running_hop': [s(run, 1), s(run+['a'], 6), s(run, 28)],
            'medium_running_hop': [s(run, 1), s(run+['a'], 24), s(run, 28)],
            'jump_vertical': [s([], 1), s(['a'], 32), s([], 24)],
            'retreat': [s(['left','b'], 24)],
            'retreat_then_jump': [s(['left'], 16), s(run, 12), s(run+['a'], 36), s(run, 24)],
            'jump_left': [s(['left'], 1), s(['left','a'], 32), s(['left'], 24)],
            'wait_then_jump': [s([], 24), s(run+['a'], 36), s(run, 24)],
            'approach_late_jump': [s(run, 24), s(run+['a'], 36), s(run, 24)],
            'wait_for_platform': [s([], 32)],
            'walk_to_pipe_then_down': [s(['right'], 8), s(['down'], 24)],
            'duck_right': [s(['right','down'], 24)],
            'fire_forward': [s(['right'], 4), s(run, 8)]*4,
        })
    if tier >= 2:
        for approach in (4, 12, 20, 32):
            for hold in (10, 20, 40):
                library[f'jump_t{approach}_h{hold}'] = [s(run, approach), s(run+['a'], hold), s(run, 32)]
        library['retreat_far_then_jump'] = [s(['left','b'], 32), s(run, 24), s(run+['a'], 40), s(run, 24)]
    return library


def issue(backend, segment):
    b, n = segment['buttons'], segment['frames']
    return backend.press(b, n, n) if b else backend.advance(n)


class CampaignPlanner:
    def __init__(self, backend, allow_warps=False):
        self.backend = backend
        self.allow_warps = allow_warps

    def forecast(self, node, state, folder, tier, deadline):
        b = self.backend
        shutil.copyfile(node/'state.State', b.states_dir/'origin.State')
        origin = fingerprint(state)
        start = values(state)
        start_d = describe(state)
        results = {}
        library = recipes(state, tier)
        targets = {f'enter_visible_pipe_{p["left"]}_{p["top"]}': p for p in pipes(state)}
        library.update({name: None for name in targets})
        for name, plan in library.items():
            if time.monotonic() >= deadline:
                raise TimeoutError('Campaign budget reached')
            current = b.load_state_named('origin', neutralize_framebuffer=False)
            if fingerprint(current) != origin:
                raise RuntimeError('determinism_failure: shadow origin mismatch')
            path, segments = [], []
            elapsed = 0
            airborne = not observe(state)['mario']['grounded']
            failure = None
            stop = False
            def parts():
                if name in targets:
                    while elapsed < 120:
                        yield pipe_controls(current, targets[name], elapsed)
                else:
                    yield from plan
            for part in parts():
                remaining = part['frames']
                while remaining:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Campaign budget reached')
                    segment = {'buttons': part['buttons'], 'frames': min(4, remaining)}
                    current = issue(b, segment)
                    segments.append(segment)
                    elapsed += segment['frames']
                    remaining -= segment['frames']
                    r = values(current)
                    d = describe(current)
                    path.append({'state': d, 'ram_sha256': fingerprint(current)})
                    failure = death_reason(r, start['lives'])
                    if not self.allow_warps and rank(r) > rank(start)+1:
                        failure = 'warp_skips_levels'
                    if area_id(r) == area_id(start) and d['x'] < start_d['x']-256:
                        failure = 'maze_loopback'
                    airborne |= r['player_state'] != 0
                    landed = airborne and r['player_state'] == 0
                    stop = bool(failure) or not playable(r) or (name not in targets and landed and elapsed >= 12 and not r['swimming'])
                    if stop:
                        break
                if stop:
                    break
            end_state = current
            end = describe(current)
            tail_failure = None
            # Neutral coasting is conservative. Swimming requires frequent strokes.
            if not failure and playable(values(current)):
                for _ in range(2 if start['swimming'] else 8):
                    current = b.advance(4)
                    tail_failure = death_reason(values(current), start['lives'])
                    if tail_failure or not playable(values(current)):
                        break
            result = {'segments': segments, 'trajectory': path,
                      'outcome': {'eligible': not failure and not tail_failure,
                         'failure': failure, 'coast_failure': tail_failure,
                         'frames': elapsed, 'progress_px': end['x']-start_d['x'],
                         'level_delta': rank(values(end_state))-rank(start),
                         'area_changed': end['area'] != start_d['area'],
                         'maze_correct_delta': end['maze_correct']-start_d['maze_correct'],
                         'end': end}, 'end_ram_sha256': fingerprint(end_state)}
            if name in targets:
                result['outcome'].update(target_pipe=targets[name],
                    pipe_entry_started=values(end_state)['routine'] == 3,
                    assistance='Generic RAM feedback alignment simulated by the program; exact resulting inputs replayed if Jev selects this option.')
            results[name] = result
            # Replanning sooner can avoid a death predicted only after a long move.
            # This is offered transparently as a distinct short action at tier 2.
            if tier >= 2 and (failure or tail_failure) and len(path) >= 3:
                prefix = path[:2]
                if all(p['state']['routine'] not in (6, 11) and p['state']['feet_y'] < 256 for p in prefix):
                    outcome = {**result['outcome'], 'eligible': True, 'failure': None,
                        'coast_failure': tail_failure or failure, 'requires_immediate_replan': True,
                        'frames': sum(s['frames'] for s in segments[:2]),
                        'progress_px': prefix[-1]['state']['x']-start_d['x'],
                        'end': prefix[-1]['state'], 'level_delta': 0, 'area_changed': False}
                    results[name+'_short_prefix'] = {'segments': segments[:2], 'trajectory': prefix,
                        'outcome': outcome, 'end_ram_sha256': prefix[-1]['ram_sha256']}
            write(folder/'forecasts.json', results)
        return results


def guide_context(current, guide):
    if not guide or current.get('level') not in guide.get('levels', {}):
        return None
    level = guide['levels'][current['level']]
    phase = next((p for p in level['phases']
                  if p.get('x_min', -1e9) <= current['x'] < p.get('x_max', 1e9)
                  and p.get('feet_min', -1e9) <= current['feet_y'] < p.get('feet_max', 1e9)), {})
    return {k: v for k, v in level.items() if k != 'phases'} | {
        'guide_id': guide['id'], 'sources': guide['sources'], 'current_navigation_goal': phase}


def novelty_cell(state, guide=None):
    key = cell(state)
    if guide and describe(state)['level'] in guide.get('levels', {}):
        return f"guide:{guide['id']}:{key}"
    return key


def make_request(state, forecasts, node, failures, history, visited, completed, allow_warps=False, guide=None):
    current = describe(state)
    hint = guide_context(current, guide)
    def signature(item):
        return json.dumps([{'buttons': sorted(s['buttons']), 'frames': s['frames']}
                           for s in item.get('segments', [])], sort_keys=True)
    banned_inputs = {signature(forecasts[name]) for name in node['banned']
                     if name in forecasts and forecasts[name].get('segments')}
    eligible = {}
    for name, item in forecasts.items():
        if (item['outcome']['eligible'] and name not in node['banned']
                and signature(item) not in banned_inputs):
            end = item['outcome']['end']
            key = f"{end['area']}:{end['x']//32}:{end['feet_y']//32}:{end['maze_correct']}"
            item['outcome']['previous_visits_to_destination'] = visited.get(key, 0)
            if hint:
                target = hint['current_navigation_goal']
                item['outcome']['external_hint_distance'] = {
                    label: abs(end[field]-target[label])
                    for label, field in (('target_x', 'x'), ('target_feet_y', 'feet_y')) if label in target}
            eligible[name] = json.dumps(item['outcome'], separators=(',', ':'))
    nearby_failures = [f for f in failures if f['from']['area'] == current['area']][-8:]
    return {'model': 'jev-latest', 'state': {
        'role': 'You are Mario campaign controller. Your choice executes immediately.',
        'objective': ('Rescue the princess in 8-4. Warp shortcuts are allowed; use a pipe to leave a warp zone. Skipped levels do not count as completed.' if allow_warps else 'Complete all 32 levels in order and rescue the princess in 8-4. Avoid warp shortcuts.')+' Optional coins have no priority.',
        'current': current, 'completed_levels': completed,
        'external_walkthrough': hint,
        'visible_geometry': observe(state)['solid_rectangles_xyxy'] if hint and state.get('ram') else None,
        'visible_enterable_pipes': pipes(state) if state.get('ram') else [],
        'warp_zone_help': 'A nonzero warp_zone_control enables warp pipes. In a warp room the exit is DOWN through a pipe, not the right wall. enter_visible_pipe skills simulate jumping, alignment and Down from the current RAM geometry. Prefer an eligible successful pipe entry over horizontal distance.',
        'previous_failed_attempts': nearby_failures, 'banned_at_this_exact_checkpoint': node['banned'],
        'recent_path': history[-5:],
        'candidate_outcomes': {k: v['outcome'] for k, v in forecasts.items()},
        'method': 'Exact emulator rollouts from a copied checkpoint. Generic pipe skills use RAM feedback for jump/alignment/Down; you choose among their simulated outcomes. Failed branches are excluded. Long-term safety is not guaranteed. After a failure the program restores a prior checkpoint and gives you this explicit failure memory; your model weights have not been updated.',
        'rules': [
            'Choose a coherent maneuver making progress towards completing the current area. Favor actual level/area completion, safe landing and forward progress.',
            'Learn from the provided failures: do NOT repeat the failed strategy through a different equivalent action. A short prefix flagged requires_immediate_replan is an emergency option, not a safe full crossing.',
            'Compare destination height and novelty. Escape repeated locations with a different height, timing, retreat, pipe entry or waiting for a moving obstacle.',
            'In water, pulse A to swim upward, release to sink, steer right toward the exit pipe. In castles, different vertical routes may be necessary; maze_loopback means the route was wrong.',
            'Down enters a vertical pipe when aligned on top. Right enters a side pipe. Up climbs a vine. B fires only when fire-powered and may need repeated presses.',
            'Retreat or wait when they enable a route; do not optimize x at the cost of repeating a dead end. Prefer fewer previous visits when progress is otherwise similar.',
        ]}, 'questions': {'maneuver': {'type': 'choice',
            'instructions': ('Follow the external walkthrough CURRENT NAVIGATION GOAL. Correct corridor/height takes priority over horizontal distance; a safe wrong corridor loops. ' if hint else '')+'Choose the best eligible maneuver to complete this area, taking recorded failed attempts into account.',
            'criteria': eligible}}}
