from runtime import ROM, EMULATOR, engine_files
"""Autonomous, bounded Jev campaign with backtracking and incremental videos."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import httpx
from run import ROOT, ENGINE, WATCH, values, write, snapshot
from lookahead import fingerprint
from run_lookahead import backend_at, query
from campaign_model import (configure, CampaignPlanner, death_reason, won, playable,
    describe, cell, novelty_cell, level_id, rank, area_id, issue, make_request, guide_context)
from campaign_media import finalize
from image_ascii import attach_ascii
from live_view import LiveView
from recovery import StallWatch
from manual_control import ManualControl, ManualRequested
import turbo
from gap_jump import has_useful_option


class DeterminismError(RuntimeError):
    pass


class NonretryableAPIError(RuntimeError):
    pass


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def campaign_backend(folder, record=False, fast=False):
    backend = backend_at(folder, record, fast=fast)
    backend.write_ram_watch({k: f'0x{v:04x},System Bus'
                             for k, v in (WATCH | {'warp_zone': 0x6d6}).items()})
    return backend


class Campaign:
    def __init__(self, args, key):
        self.args, self.key = args, key
        self.stall_watch = StallWatch(args.stuck_frames)
        self.out = args.out.resolve()
        self.out.mkdir(parents=True, exist_ok=False)
        self.live = LiveView(self.out, key)
        self.control = ManualControl(self.out)
        self.guide = read(args.hint_file) if args.hint_file else None
        if self.guide:
            write(self.out/'external_guide.json', self.guide)
        self.rom = ROM
        self.started = time.monotonic()
        self.deadline = float('inf') if args.until_complete else self.started+args.wall_seconds
        self.player = self.shadow = None
        self.state = None
        self.chapter = None
        self.chapter_events = []
        self.chapter_start = 0
        self.chapter_first_decision = 0
        self.nodes = {}
        self.current = None
        self.route = []
        self.failures = []
        self.visited = {}
        self.history = []
        self.videos = []
        self.inherited_wall = 0
        self.no_novelty = 0
        self.seen_cells = set()
        self.recovery_counts = {}
        self.summary = {'status': 'starting', 'decisions': 0, 'http_attempts': 0,
            'moves': 0, 'rewinds': 0, 'deaths': 0, 'technical_restarts': 0,
            'parity_checks': 0, 'frames_executed': 0, 'input_tokens': 0, 'output_tokens': 0,
            'completed_levels': [], 'chapters': 0, 'forecast_seconds': 0.0}
        sources = ['gap_jump.py', 'turbo.py', 'manual_control.py', 'recovery.py', 'campaign.py', 'campaign_model.py', 'campaign_media.py', 'campaign_replay.py',
                   'run.py', 'lookahead.py', 'run_lookahead.py', 'prompt_experiment.py', 'image_ascii.py', 'runtime.py', 'live_view.py']
        hashes = {}
        (self.out/'sources').mkdir()
        for name in sources:
            source = Path(__file__).with_name(name)
            hashes[name] = hashlib.sha256(source.read_bytes()).hexdigest()
            shutil.copyfile(source, self.out/'sources'/name)
        manifest = {'created_at': datetime.now(timezone.utc).isoformat(), 'policy': 'jev_campaign_v2',
            'objective': 'Final victory in 8-4; warps allowed.' if args.allow_warps else 'Complete all 32 levels in order; final victory in 8-4.',
            'rom': str(self.rom), 'rom_sha256': hashlib.sha256(self.rom.read_bytes()).hexdigest(),
            'emulator_sha256': hashlib.sha256((EMULATOR).read_bytes()).hexdigest(),
            'config_sha256': hashlib.sha256((ENGINE/'data/bizhawk_search_config_template.ini').read_bytes()).hexdigest(),
            'engine_hashes': {name: hashlib.sha256(path.read_bytes()).hexdigest()
                              for name, path in engine_files().items()},
            'source_sha256': hashes, 'watches': WATCH, 'fast_search': args.fast,
            'play_mode': 'turbo' if args.turbo else 'fast' if args.fast else 'full',
            'bounds': {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}, 'api_key_logged': False,
            'supplementary_watch': {'warp_zone': 0x6d6},
            'external_guide': self.guide,
            'ascii_observation': {'level': args.ascii_level, 'source': 'screenshot pixels only',
                                  'mode': 'background_contrast', 'columns': 64, 'rows': 30},
            'assistance': 'Direct RAM decisions without lookahead; actual death recovery and recorded input hashes.' if args.turbo else 'Exact shadow rollouts, fatal-outcome filter, generic action library including RAM-feedback pipe alignment and run-up gap jumps, Jev choice, failure memory and spatial checkpoint backtracking. Gap landings use an explicit 8-frame coasting probe and immediate replanning. Rewinds are allowed and logged.',
            'api': 'jev-latest; up to 3 HTTP attempts per round, then pause/retry for up to 15 minutes without advancing the emulator.'}
        write(self.out/'manifest.json', manifest)
        shutil.copyfile(Path(__file__).with_name('campaign_watch.html'), self.out/'watch.html')
        if args.resume:
            self.import_campaign(args.resume.resolve())
        if args.resume_node:
            ancestor = self.current
            while ancestor and ancestor != args.resume_node:
                ancestor = self.nodes[ancestor]['parent']
            if not ancestor:
                raise ValueError('resume-node must be an ancestor of the saved route')
        self.trial_start_decision = self.summary['decisions']
        if not args.trial_level:
            self.summary.pop('trial', None)
        if args.trial_level:
            self.no_novelty = 0
            self.summary['trial'] = {'level': args.trial_level, 'ascii': args.ascii_level,
                'initial_decisions': self.summary['decisions'], 'initial_rewinds': self.summary['rewinds'],
                'initial_input_tokens': self.summary['input_tokens'], 'initial_output_tokens': self.summary['output_tokens']}
        self.publish_live('booting', event='session_start')

    def publish_live(self, phase, buttons=(), event=None, **fields):
        if not hasattr(self, 'live'):
            return  # Lightweight controller test doubles do not need a viewer.
        try:
            screenshot = None
            if self.player and self.state:
                screenshot = self.player.paths.bridge_dir/'screen.png'
            self.live.publish(phase, self.summary, describe(self.state) if self.state else None,
                screenshot=screenshot, buttons=buttons, event=event,
                timeline_frame=self.state.get('timeline_frame') if self.state else None,
                play_mode='turbo' if self.args.turbo else 'fast' if self.args.fast else 'full', **fields)
        except OSError:
            pass  # Telemetry has no authority over inputs, checkpoints or API calls.

    def import_campaign(self, source):
        """Fork a stopped run without mutating its evidence or resetting its budget."""
        previous = read(source/'manifest.json')
        current = read(self.out/'manifest.json')
        same_policy = (previous.get('policy') == current['policy']
                       and previous.get('fast_search', False) == current['fast_search']
                       and previous.get('play_mode', 'full') == current['play_mode']
                       and previous.get('objective') == current['objective']
                       and previous.get('external_guide') == self.guide
                       and all(previous.get('source_sha256', {}).get(name) == current['source_sha256'][name]
                               for name in ('campaign_model.py', 'gap_jump.py', 'run.py', 'lookahead.py')))
        for field in ('rom_sha256', 'engine_hashes', 'config_sha256', 'watches'):
            if previous[field] != current[field]:
                raise DeterminismError(f'Resume {field} mismatch')
        store = read(source/'checkpoint_store.json')
        self.summary.update(read(source/'summary.json'))
        self.summary.update(status='resuming', resumed_from=str(source))
        self.summary.pop('error', None)
        self.summary.pop('video_error', None)
        self.inherited_wall = self.summary['wall_seconds']
        self.deadline = float('inf') if self.args.until_complete else self.started+max(0, self.args.wall_seconds-self.inherited_wall)
        self.nodes, self.current = store['nodes'], store['current_node']
        self.route, self.failures, self.visited = store['route'], store['failures'], store['visited']
        for node in self.nodes.values():
            folder = self.out/'nodes'/node['id']
            folder.mkdir(parents=True)
            for name in ('state.State', 'state.json'):
                shutil.copyfile(Path(node['folder'])/name, folder/name)
            node['folder'] = str(folder)
            # A budget-only continuation must not forget branches already rejected.
            if not same_policy:
                node['previous_policy_bans'] = node['banned']
                node['banned'], node['tier'] = {}, 0
        for name in ('initial.State', 'initial.json'):
            shutil.copyfile(source/name, self.out/name)
        ancestors = []
        node = self.nodes[self.current]
        while node['parent'] is not None:
            parent = self.nodes[node['parent']]
            ancestors.append({'from': parent['state'], 'action': node['via'], 'to': node['state']})
            node = parent
        self.history = list(reversed(ancestors))
        self.route = self.route[:self.nodes[self.current]['route_length']]
        self.seen_cells = set(store.get('seen_cells', self.visited))
        self.recovery_counts = store.get('recovery_counts', {})
        self.no_novelty = self.summary.get('attempts_without_new_cell', 0) if same_policy else 0
        if self.guide and store.get('novelty_policy') != 'guide_scoped_v1':
            self.no_novelty = 0
        self.videos = read(source/'videos.json')
        for video in self.videos:
            video['url'] = os.path.relpath(video['file'], self.out).replace('\\', '/')
        self.event('campaign_resumed', source=str(source), checkpoint=self.current,
                   cumulative_budget=True, policy_changed=not same_policy, allow_warps=self.args.allow_warps)

    def event(self, kind, **fields):
        data = {'event': kind, 'utc': datetime.now(timezone.utc).isoformat(), **fields}
        with (self.out/'events.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(data)+'\n')
        print(json.dumps(data), flush=True)

    def persist(self):
        self.summary['wall_seconds'] = round(self.inherited_wall+time.monotonic()-self.started, 3)
        self.summary['attempts_without_new_cell'] = self.no_novelty
        if self.state:
            self.summary['current'] = describe(self.state)
        write(self.out/'progress.json', self.summary)
        write(self.out/'checkpoint_store.json', {'current_node': self.current, 'nodes': self.nodes,
            'failures': self.failures, 'visited': self.visited, 'route': self.route,
            'seen_cells': sorted(self.seen_cells), 'recovery_counts': self.recovery_counts,
            'novelty_policy': 'guide_scoped_v1'})
        write(self.out/'videos.json', self.videos)

    def launch_player(self, checkpoint=None):
        self.summary['chapters'] += 1
        self.chapter = self.out/'chapters'/f"{self.summary['chapters']:04d}"
        self.player = campaign_backend(self.chapter, True)
        result = self.player.launch(self.rom)
        if not result.ok:
            raise RuntimeError(result.error)
        if checkpoint:
            shutil.copyfile(Path(checkpoint)/'state.State', self.player.states_dir/'resume.State')
            self.state = self.player.load_state_named('resume', neutralize_framebuffer=False)
            if fingerprint(self.state) != fingerprint(read(Path(checkpoint)/'state.json')):
                raise DeterminismError('Main checkpoint RAM mismatch')
        else:
            self.state = self.player.press(['start'], 1, 1)
            for _ in range(60):
                self.state = self.player.advance(8)
                if playable(values(self.state)) and values(self.state)['py'] > 100:
                    break
            else:
                raise RuntimeError('Could not enter playable 1-1')
        self.chapter_start = self.state['timeline_frame']
        self.chapter_events = []
        self.chapter_first_decision = self.summary['decisions']
        write(self.chapter/'initial.json', self.state)
        self.publish_live('ready', event='emulator_ready')

    def finish_chapter(self):
        if not self.player:
            return
        last_timeline = (self.state or {}).get('timeline_frame', self.chapter_start)
        self.player.close()
        self.player = None
        if self.chapter_events:
            video = finalize(self.chapter, self.chapter_events, self.chapter_start, last_timeline)
            if video:
                video['url'] = str(Path(video['file']).relative_to(self.out)).replace('\\', '/')
                self.videos.append(video)
                write(self.out/'videos.json', self.videos)
                self.event('video_ready', **video)

    def checkpoint(self, parent=None, via=None):
        node_id = f"{len(self.nodes)+1:06d}"
        folder = self.out/'nodes'/node_id
        folder.mkdir(parents=True)
        self.player.save_state_named('campaign_checkpoint')
        shutil.copyfile(self.player.states_dir/'campaign_checkpoint.State', folder/'state.State')
        write(folder/'state.json', self.state)
        node = {'id': node_id, 'parent': parent, 'via': via, 'folder': str(folder),
            'state': describe(self.state), 'route_length': len(self.route), 'history_length': len(self.history),
            'banned': {}, 'tier': 0}
        self.nodes[node_id] = node
        self.current = node_id
        if parent is None:
            shutil.copyfile(folder/'state.State', self.out/'initial.State')
            write(self.out/'initial.json', self.state)
        self.persist()
        return node

    def restore(self, node_id, reason):
        self.publish_live('rewinding', event='checkpoint_restore', reason=reason, target_node=node_id)
        node = self.nodes[node_id]
        prior = describe(self.state)
        shutil.copyfile(Path(node['folder'])/'state.State', self.player.states_dir/'restore.State')
        self.state = self.player.load_state_named('restore', neutralize_framebuffer=False)
        if fingerprint(self.state) != fingerprint(read(Path(node['folder'])/'state.json')):
            raise DeterminismError('Restored checkpoint does not match original RAM')
        self.current = node_id
        self.route = self.route[:node['route_length']]
        self.history = self.history[:node['history_length']]
        self.summary['rewinds'] += 1
        if hasattr(self, 'stall_watch'):
            self.stall_watch.reset()
        self.event('checkpoint_restored', node=node_id, reason=reason, before=prior,
                   after=describe(self.state), timeline_frame=self.state['timeline_frame'])
        self.persist()
        self.publish_live('ready', event='checkpoint_restored', reason=reason)

    def remember_failure(self, node, action, reason, actual=None):
        failure = {'node': node['id'], 'from': node['state'], 'action': action, 'reason': reason,
                   'outcome': actual or describe(self.state), 'decision': self.summary['decisions']}
        self.failures.append(failure)
        node['banned'][action] = failure
        self.event('failed_branch', **failure)

    def backtrack(self, reason):
        node = self.nodes[self.current]
        if node['parent'] is None:
            self.summary['status'] = 'search_exhausted'
            return False
        parent = self.nodes[node['parent']]
        self.remember_failure(parent, node['via'], reason)
        self.restore(parent['id'], reason)
        return True

    def death_checkpoint(self, node, choice, failure, segments):
        previous = node['banned'].get(choice, {})
        repeated = previous.get('executed_segments') == segments and previous.get('reason') == failure
        self.remember_failure(node, choice, failure)
        node['banned'][choice]['executed_segments'] = segments
        target = node
        if describe(self.state)['timer'] == 0 or values(self.state).get('timer_expired'):
            while target['parent'] and self.nodes[target['parent']]['state']['level'] == node['state']['level']:
                target = self.nodes[target['parent']]
            failure = 'game_timer_expired_restart_level'
        elif repeated and node['parent']:
            # Same checkpoint + same inputs + same actual death is a replay,
            # not a fresh attempt. Move before the committed fatal branch.
            target = self.nodes[node['parent']]
            self.remember_failure(target, node['via'], 'repeated_actual_death_from_child')
            # Adjacent nodes can be tiny prefixes of the same doomed jump.
            # Go far enough back to change its takeoff, not into another copy
            # of the committed trajectory. This is triggered by actual deaths.
            branch = node
            while (target['parent'] and self.nodes[target['parent']]['state'].get('level') == node['state'].get('level')
                   and ((target['state'].get('phase') not in (None, 'grounded')
                         and not target['state'].get('swimming'))
                        or (target['state']['area'] == node['state']['area']
                            and abs(target['state']['x']-node['state']['x']) < 128))):
                branch = target
                target = self.nodes[target['parent']]
            if branch is not node:
                self.remember_failure(target, branch['via'], 'repeated_actual_death_from_branch')
            failure = 'repeated_actual_death_replan_earlier'
        return target, failure

    def recover_plateau(self):
        """Discard a spatial branch, not hundreds of timer/velocity variants."""
        start = self.nodes[self.current]
        region = f"{start['state']['area']}:{start['state']['x']//128}"
        count = self.recovery_counts.get(region, 0)+1
        self.recovery_counts[region] = count
        distance = min(1024, 128*2**min(count-1, 3))
        child = start
        while child['parent'] is not None:
            parent = self.nodes[child['parent']]
            a, b = parent['state'], start['state']
            if a['area'] != b['area'] or abs(a['x']-b['x']) >= distance or parent['parent'] is None:
                self.remember_failure(parent, child['via'], 'spatial_branch_exhausted', b)
                self.event('strategic_backtrack', from_node=start['id'], to_node=parent['id'],
                           attempts_without_novelty=self.no_novelty, distance=distance)
                self.restore(parent['id'], 'spatial_branch_exhausted')
                self.no_novelty = 0
                return True
            child = parent
        self.summary['status'] = 'search_exhausted'
        if getattr(getattr(self, 'args', None), 'until_complete', False):
            self.restore(start['id'], 'physical_stall_at_level_entry')
            return True
        return False

    def move(self, segments, action, controller, expected=None):
        self.summary['moves'] += 1
        move_id = self.summary['moves']
        folder = self.out/'moves'/f'{move_id:06d}'
        folder.mkdir(parents=True)
        before = self.state
        write(folder/'before.json', before)
        intent = {'action': action, 'controller': controller, 'segments': segments,
                  'decision': self.summary['decisions'], 'node': self.current}
        write(folder/'action.json', intent)
        actual = []
        trace = []
        baseline_lives = values(before)['lives']
        airborne = values(before)['player_state'] != 0
        elapsed = 0
        for i, segment in enumerate(segments):
            self.state = issue(self.player, segment)
            self.publish_live('human' if controller == 'Human' else 'executing', buttons=segment['buttons'], action=action,
                controller=controller, segment_frames=segment['frames'], move=move_id)
            actual.append(segment)
            elapsed += segment['frames']
            digest = fingerprint(self.state)
            same = expected is None or digest == expected[i]['ram_sha256']
            if expected is not None:
                self.summary['parity_checks'] += 1
            trace.append({'state': describe(self.state), 'ram_sha256': digest, 'matches_prediction': same if expected is not None else None})
            self.summary['frames_executed'] += segment['frames']
            self.summary['ram_samples'] = self.summary.get('ram_samples', 0)+1
            write(folder/'trace.json', trace)
            if not same:
                raise DeterminismError(f'Execution diverged from forecast in move {move_id}')
            if death_reason(values(self.state), baseline_lives) or won(values(self.state)):
                break
            if controller == 'Jev Turbo':
                r = values(self.state)
                airborne |= r['player_state'] != 0
                if (not playable(r) or (airborne and r['player_state'] == 0 and elapsed >= 12 and not r['swimming'])
                        or self.control.read()[0] == 'human' or (self.out/'STOP').exists()):
                    break
            if controller == 'automatic_transition' and playable(values(self.state)):
                break
        write(folder/'after.json', self.state)
        event = {'controller': controller, 'action': action, 'decision': self.summary['decisions'],
            'level': level_id(values(before)), 'frames': sum(s['frames'] for s in actual),
            'timeline_start': before['timeline_frame'], 'timeline_end': self.state['timeline_frame'],
            'rewinds': self.summary['rewinds'], 'move': move_id, 'after': describe(self.state)}
        self.chapter_events.append(event)
        self.route.append({'move': move_id, 'action': action, 'controller': controller,
            'segments': actual, 'trajectory': trace, 'before_ram_sha256': fingerprint(before),
            'after_ram_sha256': fingerprint(self.state)})
        self.event('move', **event)
        self.publish_live('human' if controller == 'Human' else 'ready', buttons=actual[-1]['buttons'] if controller == 'Human' and actual else (), event='move_completed', action=action, controller=controller,
            frames=sum(s['frames'] for s in actual))
        return death_reason(values(self.state), baseline_lives)

    def transition(self):
        elapsed = 0
        while not playable(values(self.state)) and not won(values(self.state)):
            if self.control.read()[0] == 'human' or (self.out/'STOP').exists():
                return None
            if time.monotonic() >= self.deadline:
                raise TimeoutError('Campaign wall budget reached')
            failure = death_reason(values(self.state))
            if failure:
                return failure
            if elapsed >= 3600:
                return 'transition_stalled'
            failure = self.move([{'buttons': [], 'frames': 4} for _ in range(30)],
                                'wait_for_game_control', 'automatic_transition')
            elapsed += self.chapter_events[-1]['frames']
            self.persist()
            if failure:
                return failure
        return None

    def ask(self, client, payload, folder):
        self.publish_live('waiting_for_jev', event='request_sent',
            request_url=str((folder/'request.json').relative_to(self.out)).replace('\\', '/'))
        outage_deadline = self.deadline if self.args.until_complete else min(self.deadline, time.monotonic()+900)
        import itertools
        for batch in itertools.count(1):
            self.control.check()
            if time.monotonic() >= outage_deadline:
                raise TimeoutError('API unavailable beyond bounded recovery window')
            http_folder = folder/f'http_{batch:03d}'
            http_folder.mkdir()
            try:
                code, body, latency = query(client, payload, http_folder, self.key, outage_deadline, self.summary, interrupt=self.control.check)
                if code == 200:
                    answer = json.loads(body)
                    write(folder/'response.json', answer)
                    shutil.copyfile(http_folder/'response.txt', folder/'response.txt')
                    write(folder/'transport.json', {'http_round': batch, 'latency_ms': latency, 'status_code': code})
                    for key in ('input_tokens', 'output_tokens'):
                        self.summary[key] += answer.get('usage', {}).get(key, 0)
                    self.summary['actual_model'] = answer.get('model')
                    self.publish_live('ready', event='response_received',
                        response_url=str((folder/'response.json').relative_to(self.out)).replace('\\', '/'),
                        choice=answer['answers']['maneuver']['choice'], latency_ms=latency,
                        confidence=answer['answers']['maneuver'].get('confidence'))
                    return answer
                if code not in {429, 500, 502, 503, 504, 529}:
                    raise NonretryableAPIError(f'Nonretryable API status {code}')
                reason = f'HTTP {code}'
            except httpx.TransportError as exc:
                reason = type(exc).__name__
            self.summary['status'] = 'waiting_for_api'
            self.event('api_wait', reason=reason, decision=self.summary['decisions'], round=batch)
            self.publish_live('waiting_for_jev', event='api_wait', reason=reason)
            self.persist()
            pause_until = min(time.monotonic()+30, outage_deadline)
            while time.monotonic() < pause_until:
                self.control.check()
                if (self.out/'STOP').exists():
                    return None
                time.sleep(.1)
        raise RuntimeError('API recovery attempts exhausted')

    def export_route(self):
        write(self.out/'winning_route.json', {'initial_state': str(self.out/'initial.State'),
            'initial_ram': str(self.out/'initial.json'), 'moves': self.route,
            'status': self.summary['status'], 'completed_levels': self.summary['completed_levels'],
            'watches': WATCH, 'rom_sha256': hashlib.sha256(self.rom.read_bytes()).hexdigest(),
            'engine_hashes': read(self.out/'manifest.json').get('engine_hashes', {})})

    def human_play(self):
        """Use the same recorded player and route; no API or shadow inputs."""
        if self.control.read()[0] != 'human':
            return False
        self.summary['human_interventions'] = self.summary.get('human_interventions', 0)+1
        self.event('control_changed', controller='Human', state=describe(self.state))
        self.publish_live('human', event='control_changed', controller='Human')
        self.stall_watch.reset()
        checkpoint_frame = self.state['timeline_frame']
        initial = describe(self.state)
        try:
            while self.control.read()[0] == 'human':
                if (self.out/'STOP').exists() or time.monotonic() >= self.deadline:
                    break
                if shutil.disk_usage(self.out).free < 2*1024**3:
                    self.summary['status'] = 'disk_space_limit'
                    break
                mode, buttons, fresh = self.control.read()
                if mode != 'human':
                    break
                if not fresh:
                    self.publish_live('human', controller='Human', reason='Browser disconnected: inputs released, game paused')
                    time.sleep(.1)
                    continue
                started = time.monotonic()
                old = self.state
                failure = self.move([{'buttons': buttons, 'frames': 4}], 'manual_input', 'Human')
                if failure:
                    self.summary['deaths'] += 1
                    target = self.nodes[self.current]
                    if describe(self.state)['timer'] == 0 or values(self.state).get('timer_expired'):
                        while target['parent'] and self.nodes[target['parent']]['state']['level'] == target['state']['level']:
                            target = self.nodes[target['parent']]
                    self.event('human_death', reason=failure)
                    self.restore(target['id'], 'human_death: '+failure)
                    checkpoint_frame = self.state['timeline_frame']
                elif won(values(self.state)):
                    if '8-4' not in self.summary['completed_levels']:
                        self.summary['completed_levels'].append('8-4')
                    self.summary['status'] = 'game_completed_with_checkpoints'
                    snapshot(self.player, self.out, 'victory', self.state)
                    self.player.save_state_named('victory')
                    self.event('game_completed', controller='Human', proof=describe(self.state), ram=values(self.state))
                    return True
                elif rank(values(self.state)) > rank(values(old)):
                    if rank(values(self.state)) == rank(values(old))+1:
                        level = level_id(values(old))
                        if level not in self.summary['completed_levels']:
                            self.summary['completed_levels'].append(level)
                        self.event('level_completed', level=level, controller='Human')
                    else:
                        warp = {'from': level_id(values(old)), 'to': level_id(values(self.state)), 'controller': 'Human'}
                        self.summary.setdefault('warps_used', []).append(warp)
                        self.event('warp_used', **warp)
                if self.state['timeline_frame']-checkpoint_frame >= 100 and playable(values(self.state)):
                    self.history.append({'from': initial, 'action': 'Human keyboard input', 'to': describe(self.state)})
                    self.checkpoint(parent=self.current, via='Human keyboard input')
                    initial = describe(self.state)
                    checkpoint_frame = self.state['timeline_frame']
                if self.state['timeline_frame']-self.chapter_start >= 3000:
                    self.checkpoint(parent=self.current, via='Human keyboard input')
                    self.finish_chapter()
                    self.launch_player(self.nodes[self.current]['folder'])
                    checkpoint_frame = self.state['timeline_frame']
                time.sleep(max(0, .08-(time.monotonic()-started)))
        finally:
            self.history.append({'from': initial, 'action': 'Human keyboard input; resume from this actual state', 'to': describe(self.state)})
            self.checkpoint(parent=self.current, via='Human keyboard input')
            self.export_route()
            self.stall_watch.reset()
            self.event('control_changed', controller='Jev', state=describe(self.state))
            self.publish_live('ready', event='control_changed', controller='Jev')
        return False

    def play(self):
        self.launch_player(self.nodes[self.current]['folder'] if self.current else None)
        if self.args.resume_node:
            self.event('external_assistance', guide_id=self.guide['id'] if self.guide and self.nodes[self.args.resume_node]['state']['level'] in self.guide.get('levels', {}) else None,
                       reason=self.args.resume_reason,
                       from_node=self.current, to_node=self.args.resume_node)
            self.restore(self.args.resume_node, self.args.resume_reason)
            self.args.resume_node = None
        if not self.args.turbo:
            self.shadow = campaign_backend(self.out/'shadow_runs'/f"{self.summary['technical_restarts']:03d}", fast=self.args.fast)
            result = self.shadow.launch(self.rom)
            if not result.ok:
                raise RuntimeError(result.error)
        if self.current is None:
            self.checkpoint()
        with httpx.Client(follow_redirects=False, headers={'Authorization': 'Bearer '+self.key}) as client:
            while time.monotonic() < self.deadline:
                if (self.out/'STOP').exists():
                    self.summary['status'] = 'user_stop_file'
                    break
                if self.human_play():
                    break
                if (self.out/'STOP').exists():
                    self.summary['status'] = 'user_stop_file'
                    break
                if self.args.trial_level and level_id(values(self.state)) != self.args.trial_level:
                    self.summary['status'] = ('trial_level_completed' if self.args.trial_level in self.summary['completed_levels'] else 'trial_scope_exited')
                    break
                if self.args.trial_decisions and self.summary['decisions']-self.trial_start_decision >= self.args.trial_decisions:
                    self.summary['status'] = 'trial_decision_limit'
                    break
                if not self.args.until_complete and self.summary['decisions'] >= self.args.max_decisions:
                    self.summary['status'] = 'decision_limit'
                    break
                if not self.args.until_complete and self.summary['rewinds'] >= self.args.max_rewinds:
                    self.summary['status'] = 'rewind_limit'
                    break
                if not self.args.until_complete and self.summary['input_tokens'] >= self.args.max_input_tokens:
                    self.summary['status'] = 'token_limit'
                    break
                if shutil.disk_usage(self.out).free < 2*1024**3:
                    self.summary['status'] = 'disk_space_limit'
                    break
                if (self.chapter_events and self.summary['decisions']-self.chapter_first_decision >= self.args.chapter_decisions):
                    self.finish_chapter()
                    self.launch_player(self.nodes[self.current]['folder'])
                self.summary['status'] = 'running'
                node = self.nodes[self.current]
                hint = guide_context(describe(self.state), self.guide)
                if hint and not self.args.turbo:
                    minimum = hint['current_navigation_goal'].get('minimum_search_tier', 0)
                    node['tier'] = max(node['tier'], max(0, min(2, int(minimum))))
                self.summary['decisions'] += 1
                self.no_novelty += 1
                decision = self.summary['decisions']
                folder = self.out/'decisions'/f'{decision:06d}'
                folder.mkdir(parents=True)
                cycle_start = time.monotonic()
                search_seconds = 0.0
                self.publish_live('preparing' if self.args.turbo else 'simulating', event='direct_options' if self.args.turbo else 'search_started', tier=node['tier'])
                snapshot(self.player, folder, 'before', self.state)
                shutil.copyfile(folder/'before.png', self.out/'latest.png')
                build_request = turbo.request if self.args.turbo else make_request
                while True:
                    profile = 'turbo' if self.args.turbo else 'fast' if self.args.fast else 'full'
                    cache = Path(node['folder'])/f"forecast_{profile}_tier_{node['tier']}.json"
                    if self.args.turbo:
                        forecasts = turbo.options(self.state)
                        write(folder/'direct_options.json', forecasts)
                    elif cache.exists():
                        forecasts = read(cache)
                        write(folder/'forecasts.json', forecasts)
                    else:
                        t0 = time.monotonic()
                        try:
                            forecasts = CampaignPlanner(self.shadow, self.args.allow_warps, fast=self.args.fast).forecast(Path(node['folder']), self.state,
                                folder, node['tier'], self.deadline, interrupt=self.control.check)
                        except ManualRequested:
                            forecasts = None
                            break
                        elapsed = time.monotonic()-t0
                        search_seconds += elapsed
                        self.summary['forecast_seconds'] += elapsed
                        write(cache, forecasts)
                    payload = build_request(self.state, forecasts, node, self.failures,
                        self.history, self.visited, self.summary['completed_levels'], self.args.allow_warps, self.guide)
                    attach_ascii(payload, folder/'before.png', self.args.ascii_level, folder)
                    write(folder/'request.json', payload)
                    if (node['tier'] >= 2 or self.args.turbo or
                            has_useful_option(self.state, forecasts, payload['questions']['maneuver']['criteria'])):
                        break
                    node['tier'] += 1
                    self.event('search_expanded', node=node['id'], tier=node['tier'])
                if forecasts is None or self.control.read()[0] == 'human':
                    continue
                if not payload['questions']['maneuver']['criteria']:
                    payload = build_request(self.state, forecasts, node, self.failures,
                        self.history, self.visited, self.summary['completed_levels'],
                        self.args.allow_warps, self.guide, allow_risky=True)
                    attach_ascii(payload, folder/'before.png', self.args.ascii_level, folder)
                    write(folder/'request.json', payload)
                    self.event('risk_fallback', reason='All direct recipes have failed here; Jev retries with failure memory.' if self.args.turbo else 'No safe untried candidate; continue actual play, do not rewind a prediction.')
                self.persist()
                api_start = time.monotonic()
                try:
                    answer = self.ask(client, payload, folder)
                except ManualRequested:
                    continue
                if answer is None or self.control.read()[0] == 'human' or (self.out/'STOP').exists():
                    self.event('decision_discarded', reason='Control handoff or stop; no model inputs executed')
                    continue
                api_seconds = time.monotonic()-api_start
                choice = answer['answers']['maneuver']['choice']
                if choice not in payload['questions']['maneuver']['criteria']:
                    raise NonretryableAPIError('Invalid Jev choice')
                old = self.state
                chosen = forecasts[choice]
                write(folder/'chosen.json', {'choice': choice, 'outcome': chosen['outcome']})
                frames_before = self.summary['frames_executed']
                failure = self.move(chosen['segments'], choice,
                    'Jev Turbo' if self.args.turbo else 'Jev+ASCII' if 'screen_ascii' in payload['state'] else 'Jev', chosen['trajectory'])
                if not failure:
                    failure = self.transition()
                write(folder/'timing.json', {'profile': profile, 'search_seconds': search_seconds,
                    'api_seconds': api_seconds, 'cycle_seconds_through_execution': time.monotonic()-cycle_start,
                    'candidates': len(forecasts), 'executed_frames': self.summary['frames_executed']-frames_before,
                    'failure': failure})
                if failure:
                    if death_reason(values(self.state), values(old)['lives']):
                        self.summary['deaths'] += 1
                    snapshot(self.player, folder, 'failed', self.state)
                    target, failure = self.death_checkpoint(node, choice, failure, chosen['segments'])
                    self.restore(target['id'], failure)
                    continue
                if won(values(self.state)):
                    self.summary['completed_levels'].append('8-4')
                    self.summary['status'] = 'game_completed_with_checkpoints'
                    snapshot(self.player, self.out, 'victory', self.state)
                    self.player.save_state_named('victory')
                    self.event('game_completed', proof=describe(self.state), ram=values(self.state))
                    break
                now = describe(self.state)
                old_r, new_r = values(old), values(self.state)
                if not self.args.allow_warps and rank(new_r) > rank(old_r)+1:
                    self.remember_failure(node, choice, 'unexpected_level_skip')
                    self.restore(node['id'], 'unexpected_level_skip')
                    continue
                played_frames = self.summary['frames_executed']-frames_before
                stalled = self.stall_watch.observe(describe(old), now, played_frames)
                if stalled:
                    self.event('physical_stall', executed_window_frames=self.args.stuck_frames,
                               pixel_tolerance=24, state=now)
                    if not self.recover_plateau():
                        break
                    continue
                level_changed = rank(new_r) > rank(old_r)
                warped = rank(new_r) > rank(old_r)+1
                if warped:
                    transition = {'from': level_id(old_r), 'to': level_id(new_r), 'decision': decision}
                    self.summary.setdefault('warps_used', []).append(transition)
                    self.event('warp_used', **transition)
                if level_changed and not warped and level_id(old_r) not in self.summary['completed_levels']:
                    self.summary['completed_levels'].append(level_id(old_r))
                    self.event('level_completed', level=level_id(old_r), entered=level_id(new_r))
                self.history.append({'from': describe(old), 'action': choice, 'to': now})
                self.visited[cell(self.state)] = self.visited.get(cell(self.state), 0)+1
                novelty = novelty_cell(self.state, self.guide)
                if self.args.trial_level:
                    novelty = f'trial:{self.args.trial_level}:{novelty}'
                if novelty not in self.seen_cells or level_changed:
                    self.seen_cells.add(novelty)
                    self.no_novelty = 0
                snapshot(self.player, folder, 'after', self.state)
                shutil.copyfile(folder/'after.png', self.out/'latest.png')
                self.checkpoint(parent=node['id'], via=choice)
                self.export_route()
                self.event('decision_completed', decision=decision, choice=choice,
                           state=now, rewinds=self.summary['rewinds'])
                if level_changed:
                    self.finish_chapter()
                    self.launch_player(self.nodes[self.current]['folder'])
            else:
                self.summary['status'] = 'wall_limit'

    def run(self):
        try:
            while True:
                try:
                    self.play()
                    break
                except (TimeoutError, DeterminismError, NonretryableAPIError):
                    raise
                except Exception as exc:
                    if 'determinism_failure' in str(exc):
                        raise DeterminismError(str(exc)) from exc
                    if self.current is None or self.summary['technical_restarts'] >= 5 or time.monotonic() >= self.deadline:
                        raise
                    self.summary['technical_restarts'] += 1
                    self.event('automatic_infrastructure_restart', error=str(exc).replace(self.key, '[REDACTED]'),
                               checkpoint=self.current, restart=self.summary['technical_restarts'])
                    if self.shadow:
                        self.shadow.close()
                        self.shadow = None
                    try:
                        self.finish_chapter()
                    except Exception as video_exc:
                        self.event('video_finalize_error', error=str(video_exc))
                    node = self.nodes[self.current]
                    self.route = self.route[:node['route_length']]
                    self.history = self.history[:node['history_length']]
                    self.summary['rewinds'] += 1
                    time.sleep(min(10, max(0, self.deadline-time.monotonic())))
        except TimeoutError as exc:
            self.summary['status'] = 'bounded_timeout'
            self.summary['error'] = str(exc)
        except Exception as exc:
            self.summary['status'] = 'determinism_failure' if isinstance(exc, DeterminismError) else 'technical_failure'
            self.summary['error'] = {'type': type(exc).__name__, 'message': str(exc).replace(self.key, '[REDACTED]')}
            self.event('error', **self.summary['error'])
        finally:
            try:
                if self.state:
                    write(self.out/'final.json', self.state)
                self.export_route()
                self.persist()
            finally:
                try:
                    if self.shadow:
                        self.shadow.close()
                finally:
                    try:
                        self.finish_chapter()
                    except Exception as exc:
                        self.summary['video_error'] = str(exc).replace(self.key, '[REDACTED]')
            write(self.out/'summary.json', self.summary)
            write(self.out/'progress.json', self.summary)
            self.event('finished', **self.summary)
            self.publish_live('stopped', event='session_finished', reason=self.summary['status'])
        if self.summary['status'] == 'game_completed_with_checkpoints':
            with (self.out/'clean_replay.log').open('w', encoding='utf-8') as log:
                process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('campaign_replay.py')),
                    '--campaign', str(self.out), '--out', str(self.out/'clean_replay')],
                    stdout=log, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            write(self.out/'clean_replay_process.json', {'pid': process.pid, 'api_calls': 0})
        return 0 if self.summary['status'] == 'game_completed_with_checkpoints' else 2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--until-complete', action='store_true', help='No time/decision/restore/token cap; continue API outage retries until victory or STOP (usage continues)')
    parser.add_argument('--wall-seconds', type=float, default=28800)
    parser.add_argument('--max-decisions', type=int, default=2000)
    parser.add_argument('--max-rewinds', type=int, default=500)
    parser.add_argument('--max-input-tokens', type=int, default=8000000)
    parser.add_argument('--chapter-decisions', type=int, default=30)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--turbo', action='store_true', help='Direct RAM decisions without lookahead: shorter pauses, no predicted-death filter')
    modes.add_argument('--full', action='store_true', help='Use the full initial lookahead instead of the default fast mode')
    modes.add_argument('--fast', action='store_true', help='Fewer initial candidates and no shadow PNGs; same RAM parity checks, full fallback search')
    parser.add_argument('--stuck-frames', type=int, default=600, help='Executed frames in one 24px area before stall recovery; 0 disables it')
    parser.add_argument('--resume', type=Path, help='Fork a stopped campaign; budgets remain cumulative')
    parser.add_argument('--allow-warps', action='store_true')
    parser.add_argument('--hint-file', type=Path, help='Explicit user-authorized walkthrough, scoped by level')
    parser.add_argument('--resume-node', help='Optional ancestor checkpoint for a logged intervention')
    parser.add_argument('--resume-reason', default='user_requested_checkpoint_restore')
    parser.add_argument('--ascii-level', help='Add screenshot-derived ASCII only on this level')
    parser.add_argument('--trial-level', help='Stop the experiment after leaving this level')
    parser.add_argument('--trial-decisions', type=int, default=0, help='Optional new-decision limit, excluding inherited work')
    args = parser.parse_args()
    args.fast = not (args.turbo or args.full)
    if args.stuck_frames < 0:
        parser.error('stuck-frames must be nonnegative')
    if not 1 <= args.wall_seconds <= 86400 or not 1 <= args.max_decisions <= 10000 or not 1 <= args.max_rewinds <= 2000:
        parser.error('Invalid campaign bounds')
    if not 1 <= args.chapter_decisions <= 100 or args.max_input_tokens < 1:
        parser.error('Invalid video/token bounds')
    if args.trial_decisions < 0 or args.trial_decisions > 1000:
        parser.error('Invalid trial decision limit')
    key = os.environ.get('TYPESAFE_API_KEY')
    if not key:
        parser.error('TYPESAFE_API_KEY is required')
    configure()
    return Campaign(args, key).run()


if __name__ == '__main__':
    raise SystemExit(main())
