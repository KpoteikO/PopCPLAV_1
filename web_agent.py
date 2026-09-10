import os
import json
import time
import multiprocessing
import asyncio
import subprocess
import socket
import traceback
from datetime import datetime

import gradio as gr

from agent_worker import (
    BASE_DIR, WORKING_DIR, DB_PATH,
    get_installed_ollama_models, get_loaded_ollama_models, abort_ollama_hardware,
    get_conversations_list, save_chat_to_db, load_chat_by_id, delete_chat_db, rename_chat_db,
    fetch_web_sources, format_interactive_markdown_sources, clean_code_output,
    force_save_code, detect_local_file_content, get_latest_project_file, git_commit_project,
    worker_crew_execution, slugify_filename,
    docker_available, start_live_preview_process, start_live_preview_docker,
    wait_for_preview_ready, stop_any_preview, build_project_zip,
)

INSTALLED_MODELS = get_installed_ollama_models()


def _pick_default(preferred_list, fallback_index=0):
    for name in preferred_list:
        if name in INSTALLED_MODELS:
            return name
    return INSTALLED_MODELS[fallback_index] if INSTALLED_MODELS else ""


# Дефолт для роли "Безопасность" раньше указывал на тот же Qwen2.5-Coder:7B,
# что и кодер/патчер/QA, хотя в списке моделей есть специально дообученная
# Llama-3-8B-Instruct-Cybersecurity — теперь предпочитаем именно её.
DEFAULT_GENERAL = _pick_default(["Qwen2.5-Coder:7b-instruct-q4_K_M", "Qwen2.5-Coder:7B"])
DEFAULT_CODER = _pick_default(["Qwen2.5-Coder:7b-instruct-q4_K_M", "Qwen2.5-Coder:7B", "dolphin-mistral:latest"])
DEFAULT_CYBER = _pick_default(["Llama-3-8B-Instruct-Cybersecurity:Q4_K_M", "Qwen2.5-Coder:7B", "Qwen2.5-Coder:7b-instruct-q4_K_M"])
DEFAULT_PATCH = _pick_default(["Qwen2.5-Coder:7b-instruct-q4_K_M", "Qwen2.5-Coder:7B"])
DEFAULT_QA = _pick_default(["Qwen2.5-Coder:7b-instruct-q4_K_M", "Qwen2.5-Coder:7B"])


def terminate_active_agent_process(proc):
    """Раньше держал единственный процесс в модульном глобале
    CURRENT_CREW_PROCESS — общем для ВСЕХ пользователей/вкладок Gradio.
    Теперь процесс приходит явным аргументом из per-session gr.State,
    так что "Стоп" в одной сессии не может оборвать задачу другой."""
    if proc is not None and proc.is_alive():
        print("\n[CYBER_KILL] Немедленное прерывание процесса агентов...")
        proc.terminate()
        time.sleep(0.1)
        if proc.is_alive():
            proc.kill()
        proc.join(timeout=0.5)
    abort_ollama_hardware()


# ====================== РЕНДЕР САЙДБАРА ======================
def render_sidebar_html(active_chat_id="default"):
    convs = get_conversations_list()
    if not convs:
        return "<div style='color: #4b5563; font-size: 13px; padding: 16px; text-align: center;'>[ ПУСТАЯ БАЗА ]</div>"

    items_html = []
    for cid, title in convs:
        is_active = "active" if cid == active_chat_id else ""
        escaped_title = title.replace("'", "&#39;").replace('"', "&quot;")
        item = f"""
        <div class="chat-item {is_active}" data-id="{cid}">
            <span class="chat-title" onclick="appSelectChat('{cid}')" title="{escaped_title}">
                <span class="cyber-dot"></span>{escaped_title}
            </span>
            <button type="button" class="chat-menu-btn" onclick="appToggleMenu(event, '{cid}')">⋮</button>
            <div id="menu-{cid}" class="chat-dropdown-menu">
                <div class="chat-dropdown-item" onclick="appOpenRenameModal(event, '{cid}', '{escaped_title}')">
                    <span>✏️</span> Переименовать
                </div>
                <div class="chat-dropdown-item delete-item" onclick="appDeleteChat(event, '{cid}')">
                    <span>🗑️</span> Удалить
                </div>
            </div>
        </div>
        """
        items_html.append(item)
    return "".join(items_html)


