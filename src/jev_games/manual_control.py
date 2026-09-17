"""Local browser intent; only the campaign process may drive the emulator."""
import json
import time
from pathlib import Path
from run import write

BUTTONS = {'up', 'down', 'left', 'right', 'a', 'b', 'start', 'select'}


class ManualRequested(Exception):
    pass


def validate_command(data):
    if not isinstance(data, dict) or set(data) != {'mode', 'buttons'}:
        raise ValueError('Expected mode and buttons')
    if data['mode'] not in ('human', 'jev'):
        raise ValueError('Unknown control mode')
    buttons = data['buttons']
    if not isinstance(buttons, list) or len(buttons) > 8 or any(not isinstance(b, str) or b not in BUTTONS for b in buttons):
        raise ValueError('Invalid NES buttons')
    return {'mode': data['mode'], 'buttons': sorted(set(buttons)) if data['mode'] == 'human' else [], 'updated': time.time()}


class ManualControl:
    def __init__(self, directory):
        self.path = Path(directory)/'control.json'

    def read(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if data['mode'] not in ('human', 'jev'):
                raise ValueError('Unknown mode')
            # A lost tab releases buttons and pauses human play, never silently
            # hands control back to the model. T explicitly returns control.
            fresh = 0 <= time.time()-data['updated'] < 1.5
            return data['mode'], [b for b in data['buttons'] if b in BUTTONS] if fresh else [], fresh
        except (OSError, ValueError, KeyError, TypeError):
            return 'jev', [], False

    def check(self):
        if self.read()[0] == 'human':
            raise ManualRequested()
