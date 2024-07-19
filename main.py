import json
import os
import time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from kafka_producer import KafkaMessageProducer
from redis_handler import RedisHandler
from typing import List
from kafka.errors import NoBrokersAvailable

app = FastAPI()

# 세션 미들웨어 추가
app.add_middleware(SessionMiddleware, secret_key="your-secret-key")

# WebSocket 연결을 관리하는 클래스
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

# Kafka 연결 설정
kafka_bootstrap_servers = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092')

def connect_kafka(retries=5, delay=5):
    for _ in range(retries):
        try:
            return KafkaMessageProducer(bootstrap_servers=[kafka_bootstrap_servers])
        except NoBrokersAvailable:
            print(f"Kafka connection failed. Retrying in {delay} seconds...")
            time.sleep(delay)
    raise Exception("Failed to connect to Kafka after multiple attempts")

kafka_producer = connect_kafka()

# Redis 연결 설정
redis_host = os.getenv('REDIS_HOST', 'redis')
redis_handler = RedisHandler(host=redis_host)

# 로그인 상태 확인 미들웨어
@app.middleware("http")
async def check_login_status(request: Request, call_next):
    if request.url.path == "/":
        if "session" not in request.scope:
            return RedirectResponse(url="/signin-signup.html")
        session = request.session
        if not session.get("user"):
            return RedirectResponse(url="/signin-signup.html")
    response = await call_next(request)
    return response

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    client_id = None
    try:
        while True:
            data = await websocket.receive_text()
            if not client_id:
                client_info = json.loads(data)
                client_id = client_info.get('client_id')
                continue

            # 메시지 처리
            message = {
                "type": "message",
                "author": client_id,
                "content": data
            }
            await manager.broadcast(json.dumps(message))
            
            # Kafka로 메시지 전송
            kafka_producer.send_message(json.dumps(message))
            
            # Redis에 메시지 카운트 증가 및 브로드캐스트
            count = redis_handler.increment_message_count()
            await manager.broadcast(json.dumps({"type": "count", "count": count}))

    except WebSocketDisconnect:
        manager.disconnect(websocket)
        if client_id:
            message = {
                "type": "message",
                "author": "System",
                "content": f"{client_id} has left the chat"
            }
            await manager.broadcast(json.dumps(message))

# 로그인 라우트 (예시)
@app.post("/login")
async def login(request: Request):
    data = await request.json()
    username = data.get("username")
    password = data.get("password")
    # 여기에 실제 로그인 로직을 구현하세요
    if username and password:  # 임시 로직, 실제로는 데이터베이스 확인 등이 필요
        request.session["user"] = username
        return {"success": True}
    return {"success": False}

# 로그아웃 라우트 (예시)
@app.post("/logout")
async def logout(request: Request):
    request.session.pop("user", None)
    return {"success": True}

# 정적 파일 서빙 (index.html, signin-signup.html)
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)