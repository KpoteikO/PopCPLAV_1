import requests
import json
import time

base_url = "http://127.0.0.1:7860/gradio_api"

# Шаг 1: Отправляем запрос
payload = {
    "data": [
        "TEST",
        [],
        "🧠 Полный цикл (Dev + Sec + Patch + QA)",
        "gemma-4-12B-coder-fable5-composer2.5-v1-GGUF:Q4_K_M",
        "deepseek-r1:8b",
        "Llama-3-8B-Instruct-Cybersecurity:Q4_K_M",
        "Qwen2.5-Coder:7b-instruct-q4_K_M",
        "Qwen2.5-Coder:7b-instruct-q4_K_M"
    ]
}

res = requests.post(f"{base_url}/call/process_chat", json=payload)
print("POST response:", res.status_code, res.json())

event_id = res.json().get("event_id")
print("Event ID:", event_id)

# Шаг 2: Подписываемся на SSE
print("\n--- SSE stream ---")
with requests.get(f"{base_url}/call/process_chat/{event_id}", stream=True) as r:
    for line in r.iter_lines():
        if line:
            print(line.decode('utf-8'))
