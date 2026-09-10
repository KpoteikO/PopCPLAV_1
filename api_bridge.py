# api_bridge.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import subprocess
import json
import asyncio

app = FastAPI()

# Разрешаем запросы с фронтенда (порт 5173)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    message: str
    mode: str = "Полный цикл"
    model: str = "qwen2.5-coder:7b-instruct-q4_K_M"

@app.post("/api/chat")
async def chat(request: ChatRequest):
    # Здесь вызывайте вашего worker_crew_execution
    # или запускайте web_agent.py как подпроцесс

    # Простой пример: запуск через subprocess
    try:
        result = subprocess.run(
            ["python", "web_agent.py", "--mode", request.mode, "--query", request.message],
            capture_output=True,
            text=True,
            timeout=60
        )
        return {"response": result.stdout}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Timeout")
