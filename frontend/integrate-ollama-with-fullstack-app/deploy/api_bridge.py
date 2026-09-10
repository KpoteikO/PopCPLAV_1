"""PopCat integration API. Direct Ollama chat; does not run CrewAI or Gradio."""
import os
from typing import Literal
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

OLLAMA_URL = os.getenv('OLLAMA_BASE_URL', 'http://127.0.0.1:11434').rstrip('/')

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with httpx.AsyncClient(base_url=OLLAMA_URL, timeout=httpx.Timeout(290.0, connect=5.0), follow_redirects=False) as client:
        app.state.ollama = client
        yield

app = FastAPI(title='PopCat API', version='1.0.0', lifespan=lifespan)

class Message(BaseModel):
    role: Literal['user', 'assistant']
    content: str = Field(min_length=1, max_length=12000)

class ChatRequest(BaseModel):
    model: str = Field(default='qwen2.5-coder:7b-instruct-q4_K_M', min_length=1, max_length=120, pattern=r'^[a-zA-Z0-9._:/-]+$')
    messages: list[Message] = Field(min_length=1, max_length=40)

@app.get('/health')
async def health():
    return {'status': 'ok', 'service': 'popcat-api', 'mode': 'direct-chat'}

@app.get('/api/models')
async def models():
    return await ollama_request('GET', '/api/tags')

async def ollama_request(method: str, path: str, payload=None):
    try:
        response = await app.state.ollama.request(method, path, json=payload)
        response.raise_for_status()
        return response.json()
    except httpx.TimeoutException:
        raise HTTPException(504, 'Ollama timeout: model loading or inference took too long')
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, 'Ollama request failed; verify model with ollama list')
    except httpx.RequestError:
        raise HTTPException(502, 'Cannot connect to Ollama; check OLLAMA_BASE_URL')
    except ValueError:
        raise HTTPException(502, 'Invalid JSON response from Ollama')

@app.post('/api/chat')
async def chat(request: ChatRequest):
    return await ollama_request('POST', '/api/chat', {
        'model': request.model,
        'messages': [m.model_dump() for m in request.messages],
        'stream': False,
        'options': {'num_ctx': 4096, 'num_predict': 2048},
    })
