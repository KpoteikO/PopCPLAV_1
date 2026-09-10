# PopCat Control Panel

Полноценная React / Next.js панель для локальной Ollama, Nginx и FastAPI с настройками и журналом в PostgreSQL (Drizzle ORM).

Это отдельный комплект интеграции для https://github.com/KpoteikO/PopCPLAV, а не изменённый удалённый репозиторий. Исходные Gradio/CrewAI файлы не перезаписываются. Чат напрямую вызывает выбранную модель через Python API; агентный pipeline не запускается.

## Быстрый запуск Docker (отдельная Ollama на CPU)
Распакуйте полный архив в отдельную папку и из её корня выполните:

    openssl rand -hex 24 | sed 's/^/POSTGRES_PASSWORD=/' > deploy/.env
    chmod 600 deploy/.env
    docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --build
    docker compose --env-file deploy/.env -f deploy/compose.yaml exec ollama ollama pull qwen2.5-coder:7b-instruct-q4_K_M

Откройте http://127.0.0.1:8080. Нажмите «Проверить систему», затем отправьте сообщение в «Тестовом чате».

## Уже установлена локальная Ollama / Manjaro
Рекомендуется нативный запуск без Docker, чтобы сохранить существующие модели и драйверы GPU. Подробные команды — в [deploy/README.md](deploy/README.md), вариант B. В Debian/Ubuntu для создания Python venv может потребоваться пакет python3-venv; на Manjaro используйте штатный Python и отдельное venv.

Node.js: 22+. Настройте локальную БД PostgreSQL и создайте корневой .env по .env.example. Не используйте примерные учетные данные без замены.

## Архитектура
Browser -> Nginx -> Next.js BFF -> FastAPI -> Ollama.
Next.js BFF также выполняет HTTP health-check и сохраняет настройки/результаты в PostgreSQL. Frontend делает только same-origin запросы. Проверка Backend не заменяет проверку Ollama и тест генерации.

## Разработка
    npm ci
    npx drizzle-kit push
    npm run dev

Проверки: npx next typegen, npm exec tsc -- --noEmit, npm run build.
Браузерные тесты: TEST_BASE_URL=http://127.0.0.1:3000 npx playwright test.
Python API: python -m unittest discover -s deploy -p 'test_*.py' (в окружении с deploy/requirements.txt).

## Безопасность
Панель — для доверенного локального пользователя; настройки и журналы общие. По умолчанию только порт Nginx привязан к 127.0.0.1. Не публикуйте без TLS, аутентификации и rate limits. Не открывайте Ollama, PostgreSQL, Docker socket во внешнюю сеть. Полный архив не содержит .env и секретов. Проверки preview не имеют доступа к вашему ПК.

Полный разбор проблем исходников, ограничения и troubleshooting — в deploy/README.md.
