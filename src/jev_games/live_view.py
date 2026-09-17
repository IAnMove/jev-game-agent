"""Bounded, read-only browser telemetry; never advances the emulator."""
import base64
from datetime import datetime, timezone
import json
import time
from pathlib import Path

from run import write


class LiveView:
    def __init__(self, folder, secret=''):
        self.folder = Path(folder)
        self.secret = secret
        self.version = 0
        self.events = []
        self.image = None
        self.frame = None
        self.phase = None
        self.phase_since = None

    def publish(self, phase, summary, state=None, screenshot=None, buttons=(), event=None, **fields):
        now = datetime.now(timezone.utc).isoformat()
        if phase != self.phase:
            self.phase, self.phase_since = phase, now
        if screenshot:
            try:
                data = Path(screenshot).read_bytes()
                if data.startswith(b'\x89PNG\r\n\x1a\n'):
                    self.image = 'data:image/png;base64,'+base64.b64encode(data).decode()
                    self.frame = fields.get('timeline_frame')
            except OSError:
                pass  # A UI capture failure must not alter gameplay.
        self.version += 1
        if event:
            item = {'id': self.version, 'utc': now, 'event': event,
                    'decision': summary.get('decisions', 0), **fields}
            self.events.append(item)
            self.events = self.events[-60:]
            with (self.folder/'live_events.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(self.redact(item)+'\n')
        document = {'version': self.version, 'utc': now, 'phase': phase,
                    'phase_since': self.phase_since, 'image': self.image, 'image_frame': self.frame,
                    'buttons': list(buttons), 'state': state, 'events': self.events,
                    'summary': {key: summary.get(key) for key in (
                        'status', 'decisions', 'rewinds', 'input_tokens', 'output_tokens',
                        'actual_model', 'completed_levels', 'frames_executed', 'parity_checks')}, **fields}
        write(self.folder/'live.json', json.loads(self.redact(document)))

    def redact(self, value):
        text = json.dumps(value, ensure_ascii=False)
        return text.replace(self.secret, '[REDACTED]') if self.secret else text
