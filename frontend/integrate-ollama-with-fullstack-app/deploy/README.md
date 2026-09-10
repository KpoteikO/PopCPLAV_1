# PopCat · рабочий контур интеграции

## Что проверено в исходном репозитории
Репозиторий: https://github.com/KpoteikO/PopCPLAV
Изученная версия: 145d9cdcdf58b1c5cf466ee476838de8c5aa019a.

1. api_bridge.py, строки 25–38: subprocess.run запускает web_agent.py, ждёт stdout до 60 секунд. web_agent.py не разбирает --mode/--query, а запускает блокирующий Gradio UI. Поле model не используется; код завершения subprocess не проверяется. Это не API инференса.
2. Dockerfile копирует service.py и запускает service:app. Этот файл содержит только GET /health; POST /api/chat получит 404.
3. requirements.txt содержит только FastAPI и Uvicorn. У web_agent.py / agent_worker.py есть импорты gradio, crewai, ddgs, которых здесь нет. Не заменяйте полный requirements вашего агента этим минимальным комплектом.
4. В дереве нет React frontend, Nginx и Compose. CORS в bridge разрешён только для http://localhost:5173. Другая схема, порт или 127.0.0.1 — другой origin.
5. agent_worker.py использует OLLAMA_HOST как URL клиента. У самой службы Ollama эта переменная — адрес прослушивания. Не смешивайте их. В новом bridge URL называется OLLAMA_BASE_URL.
6. web_agent.py импортирует preview helpers из agent_worker, а не исправленного preview_runtime.py. Вызов demo.launch блокирует выполнение; второй launch_kwargs ниже не применяется. auto_heal_startup содержит fuser -k, но вызов в текущем main закомментирован.
7. Требуют отдельного исправления: inline HTML callback, хранение Process в gr.State, глобальная остановка Ollama, изоляция генерируемого кода. Этот комплект не включает запуск недоверенного кода.

