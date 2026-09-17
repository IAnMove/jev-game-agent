-- File bridge for retro_llm_pilot on BizHawk/EmuHawk.
--
-- Verified against local LuaCATS docs in BizHawk 2.11.1:
-- emu.frameadvance/framecount/getsystemid/getdisplaytype/islagged, client.screenshot/exit,
-- joypad.set/setanalog, memory.read_u8, savestate.save/load/saveslot/loadslot.
--
-- Protocol v3 adds named savestate actions. Python still accepts v2 ready/idle
-- for older bridges; named save/load require this v3 script.

local protocol_version = 3
local sep = package.config:sub(1, 1)
local script_dir = debug.getinfo(1, "S").source:sub(2):match("^(.*)[/\\][^/\\]+$") or "."
local bridge_dir = os.getenv("RETRO_LLM_BRIDGE_DIR")
if not bridge_dir or bridge_dir == "" then
    bridge_dir = arg and arg[1] or (script_dir .. sep .. ".." .. sep .. "lua_bridge")
end

local pending_path = bridge_dir .. sep .. "pending_action.json"
local done_path = bridge_dir .. sep .. "action_done.json"
local state_path = bridge_dir .. sep .. "emulator_state.json"
local screenshot_path = bridge_dir .. sep .. "screen.png"
local ram_watch_path = bridge_dir .. sep .. "ram_watch.txt"
local states_dir = bridge_dir .. sep .. "states"
local poll_seconds = tonumber(os.getenv("RETRO_LLM_POLL_SECONDS") or "0.05") or 0.05
local boot_frames = tonumber(os.getenv("RETRO_LLM_BOOT_FRAMES") or "120") or 120
-- Unlike emu.framecount(), this counter is deliberately not part of savestates.
-- It tracks frames appended to the current A/V dump even when the emulated
-- machine rewinds during counterfactual probes.
local timeline_frame = 0

local function read_all(path)
    local file = io.open(path, "r")
    if not file then
        return nil
    end
    local text = file:read("*a")
    file:close()
    return text
end

local function write_all(path, text)
    local temp_path = path .. ".tmp"
    local file = assert(io.open(temp_path, "w"))
    file:write(text)
    file:close()
    os.remove(path)
    os.rename(temp_path, path)
end

local function safe_call(default, fn, ...)
    local ok, value = pcall(fn, ...)
    if ok then
        return value
    end
    return default
end

local function json_escape(value)
    value = tostring(value or "")
    value = value:gsub("\\", "\\\\")
    value = value:gsub('"', '\\"')
    value = value:gsub("\r", "\\r")
    value = value:gsub("\n", "\\n")
    return value
end

local function parse_number(text, name, default)
    local pattern = '"' .. name .. '"%s*:%s*(-?%d+)'
    return tonumber(text:match(pattern)) or default
end

local function parse_string(text, name, default)
    local pattern = '"' .. name .. '"%s*:%s*"([^"]*)"'
    return text:match(pattern) or default
end

local function parse_array_strings(text, name)
    local pattern = '"' .. name .. '"%s*:%s*%[(.-)%]'
    local raw = text:match(pattern)
    local values = {}
    if not raw then
        return values
    end
    for value in raw:gmatch('"([^"]+)"') do
        table.insert(values, value)
    end
    return values
end

local function parse_analog(text)
    local raw = text:match('"analog"%s*:%s*{(.-)}')
    local values = {}
    if not raw then
        return values
    end
    for key, value in raw:gmatch('"([^"]+)"%s*:%s*(-?%d+)') do
        values[key] = tonumber(value)
    end
    return values
end

local function parse_address(raw)
    if not raw then
        return nil
    end
    raw = raw:gsub("^%s+", ""):gsub("%s+$", "")
    if raw:sub(1, 2):lower() == "0x" then
        return tonumber(raw:sub(3), 16)
    end
    return tonumber(raw)
end

