from dataclasses import dataclass

@dataclass
class LuaBridgeConfig:
    script_path: str = "scripts/bizhawk_bridge.lua"
    directory: str = "lua_bridge"
    pending_file: str = "pending_action.json"
    done_file: str = "action_done.json"
    state_file: str = "emulator_state.json"
    ram_watch_file: str = "ram_watch.txt"
    boot_frames: int = 120
    ready_timeout_seconds: float = 30.0
    timeout_seconds: float = 15.0
    poll_interval_seconds: float = 0.01
