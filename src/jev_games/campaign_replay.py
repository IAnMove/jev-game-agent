from runtime import ROM, EMULATOR
"""Replay the surviving route with no API and verify every recorded RAM digest."""
import argparse
import json
from pathlib import Path
import shutil

from run import ROOT, WATCH, write, values
from run_lookahead import backend_at
from lookahead import fingerprint
from campaign_media import finalize


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def legacy_route(folders):
    route = []
    initial = folders[0]/'calls/0001/origin.State'
    initial_ram = folders[0]/'calls/0001/before.json'
    for folder in folders:
        for call in sorted((folder/'calls').iterdir()):
            if not (call/'after.json').exists():
                continue
            action = read(call/'action.json')
            expected = read(call/'forecasts.json')[action['action']]['trajectory']
            route.append({'action': action['action'], 'controller': 'Jev',
                'segments': action['segments'], 'trajectory': expected,
                'before_ram_sha256': fingerprint(read(call/'before.json')),
                'after_ram_sha256': fingerprint(read(call/'after.json'))})
    return {'initial_state': str(initial), 'initial_ram': str(initial_ram), 'moves': route}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--campaign', type=Path)
    parser.add_argument('--legacy-runs', type=Path, nargs='+')
    args = parser.parse_args()
    if bool(args.campaign) == bool(args.legacy_runs):
        parser.error('Choose campaign or legacy-runs')
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    if args.campaign:
        route = read(args.campaign/'winning_route.json')
        WATCH.update(route['watches'])
        import hashlib
        if route['rom_sha256'] != hashlib.sha256((ROM).read_bytes()).hexdigest():
            raise RuntimeError('ROM hash mismatch')
        for name, expected in route.get('engine_hashes', {}).items():
            actual = hashlib.sha256((EMULATOR.parent/'dll'/name).read_bytes()).hexdigest()
            if actual != expected:
                raise RuntimeError(f'Emulator core hash mismatch: {name}')
    else:
        route = legacy_route([p.resolve() for p in args.legacy_runs])
    write(out/'replay_source.json', route)
    backend = backend_at(out, True)
    events = []
    state = None
    start = 0
    result = {'status': 'starting', 'api_calls': 0, 'checks': 0, 'moves': 0}
    try:
        launched = backend.launch(ROM)
        if not launched.ok:
            raise RuntimeError(launched.error)
        shutil.copyfile(route['initial_state'], backend.states_dir/'origin.State')
        state = backend.load_state_named('origin', neutralize_framebuffer=False)
        if fingerprint(state) != fingerprint(read(route['initial_ram'])):
            raise RuntimeError('Initial checkpoint RAM mismatch')
        start = state['timeline_frame']
        for i, move in enumerate(route['moves'], 1):
            if fingerprint(state) != move['before_ram_sha256']:
                raise RuntimeError(f'Before-state mismatch at move {i}')
            before = state
            for segment, expected in zip(move['segments'], move['trajectory']):
                b, n = segment['buttons'], segment['frames']
                state = backend.press(b, n, n) if b else backend.advance(n)
                result['checks'] += 1
                if fingerprint(state) != expected['ram_sha256']:
                    raise RuntimeError(f'Deterministic replay mismatch at move {i}, check {result["checks"]}')
            if fingerprint(state) != move['after_ram_sha256']:
                raise RuntimeError(f'After-state mismatch at move {i}')
            r = values(before)
            events.append({'timeline_start': before['timeline_frame'], 'timeline_end': state['timeline_frame'],
                'controller': move['controller']+' replay', 'action': move['action'], 'decision': i,
                'level': f"{r['world']+1}-{r['level']+1}", 'frames': sum(s['frames'] for s in move['segments']), 'rewinds': 0})
            result['moves'] = i
        result['status'] = 'verified_deterministic_replay'
        write(out/'final.json', state)
        shot = backend.screenshot()
        if shot:
            shutil.copyfile(shot, out/'final.png')
    except Exception as exc:
        result['status'] = 'replay_failed'
        result['error'] = str(exc)
    finally:
        backend.close()
        if events:
            result['video'] = finalize(out, events, start, state['timeline_frame'])
        write(out/'result.json', result)
        print(json.dumps(result), flush=True)
    return 0 if result['status'] == 'verified_deterministic_replay' else 2


if __name__ == '__main__':
    raise SystemExit(main())