local function parse_action(text)
    return {
        id = parse_number(text, "id", 0),
        type = parse_string(text, "type", parse_string(text, "action", "advance")),
        hold_frames = math.max(parse_number(text, "hold_frames", 0), 0),
        advance_frames = math.max(parse_number(text, "advance_frames", parse_number(text, "frames", 1)), 1),
        slot = parse_number(text, "slot", 1),
        name = parse_string(text, "name", ""),
        buttons = parse_array_strings(text, "buttons"),
        analog = parse_analog(text),
    }
end

local function ensure_states_dir()
    -- Best-effort: BizHawk Lua may lack mkdir; Python creates the folder.
    pcall(function()
        os.execute('mkdir "' .. states_dir .. '" 2>nul')
    end)
end

local function validate_state_name(name)
    if not name or name == "" then
        return nil, "missing savestate name"
    end
    if not name:match("^[%w_%-]+$") then
        return nil, "invalid savestate name (use [A-Za-z0-9_-]+ only)"
    end
    return name, nil
end

local function named_state_path(name)
    return states_dir .. sep .. name .. ".State"
end

local function idle_wait()
    local deadline = os.clock() + poll_seconds
    while os.clock() < deadline do
    end
end

local function write_screenshot()
    local ok = pcall(client.screenshot, screenshot_path)
    return ok
end

local function read_ram_watch()
    local text = read_all(ram_watch_path)
    local values = {}
    if not text then
        return values
    end
    for line in text:gmatch("[^\r\n]+") do
        local stripped = line:gsub("#.*$", "")
        local label, raw_address, domain = stripped:match("^%s*([%w_%-%.]+)%s*=%s*([^%s,]+)%s*,?%s*([%w%s_%-%.]*)")
        local address = parse_address(raw_address)
        if label and address and address >= 0 then
            local memory_domain = domain ~= "" and domain or nil
            local ok, value = pcall(memory.read_u8, address, memory_domain)
            if ok and type(value) == "number" then
                table.insert(values, { label = label, address = address, value = value, domain = memory_domain or "" })
            end
        end
    end
    return values
end

local function encode_ram(values)
    local parts = {}
    for _, item in ipairs(values) do
        table.insert(
            parts,
            string.format(
                '"%s":{"address":%d,"hex":"0x%04X","domain":"%s","value":%d}',
                json_escape(item.label),
                item.address,
                item.address,
                json_escape(item.domain),
                item.value
            )
        )
    end
    return "{" .. table.concat(parts, ",") .. "}"
end

local function encode_memory_domains()
    local ok, domains = pcall(memory.getmemorydomainlist)
    if not ok or type(domains) ~= "table" then
        return "[]"
    end
    local parts = {}
    for _, domain in ipairs(domains) do
        table.insert(parts, '"' .. json_escape(domain) .. '"')
    end
    return "[" .. table.concat(parts, ",") .. "]"
end

local function current_rom_name()
    local ok, name = pcall(gameinfo.getromname)
    if ok then
        return name
    end
    return ""
end

local function current_rom_hash()
    local ok, hash = pcall(gameinfo.getromhash)
    if ok then
        return hash
    end
    return ""
end

local function write_state(status, last_action_id, error_message)
    local screenshot_written = write_screenshot()
    local frame = safe_call(0, emu.framecount)
    local system = safe_call("", emu.getsystemid)
    local display_type = safe_call("", emu.getdisplaytype)
    local lagged = safe_call(false, emu.islagged)
    local payload = string.format(
        '{"protocol_version":%d,"status":"%s","frame":%d,"timeline_frame":%d,"system":"%s","display_type":"%s","rom":"%s","rom_hash":"%s","lagged":%s,"last_action_id":%d,"bridge_dir":"%s","screenshot_file":%s,"memory_domains":%s,"ram":%s',
        protocol_version,
        json_escape(status),
        frame,
        timeline_frame,
        json_escape(system),
        json_escape(display_type),
        json_escape(current_rom_name()),
        json_escape(current_rom_hash()),
        lagged and "true" or "false",
        last_action_id or 0,
        json_escape(bridge_dir),
        screenshot_written and '"screen.png"' or "null",
        encode_memory_domains(),
        encode_ram(read_ram_watch())
    )
    if error_message then
        payload = payload .. string.format(',"error":"%s"', json_escape(error_message))
    end
    payload = payload .. "}"
    write_all(state_path, payload)