# ====================== ОСНОВНОЙ КОНВЕЙЕР ОБРАБОТКИ ======================
async def process_chat(user_request, history, current_chat_id, user_mode_choice,
                        general_m, coder_m, cyber_m, patch_m, qa_m, proc_state):
    if not user_request or not user_request.strip():
        yield history, "", "", gr.update(), current_chat_id, gr.update(visible=True), gr.update(visible=False), proc_state
        return

    start_time = time.perf_counter()
    user_time_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    formatted_user_msg = f"{user_request}\n\n<div style='font-size: 11px; opacity: 0.4; margin-top: 4px; font-family: monospace;'>⏱ {user_time_str}</div>"

    if history is None:
        history = []

    req = user_request.lower()
    target_filename, target_file_code = detect_local_file_content(user_request)

    if user_mode_choice == "🧪 Генерация тестов (pytest)":
        mode_str = "test_gen"
        display_mode = "Автогенерация тестов безопасности (pytest) 🧪"
    elif user_mode_choice == "🛡️ Только аудит безопасности":
        mode_str = "sec_only"
        display_mode = "Аудит Инфобезопасности 🛡️"
    elif user_mode_choice == "💻 Только код":
        mode_str = "code_only"
        display_mode = "Кибер-Разработка 💻"
    elif user_mode_choice == "🧠 Полный цикл (Dev + Sec + Patch + QA)":
        mode_str = "full_dev"
        display_mode = "Полный цикл: Архитектор 🧠 -> Кодер 💻 -> Аудит 🛡️ -> Патч 🛠️ -> QA Тесты 🧪"
    elif user_mode_choice == "🔍 Поиск в сети":
        mode_str = "search"
        display_mode = "Глубокое Сканирование Сети 🔍"
    else:
        is_test_intent = any(k in req for k in ["тест", "pytest", "test", "проверк"])
        is_create_intent = any(k in req for k in ["создай", "спроектируй", "разработай", "новый микросервис", "напиши микросервис"])
        is_sec_intent = any(k in req for k in ["безопасн", "уязвим", "пентест", "аудит", "cve", "защит"])

        if is_test_intent and not is_create_intent:
            mode_str = "test_gen"
            display_mode = "Автогенерация тестов безопасности (pytest) 🧪"
        elif is_create_intent and (is_sec_intent or is_test_intent):
            mode_str = "full_dev"
            display_mode = "Полный цикл: Архитектор 🧠 -> Кодер 💻 -> Аудит 🛡️ -> Патч 🛠️ -> QA Тесты 🧪"
        elif is_sec_intent:
            mode_str = "sec_only"
            display_mode = "Аудит Инфобезопасности 🛡️"
        elif is_create_intent or "код" in req:
            mode_str = "code_only"
            display_mode = "Кибер-Разработка 💻"
        else:
            mode_str = "search"
            display_mode = "Глубокое Сканирование Сети 🔍"

    short_q = user_request[:35] + ("..." if len(user_request) > 35 else "")
    thinking_html = (
        f"<div class='thinking-container'>"
        f"<div class='cyber-scanner'></div>"
        f"<span>[СИСТЕМА] Запуск: «{short_q}» [{display_mode}]</span>"
        f"<div class='thinking-dots'><span></span><span></span><span></span></div>"
        f"</div>"
    )

    history.append({"role": "user", "content": formatted_user_msg})
    history.append({"role": "assistant", "content": thinking_html})

    # Источники поиска раньше жили в модульном глобале SESSION_SOURCES.
    # Это одна async-функция на весь запрос — локальной переменной достаточно,
    # никакого общего состояния между пользователями/вкладками не нужно.
    sources = []

    if not current_chat_id or current_chat_id == "default":
        current_chat_id = f"chat_{int(time.time())}"

    yield history, "", f"[CYBERPUNK PIPELINE INIT] Режим: '{display_mode}'...\n", gr.update(value=render_sidebar_html(current_chat_id)), current_chat_id, gr.update(visible=False), gr.update(visible=True), proc_state

    saved_file_path = ""
    saved_test_path = ""
    raw_web_context = ""

    if mode_str == "search":
        raw_web_context, sources = await asyncio.to_thread(fetch_web_sources, user_request, 7)

    result_queue = multiprocessing.Queue()
    log_queue = multiprocessing.Queue()
    live_logs = []

    crew_process = multiprocessing.Process(
        target=worker_crew_execution,
        args=(mode_str, user_request, raw_web_context, target_filename, target_file_code, general_m, coder_m, cyber_m, patch_m, qa_m, result_queue, log_queue)
    )
    crew_process.start()
    proc_state = crew_process

    try:
        while crew_process.is_alive():
            while not log_queue.empty():
                try:
                    live_logs.append(log_queue.get_nowait())
                except Exception:
                    break

            terminal_str = "".join(live_logs[-80:])
            yield history, "", terminal_str, gr.update(value=render_sidebar_html(current_chat_id)), current_chat_id, gr.update(visible=False), gr.update(visible=True), proc_state
            await asyncio.sleep(0.3)

        while not log_queue.empty():
            try:
                live_logs.append(log_queue.get_nowait())
            except Exception:
                break
        terminal_str = "".join(live_logs[-80:])

        if not result_queue.empty():
            res_data = result_queue.get()
        else:
            res_data = {"status": "error", "error": "Поток был аварийно остановлен."}

        if res_data["status"] == "error":
            raise Exception(res_data.get("error", "Неизвестная ошибка"))

        if mode_str == "full_dev":
            cleaned_coder_code = clean_code_output(res_data.get("code", ""))
            cyber_report = clean_code_output(res_data.get("sec", ""))
            tests_code = clean_code_output(res_data.get("tests", ""))
            service_filename = res_data.get("service_filename", "service.py")
            test_filename = res_data.get("test_filename", "test_service.py")

            if cleaned_coder_code:
                saved_file_path = force_save_code(cleaned_coder_code, service_filename)
            if tests_code:
                saved_test_path = force_save_code(tests_code, test_filename)
            if saved_file_path or saved_test_path:
                git_commit_project(f"full_dev: {user_request[:80]}")

            final_content = (
                f"### 🛠️ Безопасный код после патчинга (FastAPI + SQLite)\n\n{cleaned_coder_code}\n\n---\n"
                f"### 🛡️ Аудит информационной безопасности\n\n{cyber_report}\n\n---\n"
                f"### 🧪 Автоматические тесты (pytest)\n\n{tests_code}"
            )

        elif mode_str == "test_gen":
            tests_code = clean_code_output(res_data.get("tests", ""))
            test_filename = res_data.get("test_filename", "test_service.py")
            if tests_code:
                saved_test_path = force_save_code(tests_code, test_filename)
                git_commit_project(f"test_gen: {user_request[:80]}")
            final_content = f"### 🧪 Автономный набор тестов безопасности pytest\n\n{tests_code}"

        elif mode_str == "code_only":
            raw_code = clean_code_output(res_data.get("raw", ""))
            service_filename = res_data.get("service_filename", "service.py")
            if raw_code:
                saved_file_path = force_save_code(raw_code, service_filename)
                git_commit_project(f"code_only: {user_request[:80]}")
            final_content = raw_code

        elif mode_str == "search":
            formatted_report = format_interactive_markdown_sources(res_data.get("raw", ""), sources)
            sources_list_md = ""
            if sources:
                items = "\n".join([f"- [{s['id']}] [{s['title']}]({s['url']})" for s in sources])
                sources_list_md = f"<details open class='cyber-details'><summary><b>🌐 Подтвержденные источники данных ({len(sources)})</b></summary>\n\n{items}\n</details>\n\n---\n\n"
            final_content = sources_list_md + formatted_report
        else:
            final_content = clean_code_output(res_data.get("raw", ""))

        elapsed_seconds = round(time.perf_counter() - start_time, 1)
        bot_time_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

        save_badge = f"<span>💾 <b>Сервис:</b> <code>{saved_file_path}</code></span>" if saved_file_path else ""
        test_badge = f"<span>🧪 <b>Файл тестов:</b> <code>{saved_test_path}</code></span>" if saved_test_path else ""
        memory_badge = f"<span>🧠 <b>Заметка сохранена:</b> <code>chats.db -> agent_notes</code></span>" if mode_str in ["sec_only", "full_dev"] else ""

        footer_badge = (
            f"\n\n---\n"
            f"<div class='cyber-footer-badge'>"
            f"<span>⚡ <b>Инференс:</b> {elapsed_seconds}s</span>"
            f"<span>📅 <b>Ответ:</b> {bot_time_str}</span>"
            f"{save_badge}"
            f"{test_badge}"
            f"{memory_badge}"
            f"</div>"
        )

        final_answer = f"**Режим:** {display_mode}\n\n{final_content}{footer_badge}"
        history[-1] = {"role": "assistant", "content": final_answer}

        chat_title = user_request.replace("\n", " ").strip()[:35]
        save_chat_to_db(current_chat_id, chat_title, history)

        yield history, "", terminal_str, gr.update(value=render_sidebar_html(current_chat_id)), current_chat_id, gr.update(visible=True), gr.update(visible=False), None

    except asyncio.CancelledError:
        terminate_active_agent_process(crew_process)
        elapsed_seconds = round(time.perf_counter() - start_time, 1)
        cancel_msg = f"🛑 *Процесс прерван оператором ({elapsed_seconds}s)*"
        if history and history[-1]["role"] == "assistant":
            history[-1]["content"] = cancel_msg
        yield history, "", "".join(live_logs[-80:]), gr.update(value=render_sidebar_html(current_chat_id)), current_chat_id, gr.update(visible=True), gr.update(visible=False), None

    except Exception as e:
        terminate_active_agent_process(crew_process)
        elapsed_seconds = round(time.perf_counter() - start_time, 1)
        err_msg = f"**[СБОЙ НЕЙРОСЕТИ]:**\n```\n{str(e)}\n\n{traceback.format_exc()}\n```"
        history[-1] = {"role": "assistant", "content": err_msg}
        yield history, "", "".join(live_logs[-80:]), gr.update(value=render_sidebar_html(current_chat_id)), current_chat_id, gr.update(visible=True), gr.update(visible=False), None


