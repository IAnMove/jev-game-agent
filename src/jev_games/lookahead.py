"""Explicit emulator-assisted maneuvers. No level coordinates or winning route.

The shadow emulator may rewind. The recorded player never does. Jev chooses
among eligible trajectories; the eligibility rule is deterministic and logged.
"""
import hashlib
import json
import shutil
import time

from run import observe, values, write


def fingerprint(state):
    return hashlib.sha256(json.dumps(values(state), sort_keys=True).encode()).hexdigest()


def dead(state, lives):
    r = values(state)
    return r['routine'] in (6, 11) or bool(r['death_music']) or r['lives'] < lives or r['yhigh'] >= 2


def candidates(obs):
    def seg(buttons, n):
        return {'buttons': buttons, 'frames': n}
    run = ['right', 'b']
    result = {
        'run_briefly': [seg(run, 16)],
        'run_forward': [seg(run, 32)],
        'walk_briefly': [seg(['right'], 16)],
        'brake': [seg(['left'], 8)],
    }
    if obs['mario']['grounded']:
        for name, approach, hold in (
            ('running_short_jump', 1, 16), ('running_long_jump', 1, 36),
            ('approach_then_jump', 8, 36), ('approach_further_then_jump', 16, 36)):
            result[name] = [seg(run, approach), seg(run + ['a'], hold), seg(run, 24)]
        result['walking_jump'] = [seg(['right'], 1), seg(['right', 'a'], 36), seg(['right'], 24)]
    else:
        result['sustain_running_jump'] = [seg(run + ['a'], 24), seg(run, 16)]
        result['sustain_walking_jump'] = [seg(['right', 'a'], 24), seg(['right'], 16)]
        result['brake_sustain_jump'] = [seg(['left', 'a'], 8)]
    return result


def compact(state, previous=None, buttons=None):
    obs = observe(state, previous, buttons)
    m = obs['mario']
    phase = 'grounded' if m['grounded'] else ('ascending' if m['vy_raw'] < 0 else 'descending' if m['vy_raw'] > 0 else 'apex')
    return {'x': m['x'], 'feet_y': m['feet_y'], 'phase': phase,
            'vx_px_frame': m['vx_raw'] / 16, 'vy_px_frame': m['vy_raw'],
            'level': obs['level'], 'routine': obs['engine_routine'],
            'lives': obs['lives_counter']}


class ShadowPlanner:
    def __init__(self, backend):
        self.backend = backend

    def forecast(self, player, state, folder, deadline):
        player.save_state_named('forecast_origin')
        source = player.states_dir / 'forecast_origin.State'
        # Preserve every origin for reproducible offline replay of this decision.
        shutil.copyfile(source, folder / 'origin.State')
        shutil.copyfile(source, self.backend.states_dir / 'forecast_origin.State')
        origin_hash = fingerprint(state)
        obs = observe(state)
        lives = values(state)['lives']
        forecasts = {}
        for name, plan in candidates(obs).items():
            if time.monotonic() >= deadline:
                raise TimeoutError('Forecast wall budget exhausted')
            initial = self.backend.load_state_named('forecast_origin', neutralize_framebuffer=False)
            if fingerprint(initial) != origin_hash:
                raise RuntimeError('Shadow initial RAM differs from player')
            path, executed, traces = [], [], []
            current = initial
            elapsed = 0
            failed = False
            has_jumped = not obs['mario']['grounded']
            landed = False
            for segment in plan:
                remaining = segment['frames']
                while remaining:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Forecast wall budget exhausted')
                    n = min(4, remaining)
                    buttons = segment['buttons']
                    current = self.backend.press(buttons, n, n) if buttons else self.backend.advance(n)
                    executed.append({'buttons': buttons, 'frames': n})
                    elapsed += n
                    remaining -= n
                    p = compact(current)
                    p['elapsed_frames'] = elapsed
                    p['ram_sha256'] = fingerprint(current)
                    path.append(p)
                    traces.append(values(current))
                    failed = dead(current, lives)
                    has_jumped |= values(current)['player_state'] != 0
                    landed = has_jumped and values(current)['player_state'] == 0
                    # Stop a jump maneuver at its first landing. This stopping rule
                    # is simulated first; exactly these segments are then offered.
                    if failed or (landed and elapsed >= 12):
                        break
                if failed or (landed and elapsed >= 12):
                    break
            endpoint = current
            # Test momentum beyond the endpoint, including walking off a ledge.
            # These frames are prediction only, never committed with the maneuver.
            tail_dead = failed
            tail = []
            if not failed:
                for _ in range(8):
                    current = self.backend.advance(4)
                    tail.append(compact(current))
                    if dead(current, lives):
                        tail_dead = True
                        break
            end = compact(endpoint)
            forecasts[name] = {
                'segments': executed, 'frames': elapsed, 'trajectory': path,
                'ram_trace': traces, 'end_ram_sha256': fingerprint(endpoint),
                'outcome': {
                    'eligible': not failed and not tail_dead,
                    'death_during_maneuver': failed, 'death_in_32_frame_coast_test': tail_dead and not failed,
                    'progress_px': end['x'] - obs['mario']['x'],
                    'end': end, 'landed': landed,
                    'min_feet_y': min(p['feet_y'] for p in path),
                    'frames': elapsed,
                }, 'coast_test': tail,
            }
            write(folder / 'forecasts.json', forecasts)
        return forecasts


def request(state, forecasts, history):
    obs = observe(state)
    choices = {name: json.dumps(item['outcome'], separators=(',', ':'))
               for name, item in forecasts.items() if item['outcome']['eligible']}
    return {'model': 'jev-latest', 'state': {
        'role': 'You choose the next complete controller maneuver for Mario. It will really execute.',
        'goal': 'Finish the current level alive, reach the flag and enter the next level. Coins are optional.',
        'coordinates': 'World x increases right, y down. All distances pixels, time emulated frames.',
        'current': compact(state),
        'nearby_objects': obs['objects'],
        'visible_terrain': obs['solid_rectangles_xyxy'],
        'flagpole_tiles': obs['flagpole_tiles_xy'],
        'recent_committed_maneuvers': history[-4:],
        'forecast_method': 'Each maneuver was actually simulated in a shadow copy from this exact RAM and emulator state. The player has not moved. Eligible means no detected death during the maneuver or a 32-frame neutral coasting test. It does NOT guarantee long-term survival. Final position and phase matter.',
        'candidate_outcomes': {name: item['outcome'] for name, item in forecasts.items()},
        'strategy': [
            'Choose an eligible maneuver making substantial rightward progress while preserving a viable landing. A simulated safe running jump is preferable to getting stuck or walking into danger.',
            'Prefer a grounded landing when progress is similar. A maneuver ending airborne may still need further steering.',
            'Do not repeat zero-progress actions against a pipe or staircase; choose a jump that actually climbs it.',
            'When the flag animation or level transition is active, let it advance with a forward maneuver.',
            'Use recent outcomes to avoid loops. Retreat only when it enables a necessary new approach.',
        ],
    }, 'questions': {'maneuver': {'type': 'choice',
        'instructions': 'Which eligible simulated maneuver best advances Mario towards finishing the level alive? Compare measured progress, landing and recent history. Choose one coherent maneuver.',
        'criteria': choices}}}
