-- rbx-ai-bridge loader
local ROOM = "default"
local HOST = "rbx-ai-bridge.onrender.com"
local URL  = "https://" .. HOST .. "/bridge.luau?room=" .. ROOM .. "&host=" .. HOST .. "&t=" .. tostring(os.time())

local req = (syn and syn.request) or (http and http.request) or http_request or request
if not req then warn("[loader] no HTTP function") return end
local ok, res = pcall(req, { Url = URL, Method = "GET" })
if not ok or not res then warn("[loader] fetch failed: " .. tostring(res)) return end
if res.StatusCode and (res.StatusCode < 200 or res.StatusCode >= 300) then
    warn("[loader] HTTP " .. tostring(res.StatusCode)) return
end
local fn, cerr = loadstring(res.Body, "=rbx-ai-bridge")
if not fn then warn("[loader] compile: " .. tostring(cerr)) return end
local ok2, rerr = pcall(fn)
if not ok2 then warn("[loader] runtime: " .. tostring(rerr)) end