def handle_user_stop_click(proc_state):
    terminate_active_agent_process(proc_state)
    return gr.update(visible=True), gr.update(visible=False), None


# ====================== ДИСПЕТЧЕР САЙДБАРА ======================
def handle_sidebar_action(action_payload, current_id):
    if not action_payload or not action_payload.strip():
        return gr.update(), current_id, gr.update()

    try:
        data = json.loads(action_payload)
        act = data.get("action")
        cid = data.get("id")

        if act == "select":
            hist = load_chat_by_id(cid)
            return hist, cid, gr.update(value=render_sidebar_html(cid))

        elif act == "delete":
            delete_chat_db(cid)
            convs = get_conversations_list()
            new_active = convs[0][0] if convs else "default"
            hist = load_chat_by_id(new_active)
            return hist, new_active, gr.update(value=render_sidebar_html(new_active))

        elif act == "rename":
            new_title = data.get("title", "")
            rename_chat_db(cid, new_title)
            return gr.update(), current_id, gr.update(value=render_sidebar_html(current_id))

        elif act == "new":
            new_id = f"chat_{int(time.time())}"
            return [], new_id, gr.update(value=render_sidebar_html(new_id))

    except Exception as ex:
        print(f"[ERR_DISPATCHER] {ex}")

    return gr.update(), current_id, gr.update()


# ====================== ЖИВОЕ ПРЕВЬЮ ГОТОВОГО ПРОЕКТА ======================
DOCKER_IS_AVAILABLE = docker_available()
PREVIEW_PLACEHOLDER = "<div class='preview-placeholder'>Здесь появится живой предпросмотр после запуска.</div>"

# Реестр активных превью для best-effort очистки при закрытии вкладки (см.
# demo.unload ниже). Gradio не даёт unload-обработчику доступ к gr.State
# конкретной сессии (fn вызывается без аргументов), поэтому это простой
# общий список — для однопользовательского локального инструмента этого
# достаточно; при нескольких одновременных вкладках закрытие одной
# подчистит превью всех, это осознанное упрощение.
_ACTIVE_PREVIEWS = []


