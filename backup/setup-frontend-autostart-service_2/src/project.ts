export const defaultTask = 'Спроектируй и напиши микросервис на Python (FastAPI) для стеганографического анализа изображений PNG/JPG: извлечение LSB, суммы пикселей, поиск текстовых аномалий. Добавь защиту от вредоносных файлов и Path Traversal, исправления и pytest. Результат — проект с файлами, а не код в чате.';
export const projectFiles: Record<string, string> = {
  'app/__init__.py': '',
  'app/main.py': `from fastapi import FastAPI, File, HTTPException, UploadFile
from starlette.responses import JSONResponse
from app.analysis import analyze, MAX_FILE_BYTES

MAX_REQUEST_BYTES = MAX_FILE_BYTES + 64 * 1024


class BodyLimit:
    """Bound the whole request before multipart parsing, including chunked requests."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > MAX_REQUEST_BYTES:
                response = JSONResponse({"detail": "Request too large"}, status_code=413)
                return await response(scope, receive, send)
            if not message.get("more_body", False):
                break
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


app = FastAPI(title="StegoLab API", version="1.0.0")
app.add_middleware(BodyLimit)


@app.get("/health")
def health():
    return {"status": "ok", "service": "stegolab"}


def inspect_upload(file: UploadFile):
    # Filename and declared MIME type are deliberately not trusted.
    try:
        data = file.file.read(MAX_FILE_BYTES + 1)
        return analyze(data)
    finally:
        file.file.close()


@app.post("/analyze")
def analyze_image(file: UploadFile = File(...)):
    return inspect_upload(file)


@app.post("/lsb")
def extract_lsb(file: UploadFile = File(...)):
    return inspect_upload(file)["lsb"]


@app.post("/metrics")
def pixel_metrics(file: UploadFile = File(...)):
    return inspect_upload(file)["metrics"]


@app.post("/anomalies")
def text_anomalies(file: UploadFile = File(...)):
    return inspect_upload(file)["anomalies"]
`,
  'app/analysis.py': `import io
import re
import warnings

import numpy as np
from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 4_000_000
MAX_LSB_BYTES = 65536
MAX_SCAN_BYTES = 2 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def decode_image(data: bytes) -> np.ndarray:
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, "File exceeds 5 MiB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {"PNG", "JPEG"}:
                    raise HTTPException(415, "Only PNG and JPEG are supported")
                if image.width * image.height > MAX_PIXELS:
                    raise HTTPException(413, "Image exceeds 4 million pixels")
                image.verify()
            # verify() invalidates the decoder. Reopen before loading pixels.
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                return np.array(image.convert("RGB"), dtype=np.uint8)
    except HTTPException:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(413, "Image dimensions are too large") from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise HTTPException(400, "Invalid or damaged image") from None


def lsb_extract(pixels: np.ndarray) -> dict:
    # RGB, row-major order. First extracted bit is the most significant bit.
    bits = (pixels.reshape(-1) & 1)
    available = len(bits) // 8
    count = min(available, MAX_LSB_BYTES)
    payload = np.packbits(bits[:count * 8], bitorder="big").tobytes()
    return {
        "hex": payload.hex(),
        "byte_count": len(payload),
        "available_bytes": available,
        "truncated": available > count,
        "discarded_bits": len(bits) % 8,
        "bit_order": "RGB row-major, MSB-first",
    }


def text_scan(data: bytes) -> dict:
    sample = data[:MAX_SCAN_BYTES]
    matches = []
    for match in re.finditer(rb"[ -~]{6,}", sample):
        matches.append({"offset": match.start(), "text": match.group()[:160].decode("ascii")})
        if len(matches) == 20:
            break
    return {
        "ascii_ratio": sum(32 <= byte <= 126 for byte in sample) / len(sample) if sample else 0.0,
        "strings": matches,
        "scanned_bytes": len(sample),
        "truncated": len(data) > len(sample),
        "notice": "Printable strings are a heuristic, not evidence of hidden data.",
    }


def analyze(data: bytes) -> dict:
    pixels = decode_image(data)
    height, width, _ = pixels.shape
    lsb = lsb_extract(pixels)
    return {
        "metrics": {
            "width": width, "height": height,
            "pixel_count": int(width * height),
            "red_sum": int(pixels[:, :, 0].sum(dtype=np.uint64)),
            "green_sum": int(pixels[:, :, 1].sum(dtype=np.uint64)),
            "blue_sum": int(pixels[:, :, 2].sum(dtype=np.uint64)),
        },
        "lsb": lsb,
        "anomalies": {
            "file": text_scan(data),
            "lsb": text_scan(bytes.fromhex(lsb["hex"])),
        },
    }
`,
  'tests/test_analysis.py': `import io

import numpy as np
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from app.main import app, MAX_REQUEST_BYTES
from app import analysis

client = TestClient(app)


def image_bytes(fmt="PNG", size=(4, 3), color=(10, 20, 30)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format=fmt)
    return buffer.getvalue()


def upload(data, filename="image.png", endpoint="/analyze"):
    return client.post(endpoint, files={"file": (filename, data, "image/png")})


def test_health():
    assert client.get("/health").json()["status"] == "ok"


def test_metrics_and_json_types():
    result = upload(image_bytes()).json()["metrics"]
    assert result == {"width": 4, "height": 3, "pixel_count": 12,
                      "red_sum": 120, "green_sum": 240, "blue_sum": 360}
    assert all(type(value) is int for value in result.values())


def test_lsb_bit_weights_and_incomplete_byte():
    # 01000001 = ASCII A. Summing the bits would incorrectly produce 2.
    pixels = np.array([0, 1, 0, 0, 0, 0, 0, 1, 1], dtype=np.uint8).reshape(1, 3, 3)
    result = analysis.lsb_extract(pixels)
    assert result["hex"] == "41"
    assert result["discarded_bits"] == 1


def test_printable_string_offsets():
    result = analysis.text_scan(b"\\x00secret message\\x01")
    assert result["strings"][0] == {"offset": 1, "text": "secret message"}


def test_empty_text_scan():
    assert analysis.text_scan(b"")["ascii_ratio"] == 0


def test_jpeg_supported():
    assert upload(image_bytes("JPEG"), "image.jpg").status_code == 200


def test_invalid_file_and_mime_spoof():
    assert upload(b"not an image", "fake.png").status_code == 400


def test_unsupported_format():
    assert upload(image_bytes("GIF")).status_code == 415


def test_empty_upload():
    assert upload(b"").status_code == 400


def test_truncated_image():
    assert upload(image_bytes()[:25]).status_code == 400


def test_traversal_filename_is_never_used_as_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert upload(image_bytes(), "../../outside.png").status_code == 200
    assert list(tmp_path.iterdir()) == []


def test_request_size_limit():
    response = client.post("/analyze", content=b"x" * (MAX_REQUEST_BYTES + 1))
    assert response.status_code == 413


def test_file_limit(monkeypatch):
    monkeypatch.setattr(analysis, "MAX_FILE_BYTES", 10)
    assert upload(image_bytes()).status_code == 413


def test_pixel_limit(monkeypatch):
    monkeypatch.setattr(analysis, "MAX_PIXELS", 5)
    assert upload(image_bytes()).status_code == 413


def test_output_is_bounded(monkeypatch):
    monkeypatch.setattr(analysis, "MAX_LSB_BYTES", 1)
    result = analysis.lsb_extract(np.zeros((4, 4, 3), dtype=np.uint8))
    assert result["byte_count"] == 1
    assert result["truncated"] is True


@pytest.mark.parametrize("endpoint,key", [
    ("/lsb", "hex"), ("/metrics", "pixel_count"), ("/anomalies", "file")
])
def test_separate_endpoints(endpoint, key):
    response = upload(image_bytes(), endpoint=endpoint)
    assert response.status_code == 200
    assert key in response.json()
`,
  'requirements.txt': `fastapi>=0.115.12,<1.0
uvicorn[standard]>=0.34.0,<1.0
python-multipart>=0.0.22,<1.0
Pillow>=12.1.0,<13.0
numpy>=2.2.0,<3.0
`,
  'requirements-dev.txt': `-r requirements.txt
pytest>=8.3,<10.0
httpx>=0.28,<1.0
pip-audit>=2.8,<3.0
bandit>=1.8,<2.0
`,
  'run.sh': `#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo "Install Python 3.11+ first"; exit 1; }
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
`,
  'Dockerfile': `FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt requirements-dev.txt ./
RUN python -m pip install --no-cache-dir -r requirements-dev.txt
COPY app ./app
COPY tests ./tests
RUN python -m pytest -q
RUN useradd --create-home --uid 10001 appuser
USER appuser
EXPOSE 8001
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--limit-concurrency", "8", "--timeout-keep-alive", "5"]
`,
  '.dockerignore': `.venv
__pycache__
.pytest_cache
.git
*.png
*.jpg
*.zip
`,
  'SECURITY.md': `# Проверка безопасности — изменения подготовлены, инструменты не запускались здесь

## Исправления относительно примера из чата
- Расширение и MIME от клиента не считаются доказательством формата. Проверяется декодер Pillow.
- Имя UploadFile не передаётся в open и не формирует путь. Загрузка не сохраняется под пользовательским именем.
- Ограничение всего тела запроса выполняется ASGI middleware ДО multipart parser, включая chunked body.
- Ограничение файла: 5 MiB; изображения: 4 млн пикселей. DecompressionBombWarning становится ошибкой.
- После verify изображение открывается заново. Проверяется полная загрузка пикселей.
- LSB упаковывается с весами битов (np.packbits), а не суммой единиц.
- Размер ответа LSB ограничен 64 KiB; поиск строк ограничен 2 MiB и 20 результатами.
- Суммы преобразованы в Python int. pixel_count = ширина × высота, без множителя каналов.
- Ошибки 400/413/415 сохраняются; внутренние исключения не выводятся пользователю.
- Docker запускает тесты при сборке и работает не от root.

## Остаточные риски
Это локальный тестовый сервис, не завершённый аудит и не production-аттестация.
Загрузка может временно записываться multipart parser в системную временную папку; пользовательское имя не используется.
Каждый запрос ограничен, но множество параллельных запросов требует общего лимита памяти и частоты.
Не публикуйте порт в интернет без аутентификации, TLS, rate limits и изоляции.
Pillow — сложный нативный декодер: держите зависимости обновлёнными и ограничивайте контейнер.
Диапазоны версий не являются lock-файлом. После установки и аудита зафиксируйте разрешённые версии.
PNG подходит для проверки LSB; JPEG с потерями может разрушить скрытое сообщение.
Найденный ASCII-текст не доказывает наличие стеганографии.

## Выполнить локально
.venv/bin/python -m pytest -v
.venv/bin/python -m pip_audit
.venv/bin/python -m bandit -r app

Не называйте аудит или тесты пройденными до получения реального вывода этих команд.
`,
  'README.md': `# StegoLab — тестовый FastAPI-проект

Проект подготовлен встроенным шаблоном интерфейса для задачи анализа изображений.
Это не результат вызова Ollama. Python, Docker и pytest в браузере НЕ выполнялись.

## 1. Распаковка
Скачайте ZIP, распакуйте папку stegolab-api, например в:
/home/beluga/ai-multi-agent/projects/stegolab-api
Не заменяйте ею существующий backend или frontend.

## 2. Прямой запуск (Ubuntu / Debian)
Установите Python 3.11+ и пакет venv для вашей версии Python.
Для Ubuntu 24.04:
sudo apt update
sudo apt install python3 python3-venv

Откройте терминал в распакованной папке:
cd /home/beluga/ai-multi-agent/projects/stegolab-api
bash run.sh

Скрипт создаст .venv, установит requirements-dev.txt (включая uvicorn),
выполнит pytest и запустит API только при успешных тестах.
Для установки пакетов нужен интернет. Если шаг завершился ошибкой, исправьте её до продолжения.
Откройте http://localhost:8001/docs — это настоящее интерактивное превью API.
Порт 8001 выбран, чтобы не занимать обычный порт 8000 основного агента.
Ctrl+C останавливает сервер.

## 3. Docker (альтернатива)
Нужен установленный и работающий Docker Engine.
В папке проекта:
docker build -t stegolab-api .
docker run --rm --name stegolab-api --memory=512m --cpus=1 --pids-limit=128 -p 127.0.0.1:8001:8001 stegolab-api

Тесты запускаются в Docker build. При ошибке сборка остановится.
Зависимости устанавливаются внутри образа. CMD использует python -m uvicorn.
После изменения Dockerfile обязательно пересоберите образ.
Откройте http://localhost:8001/docs.

## 4. Проверка
curl http://127.0.0.1:8001/health
curl -X POST -F file=@image.png http://127.0.0.1:8001/analyze

POST /lsb — первые 64 KiB LSB-потока, порядок RGB, старший бит байта первым.
POST /metrics — суммы RGB, размеры, количество пикселей.
POST /anomalies — эвристический поиск ASCII-строк в файле и LSB.
POST /analyze — все результаты вместе.

Тесты создают PNG/JPEG в памяти, внешние test_image.jpg не нужны.
В tests/test_analysis.py — 18 тестовых случаев с учётом параметризации.

## 5. Почему агент пишет код в чат
Ollama генерирует ответы, но сама не создаёт файлы и не запускает процессы.
Бэкенду агента нужен цикл инструментов: create_file -> install -> test -> repair -> run.
Промпт без обработчика инструментов этого не обеспечивает.
В доступном фронтэнде обработчика вашего агента нет, поэтому его автоматическое исполнение не исправлено.

Для интеграции добавьте на стороне backend:
1. Изолированную рабочую папку для каждой задачи.
2. Проверку каждого пути через resolve + проверку принадлежности workspace; запрет symlink escape.
3. Инструменты чтения/создания файлов с лимитами размера и количества.
4. Runner в контейнере без host mounts, docker.sock и секретов, с лимитами CPU/RAM/времени.
5. Установку зависимостей внутри runner, тесты с реальным exit_code и выводом.
6. Цикл исправлений с максимальным числом попыток; ошибка не означает успех.
7. Manifest файлов, ZIP-артефакт и URL живого превью только после healthcheck.
8. Аутентификацию управляющего API; не выполняйте произвольный shell из ответа модели на хосте.

Контракт endpoint вашего агента неизвестен. Этот интерфейс не притворяется подключённым к нему.
См. SECURITY.md для ограничений и локального аудита.
`
};

export const directCommand = 'cd /home/beluga/ai-multi-agent/projects/stegolab-api\nbash run.sh';
export const dockerCommand = 'docker build -t stegolab-api .\ndocker run --rm --name stegolab-api --memory=512m --cpus=1 --pids-limit=128 -p 127.0.0.1:8001:8001 stegolab-api';
export const testNames = ['Healthcheck API', 'Суммы RGB и JSON-типы', 'Порядок и веса битов LSB', 'Смещения ASCII-строк', 'Пустой бинарный поток', 'Поддержка JPEG', 'Подмена MIME-типа', 'Запрет GIF', 'Пустая загрузка', 'Повреждённый PNG', 'Path Traversal в имени', 'Лимит тела запроса', 'Лимит размера файла', 'Лимит пикселей', 'Ограничение LSB-ответа', 'Эндпоинт /lsb', 'Эндпоинт /metrics', 'Эндпоинт /anomalies'];