end

local function buttons_table(buttons)
    local resolved = {}
    for _, button in ipairs(buttons) do
        resolved[button] = true
    end
    return resolved
end

local function apply_inputs(buttons, analog)
    pcall(joypad.set, buttons_table(buttons), 1)
    if next(analog) ~= nil then
        pcall(joypad.setanalog, analog, 1)
    end
end

local function clear_inputs()
    pcall(joypad.set, {}, 1)
    pcall(joypad.setanalog, {}, 1)
end

local function execute_action(action)
    if action.type == "quit" then
        write_state("quitting", action.id)
        client.exit()
        return
    end
    if action.type == "screenshot" then
        write_state("idle", action.id)
        return
    end
    if action.type == "savestate" then
        savestate.saveslot(action.slot, true)
        write_state("idle", action.id)
        return
    end
    if action.type == "loadstate" then
        savestate.loadslot(action.slot, true)
        write_state("idle", action.id)
        return
    end
    -- v3: named savestates. Path is always under bridge_dir/states/<name>.State
    -- (never an arbitrary path from JSON).
    if action.type == "savestate_named" then
        local name, name_err = validate_state_name(action.name)
        if not name then
            error(name_err)
        end
        ensure_states_dir()
        local path = named_state_path(name)
        local ok_save = savestate.save(path, true)
        if ok_save == false then
            error("savestate.save failed for " .. name)
        end
        write_state("idle", action.id)
        return
    end
    if action.type == "loadstate_named" then
        local name, name_err = validate_state_name(action.name)
        if not name then
            error(name_err)
        end
        local path = named_state_path(name)
        local ok_load = savestate.load(path, true)
        if ok_load == false then
            error("savestate.load failed for " .. name)
        end
        -- Framebuffer may still show previous frame; caller may advance 1 neutral.
        write_state("idle", action.id)
        return
    end

    local hold_frames = 0
    if action.type == "press" then
        hold_frames = math.min(action.hold_frames, action.advance_frames)
    end

    for frame = 1, action.advance_frames do
        if frame <= hold_frames then
            apply_inputs(action.buttons, action.analog)
        else
            clear_inputs()
        end
        emu.frameadvance()
        timeline_frame = timeline_frame + 1
    end
    clear_inputs()
end

local function main_loop()
    local last_action_id = 0
    local last_heartbeat = 0
    write_state("booting", last_action_id)
    for _ = 1, math.max(boot_frames, 0) do
        clear_inputs()
        emu.frameadvance()
        timeline_frame = timeline_frame + 1
    end
    clear_inputs()
    write_state("ready", last_action_id)

    while true do
        local pending = read_all(pending_path)
        if pending then
            local action = parse_action(pending)
            if action.id > last_action_id then
                local ok, err = pcall(execute_action, action)
                os.remove(pending_path)
                if ok then
                    last_action_id = action.id
                    write_state("idle", last_action_id)
                    write_all(done_path, string.format('{"id":%d,"frame":%d,"timeline_frame":%d,"status":"done"}', action.id, safe_call(0, emu.framecount), timeline_frame))
                else
                    write_state("error", last_action_id, err)
                    write_all(done_path, string.format('{"id":%d,"frame":%d,"timeline_frame":%d,"status":"error","error":"%s"}', action.id, safe_call(0, emu.framecount), timeline_frame, json_escape(err)))
                end
            else
                os.remove(pending_path)
            end
        else
            if os.time() - last_heartbeat >= 1 then
                write_state("idle", last_action_id)
                last_heartbeat = os.time()
            end
            idle_wait()
        end
    end
end

local ok, err = pcall(main_loop)
if not ok then
    write_all(bridge_dir .. sep .. "bridge_error.txt", tostring(err))
    write_all(state_path, string.format('{"protocol_version":%d,"status":"error","frame":0,"timeline_frame":%d,"system":"","display_type":"","rom":"","rom_hash":"","lagged":false,"last_action_id":0,"bridge_dir":"%s","screenshot_file":null,"memory_domains":[],"ram":{},"error":"%s"}', protocol_version, timeline_frame, json_escape(bridge_dir), json_escape(err)))
end