def _register_preview(info):
    if info:
        _ACTIVE_PREVIEWS.append(info)


def _unregister_preview(info):
    if info in _ACTIVE_PREVIEWS:
        _ACTIVE_PREVIEWS.remove(info)


def _cleanup_all_previews():
    for info in list(_ACTIVE_PREVIEWS):
        try:
            stop_any_preview(info)
        except Exception:
            pass
    _ACTIVE_PREVIEWS.clear()


async def start_preview(preview_mode_choice, preview_state):
    # Если в этой сессии уже что-то запущено — сначала останавливаем.
    if preview_state:
        stop_any_preview(preview_state)
        _unregister_preview(preview_state)

    service_filename, _ = get_latest_project_file()
    if not service_filename:
        yield ("🔴 Не найден сгенерированный сервис. Сначала создайте проект в чате (режим «Полный цикл» или «Только код»).",
               gr.update(value=PREVIEW_PLACEHOLDER), gr.update(visible=True), gr.update(visible=False), None)
        return

    use_docker = "Docker" in preview_mode_choice
    if use_docker and not docker_available():
        yield ("🔴 Docker не найден или демон не запущен. Выберите «Прямой запуск» либо установите/запустите Docker и попробуйте снова.",
               gr.update(value=PREVIEW_PLACEHOLDER), gr.update(visible=True), gr.update(visible=False), None)
        return

    if use_docker:
        yield ("🐳 Собираю Docker-образ — при первой сборке может занять минуту-две...",
               gr.update(value=PREVIEW_PLACEHOLDER), gr.update(visible=False), gr.update(visible=False), None)
        info = await asyncio.to_thread(start_live_preview_docker, service_filename, slugify_filename(service_filename))
        ready_timeout = 40.0
    else:
        yield ("⚡ Готовлю окружение (venv, pip install) и запускаю сервис...",
               gr.update(value=PREVIEW_PLACEHOLDER), gr.update(visible=False), gr.update(visible=False), None)
        info = await asyncio.to_thread(start_live_preview_process, service_filename)
        ready_timeout = 25.0

    ok, msg = await asyncio.to_thread(wait_for_preview_ready, info, ready_timeout)

    if not ok:
        yield (f"🔴 Не удалось запустить превью:\n```\n{msg}\n```",
               gr.update(value=PREVIEW_PLACEHOLDER), gr.update(visible=True), gr.update(visible=False), None)
        return

    _register_preview(info)
    iframe_html = f"<iframe src='{info['url']}' class='preview-iframe'></iframe>"
    mode_label = "Docker" if use_docker else "прямой процесс"
    yield (f"🟢 Превью запущено ({mode_label}): {info['url']}",
           gr.update(value=iframe_html), gr.update(visible=False), gr.update(visible=True), info)


def stop_preview(preview_state):
    if preview_state:
        stop_any_preview(preview_state)
        _unregister_preview(preview_state)
    return "⏹ Превью остановлено.", gr.update(value=PREVIEW_PLACEHOLDER), gr.update(visible=True), gr.update(visible=False), None


def download_project_zip(_current_chat_id):
    service_filename, _ = get_latest_project_file()
    if not service_filename:
        gr.Warning("Не найден сгенерированный сервис — сначала создайте проект в чате.")
        return gr.update(visible=False)

    test_filename = f"test_{service_filename}"
    sec_report = None
    sec_path = os.path.join(WORKING_DIR, "SECURITY_AUDIT.md")
    if os.path.exists(sec_path):
        try:
            with open(sec_path, "r", encoding="utf-8", errors="ignore") as f:
                sec_report = f.read()
        except Exception:
            sec_report = None

    try:
        zip_path = build_project_zip(service_filename, test_filename=test_filename, sec_report=sec_report)
    except Exception as e:
        gr.Warning(f"Не удалось собрать архив: {e}")
        return gr.update(visible=False)

    gr.Info(f"Архив собран: {os.path.basename(zip_path)}")
    return gr.update(value=zip_path, visible=True)


