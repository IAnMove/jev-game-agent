"""Bounded Jev experiment: RAM observations, direct inputs, complete audit log.

Uses the frozen BizHawk backend without modifying it. No savestate search.
RAM reference: https://github.com/nwoeanhinnogaehr/smb-assembler/blob/master/smbdis.asm
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

from runtime import ROOT, ENGINE
sys.path.insert(0, str(ENGINE / "src"))
from retro_llm_pilot.backends.bizhawk import BizHawkBackend
from retro_llm_pilot.config import LuaBridgeConfig
import httpx
from prompt_experiment import relative_state, atomic_questions, terrain_state, controller_questions

WATCH = {"routine": 0x0e, "mode": 0x770, "mode_task": 0x772,
         "player_state": 0x1d, "px": 0x86, "page": 0x6d,
         "py": 0xce, "yhigh": 0xb5, "vx": 0x57, "vy": 0x9f,
         "camera_page": 0x71a, "camera_x": 0x71c,
         "size": 0x754, "power": 0x756, "lives": 0x75a,
         "level": 0x75c, "world": 0x75f, "area": 0x760,
         "death_music": 0x712}
for i in range(6):
    for label, address in {"active": 0x0f, "kind": 0x16, "state": 0x1e,
                           "page": 0x6e, "x": 0x87, "y": 0xcf,
                           "yhigh": 0xb6, "vx": 0x58}.items():
        WATCH[f"e{i}_{label}"] = address + i
for i in range(416):
    WATCH[f"tile{i}"] = 0x500 + i

ACTIONS = {"right": ["right"], "run_right": ["right", "b"],
           "jump_right": ["right", "a"], "run_jump_right": ["right", "a", "b"],
           "left": ["left"], "jump_left": ["left", "a"],
           "jump": ["a"], "neutral": []}
DESCRIPTIONS = {"right": "Hold Right; walk right and release jump.",
                "run_right": "Hold Right+B; run right and release jump.",
                "jump_right": "Hold Right+A; jump right, or sustain an existing jump.",
                "run_jump_right": "Hold Right+B+A; running jump right, or sustain it.",
                "left": "Hold Left; brake rightward momentum or move left; release jump.",
                "jump_left": "Hold Left+A; jump or sustain jump while steering left.",
                "jump": "Hold A without a direction; jump or sustain jump.",
                "neutral": "Release all buttons; momentum and gravity still apply."}


def write(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    # The read-only HTTP viewer can briefly hold the destination open on Windows.
    for attempt in range(40):
        try:
            temp.replace(path)
            return
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.025)


def signed(v):
    return v if v < 128 else v - 256


def values(state):
    raw = state.get("ram", {})
    if set(WATCH) - set(raw):
        raise ValueError("Missing RAM watches; observation is not trustworthy")
    return {k: raw[k]["value"] for k in WATCH}


class BridgeTimeoutError(RuntimeError):
    """An unacknowledged emulator action; restart from a verified checkpoint."""


class ReliableBackend(BizHawkBackend):
    def _send_action(self, payload, timeout=None):
        try:
            state = super()._send_action(payload, timeout)
        except TimeoutError as exc:
            # Do not resend an action with an unknown outcome. The campaign's
            # infrastructure recovery closes this emulator and loads a checkpoint.
            raise BridgeTimeoutError(str(exc)) from exc
        if payload.get("type") == "quit":
            return state
        expected_id = self._next_action_id - 1
        deadline = time.monotonic() + 3
        rereads = 0
        while not state or state.get("last_action_id") != expected_id or set(WATCH) - set(state.get("ram", {})):
            if time.monotonic() >= deadline:
                raise RuntimeError(f"No complete state for acknowledged action {expected_id}")
            # Re-read only: never re-send a control command that was acknowledged.
            time.sleep(0.01)
            state = self.read_state()
            rereads += 1
        if rereads:
            with (self.run_dir / "state_rereads.jsonl").open("a", encoding="utf-8") as log:
                log.write(json.dumps({"action_id": expected_id, "rereads": rereads}) + "\n")
        return state


def observe(state, previous=None, buttons=None):
    r = values(state)
    x = r["page"] * 256 + r["px"]
    y = (r["yhigh"] - 1) * 256 + r["py"]
    camera = r["camera_page"] * 256 + r["camera_x"]
    # SMB uses a 32-pixel player reference box, even for small Mario.
    top = y + (16 if r["size"] else 0)
    enemies = []
    for i in range(6):
        ex = r[f"e{i}_page"] * 256 + r[f"e{i}_x"]
        ey = (r[f"e{i}_yhigh"] - 1) * 256 + r[f"e{i}_y"]
        if r[f"e{i}_active"] and camera - 16 <= ex < camera + 256:
            kind = r[f"e{i}_kind"]
            enemies.append({"slot": i, "kind": {6: "goomba", 0: "green_koopa",
                            1: "red_koopa", 0x24: "powerup", 0x30: "flagpole"}.get(kind, f"object_{kind}"),
                            "x": ex, "y": ey, "width": 16,
                            "state_raw": r[f"e{i}_state"], "vx_raw": signed(r[f"e{i}_vx"])})
    # Two 16x13 block buffers alternate by world page; row zero starts at y=32.
    terrain = []
    flags = []
    hidden_blocks = []
    for row in range(13):
        segment = None
        for col in range(camera // 16, (camera + 255) // 16 + 1):
            index = ((col // 16) % 2) * 208 + row * 16 + col % 16
            tile = r[f"tile{index}"]
            # Coins and climbable pole/vine tiles are not solid obstacles.
            solid = tile != 0 and tile not in (0x24, 0x25, 0x26, 0x5f, 0x60, 0xc2, 0xc3)
            # ChkInvisibleMTiles: these blocks must first be hit from below.
            # Exposing their RAM position is assistance, not pixel perception.
            if tile in (0x5f, 0x60):
                hidden_blocks.append({'rect': [col*16, row*16+32, col*16+16, row*16+48],
                                      'kind': 'hidden_coin' if tile == 0x5f else 'hidden_1up',
                                      'instruction': 'Hit from below to reveal; not yet a solid landing step.'})
            if tile in (0x24, 0x25):
                flags.append([col * 16, row * 16 + 32])
            if solid:
                if segment is not None and segment[2] == col * 16:
                    segment[2] += 16
                else:
                    segment = [col * 16, row * 16 + 32, col * 16 + 16, row * 16 + 48]
                    terrain.append(segment)
            else:
                segment = None
    delta = None
    if previous and state["frame"] > previous["frame"]:
        n = state["frame"] - previous["frame"]
        delta = {"x_px_per_frame": round((x - previous["mario"]["x"]) / n, 3),
                 "y_px_per_frame": round((y - previous["mario"]["reference_y"]) / n, 3)}
    return {"frame": state["frame"], "display": state.get("display_type"),
            "level": f"{r['world'] + 1}-{r['level'] + 1}",
            "camera_x": camera, "visible_x": [camera, camera + 255],
            "mario": {"x": x, "reference_y": y, "body_top": top,
                      "feet_y": y + 32, "width": 16, "small": bool(r["size"]),
                      "grounded": r["player_state"] == 0,
                      "motion": {0: "grounded", 1: "jumping", 2: "falling", 3: "climbing"}.get(r["player_state"], "unknown"),
                      "vx_raw": signed(r["vx"]), "vy_raw": signed(r["vy"]),
                      "measured_velocity": delta},
            "objects": enemies, "solid_rectangles_xyxy": terrain,
            "hidden_blocks_from_ram": hidden_blocks,
            "flagpole_tiles_xy": flags, "previous_buttons": buttons or [],
            "engine_routine": r["routine"], "lives_counter": r["lives"]}


def request_for(observation, history, frames):
    return {"model": "jev-latest", "state": {
        "game": "Super Mario Bros NES", "goal": "Survive and finish World 1-1, reach 1-2.",
        "coordinates": "Pixels: x increases right, y increases DOWN. All x are world coordinates. Terrain rectangles are [left,top,right,bottom]. Missing ground means a pit. Mario collides with pipes/blocks; jump onto or over them. Objects are from RAM, not images.",
        "physics": "Right+B runs, A jumps only when on ground and A was previously released. Holding A sustains a higher jump; release A for a short jump. Holding A through landing does not start a new jump: release then press. Mario has momentum. Stomp goombas from above; side contact kills. Do not walk into pits. Prioritize completion, not coins. vx_raw is signed fixed-point units of 1/16 pixel per frame; measured velocity is in pixels/frame.",
        "horizon_frames": frames, "observation": observation, "recent": history[-3:]},
        "questions": {"action": {"type": "choice", "instructions":
            f"Which buttons should Mario hold for the next {frames} frames to survive and progress right towards the flag? Use distances, velocity, terrain height and current jump phase. Choose exactly one action.",
            "criteria": DESCRIPTIONS}}}


def snapshot(backend, folder, name, state):
    write(folder / f"{name}.json", state)
    shot = backend.screenshot()
    if shot:
        shutil.copyfile(shot, folder / f"{name}.png")


def decode_action(answer, policy):
    if policy == "raw_joint":
        action = answer["answers"]["action"]["choice"]
        if action not in ACTIONS:
            raise ValueError("Invalid action returned by API")
        return action, ACTIONS[action], answer["answers"]["action"].get("confidence")
    movement = answer["answers"]["movement"]["choice"]
    jump = answer["answers"]["jump"]["choice"]
    directions = {"walk_right": ["right"], "run_right": ["right", "b"],
                  "left": ["left"], "neutral": []}
    if movement not in directions or jump not in {"hold", "release", "start_jump"}:
        raise ValueError("Invalid movement/jump returned by API")
    buttons = directions[movement] + (["a"] if jump in {"hold", "start_jump"} else [])
    return f"{movement}+{jump}", buttons, {
        name: answer["answers"][name].get("confidence") for name in ("movement", "jump")}
