export const models = [
 ['Qwen2.5-Coder:7b-instruct-q4_K_M','4.7 GB'], ['gemma-4-12B-coder-fable5-composer2.5-v1-GGUF:Q4_K_M','7.4 GB'], ['llama3.1-8b-abliterated:latest','4.7 GB'], ['dolphin-mistral:latest','4.1 GB'], ['deepseek-r1:8b','5.2 GB'], ['Llama-3-8B-Instruct-Cybersecurity:Q4_K_M','4.9 GB'], ['qwen2.5:7B','4.7 GB'], ['the-xploiter:latest','9.2 GB'], ['Qwen2.5-Coder:1.5b-base','986 MB'], ['qwen3.5-claude-4.6-opus:4b','5.3 GB'], ['llama3.1:8b','4.9 GB'], ['deepseek-r1:14b','9.0 GB'], ['Qwen2.5-Coder:14b','9.0 GB'], ['nomic-embed-text:latest','274 MB'], ['gemma4:e2b','7.2 GB'], ['gemma4:e4b','9.6 GB'], ['Qwen2.5-Coder:7B','8.1 GB']
];
export const projectFiles: Record<string, string> = {
 'preview_runtime.py': `"""Explicit FastAPI preview launcher. Run only code you trust.
Standalone replacement for the preview process helpers; see README.md.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import venv

BASE = Path(os.environ.get("AGENT_BASE_DIR", "~/ai-multi-agent")).expanduser().resolve()
OUTPUT = BASE / "output"


def safe_path(name):
    base = OUTPUT.resolve()
    target = (base / name).resolve()
    if not target.is_relative_to(base):
        raise ValueError("Path is outside output directory")
    return target


def checked(command, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    if result.returncode:
        raise RuntimeError((result.stdout + result.stderr)[-4000:])
    return result


def stop_any_preview(info):
    if not info:
        return
    proc = info.get("proc")
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
    log = info.get("log")
    if log:
        log.close()


def start_live_preview_process(service_filename):
    info = {"mode": "process"}
    try:
        source = safe_path(service_filename)
        if source.suffix != ".py" or not source.stem.isidentifier():
            raise ValueError("Select a Python service with a valid module name")
        if not source.is_file() or source.parent != OUTPUT.resolve():
            raise ValueError("Service must be a file directly inside output/")
        # Isolate each run to prevent dependency races between sessions.
        runtime = Path(tempfile.mkdtemp(prefix="preview-", dir=BASE))
        info["runtime"] = str(runtime)
        venv.EnvBuilder(with_pip=True).create(runtime / "venv")
        python = str(runtime / "venv/bin/python")
        requirements = OUTPUT / "requirements.txt"
        if requirements.is_file():
            checked([python, "-m", "pip", "install", "-r", str(requirements)], timeout=180)
        # Uvicorn is a runtime dependency, not necessarily a source import.
        checked([python, "-m", "pip", "install", "fastapi", "uvicorn[standard]"], timeout=180)
        checked([python, "-m", "pip", "check"], timeout=30)
        checked([python, "-c", "import uvicorn, fastapi"], timeout=10)
        log = (runtime / "server.log").open("w+")
        info["log"] = log
        # Parent owns the socket; no free-port check / bind race.
        import socket
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(128)
            port = listener.getsockname()[1]
            proc = subprocess.Popen(
                [python, "-m", "uvicorn", source.stem + ":app", "--fd", str(listener.fileno())],
                cwd=OUTPUT, pass_fds=(listener.fileno(),), stdout=log,
                stderr=subprocess.STDOUT,
            )
        info.update(proc=proc, port=port, url=f"http://127.0.0.1:{port}/docs")
        return info
    except Exception as exc:
        stop_any_preview(info)
        info["error"] = str(exc)
        return info


def wait_for_preview_ready(info, timeout=25):
    if info.get("error"):
        return False, info["error"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        proc = info["proc"]
        if proc.poll() is not None:
            log = info["log"]
            log.seek(0)
            message = log.read()[-4000:]
            stop_any_preview(info)
            return False, message or f"Process exited: {proc.returncode}"
        try:
            with urllib.request.urlopen(info["url"], timeout=1) as response:
                if response.status == 200:
                    return True, "HTTP readiness check passed"
        except (OSError, ValueError):
            pass
        time.sleep(0.3)
    stop_any_preview(info)
    return False, "Readiness timeout; process stopped. See runtime/server.log"
`,
 'requirements.txt': 'fastapi\nuvicorn[standard]\n',
 'Dockerfile': `FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt \\
    && python -m pip install --no-cache-dir 'uvicorn[standard]' fastapi \\
    && python -m pip check \\
    && python -c "import uvicorn, fastapi"
RUN useradd --create-home appuser && chown appuser /app
COPY --chown=appuser:appuser service.py .
USER appuser
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "service:app", "--host", "0.0.0.0", "--port", "8000"]
`,
 'service.py': `"""Minimal health-check example, not an agent-generated project."""
from fastapi import FastAPI

app = FastAPI(title="PopCat Preview", version="1.0.0")

@app.get("/health", tags=["System"])
def health():
    return {"status": "ok", "service": "popcat-preview"}
`,
 'README.md': `# PopCat Pro — исправление превью

Это отдельный комплект интеграции, а не полная замена agent_worker.py.
Веб-интерфейс — клиент и демонстрация. Он не запускает Python или Docker
и не имеет доступа к вашей Manjaro-системе. Сервис service.py — пример.

## Причина ошибки
Uvicorn отсутствует в .preview_venv и в финальном Docker-образе.
В исходном коде start_live_preview_process не вызывает scaffold перед
установкой зависимостей. В code_only scaffold также не создаётся.
Dockerfile сохраняется между проектами и может содержать старый модуль.
Проверка строки FastAPI( не гарантирует наличие runtime-зависимостей.

## Быстрое восстановление (выполнить локально)
cd ~/ai-multi-agent
python -m venv .preview_venv
.preview_venv/bin/python -m pip install 'uvicorn[standard]' fastapi
.preview_venv/bin/python -m pip check
.preview_venv/bin/python -c 'import uvicorn; print(uvicorn.__version__)'

Не используйте sudo pip или --break-system-packages.
Добавьте uvicorn[standard] к зависимостям сервиса независимо от импортов.
Не заменяйте полный requirements.txt минимальным файлом этого примера:
сохраните SQLAlchemy, библиотеки авторизации и прочие зависимости проекта.

## Надёжный прямой запуск
Сохраните preview_runtime.py рядом с agent_worker.py.
В web_agent.py импортируйте start_live_preview_process и
wait_for_preview_ready из preview_runtime. Этот wait поддерживает только
прямой процесс: для Docker сохраните прежний helper под другим именем
и выбирайте его по info['mode']. Аналогично диспетчеризуйте stop_any_preview.
Новый runtime использует уникальное venv на запуск, HTTP-проверку готовности,
проверку pip, безопасные пути, файл логов вместо блокирующего PIPE.
Он предназначен для Linux. После завершения удаляйте неиспользуемые preview-*
папки вручную. Это venv, НЕ защитная песочница: исполняйте доверенный код.

## Docker
Шаблон рассчитан на service.py. Замените его имя и service:app на ваш модуль.
Убедитесь, что requirements.txt полный, затем пересоберите образ:
docker build --no-cache -t popcat-preview .
docker run --rm --cap-drop ALL --security-opt no-new-privileges \\
  --memory 1g --cpus 2 -p 127.0.0.1:8000:8000 popcat-preview
Откройте http://127.0.0.1:8000/docs и проверьте GET /health.
Не публикуйте порт на 0.0.0.0 без необходимости.
Для недоверенного кода используйте более строгую изоляцию, без секретов
и монтирования Docker socket. Docker сам по себе не абсолютная защита.

## Другие важные исправления исходного проекта
- safe_path: Path.resolve + is_relative_to, не startswith; применить ко всем записям.
- sanitize_html=True; убрать inline onclick и экранировать HTML.
- Не хранить Process/Popen в gr.State (требует deepcopy): хранить ID сессии,
  а процессы — в защищённом реестре сервера; очистка только своей сессии.
- Читать multiprocessing.Queue во время работы, не полагаться на empty().
  Использовать get(timeout=...), queue.Empty и закрывать очереди в finally.
- Не вызывать общий abort_ollama_hardware при остановке одной сессии.
- Не убивать неизвестный процесс на порту 7860 через fuser -k.
- Разделить output по project_id, выбирать явный манифест, не последний файл.
- Не перезаписывать отредактированный код сырым результатом callback.
- Сохранять и показывать фактический exit code pytest, а не мнение модели.

## Полезные навыки
Environment Doctor: pip check, import uvicorn, HTTP readiness.
Security Review: локальный bandit, отчёт с CWE; не обещает отсутствие уязвимостей.
Dependency Audit: pip-audit (требует сети); фиксировать версии после проверки.
Regression Tests: pytest + httpx; запускать только в изолированном окружении.
RAG: nomic-embed-text на CPU, проверять размерность и версию embedding-модели.

## Ryzen 7 5700G / 16 GB RAM / RX 6600 8 GB
Начните с Qwen2.5-Coder:7b-instruct-q4_K_M, context 4096, один запрос.
Размер файла модели не равен VRAM: оставьте запас для KV-кэша и GPU.
OLLAMA_MAX_LOADED_MODELS=1 и OLLAMA_NUM_PARALLEL=1 задаются службе Ollama.
Поддержка ускорения RX 6600 зависит от backend и версии драйвера; проверьте
ollama ps и журнал службы, не считайте загрузку GPU гарантированной.
14B/9 GB модели потребуют частичного CPU offload и могут быть медленнее.

## Подключение веб-клиента
В настройках задайте URL Ollama и нажмите «Проверить подключение».
Клиент использует GET /api/tags и POST /api/chat (stream=false).
Нужны разрешённый origin (OLLAMA_ORIGINS) и совместимая схема HTTP/HTTPS.
Не открывайте Ollama публично. Веб-клиент делает один запрос выбранной модели,
а не исполняет CrewAI-пайплайн. Навыки задают рекомендации для промпта.
`
};
export const demoAnswer = 'Нашёл причину сбоя превью. Uvicorn — зависимость запуска, поэтому его необходимо устанавливать даже без import uvicorn в коде сервиса.\n\n1. Перед запуском проверьте зависимости через тот же Python, которым запускается сервис.\n2. Добавьте uvicorn[standard] в requirements.txt и используйте python -m uvicorn в Dockerfile.\n3. Пересоберите Docker-образ: старый слой может не содержать пакет.\n4. Проверяйте HTTP-готовность, а при ошибке останавливайте процесс и сохраняйте лог.\n\nКомплект исправлений и подробная инструкция доступны во вкладке «Файлы проекта». Это демонстрационный разбор предоставленного кода — команды на вашем компьютере не выполнялись.';