# ====================== СТИЛИ КИБЕРПАНК GROK / GEMINI ======================
custom_css = """
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;700&family=Rajdhani:wght@500;600;700&display=swap');

:root {
    --cyber-bg: #07090e;
    --cyber-panel: #0d1017;
    --cyber-border: #1a2233;
    --cyber-cyan: #00f0ff;
    --cyber-neon: #00ffcc;
    --cyber-pink: #ff0055;
    --cyber-blue: #2563eb;
    --cyber-text: #e2e8f0;
}

body, .gradio-container {
    background-color: var(--cyber-bg) !important;
    color: var(--cyber-text) !important;
    font-family: 'JetBrains Mono', -apple-system, sans-serif !important;
}

#sidebar-col {
    background-color: var(--cyber-panel) !important;
    border-right: 1px solid var(--cyber-border) !important;
    padding: 16px 14px !important;
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
    overflow: hidden !important;
    box-shadow: 2px 0 15px rgba(0, 0, 0, 0.5) !important;
}

#sidebar-col.sidebar-collapsed {
    width: 0 !important;
    min-width: 0 !important;
    max-width: 0 !important;
    padding: 0 !important;
    margin: 0 !important;
    opacity: 0 !important;
    border-right: none !important;
    pointer-events: none !important;
}

.sidebar-toggle-btn {
    background: #131722;
    border: 1px solid var(--cyber-border);
    color: var(--cyber-cyan);
    padding: 6px 10px;
    border-radius: 8px;
    font-size: 14px;
    cursor: pointer;
    transition: all 0.2s ease;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-weight: 600;
}
.sidebar-toggle-btn:hover {
    border-color: var(--cyber-cyan);
    box-shadow: 0 0 10px rgba(0, 240, 255, 0.3);
    color: #ffffff;
}

.hidden-bridge {
    position: fixed !important;
    top: -9999px !important;
    left: -9999px !important;
    width: 1px !important;
    height: 1px !important;
    opacity: 0 !important;
    pointer-events: none !important;
}

.chat-item {
    position: relative;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 9px 12px;
    margin-bottom: 4px;
    border-radius: 8px;
    cursor: pointer;
    transition: all 0.15s ease;
    color: #94a3b8;
    font-size: 13px;
    border: 1px solid transparent;
}
.chat-item:hover {
    background-color: #161b26;
    color: #ffffff;
    border-color: rgba(0, 240, 255, 0.2);
}
.chat-item.active {
    background-color: #121d2d;
    color: var(--cyber-cyan);
    border-color: var(--cyber-cyan);
    box-shadow: inset 0 0 10px rgba(0, 240, 255, 0.1);
}

.cyber-dot {
    display: inline-block;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background-color: var(--cyber-cyan);
    margin-right: 8px;
    box-shadow: 0 0 6px var(--cyber-cyan);
}

.chat-title {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    width: 170px;
    display: flex;
    align-items: center;
}

.chat-menu-btn {
    opacity: 0;
    background: transparent;
    border: none;
    color: #94a3b8;
    cursor: pointer;
    font-size: 18px;
    line-height: 1;
    padding: 2px 6px;
    border-radius: 4px;
    transition: all 0.15s ease;
}
.chat-item:hover .chat-menu-btn, .chat-item.active .chat-menu-btn {
    opacity: 1;
}
.chat-menu-btn:hover {
    color: #ffffff;
    background-color: #263147;
}

.chat-dropdown-menu {
    display: none;
    position: absolute;
    right: 8px;
    top: 36px;
    background-color: #10141f;
    border: 1px solid var(--cyber-cyan);
    border-radius: 8px;
    box-shadow: 0 8px 30px rgba(0, 0, 0, 0.8), 0 0 15px rgba(0, 240, 255, 0.15);
    z-index: 1000;
    min-width: 150px;
    padding: 6px;
}
.chat-dropdown-item {
    padding: 8px 12px;
    font-size: 12px;
    color: #cbd5e1;
    border-radius: 6px;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 8px;
    transition: all 0.15s ease;
}
.chat-dropdown-item:hover {
    background-color: #1a2234;
    color: var(--cyber-cyan);
}
.chat-dropdown-item.delete-item:hover {
    background-color: #3b111e;
    color: #ff3366;
}

.thinking-container {
    display: inline-flex;
    align-items: center;
    gap: 12px;
    color: var(--cyber-cyan);
    font-size: 13.5px;
    padding: 8px 0;
    letter-spacing: 0.5px;
}
.cyber-scanner {
    width: 12px;
    height: 12px;
    border: 2px solid var(--cyber-cyan);
    border-top-color: transparent;
    border-radius: 50%;
    animation: cyber-spin 0.8s linear infinite;
}
@keyframes cyber-spin {
    to { transform: rotate(360deg); }
}
.thinking-dots {
    display: inline-flex;
    gap: 5px;
}
.thinking-dots span {
    width: 6px;
    height: 6px;
    background-color: var(--cyber-cyan);
    border-radius: 50%;
    box-shadow: 0 0 8px var(--cyber-cyan);
    animation: pulse-dot 1.4s infinite ease-in-out both;
}
.thinking-dots span:nth-child(1) { animation-delay: -0.32s; }
.thinking-dots span:nth-child(2) { animation-delay: -0.16s; }
.thinking-dots span:nth-child(3) { animation-delay: 0s; }

@keyframes pulse-dot {
    0%, 80%, 100% { transform: scale(0.2); opacity: 0.2; }
    40% { transform: scale(1.0); opacity: 1; }
}

#custom-rename-modal {
    display: none;
    position: fixed;
    top: 0;
    left: 0;
    width: 100vw;
    height: 100vh;
    background: rgba(4, 6, 10, 0.85);
    backdrop-filter: blur(8px);
    z-index: 99999;
    align-items: center;
    justify-content: center;
}
.modal-card {
    background-color: #0d111a;
    border: 1px solid var(--cyber-cyan);
    border-radius: 12px;
    width: 90%;
    max-width: 400px;
    padding: 24px;
    box-shadow: 0 0 35px rgba(0, 240, 255, 0.2);
}
.modal-card h3 {
    margin: 0 0 16px 0;
    font-size: 16px;
    color: var(--cyber-cyan);
    text-transform: uppercase;
    letter-spacing: 1px;
}
.modal-card input {
    width: 100%;
    background-color: #06080d;
    border: 1px solid var(--cyber-border);
    border-radius: 8px;
    color: #ffffff;
    font-size: 14px;
    padding: 12px 14px;
    margin-bottom: 20px;
    outline: none;
    box-sizing: border-box;
}
.modal-card input:focus {
    border-color: var(--cyber-cyan);
    box-shadow: 0 0 12px rgba(0, 240, 255, 0.3);
}
.modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: 12px;
}
.modal-btn {
    padding: 9px 18px;
    border-radius: 8px;
    font-size: 13px;
    cursor: pointer;
    border: none;
    font-weight: 600;
}
.modal-btn-cancel {
    background-color: #1e2638;
    color: #94a3b8;
}
.modal-btn-save {
    background-color: var(--cyber-cyan);
    color: #000000;
    font-weight: 700;
    box-shadow: 0 0 15px rgba(0, 240, 255, 0.4);
}

#chat-box {
    background-color: transparent !important;
    border: none !important;
}

#input-container {
    background-color: #0d111a !important;
    border: 1px solid var(--cyber-border) !important;
    border-radius: 16px !important;
    padding: 8px 12px !important;
    box-shadow: 0 8px 30px rgba(0, 0, 0, 0.6) !important;
    display: flex !important;
    align-items: center !important;
    min-height: 58px !important;
    transition: all 0.2s ease !important;
}
#input-container:focus-within {
    border-color: var(--cyber-cyan) !important;
    box-shadow: 0 0 20px rgba(0, 240, 255, 0.25) !important;
}

#user-input {
    flex-grow: 1 !important;
    margin: 0 !important;
    padding: 0 !important;
}
#user-input textarea {
    background: transparent !important;
    border: none !important;
    color: #ffffff !important;
    font-size: 15px !important;
    box-shadow: none !important;
    height: 44px !important;
    min-height: 44px !important;
    padding: 10px 12px !important;
    line-height: 24px !important;
    box-sizing: border-box !important;
    resize: none !important;
}

#submit-btn {
    background: linear-gradient(135deg, #00f0ff 0%, #0088ff 100%) !important;
    color: #000000 !important;
    border-radius: 12px !important;
    font-weight: 700 !important;
    border: none !important;
    height: 46px !important;
    min-height: 46px !important;
    min-width: 135px !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    margin: 0 0 0 8px !important;
    box-shadow: 0 0 15px rgba(0, 240, 255, 0.35) !important;
    transition: all 0.2s ease !important;
}
#submit-btn:hover {
    box-shadow: 0 0 25px rgba(0, 240, 255, 0.6) !important;
    transform: translateY(-1px);
}

#stop-btn {
    background: linear-gradient(135deg, #ff0055 0%, #aa0033 100%) !important;
    color: #ffffff !important;
    border-radius: 12px !important;
    font-weight: 700 !important;
    border: none !important;
    height: 46px !important;
    min-height: 46px !important;
    min-width: 135px !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    margin: 0 0 0 8px !important;
    box-shadow: 0 0 20px rgba(255, 0, 85, 0.4) !important;
}

#terminal-log textarea {
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 11px !important;
    background-color: #05070a !important;
    color: #00f0ff !important;
    border: 1px solid var(--cyber-border) !important;
    border-radius: 8px !important;
}

.cyber-details {
    background-color: #0b0f17;
    border: 1px solid var(--cyber-border);
    border-radius: 10px;
    padding: 10px 14px;
    margin-bottom: 14px;
}
.cyber-details summary {
    cursor: pointer;
    color: var(--cyber-cyan);
    font-size: 13px;
    font-weight: 600;
}

.cyber-footer-badge {
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
    font-size: 11px;
    color: #94a3b8;
    margin-top: 10px;
    border-top: 1px solid var(--cyber-border);
    padding-top: 8px;
}

.preview-iframe {
    width: 100%;
    height: 620px;
    border: 1px solid var(--cyber-border);
    border-radius: 10px;
    background: #ffffff;
}
.preview-placeholder {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 300px;
    color: #64748b;
    font-size: 13px;
    text-align: center;
    padding: 20px;
    border: 1px dashed var(--cyber-border);
    border-radius: 10px;
}
"""

