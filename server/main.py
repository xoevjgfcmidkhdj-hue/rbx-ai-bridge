import asyncio, json, uuid, os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import google.generativeai as genai

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

ROOMS: dict[str, dict] = {}
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)

@app.get("/health")
async def health():
    return {"ok": True, "rooms": list(ROOMS.keys())}

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    try:
        with open(os.path.join(os.path.dirname(__file__), "index.html"), encoding="utf-8") as f:
            return f.read()
    except Exception:
        return "<h1>index.html not found</h1>"

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
    try:
        while True:
            msg = await ws.receive_text()
            data = json.loads(msg)
            if data.get("type") == "handshake": continue
            reqId = data.get("reqId")
            if reqId and reqId in ROOMS[room]["pending"]:
                fut = ROOMS[room]["pending"].pop(reqId)
                if not fut.done(): fut.set_result(data)
    except WebSocketDisconnect:
        ROOMS[room]["clients"].pop(cid, None)

async def call_bridge(room: str, tool: str, args: dict, timeout: float = 3.0) -> dict:
    clients = ROOMS.get(room, {}).get("clients", {})
    if not clients: return {"success": False, "error": "no bridge"}
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

class ChatReq(BaseModel):
    room: str = "default"
    message: str

@app.post("/chat")
async def chat_endpoint(req: ChatReq):
    if not GEMINI_KEY: return {"reply": "⚠️ ยังไม่ได้ตั้งค่า GEMINI_API_KEY"}
    
    is_connected = len(ROOMS.get(req.room, {}).get("clients", {})) > 0
    game_context = "ไม่ได้เชื่อมต่อกับเกม (ตอบคำถามทั่วไปได้เลย)"
    
    if is_connected:
        state = await call_bridge(req.room, "get_game_state", {})
        if state.get("success"):
            game_context = f"เชื่อมต่อเกมอยู่ ข้อมูล: {json.dumps(state.get('output'))}"
    
    system_prompt = f"You are an expert Roblox AI. Status: {game_context}. If you write Lua code, format it with ```lua and it will run automatically."
    
    try:
        # แนะนำให้ใช้ 2.5-flash ถ้า 3.8 ยังไม่รองรับ
        model = genai.GenerativeModel('gemini-2.5-flash', system_instruction=system_prompt)
        response = model.generate_content(req.message)
        text = response.text
        
        executed = False
        if is_connected and "```lua" in text:
            code = text.split("```lua")[1].split("```")[0].strip()
            res = await call_bridge(req.room, "run_script", {"code": code})
            if res.get("success"):
                text += "\n\n✅ *สคริปต์ถูกนำไปรันในเกมแล้ว*"
                executed = True
            else:
                text += f"\n\n❌ *รันไม่สำเร็จ:* {res.get('error')}"
        return {"reply": text, "executed": executed}
    except Exception as e:
        return {"reply": f"AI Error: {str(e)} (ลองแก้ชื่อโมเดลเป็น gemini-2.5-flash หรือ 1.5-pro ดูครับ)"}
