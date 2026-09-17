"""Compare prompt formulations on saved observations; no emulator actions."""
import argparse
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

import httpx


def relative_state(payload):
    old = payload["state"]
    obs = old["observation"]
    mario = obs["mario"]
    velocity = mario["vx_raw"] / 16
    objects = []
    for enemy in obs["objects"]:
        gap = enemy["x"] - mario["x"] - mario["width"]
        closing = velocity - enemy["vx_raw"] / 16
        objects.append({"type": enemy["kind"],
            "horizontal_gap_from_mario_front_px_approx": gap,
            "relative_x_from_mario_px": enemy["x"] - mario["x"],
            "reference_y": enemy["y"], "state_raw": enemy["state_raw"],
            "moving_x_px_per_frame": enemy["vx_raw"] / 16,
            "frames_until_horizontal_overlap_if_unchanged_approx":
                round(max(0, gap) / closing, 1) if gap >= 0 and closing > 0 else None})
    return {
        "role": "You control Mario's controller in an actual ongoing NES game. Each choice is executed immediately, not a suggestion.",
        "objective_priorities": ["Avoid dying: enemy side contact and falling into pits kill Mario.",
            "Reach the flag to the right and finish 1-1.", "Coins and powerups are optional; do not risk the run for them."],
        "decision_horizon_frames": old["horizon_frames"],
        "rules": ["Right moves forward; B increases running speed; Left brakes rightward momentum before moving left.",
            "A starts a jump only from ground, after A has been released. Keep holding A while rising for a higher/longer jump; releasing shortens the jump.",
            "Touching a Goomba from the side kills small Mario. Landing on it from above stomps it. Jump early enough to gain height before reaching it.",
            "A pipe or tall solid block obstructs forward movement. Jump onto or over it, with enough height before reaching its side.",
            "Cross pits in the air and land on solid terrain. Being able to move right is not evidence that continuing right on the ground is safe.",
            "Holding A continuously through landing cannot trigger a second jump; release it between jumps.",
            "Choose from CURRENT geometry, not by copying previous actions. Previous inputs are provided only to understand momentum and whether A is held."],
        "coordinates": "x rightward, y downward; pixels. Terrain rectangles use absolute [left,top,right,bottom]. Enemy gap/overlap timing are approximate geometric measurements, not recommended actions.",
        "mario": {"x": mario["x"], "feet_y": mario["feet_y"], "body_top": mario["body_top"],
            "small": mario["small"], "grounded": mario["grounded"], "jump_phase":
                "grounded" if mario["grounded"] else "ascending" if mario["vy_raw"] < 0 else "descending" if mario["vy_raw"] > 0 else "apex",
            "horizontal_speed_px_per_frame": velocity, "vertical_speed_px_per_frame": mario["vy_raw"],
            "jump_button_currently_held": "a" in obs["previous_buttons"]},
        "nearby_objects": objects, "solid_terrain": obs["solid_rectangles_xyxy"],
        "visible_world_x": obs["visible_x"], "last_buttons": obs["previous_buttons"]}


def atomic_questions(frames):
    return {
        "movement": {"type": "choice", "instructions": f"Select horizontal steering for the next {frames} frames. Jump is controlled independently; do not assume that choosing forward prevents jumping. Prioritize surviving, then reaching the flag to the right.",
            "criteria": {"walk_right": "Hold Right. Walk forward or steer right while airborne.",
                "run_right": "Hold Right+B. Accelerate/run forward or steer right while airborne.",
                "left": "Hold Left. Brake forward momentum or steer left.",
                "neutral": "No horizontal input. Momentum still applies."}},
        "jump": {"type": "choice", "instructions": f"Should A be held or released during the next {frames} frames? Decide whether to initiate/sustain a jump or let Mario stay grounded/descend. Consider approaching enemies, pits, obstacles, jump phase and whether A was already held. Survival has priority over merely walking forward.",
            "criteria": {"hold": "Hold A: initiate a jump if grounded and previously released, or sustain the current jump while rising.",
                "release": "Release A: stay grounded, shorten a jump, descend, or rearm A before the next jump."}}}


