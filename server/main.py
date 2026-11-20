import asyncio, json, uuid, os, time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse, HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google import genai
from google.genai import types

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

ROOMS: dict[str, dict] = {}
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")

# ใช้ SDK ตัวใหม่ล่าสุด
client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None

def clean_stale_rooms():
    now = time.time()
    for room in list(ROOMS.keys()):
        for cid, info in list(ROOMS[room]["clients"].items()):
            if now - info.get("last_seen", now) > 40:
                ROOMS[room]["clients"].pop(cid, None)
        if not ROOMS[room]["clients"] and not ROOMS[room]["pending"]:
            ROOMS.pop(room, None)

@app.get("/health")
async def health():
    clean_stale_rooms()
    return {"ok": True, "rooms": list(ROOMS.keys())}

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    try:
        with open(os.path.join(os.path.dirname(__file__), "index.html"), encoding="utf-8") as f:
            return f.read()
    except:
        return "<h1>index.html not found</h1>"

@app.get("/bridge.luau", response_class=PlainTextResponse)
async def serve_bridge(room: str = "default", host: str = None):
    bridge_path = os.path.join(os.path.dirname(__file__), "..", "bridge", "bridge.luau")
    try:
        with open(bridge_path, encoding="utf-8") as f:
            src = f.read()
    except Exception as e:
        return f"-- Error: {e}"
    src = src.replace('ROOM_ID     = "default"', f'ROOM_ID     = "{room}"')
    if host:
        src = src.replace('SERVER_HOST = "PLACEHOLDER.onrender.com"', f'SERVER_HOST = "{host}"')
    return src

@app.websocket("/ws/roblox")
async def ws_roblox(ws: WebSocket, room: str = "default"):
    await ws.accept()
    cid = "rbx_" + uuid.uuid4().hex[:8]
    ROOMS.setdefault(room, {"clients": {}, "pending": {}})
    ROOMS[room]["clients"][cid] = {"ws": ws, "last_seen": time.time()}
    
    try:
        while True:
            msg = await ws.receive_text()
            ROOMS[room]["clients"][cid]["last_seen"] = time.time()
            data = json.loads(msg)
            if data.get("type") in ["handshake", "ping"]: continue
            reqId = data.get("reqId")
            if reqId and reqId in ROOMS[room]["pending"]:
                fut = ROOMS[room]["pending"].pop(reqId)
                if not fut.done(): fut.set_result(data)
    except Exception:
        pass
    finally:
        if room in ROOMS and cid in ROOMS[room]["clients"]:
            ROOMS[room]["clients"].pop(cid, None)
            if not ROOMS[room]["clients"] and not ROOMS[room]["pending"]:
                ROOMS.pop(room, None)

async def call_bridge(room: str, tool: str, args: dict, timeout: float = 15.0) -> dict:
    clients = ROOMS.get(room, {}).get("clients", {})
    if not clients: return {"success": False, "error": "no bridge connected"}
    cid = next(iter(clients.keys()))
    ws = clients[cid]["ws"]
    reqId = uuid.uuid4().hex
    fut = asyncio.get_event_loop().create_future()
    ROOMS[room]["pending"][reqId] = fut
    try:
        await ws.send_text(json.dumps({"type": "tool", "reqId": reqId, "name": tool, "args": args}))
        return await asyncio.wait_for(fut, timeout=timeout)
    except Exception as e:
        ROOMS[room]["pending"].pop(reqId, None)
        return {"success": False, "error": str(e)}

class ChatReq(BaseModel):
    room: str = "default"
    message: str

@app.post("/chat")
async def chat_endpoint(req: ChatReq):
    if not client: 
        return {"reply": "⚠️ ยังไม่ได้ตั้งค่า GEMINI_API_KEY ใน Render"}
    
    clean_stale_rooms()
    is_connected = len(ROOMS.get(req.room, {}).get("clients", {})) > 0
    msg_lower = req.message.lower()
    
    game_keywords = ["แมพ", "map", "ผู้เล่น", "player", "esp", "aimbot", "god mode", "วาร์ป", "teleport", "เลือด", "health", "ตำแหน่ง", "position", "เช็คเกม", "ดูในเกม", "มีใครบ้าง", "สคริปต์", "script"]
    needs_game_data = any(k in msg_lower for k in game_keywords)
    
    game_context = "ไม่ได้เชื่อมต่อกับเกม (ตอบคำถามทั่วไป หรือเขียนโค้ดให้เขาเอาไปรันเองได้)"
    if is_connected:
        if needs_game_data:
            state = await call_bridge(req.room, "get_game_state", {})
            if state.get("success"):
                game_context = f"เชื่อมต่อเกมอยู่ ข้อมูลปัจจุบัน: {json.dumps(state.get('output'))}"
        else:
            game_context = "เชื่อมต่อเกมอยู่ แต่คุณกำลังคุยเรื่องทั่วไป"
    
    system_prompt = f"""You are a highly intelligent AI assistant specialized in Roblox and Luau scripting.
    Status: {game_context}
    
    If you write Lua code for the user, format it beautifully inside ```lua blocks.
    If the user asks you to do something in the game (like ESP, Aimbot, Teleport), write the complete script and they will run it automatically.
    Always answer in Thai naturally.
    """
    
    async def generate():
        try:
            # ใช้โมเดล gemini-2.0-flash ซึ่งใหม่ล่าสุดและฟรี
            response_stream = await client.aio.models.generate_content_stream(
                model='gemini-2.0-flash',
                contents=req.message,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.7,
                )
            )
            async for chunk in response_stream:
                if chunk.text:
                    yield chunk.text
        except Exception as e:
            yield f"\n[AI Error: {str(e)}]"

    return StreamingResponse(generate(), media_type="text/plain")
