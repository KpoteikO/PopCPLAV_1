# PopCat Pro — исправление превью

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
docker run --rm --cap-drop ALL --security-opt no-new-privileges \
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
