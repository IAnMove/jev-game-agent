"""Generic run-up and edge-jump skills from visible collision rectangles."""
from run import observe


def visible_gap(state):
    obs = observe(state)
    m = obs['mario']
    if not m['grounded']:
        return None
    floor = sorted(r for r in obs['solid_rectangles_xyxy'] if abs(r[1]-m['feet_y']) <= 2)
    support = next((r for r in floor if r[0] <= m['x']+8 < r[2]), None)
    if support is None or not 0 < support[2]-m['x'] <= 144:
        return None
    landing = next((r for r in floor if r[0] > support[2]), None)
    if landing is None or not 32 <= landing[0]-support[2] <= 160:
        return None
    return {'runway_left': support[0], 'edge_x': support[2],
            'landing_x': landing[0], 'floor_y': support[1],
            'width': landing[0]-support[2]}


class GapJump:
    def __init__(self, gap, runup, margin=8, brake_landing=False):
        self.gap = gap
        self.target = max(gap['runway_left']+16, gap['edge_x']-runup)
        self.phase = 'retreat' if runup else 'approach'
        self.margin = margin
        self.jump_frame = None
        self.launch_speed = None
        self.brake_landing = brake_landing

    def controls(self, state, elapsed):
        m = observe(state)['mario']
        if self.phase == 'retreat' and m['x'] <= self.target:
            self.phase = 'approach'
        if self.phase == 'retreat':
            return {'buttons': ['left', 'b'], 'frames': 4}
        if self.phase == 'approach':
            if m['grounded'] and m['x'] >= self.gap['edge_x']-self.margin:
                self.phase = 'jump'
                self.jump_frame = elapsed
                self.launch_speed = m['vx_raw']/16
            else:
                return {'buttons': ['right', 'b'], 'frames': 1 if self.gap['edge_x']-m['x'] < 32 else 4}
        buttons = ['right', 'b']
        if self.brake_landing and m['x'] >= self.gap['landing_x']-8:
            buttons = ['left'] if m['vx_raw'] > 8 else []
        if elapsed-self.jump_frame < 36:
            buttons.append('a')
        return {'buttons': buttons, 'frames': 4}


def has_useful_option(state, forecasts, criteria):
    feet = observe(state)['mario']['feet_y']
    return any(
        o.get('progress_px', 0) >= 24 or o.get('level_delta', 0) > 0 or o.get('area_changed')
        or o.get('pipe_entry_started') or o.get('revealed_hidden_blocks') or o.get('landed_on_target')
        or abs(o.get('end', {}).get('feet_y', feet)-feet) >= 16
        for name in criteria for o in [forecasts[name]['outcome']])
