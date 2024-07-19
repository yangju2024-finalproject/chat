import json
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, Depends
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from kafka_producer import KafkaMessageProducer
from redis_handler import RedisHandler
from typing import List
from pymongo import MongoClient
from bson import ObjectId
import bcrypt

app = FastAPI()

# MongoDB 연결
mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/chatapp")
mongo_client = MongoClient(mongo_uri)
db = mongo_client.get_database()
users_collection = db["users"]

# Kafka 연결
kafka_bootstrap_servers = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092')
kafka_producer = KafkaMessageProducer([kafka_bootstrap_servers])

# Redis 연결
redis_host = os.getenv('REDIS_HOST', 'redis')
redis_handler = RedisHandler(host=redis_host)

class WebSocketSessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if "websocket" in request.scope["type"]:
            request.scope["session"] = request.scope.get("session", {})
        return await call_next(request)

app.add_middleware(SessionMiddleware, secret_key="your-secret-key")
app.add_middleware(WebSocketSessionMiddleware)

# WebSocket 연결 관리
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)

manager = ConnectionManager()

# 로그인 상태 확인
def get_current_user(session: dict):
    user_id = session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = users_collection.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

@app.get("/user-info")
async def user_info(request: Request):
    user = get_current_user(request.session)
    return {"username": user["username"]}

@app.post("/signup")
async def signup(request: Request):
    data = await request.json()
    username = data.get("username")
    password = data.get("password")
    email = data.get("email")

    if not username or not password or not email:
        return JSONResponse(status_code=400, content={"success": False, "message": "All fields are required"})

    existing_user = users_collection.find_one({"username": username})
    if existing_user:
        return JSONResponse(status_code=400, content={"success": False, "message": "Username already exists"})

    hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
    user_id = users_collection.insert_one({
        "username": username,
        "password": hashed_password,
        "email": email
    }).inserted_id

    request.session["user_id"] = str(user_id)
    return JSONResponse(content={"success": True})

@app.post("/login")
async def login(request: Request):
    data = await request.json()
    username = data.get("username")
    password = data.get("password")

    user = users_collection.find_one({"username": username})
    if user and bcrypt.checkpw(password.encode('utf-8'), user["password"]):
        request.session["user_id"] = str(user["_id"])
        return JSONResponse(content={"success": True})
    return JSONResponse(status_code=401, content={"success": False, "message": "Invalid credentials"})

@app.post("/check-duplicate")
async def check_duplicate(request: Request):
    data = await request.json()
    username = data.get("username")

    if not username:
        return JSONResponse(status_code=400, content={"success": False, "message": "Username is required"})

    existing_user = users_collection.find_one({"username": username})
    
    return JSONResponse(content={"isDuplicate": existing_user is not None})
    
@app.post("/logout")
async def logout(request: Request):
    request.session.pop("user_id", None)
    return JSONResponse(content={"success": True})

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        session = websocket.scope.get("session", {})
        user = get_current_user(session)
        while True:
            data = await websocket.receive_text()
            message_data = json.loads(data)
            message = {
                "type": "message",
                "author": user["username"],
                "content": message_data["content"]
            }
            await manager.broadcast(json.dumps(message))
            kafka_producer.send_message("chat_messages", message)
            count = redis_handler.increment_message_count()
            await manager.broadcast(json.dumps({"type": "count", "count": count}))
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        message = {
            "type": "message",
            "author": "System",
            "content": f"{user['username']} has left the chat"
        }
        await manager.broadcast(json.dumps(message))
    except Exception as e:
        print(f"WebSocket error: {e}")
        manager.disconnect(websocket)

# 정적 파일 서빙 (index.html, signin-signup.html)
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)