## Архитектура
React (Next.js) -> Nginx :8080 -> Next.js BFF :3000 -> FastAPI :8000 -> Ollama :11434.
Браузер обращается только к относительным /api/*; CORS и OLLAMA_ORIGINS=* не нужны.
PostgreSQL хранит настройки и историю диагностик. Чат вызывает одну модель, НЕ CrewAI-пайплайн. /health Python проверяет процесс, а не Ollama; в панели эти сервисы проверяются отдельно. Успешные health-check не заменяют тест генерации в разделе «Тестовый чат».

## Вариант A — всё в Docker (CPU, независимая Ollama)
Нужен полный исходный код этой панели, Docker Engine и Compose v2. Полный архив popcat-app.tar.gz доступен в разделе «Документация» → «Скачать приложение». Распакуйте его в отдельную директорию. Архив popcat-integration.tar.gz содержит только deploy-файлы и Dockerfile; это не весь frontend.
В корне приложения:

    openssl rand -hex 24 | sed 's/^/POSTGRES_PASSWORD=/' > deploy/.env
    chmod 600 deploy/.env
    docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --build
    docker compose --env-file deploy/.env -f deploy/compose.yaml exec ollama ollama pull qwen2.5-coder:7b-instruct-q4_K_M

Открыть http://127.0.0.1:8080, нажать «Проверить систему», затем «Тестовый чат».
Внутренние имена: app:3000, backend:8000, ollama:11434, nginx:80. Не заменять их на localhost. Настройки из БД имеют приоритет над переменными окружения: при смене способа запуска обновите их в панели.
Контейнер Ollama использует отдельный volume и CPU. Это НЕ существующая Ollama и не автоматически настроенный RX 6600. Для уже установленной Ollama используйте вариант B, чтобы не менять драйверы и не загружать модели повторно.

Проверки:

    docker compose --env-file deploy/.env -f deploy/compose.yaml ps
    docker compose --env-file deploy/.env -f deploy/compose.yaml logs --tail=100 nginx backend app ollama
    curl -fsS http://127.0.0.1:8080/health
    curl -fsS http://127.0.0.1:8080/api/health
    curl -fsS http://127.0.0.1:8080/api/models
    curl -fsS http://127.0.0.1:8080/api/chat -H 'Content-Type: application/json' -d '{"messages":[{"role":"user","content":"Ответь одним словом: работает?"}]}'

## Вариант B — существующая локальная Ollama (рекомендовано для Manjaro)
Все процессы ниже должны быть на вашей машине, НЕ в облачном preview.
1. Проверьте службу и модель:

    sudo systemctl enable --now ollama
    curl -fsS http://127.0.0.1:11434/api/tags
    ollama pull qwen2.5-coder:7b-instruct-q4_K_M
    ollama ps

2. Запустите отдельный Python bridge, не подменяя файлы исходного агента:

    python -m venv deploy/.venv
    deploy/.venv/bin/python -m pip install -r deploy/requirements.txt
    deploy/.venv/bin/python -m pip check
    OLLAMA_BASE_URL=http://127.0.0.1:11434 deploy/.venv/bin/python -m uvicorn api_bridge:app --app-dir deploy --host 127.0.0.1 --port 8000

3. Установите Node.js 22+, PostgreSQL и создайте БД / роль приложения. В корневом .env задайте ваш DATABASE_URL (не публикуйте пароль), OLLAMA_BASE_URL=http://127.0.0.1:11434, BACKEND_URL=http://127.0.0.1:8000, NGINX_URL=http://127.0.0.1:8080. После этого:

    npm ci
    npx drizzle-kit push
    npm run build
    npm run start -- --hostname 127.0.0.1

4. Добавьте deploy/nginx.local.conf в директорию server-конфигураций, подключённую через include в /etc/nginx/nginx.conf (на Manjaro проверьте nginx -T). Не перезаписывайте весь nginx.conf.

    sudo nginx -t
    sudo systemctl reload nginx

Если Nginx ещё не запущен: sudo systemctl enable --now nginx. Откройте http://127.0.0.1:8080. Для постоянной работы оформите Node и Uvicorn как systemd services от непривилегированного пользователя с WorkingDirectory корня проекта, EnvironmentFile и Restart=on-failure.

## Если backend в Docker, а Ollama на хосте
localhost внутри контейнера — контейнер. На Linux добавьте extra_hosts: ["host.docker.internal:host-gateway"] нужным сервисам и используйте http://host.docker.internal:11434. Ollama, слушающая только 127.0.0.1, недоступна через bridge: потребуется адрес docker bridge и firewall, разрешающий только эту сеть. Не открывайте 11434 в интернет. Более простой безопасный путь для Manjaro — вариант B.

## Ошибки и их проверка
- Connection refused: неверный адрес, процесс не запущен, порт не слушается. Проверить ss -ltnp и systemctl status ollama.
- 404 /api/chat: запущен service:app вместо api_bridge:app или proxy_pass удаляет /api/.
- 502: Nginx не видит app, либо API не видит Ollama. Читайте журнал конкретного сервиса.
- 504: загрузка модели / генерация дольше тайм-аута; уменьшите модель и контекст, проверьте RAM / ollama ps. Тайм-ауты: Ollama-клиент 290s, BFF 300s, Nginx 310s.
- CORS / mixed content: открывайте UI через Nginx и используйте относительные /api/*, а не прямой HTTP URL Ollama из HTTPS-страницы.
- Model not found: ollama list, имя модели должно совпадать точно, включая регистр и тег.
- GPU: поддержка RX 6600 зависит от версии Ollama/ROCm/Vulkan; этот комплект не обещает GPU-ускорение. Начните с 7B Q4, num_ctx=4096, OLLAMA_NUM_PARALLEL=1.

## Безопасность и ограничения
Панель предназначена для одного доверенного локального пользователя. Не публиковать без TLS, авторизации и rate limits. Все настройки и журналы общие. Разрешённые адреса фиксируются сервером через SERVICE_ALLOWED_ORIGINS (origin через запятую); это ограничивает произвольные серверные запросы. Не включайте туда недоверенные DNS-имена. Запросы не следуют перенаправлениям. Не монтировать Docker socket. Облачный preview не имеет доступа к localhost вашего компьютера и не устанавливает программы на него.

## Документация
https://docs.ollama.com/faq
https://docs.ollama.com/api/tags
https://docs.ollama.com/api/chat
https://nginx.org/en/docs/http/ngx_http_proxy_module.html
