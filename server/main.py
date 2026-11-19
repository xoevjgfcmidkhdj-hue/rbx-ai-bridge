import asyncio, json, uuid, os, time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse, HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import openai

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

ROOMS: dict[str, dict] = {}
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")

client = openai.AsyncOpenAI(
    api_key=OPENROUTER_KEY, 
    base_url="https://openrouter.ai/api/v1",
    default_headers={
        "HTTP-Referer": "https://rbx-ai-bridge.onrender.com",
        "X-Title": "RBX AI Bridge"
    }
)

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

async def call_bridge(room: str, tool: str, args: dict, timeout: float = 10.0) -> dict:
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
    if not OPENROUTER_KEY: 
        return {"reply": "⚠️ ยังไม่ได้ตั้งค่า OPENROUTER_API_KEY ใน Render"}
    
    clean_stale_rooms()
    is_connected = len(ROOMS.get(req.room, {}).get("clients", {})) > 0
    msg_lower = req.message.lower()
    
    game_keywords = ["แมพ", "map", "ผู้เล่น", "player", "esp", "aimbot", "god mode", "วาร์ป", "teleport", "เลือด", "health", "ตำแหน่ง", "position", "เช็คเกม", "ดูในเกม", "มีใครบ้าง", "ทำสคริปต์ในเกม"]
    needs_game_data = any(k in msg_lower for k in game_keywords)
    
    game_context = "ไม่ได้เชื่อมต่อกับเกม (คุณสามารถคุยเล่น ถามความรู้ หรือให้เขียนสคริปต์ได้ แต่จะไม่มีการรันสคริปต์ลงเกม)"
    if is_connected:
        if needs_game_data:
            state = await call_bridge(req.room, "get_game_state", {})
            if state.get("success"):
                game_context = f"เชื่อมต่อเกมอยู่ ข้อมูลปัจจุบัน: {json.dumps(state.get('output'))}"
        else:
            game_context = "เชื่อมต่อเกมอยู่ แต่คุณกำลังคุยเรื่องทั่วไป"
    
    system_prompt = f"You are a helpful and casual AI assistant. Status: {game_context}. Answer in Thai naturally."
    run_keywords = ["รัน", "execute", "รันให้หน่อย", "ลองรัน", "ทำให้หน่อย"]
    user_wants_run = any(k in msg_lower for k in run_keywords)

    async def generate():
        try:
            # ใช้ Free Models Router เพื่อให้ OpenRouter เลือกโมเดลฟรีที่ยังใช้งานได้ให้เอง
            response = await client.chat.completions.create(
                model="openrouter/free",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": req.message}
                ],
                stream=True
            )
            full_text = ""
            async for chunk in response:
                if chunk.choices[0].delta.content:
                    text = chunk.choices[0].delta.content
                    full_text += text
                    yield text
                    
            if is_connected and user_wants_run and "```lua" in full_text:
                try:
                    code = full_text.split("```lua")[1].split("```")[0].strip()
                    res = await call_bridge(req.room, "run_script", {"code": code})
                    if res.get("success"):
                        yield "\n\n✅ *สคริปต์ถูกนำไปรันในเกมแล้ว*"
                    else:
                        yield f"\n\n❌ *รันไม่สำเร็จ:* {res.get('error')}"
                except Exception as ex:
                    yield f"\n\n❌ *ดึงโค้ดล้มเหลว:* {str(ex)}"
        except Exception as e:
            yield f"\n[AI Error: {str(e)}]"

    return StreamingResponse(generate(), media_type="text/plain")