# ====================== JAVASCRIPT ======================
custom_js = """
<script>
let currentRenamingId = null;

function toggleCyberSidebar() {
    const sb = document.getElementById('sidebar-col');
    const btn = document.getElementById('toggle-sidebar-btn');
    if (sb) {
        sb.classList.toggle('sidebar-collapsed');
        if (btn) {
            btn.innerHTML = sb.classList.contains('sidebar-collapsed') ? '⚡ Развернуть [ ☰ ]' : '[ ☰ ] Свернуть панель';
        }
    }
}

function appToggleMenu(e, id) {
    e.stopPropagation();
    document.querySelectorAll('.chat-dropdown-menu').forEach(m => {
        if (m.id !== 'menu-' + id) m.style.display = 'none';
    });
    const m = document.getElementById('menu-' + id);
    if (m) {
        m.style.display = (m.style.display === 'block') ? 'none' : 'block';
    }
}

document.addEventListener('click', function() {
    document.querySelectorAll('.chat-dropdown-menu').forEach(m => m.style.display = 'none');
});

function sendGradioBridgeAction(payload) {
    const bridgeContainer = document.querySelector('#action-bridge-input');
    const bridgeBtn = document.querySelector('#action-bridge-btn');
    if (!bridgeContainer || !bridgeBtn) return;

    const bridgeInput = bridgeContainer.querySelector('textarea') || bridgeContainer.querySelector('input');
    if (bridgeInput) {
        const strVal = JSON.stringify(payload);
        const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value")?.set
                          || Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
        if (nativeSetter) {
            nativeSetter.call(bridgeInput, strVal);
        } else {
            bridgeInput.value = strVal;
        }

        bridgeInput.dispatchEvent(new Event('input', { bubbles: true }));
        bridgeInput.dispatchEvent(new Event('change', { bubbles: true }));

        setTimeout(() => {
            bridgeBtn.click();
        }, 50);
    }
}

function appSelectChat(id) {
    sendGradioBridgeAction({ action: 'select', id: id });
}

function appDeleteChat(e, id) {
    e.stopPropagation();
    sendGradioBridgeAction({ action: 'delete', id: id });
}

function appOpenRenameModal(e, id, title) {
    e.stopPropagation();
    currentRenamingId = id;
    const modal = document.getElementById('custom-rename-modal');
    const inp = document.getElementById('modal-rename-input');
    if (modal && inp) {
        inp.value = title;
        modal.style.display = 'flex';
        setTimeout(() => inp.focus(), 100);
    }
}

function appCloseRenameModal() {
    const modal = document.getElementById('custom-rename-modal');
    if (modal) modal.style.display = 'none';
    currentRenamingId = null;
}

function appSubmitRename() {
    const inp = document.getElementById('modal-rename-input');
    if (inp && currentRenamingId && inp.value.trim()) {
        sendGradioBridgeAction({
            action: 'rename',
            id: currentRenamingId,
            title: inp.value.trim()
        });
        appCloseRenameModal();
    }
}
</script>
"""