def terrain_state(payload):
    """Geometric facts, not a scripted choice of buttons or a future level map."""
    state = relative_state(payload)
    obs = payload["state"]["observation"]
    m = obs["mario"]
    rectangles = obs["solid_rectangles_xyxy"]
    # Merge vertically adjacent rows with identical horizontal extents.
    merged = []
    for rect in sorted(rectangles, key=lambda r: (r[0], r[2], r[1])):
        if merged and merged[-1][0] == rect[0] and merged[-1][2] == rect[2] and merged[-1][3] == rect[1]:
            merged[-1][3] = rect[3]
        else:
            merged.append(list(rect))
    front = m["x"] + m["width"]
    obstacles = []
    for left, top, right, bottom in merged:
        if right <= front or left < m["x"] or top >= m["feet_y"] or bottom <= m["body_top"]:
            continue
        obstacles.append({"front_gap_px": max(0, left - front), "world_x": left,
            "width_px": right-left, "top_y": top,
            "rise_from_current_feet_px": m["feet_y"] - top,
            "intersects_current_body_height": True})
    obstacles.sort(key=lambda v: v["front_gap_px"])
    # Visible gaps at the ground baseline of this 1-1 adapter (y=208).
    ground = sorted((r[0], r[2]) for r in rectangles if r[1] <= 208 < r[3])
    gaps = []
    # Scan from the visible boundary, not Mario: entering a pit must not shrink it.
    cursor = obs["visible_x"][0]
    for left, right in ground:
        if right <= cursor:
            continue
        if left > cursor:
            if left > m["x"]:
                clipped = cursor == obs["visible_x"][0]
                gaps.append({"world_start_x": None if clipped else cursor, "world_end_x": left,
                             "gap_from_mario_front_px": None if clipped else cursor-front,
                             "width_px": None if clipped else left-cursor,
                             "left_boundary_unknown": clipped,
                             "remaining_distance_from_mario_x_px": left-m["x"]})
        cursor = max(cursor, right)
    # Beyond the last known floor segment, the screen boundary may cut off a pit.
    if cursor < obs["visible_x"][1]:
        gaps.append({"world_start_x": cursor, "world_end_x": None,
                     "gap_from_mario_front_px": cursor-front,
                     "visible_width_px": obs["visible_x"][1]-cursor})
    history = payload["state"].get("recent", [])
    no_move = sum(abs(h["result_x"]-h["x"]) < 1 for h in history)
    state["local_geometry"] = {"nearest_forward_obstacle": obstacles[0] if obstacles else None,
        "visible_ground_gaps": gaps, "mario_feet_need_to_be_above_obstacle_top_to_cross": True,
        "last_displacement_px": None if not history else history[-1]["result_x"]-history[-1]["x"],
        "recent_steps_with_zero_horizontal_displacement": no_move}
    state["solid_terrain"] = merged
    state["rules"].extend([
        "If a forward obstacle intersects your body height and forward input produces zero movement, walking alone cannot cross it. Use a jump to gain height; maintain the jump while rising until your feet clear its top.",
        "Keep A held while ascending over an obstacle or gap; release when descending/landed so the next jump can trigger. A held while already grounded cannot retrigger; release for one decision before another jump.",
        "Plan ahead beyond the next 8 frames: jumping needs time to gain height. Running provides more horizontal range for gaps, but takeoff must be before the edge."])
    return state


def controller_questions(frames, observation):
    questions = atomic_questions(frames)
    if observation["mario"]["grounded"] and frames >= 2:
        questions["jump"] = {"type": "choice", "instructions":
            f"Mario is ON THE GROUND. Should he start a fresh jump NOW, or stay on the ground for the next {frames} frames? Jump to clear approaching enemies, blocking obstacles and pits. Stay grounded on safe clear ground. Choose based on the current local geometry.",
            "criteria": {"start_jump":
                f"Initiate a fresh jump: release A for 1 frame then press/hold A for {frames-1} frames. The controller handles this even when A was previously held. Horizontal steering is chosen separately.",
                "release": "Do not jump. Release A and stay grounded; horizontal steering is chosen separately."}}
    return questions
