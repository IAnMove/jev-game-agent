"""Small feedback maneuvers derived from local RAM geometry, simulated first."""
from run import observe


def targets(state):
    obs = observe(state)
    m = obs['mario']
    if not m['grounded']:
        return {}
    result = {}
    for block in obs['hidden_blocks_from_ram']:
        left, top, right, bottom = block['rect']
        if abs(left-m['x']) <= 128 and 24 <= m['feet_y']-top <= 112:
            result[f'reveal_block_{left}_{top}'] = {'kind': 'reveal', 'rect': block['rect']}
    surfaces = obs['solid_rectangles_xyxy']
    for rect in surfaces:
        left, top, right, bottom = rect
        if not (16 <= right-left <= 64 and 16 <= m['feet_y']-top <= 96
                and abs((left+right)/2-8-m['x']) <= 128):
            continue
        # Internal rows of a pipe are not separate landing surfaces.
        if any(a < right and c > left and d == top for a, b, c, d in surfaces):
            continue
        if m['feet_y']-top <= 48 and abs((left+right)/2-8-m['x']) <= 64:
            result[f'land_on_{left}_{top}_direct'] = {
                'kind': 'land', 'rect': rect, 'staging_x': m['x'], 'direct': True}
        for side in ('left', 'right'):
            staging = max(obs['visible_x'][0], left-64) if side == 'left' else right+48
            if obs['visible_x'][0] <= staging <= obs['visible_x'][1]-16:
                result[f'land_on_{left}_{top}_from_{side}'] = {
                    'kind': 'land', 'rect': rect, 'staging_x': staging,
                    'launch_x': left-40 if side == 'left' else right+24, 'side': side}
    return result


def steer(x, vx, target):
    desired = max(-1.5, min(1.5, (target-x)*0.16))
    if vx < desired-0.08:
        return ['right']
    if vx > desired+0.08:
        return ['left']
    return []


class PlatformSkill:
    def __init__(self, target):
        self.target = target
        a, b, c, d = target['rect']
        self.target_x = (a+c)/2-8
        self.stage = target.get('staging_x', self.target_x)
        self.jump_frame = None
        self.aligned = False

    def controls(self, state, elapsed):
        m = observe(state)['mario']
        vx = m['vx_raw']/16
        if self.jump_frame is None and not self.aligned:
            if self.target.get('direct') and elapsed:
                self.aligned = True
            # Alignment also releases A, including when already at the target.
            elif elapsed and m['grounded'] and abs(m['x']-self.stage) <= 1 and abs(vx) <= .125:
                self.aligned = True
            else:
                return {'buttons': steer(m['x'], vx, self.stage), 'frames': 1}
        if self.jump_frame is None:
            if self.target['kind'] == 'land' and not self.target.get('direct'):
                direction = 1 if self.target['side'] == 'left' else -1
                if direction*(m['x']-self.target['launch_x']) < 0:
                    return {'buttons': ['right' if direction == 1 else 'left', 'b'], 'frames': 1}
            self.jump_frame = elapsed
        buttons = steer(m['x'], vx, self.target_x)
        if elapsed-self.jump_frame < 36:
            buttons.append('a')
        return {'buttons': buttons, 'frames': 1}


def revealed_blocks(before, after):
    obs = observe(after)
    remaining = {tuple(b['rect']) for b in obs['hidden_blocks_from_ram']}
    # Ignore objects that disappeared merely because the camera moved away.
    return [b['rect'] for b in observe(before)['hidden_blocks_from_ram']
            if obs['visible_x'][0] <= b['rect'][0] < obs['visible_x'][1]
            and tuple(b['rect']) not in remaining]


def landed_on(state, target):
    m = observe(state)['mario']
    a, top, c, _ = target['rect']
    return m['grounded'] and abs(m['feet_y']-top) <= 1 and m['x']+16 > a and m['x'] < c
