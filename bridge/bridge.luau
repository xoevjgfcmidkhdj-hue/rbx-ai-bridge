--[[
    rbx-ai-bridge — MVP Bridge v0.1
    Tools: get_game_state, run_script
    Transport: WebSocket
]]

local HttpService = game:GetService("HttpService")
local Players     = game:GetService("Players")
local StarterGui  = game:GetService("StarterGui")

-- ── CONFIG (Server will inject these) ──
local SERVER_HOST = "PLACEHOLDER.onrender.com"
local ROOM_ID     = "default"
local USE_HTTPS   = true

local proto   = USE_HTTPS and "https://" or "http://"
local wsProto = USE_HTTPS and "wss://"   or "ws://"
local BASE    = proto   .. SERVER_HOST
local WS_URL  = wsProto .. SERVER_HOST .. "/ws/roblox?room=" .. HttpService:UrlEncode(ROOM_ID)

local LocalPlayer = Players.LocalPlayer or Players:GetPropertyChangedSignal("LocalPlayer"):Wait()
local clientId    = "rbx_" .. tostring(math.random(100000, 999999))
local isConnected = false
local wsConn      = nil

-- ── Instance guard ──
local BRIDGE_ID = HttpService:GenerateGUID(false)
do
    local prev = getgenv().__RBX_AI_BRIDGE
    if prev and prev.ws then pcall(function() prev.ws:Close() end) end
    getgenv().__RBX_AI_BRIDGE = { id = BRIDGE_ID }
end
local function isCurrent() return getgenv().__RBX_AI_BRIDGE.id == BRIDGE_ID end

-- ── Helpers ──
local function notify(text)
    pcall(function()
        StarterGui:SetCore("SendNotification", {
            Title = "rbx-ai-bridge", Text = text, Duration = 3
        })
    end)
end

local function gameState()
    local char = LocalPlayer.Character
    local hrp  = char and char:FindFirstChild("HumanoidRootPart")
    local hum  = char and char:FindFirstChildOfClass("Humanoid")
    local plist = {}
    for _, p in ipairs(Players:GetPlayers()) do
        table.insert(plist, { name = p.Name, userId = p.UserId })
    end
    return {
        placeId  = game.PlaceId,
        jobId    = game.JobId,
        player   = LocalPlayer.Name,
        health   = hum and hum.Health or 0,
        position = hrp and { x = hrp.Position.X, y = hrp.Position.Y, z = hrp.Position.Z } or nil,
        players  = plist,
    }
end

local function runCode(code)
    local fn, cerr = loadstring(code)
    if not fn then return false, { error = "compile: " .. tostring(cerr) } end
    local logs = {}
    local function cap(kind)
        return function(...)
            local parts = {}
            for i = 1, select("#", ...) do parts[i] = tostring((select(i, ...))) end
            table.insert(logs, kind .. table.concat(parts, " "))
            if kind == "" then print(...) else warn(...) end
        end
    end
    local env = setmetatable({ print = cap(""), warn = cap("[warn] ") }, { __index = getfenv(0) })
    pcall(setfenv, fn, env)
    local res = table.pack(pcall(fn))
    if not res[1] then return false, { error = tostring(res[2]), logs = logs } end
    local returns = {}
    for i = 2, res.n do returns[i-1] = tostring(res[i]) end
    return true, { returns = returns, logs = logs }
end

-- ── Tool dispatcher ──
local Tools = {}
function Tools.get_game_state() return gameState() end
function Tools.run_script(a)
    local ok, out = runCode(tostring(a.code or ""))
    if not ok then error(out.error .. (#(out.logs or {}) > 0 and ("\nlogs:\n" .. table.concat(out.logs, "\n")) or ""), 0) end
    return out
end

local function handlePacket(pkt)
    if type(pkt) ~= "table" then return nil end
    local tool = Tools[pkt.name]
    if not tool then
        return { type = "result", reqId = pkt.reqId, success = false, error = "unknown tool: " .. tostring(pkt.name) }
    end
    local ok, out = pcall(tool, pkt.args or {})
    if not ok then
        return { type = "result", reqId = pkt.reqId, success = false, error = tostring(out) }
    end
    return { type = "result", reqId = pkt.reqId, success = true, output = out }
end

-- ── WebSocket transport ──
local function startWS()
    local connect = (syn and syn.websocket and syn.websocket.connect) or (WebSocket and WebSocket.connect)
    if not connect then return false, "no WebSocket API" end
    local ok, ws = pcall(connect, WS_URL)
    if not ok or not ws then return false, "connect failed: " .. tostring(ws) end

    wsConn      = ws
    getgenv().__RBX_AI_BRIDGE.ws = ws
    isConnected = true

    ws:Send(HttpService:JSONEncode({
        type = "handshake", room = ROOM_ID, clientId = clientId,
        player = LocalPlayer.Name, placeId = game.PlaceId,
    }))
    notify("เชื่อมต่อแล้ว: " .. ROOM_ID)

    ws.OnMessage:Connect(function(msg)
        local ok, data = pcall(function() return HttpService:JSONDecode(msg) end)
        if not ok then return end
        local reply = handlePacket(data)
        if reply and wsConn then
            pcall(function() wsConn:Send(HttpService:JSONEncode(reply)) end)
        end
    end)

    ws.OnClose:Connect(function()
        if wsConn ~= ws then return end
        isConnected = false; wsConn = nil
        if not isCurrent() then return end
        notify("ขาดการเชื่อมต่อ กำลังลองใหม่...")
        task.spawn(function()
            local delay = 2
            while not isConnected and isCurrent() do
                task.wait(delay)
                if startWS() then return end
                delay = math.min(delay * 2, 30)
            end
        end)
    end)
    return true
end

task.spawn(function()
    print("[rbx-ai-bridge] connecting to " .. BASE .. " room=" .. ROOM_ID)
    local ok, err = startWS()
    if not ok then warn("[rbx-ai-bridge] " .. tostring(err)) end
end)
