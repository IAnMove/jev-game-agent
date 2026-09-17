from runtime import ROM, EMULATOR
"""Recorded Jev + shadow simulation experiment; main timeline never rewinds."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import httpx
from run import ROOT, ENGINE, WATCH, ReliableBackend, LuaBridgeConfig, observe, values, write, snapshot
from lookahead import ShadowPlanner, compact, dead, fingerprint, request


def backend_at(folder, record=False, fast=False):
    backend = ReliableBackend(EMULATOR, ENGINE, folder,
        LuaBridgeConfig(script_path='scripts/bizhawk_bridge.lua', boot_frames=120,
                       ready_timeout_seconds=30, timeout_seconds=15, poll_interval_seconds=0.01),
        config_template=ENGINE / 'data/bizhawk_search_config_template.ini',
        record={'enabled': record, 'writer': 'nut', 'container': 'nut'})
    backend.skip_screenshots = bool(fast and not record)
    backend.write_ram_watch({k: f'0x{v:04x},System Bus' for k, v in WATCH.items()})
    return backend


def query(client, payload, folder, key, deadline, summary):
    """Retry only API transport; the paused controller is never invoked here."""
    for attempt in range(1, 4):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Wall budget exhausted during API retries')
        started = time.perf_counter()
        summary['http_attempts'] += 1
        try:
            response = client.post('https://api.typesafe.ai/v1/systemone', json=payload, timeout=min(20, remaining))
        except httpx.TransportError as exc:
            write(folder / f'transport_attempt_{attempt}.json', {
                'attempt': attempt, 'error': type(exc).__name__,
                'message': str(exc).replace(key, '[REDACTED]'),
                'latency_ms': round((time.perf_counter()-started)*1000, 3)})
            if attempt == 3:
                raise
            response = None
        else:
            latency = round((time.perf_counter()-started)*1000, 3)
            body = response.text.replace(key, '[REDACTED]')
            (folder / f'response_attempt_{attempt}.txt').write_text(body, encoding='utf-8')
            transport = {'attempt': attempt, 'status_code': response.status_code, 'latency_ms': latency,
                'response_headers': {k: v for k, v in response.headers.items() if k.lower() in {'content-type', 'date', 'x-request-id', 'retry-after'}}}
            write(folder / f'transport_attempt_{attempt}.json', transport)
            if response.status_code not in {429, 500, 502, 503, 504, 529} or attempt == 3:
                (folder / 'response.txt').write_text(body, encoding='utf-8')
                write(folder / 'transport.json', transport)
                return response.status_code, body, latency
        delay = 2 ** attempt
        if response is not None:
            try:
                delay = max(delay, min(30, float(response.headers.get('retry-after', delay))))
            except ValueError:
                pass
        print(json.dumps({'event': 'api_retry', 'attempt': attempt,
                          'status_code': None if response is None else response.status_code,
                          'delay_seconds': delay}), flush=True)
        time.sleep(min(delay, max(0, deadline-time.monotonic())))
