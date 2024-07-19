import secrets
from fastapi import FastAPI, HTTPException, Depends, WebSocket, WebSocketDisconnect, Body
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from motor.motor_asyncio import AsyncIOMotorClient
from passlib.context import CryptContext
from jose import JWTError, jwt
from datetime import datetime, timedelta
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import json
import os
from typing import List
from kafka_producer import KafkaMessageProducer
from redis_handler import RedisHandler

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 실제 운영 환경에서는 특정 출처만 허용하도록 설정
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 정적 파일을 위한 디렉토리 설정
app.mount("/static", StaticFiles(directory="static"), name="static")

# Pydantic 모델 정의
class UserIn(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=6)

class UserOut(BaseModel):
    username: str
    email: EmailStr

class Token(BaseModel):
    access_token: str
    token_type: str

class UsernameCheck(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)

# MongoDB 연결
MONGO_URL = os.getenv("MONGO_URI", "mongodb://localhost:27017")
client = AsyncIOMotorClient(MONGO_URL)
db = client.userdb
users_collection = db.users

# Redis 연결
redis_host = os.getenv('REDIS_HOST', 'redis')
redis_handler = RedisHandler(host=redis_host)

# Kafka 연결
kafka_bootstrap_servers = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092')
kafka_producer = KafkaMessageProducer([kafka_bootstrap_servers])

# 비밀번호 해싱
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT 설정
SECRET_KEY = secrets.token_urlsafe(32)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=401,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = await users_collection.find_one({"username": username})
    if user is None:
        raise credentials_exception
    return user

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

@app.get("/")
async def read_index():
    return FileResponse('static/index.html')

@app.post("/signup", response_model=UserOut)
async def signup(username: str = Body(..., embed=True), email: EmailStr = Body(..., embed=True), password: str = Body(..., embed=True)):
    existing_user = await users_collection.find_one({"$or": [{"email": email}, {"username": username}]})
    if existing_user:
        if existing_user["email"] == email:
            raise HTTPException(status_code=400, detail="Email already registered")
        else:
            raise HTTPException(status_code=400, detail="Username already taken")
    hashed_password = pwd_context.hash(password)
    new_user = {"username": username, "email": email, "hashed_password": hashed_password}
    await users_collection.insert_one(new_user)
    return UserOut(**new_user)

@app.post("/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    db_user = await users_collection.find_one({"username": form_data.username})
    if not db_user or not pwd_context.verify(form_data.password, db_user["hashed_password"]):
        raise HTTPException(status_code=400, detail="Incorrect username or password")
    
    access_token = create_access_token(data={"sub": db_user["username"]})
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/check-duplicate")
async def check_duplicate(username: str = Body(..., embed=True)):
    existing_user = await users_collection.find_one({"username": username})
    return {"isDuplicate": existing_user is not None}

@app.get("/user-info", response_model=UserOut)
async def user_info(current_user: dict = Depends(get_current_user)):
    return UserOut(username=current_user["username"], email=current_user["email"])

@app.post("/logout")
async def logout(token: str = Depends(oauth2_scheme)):
    # JWT는 stateless이므로 서버 측에서 무효화할 수 없습니다.
    # 클라이언트 측에서 토큰을 삭제하도록 안내하는 메시지만 반환합니다.
    return {"message": "Logout successful. Please remove the token from the client."}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            message_data = json.loads(data)
            user = await get_current_user(message_data.get("token"))
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
        user = await get_current_user(message_data.get("token"))
        message = {
            "type": "message",
            "author": "System",
            "content": f"{user['username']} has left the chat"
        }
        await manager.broadcast(json.dumps(message))
    except Exception as e:
        print(f"WebSocket error: {e}")
        manager.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)