# ====================== АВТОИЦЕЛЕНИЕ ОКРУЖЕНИЯ ======================
def auto_heal_startup():
    print("[🛡️ CYBER DOCTOR] Сканирование окружения перед запуском...")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', 7860)) == 0:
                print("[!] Обнаружен занятый порт 7860. Пытаюсь освободить (без sudo — предполагается, что это ваш же старый процесс)...")
                try:
                    subprocess.run(["fuser", "-k", "7860/tcp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    print("[!] Не удалось освободить порт автоматически (нет fuser или нет прав) — освободите его вручную.")
                time.sleep(1)
    except Exception:
        pass


# ====================== ИНТЕРФЕЙС GRADIO ======================
with gr.Blocks(title="PopCat Cyber-Agent Pro") as demo:
    current_chat_id = gr.State(value="default")
    # Раньше активный multiprocessing.Process хранился в модульном
    # глобале CURRENT_CREW_PROCESS, общем для всех сессий Gradio —
    # кнопка "Стоп" в одной вкладке могла оборвать задачу в другой.
    # Теперь это per-session state.
    proc_state = gr.State(value=None)

    with gr.Row(elem_classes=["hidden-bridge"]):
        bridge_action_input = gr.Textbox(elem_id="action-bridge-input")
        bridge_action_btn = gr.Button(elem_id="action-bridge-btn")

    gr.HTML(
        """
        <div id="custom-rename-modal">
            <div class="modal-card">
                <h3>Переименовать узел чата</h3>
                <input type="text" id="modal-rename-input" placeholder="Введите имя..." onkeydown="if(event.key==='Enter')appSubmitRename();if(event.key==='Escape')appCloseRenameModal();" />
                <div class="modal-actions">
                    <button type="button" class="modal-btn modal-btn-cancel" onclick="appCloseRenameModal()">Отмена</button>
                    <button type="button" class="modal-btn modal-btn-save" onclick="appSubmitRename()">Сохранить</button>
                </div>
            </div>
        </div>
        """
    )

    with gr.Row():
        with gr.Column(scale=2, min_width=260, elem_id="sidebar-col"):
            gr.HTML(
                """
                <div style='display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px;'>
                    <span style='font-size: 19px; font-weight: 700; color: #00f0ff; letter-spacing: 0.5px;'>⚡ PopCat Pro</span>
                </div>
                """
            )
            new_chat_btn = gr.Button("+ Новый сеанс", variant="secondary", size="sm")

            gr.HTML("<div style='font-size: 11px; text-transform: uppercase; color: #64748b; font-weight: 700; margin: 18px 0 8px 4px; letter-spacing: 1px;'>Архив сессий</div>")

            sidebar_html = gr.HTML(value=render_sidebar_html())

            with gr.Accordion("⚙️ Настройки моделей Ollama", open=False):
                model_general = gr.Dropdown(label="1. Аналитик / Архитектор", choices=INSTALLED_MODELS, value=DEFAULT_GENERAL)
                model_coder = gr.Dropdown(label="2. Кодер / Разработчик", choices=INSTALLED_MODELS, value=DEFAULT_CODER)
                model_cyber = gr.Dropdown(label="3. Безопасность (Cyber Expert)", choices=INSTALLED_MODELS, value=DEFAULT_CYBER)
                model_patch = gr.Dropdown(label="4. Инженер исправлений (Patch)", choices=INSTALLED_MODELS, value=DEFAULT_PATCH)
                model_qa = gr.Dropdown(label="5. Тестирование / QA (pytest)", choices=INSTALLED_MODELS, value=DEFAULT_QA)

        with gr.Column(scale=8):
            with gr.Row():
                gr.HTML(
                    """
                    <div style='margin-bottom: 8px;'>
                        <button id='toggle-sidebar-btn' class='sidebar-toggle-btn' onclick='toggleCyberSidebar()'>[ ☰ ] Свернуть панель</button>
                    </div>
                    """
                )

            mode_selector = gr.Dropdown(
                choices=[
                    "⚡ Авто (умный выбор)",
                    "🧪 Генерация тестов (pytest)",
                    "🛡️ Только аудит безопасности",
                    "💻 Только код",
                    "🧠 Полный цикл (Dev + Sec + Patch + QA)",
                    "🔍 Поиск в сети"
                ],
                value="⚡ Авто (умный выбор)",
                label="Режим работы агентов"
            )

            chatbot = gr.Chatbot(height=540, render_markdown=True, sanitize_html=True, elem_id="chat-box")

            with gr.Row(elem_id="input-container", equal_height=True):
                user_input = gr.Textbox(
                    placeholder="Введи задачу: сгенерировать pytest тесты, провести аудит или создать сервис...",
                    show_label=False,
                    scale=9,
                    lines=1,
                    max_lines=5,
                    elem_id="user-input"
                )
                submit_btn = gr.Button("Отправить ↑", scale=1, elem_id="submit-btn", visible=True)
                stop_btn = gr.Button("Остановить ⏹", scale=1, elem_id="stop-btn", visible=False)

            with gr.Accordion("📡 Консоль агентов (Real-Time мысли и Sandbox)", open=False):
                terminal_output = gr.Textbox(
                    label="Служебный терминал",
                    lines=10,
                    interactive=False,
                    elem_id="terminal-log"
                )

    # Выезжающая справа панель — как Artifacts на claude.ai: запустить готовый
    # проект прямо в браузере, проверить, что он работает, и скачать архив.
    preview_state = gr.State(value=None)
    docker_note = "" if DOCKER_IS_AVAILABLE else " (не обнаружен на этой машине)"
    with gr.Sidebar(position="right", open=False, width=560, label="🖥️ Live Preview", elem_id="preview-sidebar"):
        gr.Markdown("Запустите последний собранный агентами сервис и проверьте его прямо здесь, прежде чем скачивать.")
        preview_mode = gr.Radio(
            ["⚡ Прямой запуск (быстро)", f"🐳 Docker (универсально){docker_note}"],
            value="⚡ Прямой запуск (быстро)",
            label="Режим превью"
        )
        with gr.Row():
            start_preview_btn = gr.Button("▶ Запустить превью", variant="primary", size="sm")
            stop_preview_btn = gr.Button("⏹ Остановить", size="sm", visible=False)
        preview_status = gr.Markdown("Превью ещё не запущено.")
        preview_frame = gr.HTML(value=PREVIEW_PLACEHOLDER)
        gr.HTML("<hr style='border-color: var(--cyber-border); margin: 14px 0;'>")
        gr.Markdown("**Готово? Заберите проект с собой:**")
        download_zip_btn = gr.Button("📦 Собрать и скачать .zip")
        download_file = gr.File(label="Архив проекта (код + тесты + Dockerfile + инструкция)", visible=False)

    click_event = submit_btn.click(
        process_chat,
        inputs=[user_input, chatbot, current_chat_id, mode_selector, model_general, model_coder, model_cyber, model_patch, model_qa, proc_state],
        outputs=[chatbot, user_input, terminal_output, sidebar_html, current_chat_id, submit_btn, stop_btn, proc_state]
    )
    submit_event = user_input.submit(
        process_chat,
        inputs=[user_input, chatbot, current_chat_id, mode_selector, model_general, model_coder, model_cyber, model_patch, model_qa, proc_state],
        outputs=[chatbot, user_input, terminal_output, sidebar_html, current_chat_id, submit_btn, stop_btn, proc_state]
    )

    stop_btn.click(
        fn=handle_user_stop_click,
        inputs=[proc_state],
        outputs=[submit_btn, stop_btn, proc_state],
        cancels=[click_event, submit_event]
    )

    bridge_action_btn.click(
        handle_sidebar_action,
        inputs=[bridge_action_input, current_chat_id],
        outputs=[chatbot, current_chat_id, sidebar_html]
    )

    new_chat_btn.click(
        lambda: ([], f"chat_{int(time.time())}", gr.update(value=render_sidebar_html("default"))),
        outputs=[chatbot, current_chat_id, sidebar_html]
    )

    start_preview_btn.click(
        start_preview,
        inputs=[preview_mode, preview_state],
        outputs=[preview_status, preview_frame, start_preview_btn, stop_preview_btn, preview_state]
    )
    stop_preview_btn.click(
        stop_preview,
        inputs=[preview_state],
        outputs=[preview_status, preview_frame, start_preview_btn, stop_preview_btn, preview_state]
    )
    download_zip_btn.click(
        download_project_zip,
        inputs=[current_chat_id],
        outputs=[download_file]
    )

    # На случай, если пользователь просто закроет вкладку, не нажав "Остановить" —
    # подчищаем запущенные превью-процессы/контейнеры, чтобы они не висели в фоне.
    demo.unload(_cleanup_all_previews)

if __name__ == "__main__":
    import logging
    import sys

    # Настраиваем логирование
    log_dir = os.path.join(BASE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "web_agent.log")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout)
        ]
    )

    try:
        # auto_heal_startup()  # Отключено для systemd — убивает сам себя
        multiprocessing.set_start_method("spawn", force=True)

        launch_host = os.environ.get("AGENT_HOST", "127.0.0.1")
        auth_user = os.environ.get("AGENT_AUTH_USER")
        auth_pass = os.environ.get("AGENT_AUTH_PASS")

        launch_kwargs = dict(
            server_name=launch_host,
            server_port=7860,
            show_error=True,
            css=custom_css,
            head=custom_js,
            allowed_paths=["/home/beluga/ai-multi-agent/output"],
            # allowed_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
        )
        if auth_user and auth_pass:
            launch_kwargs["auth"] = (auth_user, auth_pass)
        elif launch_host == "0.0.0.0":
            logging.warning("Сервер слушает 0.0.0.0 без авторизации (AGENT_AUTH_USER/AGENT_AUTH_PASS не заданы).")

        logging.info(f"Запуск сервера на {launch_host}:7860")
        demo.launch(**launch_kwargs)

    except Exception as e:
        logging.error(f"Критическая ошибка: {e}")
        import traceback
        logging.error(traceback.format_exc())
        sys.exit(1)

