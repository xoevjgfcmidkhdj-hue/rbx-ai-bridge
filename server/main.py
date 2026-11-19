# rbx-ai-bridge — MVP backend
import asyncio, json, uuid, os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# room_id -> {"clients": {cid: ws}, "pending": {reqId: Future}}
ROOMS: dict[str, dict] = {}

@app.get("/health")
async def health():
    return {"ok": True, "rooms": list(ROOMS.keys())}

@app.get("/bridge.luau", response_class=PlainTextResponse)
async def serve_bridge(room: str = "default", host: str = None):
    bridge_path = os.path.join(os.path.dirname(__file__), "..", "bridge", "bridge.luau")
    try:
        with open(bridge_path, encoding="utf-8") as f:
            src = f.read()
    except Exception as e:
        return f"-- Error loading bridge.luau: {e}"
        
    src = src.replace('ROOM_ID     = "default"', f'ROOM_ID     = "{room}"')
    if host:
        src = src.replace('SERVER_HOST = "PLACEHOLDER.onrender.com"', f'SERVER_HOST = "{host}"')
    return src

@app.websocket("/ws/roblox")
async def ws_roblox(ws: WebSocket, room: str = "default"):
    await ws.accept()
    cid = "rbx_" + uuid.uuid4().hex[:8]
    ROOMS.setdefault(room, {"clients": {}, "pending": {}})
    ROOMS[room]["clients"][cid] = ws
    print(f"[ws] {cid} joined room={room}")
    try:
        while True:
            msg = await ws.receive_text()
            data = json.loads(msg)
            if data.get("type") == "handshake":
                print(f"[ws] handshake from {cid}: {data}")
                continue
            reqId = data.get("reqId")
            if reqId and reqId in ROOMS[room]["pending"]:
                fut = ROOMS[room]["pending"].pop(reqId)
                if not fut.done():
                    fut.set_result(data)
    except WebSocketDisconnect:
        ROOMS[room]["clients"].pop(cid, None)
        print(f"[ws] {cid} left")

async def call_bridge(room: str, tool: str, args: dict, timeout: float = 30.0) -> dict:
    clients = ROOMS.get(room, {}).get("clients", {})
    if not clients:
        return {"success": False, "error": "no bridge connected in room " + room}
    ws = next(iter(clients.values()))
    reqId = uuid.uuid4().hex
    fut = asyncio.get_event_loop().create_future()
    ROOMS[room]["pending"][reqId] = fut
    await ws.send_text(json.dumps({"type": "tool", "reqId": reqId, "name": tool, "args": args}))
    try:
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        ROOMS[room]["pending"].pop(reqId, None)
        return {"success": False, "error": "timeout"}

class CallReq(BaseModel):
    room: str = "default"
    tool: str
    args: dict = {}

@app.post("/call")
async def call_endpoint(req: CallReq):
    """ทดสอบสั่ง tool ตรง ๆ ไม่ผ่าน AI"""
    return await call_bridge(req.room, req.tool, req.args)
