"""
agent_worker.py
================
Вся "тяжёлая" логика мульти-агентной системы: работа с БД, веб-поиск,
RAG-память на эмбеддингах (nomic-embed-text), инструменты агентов (@tool)
и сам CrewAI-пайплайн (worker_crew_execution).

Почему это отдельный файл, а не часть web_agent.py:
multiprocessing со start_method="spawn" в дочернем процессе заново
ИМПОРТИРУЕТ модуль, в котором объявлена целевая функция. Если бы
worker_crew_execution жила в web_agent.py вместе с gr.Blocks(...),
CSS и JS, каждый(!) запуск задачи в чате пересобирал бы в дочернем
процессе весь Gradio-интерфейс и заново дёргал Ollama API при импорте.
Здесь же дочерний процесс импортирует только то, что ему реально нужно.
"""

import os
import sys
import io
import re
import json
import math
import time
import sqlite3
import subprocess
import socket
import urllib.request
from datetime import datetime

from crewai import Agent, Task, Crew, Process, LLM
from crewai.tools import tool
from ddgs import DDGS

# ====================== ПУТИ И НАСТРОЙКИ ======================
# Раньше путь был жёстко зашит как /home/beluga/ai-multi-agent.
# os.path.expanduser("~/...") резолвится в то же самое для пользователя
# beluga, но теперь переносимо на любую машину/пользователя, и его можно
# переопределить переменной окружения без правки кода.
BASE_DIR = os.environ.get("AGENT_BASE_DIR", os.path.expanduser("~/ai-multi-agent"))
WORKING_DIR = os.path.join(BASE_DIR, "output")
DB_PATH = os.path.join(BASE_DIR, "chats.db")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
EMBED_MODEL = os.environ.get("AGENT_EMBED_MODEL", "nomic-embed-text")

os.makedirs(WORKING_DIR, exist_ok=True)

# ====================== БАЗА ДАННЫХ И ПАМЯТЬ АГЕНТОВ ======================
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT,
                updated_at TEXT,
                history_json TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                content TEXT,
                tags TEXT,
                created_at TEXT,
                embedding TEXT
            )
        """)
        conn.commit()
        # На случай апгрейда со старой БД, где колонки embedding ещё нет.
        cols = [r[1] for r in conn.execute("PRAGMA table_info(agent_notes)").fetchall()]
        if "embedding" not in cols:
            conn.execute("ALTER TABLE agent_notes ADD COLUMN embedding TEXT")
            conn.commit()

init_db()


def get_conversations_list():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, title FROM conversations ORDER BY updated_at DESC LIMIT 30")
        return cursor.fetchall()


def save_chat_to_db(chat_id: str, title: str, history: list):
    with sqlite3.connect(DB_PATH) as conn:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        clean_title = title.replace("\n", " ").strip()[:35]
        conn.execute("""
            INSERT INTO conversations (id, title, updated_at, history_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,
                updated_at=excluded.updated_at,
                history_json=excluded.history_json
        """, (chat_id, clean_title, now_str, json.dumps(history, ensure_ascii=False)))
        conn.commit()


def load_chat_by_id(chat_id: str) -> list:
    if not chat_id or chat_id == "default":
        return []
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT history_json FROM conversations WHERE id = ?", (chat_id,))
        row = cursor.fetchone()
        if row and row[0]:
            try:
                return json.loads(row[0])
            except Exception:
                return []
    return []


def delete_chat_db(chat_id: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM conversations WHERE id = ?", (chat_id,))
        conn.commit()


def rename_chat_db(chat_id: str, new_title: str):
    clean_title = new_title.replace("\n", " ").strip()[:35]
    if clean_title:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("UPDATE conversations SET title = ? WHERE id = ?", (clean_title, chat_id))
            conn.commit()


def save_note_direct(title: str, content: str, tags: str = "security", embedding: list = None):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            emb_json = json.dumps(embedding) if embedding else None
            conn.execute(
                "INSERT INTO agent_notes (title, content, tags, created_at, embedding) VALUES (?, ?, ?, ?, ?)",
                (title[:80], content, tags[:50], now_str, emb_json)
            )
            conn.commit()
    except Exception:
        pass


# ====================== RAG-ПАМЯТЬ (nomic-embed-text) ======================
# У вас уже установлена nomic-embed-text (274 MB, быстрая даже на CPU),
# но раньше она нигде не использовалась. Задействуем её для простого
# векторного поиска по прошлым заметкам аудита — без внешней vector DB,
# просто косинусная близость в SQLite (записей обычно немного, это дёшево).

def get_embedding(text: str, model: str = None) -> list:
    model = model or EMBED_MODEL
    try:
        payload = json.dumps({"model": model, "prompt": text[:2000]}).encode("utf-8")
        req = urllib.request.Request(
            f"{OLLAMA_HOST}/api/embeddings", data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("embedding", []) or []
    except Exception:
        return []


def cosine_similarity(a: list, b: list) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def save_note_with_embedding(title: str, content: str, tags: str = "security"):
    """Сохраняет заметку и сразу считает для неё эмбеддинг, если Ollama/модель доступны.
    Если эмбеддинг посчитать не удалось — заметка всё равно сохранится (просто без RAG-индекса)."""
    embedding = get_embedding(f"{title}\n{content[:1500]}")
    save_note_direct(title, content, tags, embedding=embedding or None)


def recall_relevant_notes(query: str, top_k: int = 3, min_score: float = 0.55) -> list:
    """Достаёт top_k наиболее релевантных прошлых заметок по косинусной близости эмбеддингов.
    Деградирует тихо (возвращает []), если Ollama/модель эмбеддингов недоступны —
    остальной пайплайн при этом продолжает работать как обычно."""
    q_emb = get_embedding(query)
    if not q_emb:
        return []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT title, content, tags, created_at, embedding FROM agent_notes "
                "WHERE embedding IS NOT NULL ORDER BY id DESC LIMIT 300"
            )
            rows = cursor.fetchall()
    except Exception:
        return []

    scored = []
    for title, content, tags, created_at, emb_json in rows:
        try:
            emb = json.loads(emb_json)
        except Exception:
            continue
        score = cosine_similarity(q_emb, emb)
        if score >= min_score:
            scored.append((score, title, content, tags, created_at))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {"score": round(s, 3), "title": t, "content": c, "tags": tg, "date": d}
        for s, t, c, tg, d in scored[:top_k]
    ]


def format_recall_block(notes: list) -> str:
    """Готовит блок для вставки прямо в описание Task — так RAG-контекст
    доходит до модели гарантированно, а не зависит от того, вызовет ли
    маленькая локальная модель инструмент recall_audit_memory сама."""
    if not notes:
        return ""
    lines = ["РЕЛЕВАНТНЫЙ КОНТЕКСТ ИЗ ПРОШЛЫХ АУДИТОВ (используй как справочную информацию, не копируй дословно):"]
    for n in notes:
        lines.append(f"- [{n['date']}, сходство {n['score']}] {n['title']}: {n['content'][:300]}")
    return "\n".join(lines) + "\n\n"


# ====================== МОДЕЛИ OLLAMA ======================
def get_installed_ollama_models() -> list:
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = [m["name"] for m in data.get("models", [])]
            if models:
                return sorted(models)
    except Exception:
        pass
    return ["llama3.1-8b-abliterated:latest", "Qwen2.5-Coder:7B", "Llama-3-8B-Instruct-Cybersecurity:Q4_K_M"]


def get_loaded_ollama_models() -> list:
    """Модели, реально сейчас находящиеся в памяти/VRAM (через /api/ps)."""
    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/ps")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def abort_ollama_hardware():
    """Выгружает из VRAM только реально загруженные модели.
    Раньше здесь перебирались ВСЕ установленные модели (включая никогда
    не запускавшиеся в этой сессии), что означало до 16 лишних
    load/unload round-trip'ов к Ollama на каждый Stop/ошибку."""
    for m in get_loaded_ollama_models():
        try:
            payload = json.dumps({"model": m, "keep_alive": 0}).encode("utf-8")
            req = urllib.request.Request(
                f"{OLLAMA_HOST}/api/generate", data=payload,
                headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=2)
        except Exception:
            pass


# ====================== МЕЖПРОЦЕССНЫЙ ЛОГГЕР ТЕРМИНАЛА ======================
class QueueStreamLogger(io.StringIO):
    def __init__(self, queue):
        super().__init__()
        self.queue = queue
        self.terminal = sys.__stdout__

    def write(self, message):
        self.terminal.write(message)
        self.terminal.flush()
        if message:
            clean_msg = message
            for code in ["\033[95m", "\033[92m", "\033[93m", "\033[91m", "\033[0m", "\033[1m"]:
                clean_msg = clean_msg.replace(code, "")
            self.queue.put(clean_msg)

    def flush(self):
        self.terminal.flush()


# ====================== ПОИСК В СЕТИ ======================
def clean_search_query(raw_query: str) -> str:
    q = raw_query.lower()
    for sp in ["выдай отчет про", "напиши отчет", "найди мне", "что слышно про", "последние новости", "и.т.д.", "пожалуйста"]:
        q = q.replace(sp, "")
    return " ".join(q.split()) if len(q.split()) > 1 else raw_query


def fetch_web_sources(query: str, max_results: int = 7) -> tuple[str, list]:
    """Возвращает (форматированный_контекст, список_источников).
    Раньше список источников писался в модульный глобал SESSION_SOURCES —
    убрано: единственный вызывающий код (process_chat) держит источники
    в локальной переменной внутри одного запроса, глобал был не нужен
    и создавал гонку между параллельными пользователями/вкладками."""
    clean_q = clean_search_query(query)
    print(f"\n[CYBER_NET_SCAN] Сканирование сети: '{clean_q}'...")

    results = []
    try:
        results = list(DDGS().news(clean_q, max_results=max_results))
    except Exception:
        pass

    if not results:
        try:
            results = list(DDGS().text(f"{clean_q} новости", max_results=max_results))
        except Exception:
            pass

    sources = []
    formatted_context = []
    for idx, r in enumerate(results, 1):
        title = r.get("title", f"Источник {idx}").replace('"', "'")
        body = (r.get("body", "") or r.get("snippet", "")).replace('"', "'")
        href = r.get("url", "") or r.get("href", "#")
        if not body or len(body.strip()) < 30:
            continue

        sources.append({"id": idx, "title": title, "snippet": body[:250], "url": href})
        formatted_context.append(f"УЗЕЛ ДАННЫХ [{idx}]:\nЗаголовок: {title}\nФакты: {body}\nURL: {href}")

    return "\n\n".join(formatted_context), sources


def clean_duplicated_tail_citations(text: str) -> str:
    cleaned = re.sub(r'(\n|\s)*(Примечания|Источники|Ссылки|Литература)?(\s*\[\d+\][\s\S]*)$', '', text.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r'(\s*\[\d+\]\s*)+$', '', cleaned.strip())
    return cleaned


def format_interactive_markdown_sources(text: str, sources: list) -> str:
    if not sources:
        return text

    clean_text = clean_duplicated_tail_citations(text)

    def replace_citation(match):
        idx_str = match.group(1)
        try:
            idx = int(idx_str)
            src = next((s for s in sources if s["id"] == idx), None)
            if src:
                tooltip = f"{src['title']} — {src['snippet'][:180]}"
                return f' [[{idx}]]({src["url"]} "{tooltip}")'
        except Exception:
            pass
        return match.group(0)

    return re.sub(r'\[(\d+)\]', replace_citation, clean_text)


# ====================== ФАЙЛЫ ПРОЕКТА ======================
_KNOWN_TOOL_NAMES = (
    "explore_project_structure|run_pytest_suite|cyber_environment_doctor|"
    "validate_python_syntax|save_code_to_sandbox|recall_audit_memory|save_finding_to_memory"
)


def clean_code_output(raw_text: str) -> str:
    cleaned = raw_text.strip()
    if cleaned.startswith("{") and "report" in cleaned:
        try:
            parsed = json.loads(cleaned)
            rep = parsed.get("report", {})
            lines = [f"# {rep.get('title', 'Отчет безопасности')}\n"]
            for sec in rep.get("sections", []):
                lines.append(f"### {sec.get('section_title', '')}\n{sec.get('content', '')}\n")
            for issue in rep.get("security_issues", []):
                lines.append(f"- **{issue.get('issue')}**: {issue.get('description')} (`{issue.get('location')}`)")
            return "\n".join(lines)
        except Exception:
            pass

    cleaned = re.sub(r'```json\s*\{\s*"name":\s*".*?"\s*\}\s*```', '', cleaned, flags=re.DOTALL)
    cleaned = re.sub(r'\{\s*"name":\s*"(' + _KNOWN_TOOL_NAMES + r')".*?\}', '', cleaned, flags=re.DOTALL)
    return cleaned.strip()


def slugify_filename(text: str, default: str = "service", max_words: int = 5) -> str:
    """Превращает запрос пользователя в короткое латинское имя файла.
    Нужно, чтобы разные задачи (auth-сервис, сервис заказов, ...) не
    перезаписывали один и тот же auth_service.py, как это было раньше."""
    text = (text or "").lower()
    translit_map = str.maketrans({
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
        "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
        "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
        "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    })
    text = text.translate(translit_map)
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    stop_words = {"sozdai", "sozday", "napishi", "razrabotay", "razrabotai", "novyy", "novyi",
                  "mikroservis", "servis", "the", "a", "an", "for", "please", "pozhaluysta"}
    words = [w for w in text.split("_") if w and w not in stop_words][:max_words]
    slug = "_".join(words) if words else default
    return slug[:40]


def force_save_code(content: str, default_filename: str = "service.py") -> str:
    try:
        code_to_save = content
        if "```python" in content:
            code_to_save = content.split("```python")[1].split("```")[0].strip()
        elif "```" in content:
            code_to_save = content.split("```")[1].split("```")[0].strip()

        code_to_save = clean_code_output(code_to_save)
        if not code_to_save:
            return ""
        file_path = os.path.join(WORKING_DIR, default_filename)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(code_to_save)
        return file_path
    except Exception as e:
        print(f"\n[ERROR_DISK_WRITE] {e}")
        return ""


def detect_local_file_content(user_request: str) -> tuple[str, str]:
    """Если пользователь явно называет существующий файл — берём его.
    Иначе, вместо жёсткого fallback на auth_service.py (которого может
    и не быть), берём самый свежий .py файл в песочнице — так работает
    интуитивно понятнее с несколькими проектами в output/."""
    req = user_request.lower()
    candidates = []
    for root, _, files in os.walk(WORKING_DIR):
        for f in files:
            if not f.endswith(".py") or f.startswith("test_"):
                continue
            fpath = os.path.join(root, f)
            try:
                mtime = os.path.getmtime(fpath)
            except OSError:
                continue
            candidates.append((fpath, f, mtime))

    for fpath, f, _ in candidates:
        if f.lower() in req:
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as fp:
                    lines = fp.readlines()
                return f, "".join(lines[:150])
            except Exception:
                pass

    if candidates:
        candidates.sort(key=lambda x: x[2], reverse=True)
        fpath, f, _ = candidates[0]
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as fp:
                lines = fp.readlines()
            return f, "".join(lines[:150])
        except Exception:
            pass
    return "", ""


def get_latest_project_file() -> tuple:
    """Обёртка над detect_local_file_content для мест, где нужен просто
    "последний созданный проектный файл" безотносительно текста запроса
    (например, кнопки живого превью и скачивания архива)."""
    return detect_local_file_content("")


def git_commit_project(commit_message: str = "Автоматический коммит агента") -> str:
    """Версионирует output/ через git — история всех сгенерированных и
    пропатченных версий сервисов достаётся бесплатно, без своей БД версий.
    Вызывается детерминированно из оркестрации (не полагаемся на то,
    что маленькая модель сама вспомнит вызвать такой инструмент)."""
    try:
        if not os.path.exists(os.path.join(WORKING_DIR, ".git")):
            subprocess.run(["git", "init"], cwd=WORKING_DIR, capture_output=True, timeout=10)
            subprocess.run(["git", "config", "user.email", "agent@local"], cwd=WORKING_DIR, capture_output=True, timeout=5)
            subprocess.run(["git", "config", "user.name", "CyberAgent"], cwd=WORKING_DIR, capture_output=True, timeout=5)
        subprocess.run(["git", "add", "-A"], cwd=WORKING_DIR, capture_output=True, timeout=10)
        msg = (commit_message or "Автоматический коммит агента")[:200].replace('"', "'")
        proc = subprocess.run(
            ["git", "commit", "-m", msg], cwd=WORKING_DIR,
            capture_output=True, text=True, timeout=10
        )
        if proc.returncode == 0:
            return f"OK: {proc.stdout.strip()[:200]}"
        if "nothing to commit" in (proc.stdout + proc.stderr).lower():
            return "Нет изменений для коммита."
        return f"git commit код {proc.returncode}: {proc.stderr[:300]}"
    except FileNotFoundError:
        return "git не установлен — коммит пропущен."
    except Exception as e:
        return f"Ошибка git: {str(e)}"


# ====================== КИБЕР-СКИЛЛЫ АГЕНТОВ (CrewAI tools) ======================
def safe_path(file_path: str) -> str:
    base = os.path.abspath(WORKING_DIR)
    target = os.path.abspath(os.path.join(base, file_path))
    if not target.startswith(base):
        raise ValueError("ACCESS DENIED: Попытка выхода за пределы рабочей директории.")
    return target


@tool
def explore_project_structure(dummy: str = "") -> str:
    """Исследует файловую структуру каталога output/. Возвращает дерево всех файлов и их размеры."""
    try:
        tree = []
        base = os.path.abspath(WORKING_DIR)
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d != ".git"]
            level = root.replace(base, '').count(os.sep)
            indent = ' ' * 4 * level
            tree.append(f"{indent}📁 {os.path.basename(root)}/")
            subindent = ' ' * 4 * (level + 1)
            for f in sorted(files):
                fpath = os.path.join(root, f)
                size_kb = round(os.path.getsize(fpath) / 1024, 1)
                tree.append(f"{subindent}📄 {f} ({size_kb} KB)")
        if not tree:
            return "Папка проекта пуста."
        return "\n".join(tree)
    except Exception as e:
        return f"Ошибка сканирования дерева: {str(e)}"


@tool
def run_pytest_suite(test_file: str = "test_service.py") -> str:
    """Запускает набор тестов pytest в песочнице output/ и возвращает результат выполнения.
    Файл теста и файл сервиса, который он импортирует, должны уже быть сохранены на диск
    (через save_code_to_sandbox) — иначе тест не найдёт модуль для импорта."""
    try:
        target = safe_path(test_file)
        if not os.path.exists(target):
            return f"Ошибка: Файл тестов {test_file} не найден. Сначала сохрани его через save_code_to_sandbox."

        cmd = [sys.executable, "-m", "pytest", target, "-v", "--no-header", "-tb=short"]
        proc = subprocess.run(
            cmd,
            cwd=WORKING_DIR,
            capture_output=True,
            text=True,
            timeout=35
        )
        out = proc.stdout if proc.stdout else ""
        err = proc.stderr if proc.stderr else ""
        return f"[PYTEST RETURN CODE: {proc.returncode}]\n{out}\n{err}"
    except subprocess.TimeoutExpired:
        return "Ошибка: Тесты превысили лимит времени выполнения (35 сек)."
    except Exception as e:
        return f"Ошибка запуска тестов: {str(e)}"


@tool
def validate_python_syntax(file_path: str) -> str:
    """Быстро проверяет, что Python-файл в песочнице output/ синтаксически корректен
    (через py_compile, без реального запуска кода). Используй перед run_pytest_suite,
    чтобы сразу отличить синтаксическую ошибку от логической."""
    try:
        target = safe_path(file_path)
        if not os.path.exists(target):
            return f"Ошибка: файл {file_path} не найден. Сначала сохрани его через save_code_to_sandbox."
        proc = subprocess.run(
            [sys.executable, "-m", "py_compile", target],
            capture_output=True, text=True, timeout=10
        )
        if proc.returncode == 0:
            return f"OK: {file_path} — синтаксис корректен."
        return f"ОШИБКА СИНТАКСИСА в {file_path}:\n{proc.stderr[:1500]}"
    except subprocess.TimeoutExpired:
        return "Ошибка: проверка синтаксиса превысила лимит времени."
    except Exception as e:
        return f"Ошибка проверки синтаксиса: {str(e)}"


@tool
def save_code_to_sandbox(code: str, file_name: str) -> str:
    """Сохраняет Python-код в песочницу output/. Используй, чтобы записать
    промежуточную версию файла на диск перед проверкой через validate_python_syntax
    или run_pytest_suite — без этого те инструменты не увидят твой код."""
    try:
        path = force_save_code(code, file_name)
        return f"Сохранено: {path}" if path else "Не удалось сохранить файл (пустой код?)."
    except Exception as e:
        return f"Ошибка сохранения: {str(e)}"


@tool
def read_sandbox_file(file_name: str) -> str:
    """Читает содержимое текстового файла из песочницы output/ (код, Dockerfile,
    requirements.txt и т.д.) — до 4000 символов. Используй, когда нужно свериться
    с реальным содержимым файла на диске, а не только с текстом из контекста задачи."""
    try:
        target = safe_path(file_name)
        if not os.path.exists(target):
            return f"Файл {file_name} не найден в песочнице."
        with open(target, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        return content[:4000]
    except Exception as e:
        return f"Ошибка чтения файла: {str(e)}"


@tool
def cyber_environment_doctor(check_type: str = "full") -> str:
    """Диагностирует окружение: проверяет порт 7860, доступность Ollama API и целостность базы данных."""
    report = []
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        port_in_use = s.connect_ex(('127.0.0.1', 7860)) == 0
        report.append(f"🔌 Порт 7860 (Gradio): {'Занят (активен)' if port_in_use else 'Свободен'}")

    try:
        req = urllib.request.Request(f"{OLLAMA_HOST}/api/tags")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models_count = len(data.get("models", []))
            report.append(f"🟢 Ollama API: Доступен (Моделей найдено: {models_count})")
    except Exception:
        report.append("🔴 Ollama API: Ошибка подключения (сервер не отвечает)")

    db_exists = os.path.exists(DB_PATH)
    output_exists = os.path.exists(WORKING_DIR)
    report.append(f"💾 База данных (chats.db): {'Найдена' if db_exists else 'Отсутствует'}")
    report.append(f"📁 Папка песочницы (output/): {'Готова' if output_exists else 'Создается'}")

    return "\n".join(report)


@tool
def recall_audit_memory(topic: str) -> str:
    """Ищет в базе прошлых аудитов безопасности (agent_notes) записи,
    семантически близкие к теме — через эмбеддинги nomic-embed-text."""
    notes = recall_relevant_notes(topic, top_k=3)
    if not notes:
        return "Релевантных прошлых заметок не найдено."
    lines = []
    for n in notes:
        lines.append(f"[{n['date']}] (сходство {n['score']}) {n['title']}:\n{n['content'][:400]}")
    return "\n\n".join(lines)


@tool
def save_finding_to_memory(title: str, content: str) -> str:
    """Сохраняет важную находку (уязвимость, паттерн, решение) в долговременную
    память агентов с векторным индексом для последующего поиска через recall_audit_memory."""
    try:
        save_note_with_embedding(title, content, tags="agent_finding")
        return "Заметка сохранена в базу знаний."
    except Exception as e:
        return f"Ошибка сохранения заметки: {str(e)}"


# Наборы инструментов по ролям. Раньше все агенты получали один и тот же
# список из 3 инструментов — с маленькими локальными моделями (7-8B)
# каждый лишний инструмент в схеме немного снижает надёжность
# tool-calling, поэтому даём только то, что реально нужно роли.
TOOLS_ARCHITECT = [explore_project_structure, recall_audit_memory, cyber_environment_doctor]
TOOLS_CODER = [explore_project_structure, save_code_to_sandbox, validate_python_syntax, read_sandbox_file]
TOOLS_SECURITY = [explore_project_structure, recall_audit_memory, save_finding_to_memory, read_sandbox_file]
TOOLS_PATCH = [save_code_to_sandbox, validate_python_syntax, run_pytest_suite, read_sandbox_file]
TOOLS_QA = [save_code_to_sandbox, validate_python_syntax, run_pytest_suite, read_sandbox_file]
TOOLS_RESEARCH = [explore_project_structure, cyber_environment_doctor]
TOOLS_DEVOPS = [read_sandbox_file, save_code_to_sandbox, explore_project_structure, validate_python_syntax]


# ====================== СБОРКА ПРОЕКТА: requirements.txt / Dockerfile ======================
# Список сознательно небольшой и по делу — под тот стек, который реально
# генерирует пайплайн (FastAPI + SQLAlchemy), плюс частые смежные пакеты.
_IMPORT_TO_PACKAGE = {
    "fastapi": "fastapi", "uvicorn": "uvicorn[standard]", "sqlalchemy": "sqlalchemy",
    "pydantic": "pydantic", "starlette": "starlette", "jose": "python-jose[cryptography]",
    "jwt": "pyjwt", "passlib": "passlib[bcrypt]", "bcrypt": "bcrypt", "requests": "requests",
    "httpx": "httpx", "aiofiles": "aiofiles", "dotenv": "python-dotenv",
    "psycopg2": "psycopg2-binary", "redis": "redis", "celery": "celery", "pytest": "pytest",
    "email_validator": "email-validator", "multipart": "python-multipart", "alembic": "alembic",
    "numpy": "numpy", "pandas": "pandas",
}
_STD_LIB_MODULES = set(getattr(__import__("sys"), "stdlib_module_names", ()))

DOCKERFILE_TEMPLATE = """FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY {service_filename} .

EXPOSE 8000
CMD ["uvicorn", "{module_name}:app", "--host", "0.0.0.0", "--port", "8000"]
"""


def infer_requirements(code: str) -> list:
    """Определяет зависимости по import-ам в коде (регэксп, без выполнения кода —
    безопасно для непроверенного сгенерированного кода)."""
    modules = set(re.findall(r'^\s*(?:import|from)\s+([a-zA-Z0-9_\.]+)', code or "", flags=re.MULTILINE))
    top_level = {m.split(".")[0] for m in modules}
    packages = set()
    for m in top_level:
        if not m or m in _STD_LIB_MODULES or m == "__future__":
            continue
        packages.add(_IMPORT_TO_PACKAGE.get(m, m))
    # uvicorn нужен для запуска (`uvicorn module:app`), даже если сам код его
    # не импортирует — раньше здесь стояло "and fastapi not in packages", из-за
    # чего uvicorn пропускался всякий раз, когда fastapi уже находился через
    # прямой импорт. Без uvicorn в requirements.txt превью падает с
    # "No module named uvicorn" сразу после установки зависимостей.
    if "FastAPI(" in (code or ""):
        packages.add("fastapi")
        packages.add("uvicorn[standard]")
    return sorted(packages)


def generate_dockerfile(service_filename: str) -> str:
    module_name = service_filename[:-3] if service_filename.endswith(".py") else service_filename
    return DOCKERFILE_TEMPLATE.format(service_filename=service_filename, module_name=module_name)


def write_project_scaffold(service_filename: str, code: str,
                            refresh_requirements: bool = True, refresh_dockerfile: bool = False) -> dict:
    """Пишет requirements.txt и Dockerfile рядом с сервисом.
    requirements.txt обновляется по умолчанию при каждом вызове (дёшево, безопасно,
    держит зависимости в актуальном состоянии). Dockerfile — только если его ещё нет
    (или явно попросили refresh_dockerfile=True) — так не затираются правки
    DevOps-агента или человека."""
    req_path = os.path.join(WORKING_DIR, "requirements.txt")
    docker_path = os.path.join(WORKING_DIR, "Dockerfile")
    gitignore_path = os.path.join(WORKING_DIR, ".gitignore")

    reqs = infer_requirements(code)
    if refresh_requirements or not os.path.exists(req_path):
        with open(req_path, "w", encoding="utf-8") as f:
            f.write("\n".join(reqs) + ("\n" if reqs else ""))

    if refresh_dockerfile or not os.path.exists(docker_path):
        with open(docker_path, "w", encoding="utf-8") as f:
            f.write(generate_dockerfile(service_filename))

    if not os.path.exists(gitignore_path):
        with open(gitignore_path, "w", encoding="utf-8") as f:
            f.write("__pycache__/\n*.pyc\n.pytest_cache/\n.preview_venv/\n")

    return {"requirements_path": req_path, "dockerfile_path": docker_path, "requirements": reqs}


# ====================== ЖИВОЕ ПРЕВЬЮ (прямой запуск / Docker) ======================
def find_free_port(start: int = 8001, end: int = 8999) -> int:
    import random
    ports = list(range(start, end))
    random.shuffle(ports)
    for p in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', p)) != 0:
                return p
    raise RuntimeError("Нет свободных портов для превью (диапазон 8001-8998 занят).")


def ensure_preview_venv() -> str:
    """Отдельное venv для запуска превью — не засоряет системный Python и не
    требует --break-system-packages. Лежит вне output/, чтобы не попасть
    случайно в git-коммиты проекта или в ZIP-экспорт."""
    venv_dir = os.path.join(BASE_DIR, ".preview_venv")
    venv_python = os.path.join(venv_dir, "bin", "python")
    if not os.path.exists(venv_python):
        subprocess.run([sys.executable, "-m", "venv", venv_dir], timeout=90, capture_output=True)
    return venv_python


def docker_available() -> bool:
    try:
        proc = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
        return proc.returncode == 0
    except Exception:
        return False


def start_live_preview_process(service_filename: str) -> dict:
    """Прямой запуск: venv + pip install -r requirements.txt + uvicorn. Быстрее
    Docker-варианта, но работает только для того же Python/FastAPI-стека,
    который реально генерирует текущий пайплайн."""
    module_name = service_filename[:-3] if service_filename.endswith(".py") else service_filename
    venv_python = ensure_preview_venv()

    req_path = os.path.join(WORKING_DIR, "requirements.txt")
    if os.path.exists(req_path):
        pip_proc = subprocess.run(
            [venv_python, "-m", "pip", "install", "-q", "-r", req_path],
            cwd=WORKING_DIR, capture_output=True, text=True, timeout=180
        )
        if pip_proc.returncode != 0:
            return {"mode": "process", "error": f"Не удалось установить зависимости:\n{pip_proc.stderr[-1500:]}"}

    port = find_free_port()
    env = os.environ.copy()
    env["PYTHONPATH"] = WORKING_DIR
    proc = subprocess.Popen(
        [venv_python, "-m", "uvicorn", f"{module_name}:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=WORKING_DIR, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    return {"proc": proc, "port": port, "mode": "process", "url": f"http://127.0.0.1:{port}/docs"}


def start_live_preview_docker(service_filename: str, project_slug: str) -> dict:
    """DevOps-путь: docker build -> docker run. Всеяден по стеку (Rust, Go, PHP —
    что угодно, если Dockerfile корректный), но первая сборка образа может
    занять минуту-другую (скачивание базового образа)."""
    image_tag = f"agent-preview-{project_slug}"
    build_proc = subprocess.run(
        ["docker", "build", "-t", image_tag, "."],
        cwd=WORKING_DIR, capture_output=True, text=True, timeout=300
    )
    if build_proc.returncode != 0:
        return {"mode": "docker", "error": f"Ошибка сборки Docker-образа:\n{build_proc.stderr[-2000:]}"}

    port = find_free_port()
    run_proc = subprocess.run(
        ["docker", "run", "-d", "--rm", "-p", f"{port}:8000", image_tag],
        capture_output=True, text=True, timeout=30
    )
    if run_proc.returncode != 0:
        return {"mode": "docker", "error": f"Ошибка запуска контейнера:\n{run_proc.stderr[-2000:]}"}

    return {"mode": "docker", "container_id": run_proc.stdout.strip(), "port": port,
            "url": f"http://127.0.0.1:{port}/docs"}


def wait_for_preview_ready(info: dict, timeout: float = 25.0) -> tuple:
    """Ждёт открытия порта; если процесс/контейнер уже упал — не ждёт таймаут
    впустую, а сразу возвращает понятную ошибку с хвостом лога."""
    if not info or info.get("error"):
        return False, (info or {}).get("error", "Неизвестная ошибка запуска превью.")

    port = info.get("port")
    start = time.time()
    while time.time() - start < timeout:
        if info.get("mode") == "process":
            proc = info.get("proc")
            if proc is not None and proc.poll() is not None:
                try:
                    out = proc.stdout.read() if proc.stdout else ""
                except Exception:
                    out = ""
                return False, f"Сервис упал при запуске (код {proc.returncode}):\n{out[-1500:]}"
        elif info.get("mode") == "docker":
            cid = info.get("container_id")
            check = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", cid],
                                    capture_output=True, text=True, timeout=5)
            if check.returncode == 0 and check.stdout.strip() != "true":
                logs = subprocess.run(["docker", "logs", cid], capture_output=True, text=True, timeout=5)
                return False, f"Контейнер остановился:\n{(logs.stdout + logs.stderr)[-1500:]}"

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', port)) == 0:
                return True, "OK"
        time.sleep(0.4)
    return False, f"Сервис не поднялся за {int(timeout)} секунд."


def stop_any_preview(info: dict):
    if not info:
        return
    if info.get("mode") == "docker":
        cid = info.get("container_id")
        if cid:
            try:
                subprocess.run(["docker", "stop", cid], capture_output=True, timeout=15)
            except Exception:
                pass
    else:
        proc = info.get("proc")
        try:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception:
            pass


# ====================== ЭКСПОРТ ПРОЕКТА (.zip + понятная инструкция) ======================
def build_deployment_instructions(service_filename: str, requirements: list, has_dockerfile: bool) -> str:
    module_name = service_filename[:-3] if service_filename.endswith(".py") else service_filename
    L = []
    L.append("=" * 70)
    L.append("ИНСТРУКЦИЯ ПО РАЗВЁРТЫВАНИЮ ПРОЕКТА")
    L.append(f"Файл сервиса: {service_filename}")
    L.append("=" * 70)
    L.append("")
    L.append("Инструкция рассчитана на новичка — просто выполняйте шаги по порядку.")
    L.append("")
    L.append("-" * 70)
    L.append("ВАРИАНТ 1. Быстрый запуск на своём компьютере (без Docker)")
    L.append("-" * 70)
    L.append("1. Установите Python 3.10 или новее: https://www.python.org/downloads/")
    L.append("2. Распакуйте архив в отдельную папку и откройте в ней терминал.")
    L.append("3. Создайте и активируйте виртуальное окружение:")
    L.append("     python3 -m venv venv")
    L.append("     source venv/bin/activate        (Linux / macOS)")
    L.append("     venv\\Scripts\\activate           (Windows)")
    L.append("4. Установите зависимости:")
    L.append("     pip install -r requirements.txt")
    L.append("5. Запустите сервис:")
    L.append(f"     uvicorn {module_name}:app --host 0.0.0.0 --port 8000")
    L.append("6. Откройте в браузере: http://localhost:8000/docs")
    L.append("   Это встроенная Swagger-документация FastAPI — там можно сразу")
    L.append("   протестировать все ручки API прямо из браузера, без Postman.")
    L.append("")
    if has_dockerfile:
        L.append("-" * 70)
        L.append("ВАРИАНТ 2. Через Docker (рекомендуется для сервера)")
        L.append("-" * 70)
        L.append("1. Установите Docker: https://docs.docker.com/get-docker/")
        L.append("2. В папке с проектом соберите образ:")
        L.append(f"     docker build -t {module_name} .")
        L.append("3. Запустите контейнер:")
        L.append(f"     docker run -d -p 8000:8000 --name {module_name} {module_name}")
        L.append("4. Откройте в браузере: http://localhost:8000/docs")
        L.append(f"5. Чтобы остановить и удалить контейнер: docker stop {module_name} && docker rm {module_name}")
        L.append("")
    L.append("-" * 70)
    L.append("ВАРИАНТ 3. Развёртывание на удалённом сервере (VPS)")
    L.append("-" * 70)
    L.append("1. Скопируйте архив на сервер (пример через scp):")
    L.append("     scp project.zip user@ваш_сервер:/home/user/")
    L.append("2. Подключитесь по SSH и распакуйте:")
    L.append("     ssh user@ваш_сервер")
    L.append("     unzip project.zip -d myproject && cd myproject")
    L.append("3. Повторите шаги ВАРИАНТА 1 или (предпочтительнее для сервера) ВАРИАНТА 2.")
    L.append("4. Чтобы сервис не падал при закрытии SSH-сессии:")
    L.append("   - с Docker ничего дополнительно делать не нужно, контейнер и так в фоне;")
    L.append("   - без Docker — заверните запуск в systemd-юнит или используйте `screen`/`tmux`.")
    L.append("")
    L.append("-" * 70)
    L.append("ЧТО ВНУТРИ АРХИВА")
    L.append("-" * 70)
    L.append(f"  {service_filename:<24} — код сервиса")
    L.append(f"  {'test_' + service_filename:<24} — автотесты (запуск: pytest test_*.py)")
    L.append(f"  {'requirements.txt':<24} — зависимости Python")
    if has_dockerfile:
        L.append(f"  {'Dockerfile':<24} — рецепт сборки Docker-образа")
    L.append(f"  {'SECURITY_AUDIT.md':<24} — отчёт аудита безопасности (если создавался)")
    L.append("")
    if requirements:
        L.append("Зависимости проекта: " + ", ".join(requirements))
        L.append("")
    L.append("Если что-то не работает: проверьте версию Python (нужна 3.10+) и что порт")
    L.append("8000 не занят другим приложением (можно запустить и на другом порту —")
    L.append("замените 8000 на любой свободный в командах выше).")
    return "\n".join(L)


def build_project_zip(service_filename: str, test_filename: str = None, sec_report: str = None) -> str:
    import zipfile

    project_name = service_filename[:-3] if service_filename.endswith(".py") else service_filename
    zip_path = os.path.join(BASE_DIR, f"{project_name}_export.zip")

    service_path = os.path.join(WORKING_DIR, service_filename)
    if not os.path.exists(service_path):
        raise FileNotFoundError(f"Файл сервиса {service_filename} не найден в песочнице.")

    with open(service_path, "r", encoding="utf-8", errors="ignore") as f:
        code = f.read()
    scaffold = write_project_scaffold(service_filename, code)

    if sec_report:
        with open(os.path.join(WORKING_DIR, "SECURITY_AUDIT.md"), "w", encoding="utf-8") as f:
            f.write(sec_report)

    instructions = build_deployment_instructions(service_filename, scaffold["requirements"], has_dockerfile=True)
    instructions_path = os.path.join(WORKING_DIR, "DEPLOY_INSTRUCTIONS.txt")
    with open(instructions_path, "w", encoding="utf-8") as f:
        f.write(instructions)

    files_to_zip = [service_filename, "requirements.txt", "Dockerfile", "DEPLOY_INSTRUCTIONS.txt"]
    if test_filename and os.path.exists(os.path.join(WORKING_DIR, test_filename)):
        files_to_zip.append(test_filename)
    if sec_report or os.path.exists(os.path.join(WORKING_DIR, "SECURITY_AUDIT.md")):
        files_to_zip.append("SECURITY_AUDIT.md")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in files_to_zip:
            fpath = os.path.join(WORKING_DIR, fname)
            if os.path.exists(fpath):
                zf.write(fpath, arcname=fname)

    return zip_path


# ====================== ВНЕШНИЙ ПРОЦЕСС CREWAI ======================
def worker_crew_execution(mode, user_request, raw_web_context, target_filename, target_file_code,
                           general_m, coder_m, cyber_m, patch_m, qa_m, result_queue, log_queue):
    sys.stdout = QueueStreamLogger(log_queue)
    try:
        llm_general = LLM(model=f"ollama/{general_m}", base_url=OLLAMA_HOST, temperature=0.2, max_tokens=3000, timeout=300)
        llm_coder = LLM(model=f"ollama/{coder_m}", base_url=OLLAMA_HOST, temperature=0.1, max_tokens=4096, timeout=300)
        llm_cyber = LLM(model=f"ollama/{cyber_m}", base_url=OLLAMA_HOST, temperature=0.1, max_tokens=2500, timeout=300)
        llm_patch = LLM(model=f"ollama/{patch_m}", base_url=OLLAMA_HOST, temperature=0.1, max_tokens=4096, timeout=300)
        llm_qa = LLM(model=f"ollama/{qa_m}", base_url=OLLAMA_HOST, temperature=0.1, max_tokens=3500, timeout=300)

        # Имя файла теперь не жёстко "auth_service.py": если правим существующий
        # файл — берём его имя, иначе генерируем короткий slug из запроса,
        # чтобы разные задачи не затирали друг друга на диске.
        service_filename = target_filename or f"{slugify_filename(user_request)}.py"
        test_filename = f"test_{service_filename}" if not service_filename.startswith("test_") else service_filename

        def _save_service_cb(task_output):
            try:
                force_save_code(str(task_output.raw), service_filename)
            except Exception:
                pass

        def _save_patched_and_scaffold_cb(task_output):
            try:
                force_save_code(str(task_output.raw), service_filename)
                svc_path = os.path.join(WORKING_DIR, service_filename)
                if os.path.exists(svc_path):
                    with open(svc_path, "r", encoding="utf-8", errors="ignore") as f:
                        final_code = f.read()
                    # requirements.txt обновляется всегда (дёшево и держит зависимости
                    # в актуальном состоянии); Dockerfile создаётся один раз — дальше
                    # его может доработать DevOps-агент (t6), и это не затирается.
                    write_project_scaffold(service_filename, final_code)
            except Exception:
                pass

        def _save_test_cb(task_output):
            try:
                force_save_code(str(task_output.raw), test_filename)
            except Exception:
                pass

        if mode == "test_gen":
            qa_engineer = Agent(
                role="Senior QA Security Test Engineer",
                goal="Написание автономных тестов pytest с TestClient",
                backstory="Ведущий QA-инженер по безопасности API.",
                llm=llm_qa,
                tools=TOOLS_QA,
                max_iter=8,
                handle_parsing_errors=True,
                verbose=True
            )
            t = Task(
                description=(
                    f"ЗАДАЧА: Напиши изолированный файл тестов pytest для файла `{target_filename or service_filename}`.\n\n"
                    f"РЕАЛЬНЫЙ КОД СЕРВИСА НА ДИСКЕ:\n```python\n{target_file_code}\n```\n\n"
                    "ОБЯЗАТЕЛЬНЫЕ ПРАВИЛА:\n"
                    "1. Импортируй: `from " + service_filename.replace(".py", "") + " import app`, `from fastapi.testclient import TestClient`.\n"
                    "2. Создай: `client = TestClient(app)`.\n"
                    "3. Напиши позитивные и негативные кейсы (ошибки 401, 400, 403).\n"
                    "4. СТРОГИЙ ЗАПРЕТ: Не пиши код самого сервиса в тестах. Только тесты в одном блоке ```python ... ```.\n"
                    f"5. Когда код теста готов, сохрани его через save_code_to_sandbox с file_name='{test_filename}', "
                    "затем проверь через validate_python_syntax."
                ),
                expected_output="Python-код файла тестов pytest в блоке ```python```.",
                agent=qa_engineer,
                callback=_save_test_cb,
            )
            crew = Crew(agents=[qa_engineer], tasks=[t], process=Process.sequential, verbose=True)
            res = crew.kickoff()
            result_queue.put({"status": "ok", "tests": str(res), "raw": str(res),
                               "service_filename": service_filename, "test_filename": test_filename})

        elif mode == "full_dev":
            recall_notes = recall_relevant_notes(user_request, top_k=3)
            recall_block = format_recall_block(recall_notes)

            architect = Agent(
                role="System Architect",
                goal="Архитектура микросервисов с глубоким исследованием",
                backstory="Кибер-архитектор систем. Следуй принципам Research-инженерии: опирайся на точные факты, проектируй надежные схемы без домыслов.",
                llm=llm_general,
                tools=TOOLS_ARCHITECT,
                verbose=True
            )
            coder = Agent(
                role="Senior Backend Developer",
                goal="Написание начального кода",
                backstory="Senior Python разработчик.",
                llm=llm_coder,
                tools=TOOLS_CODER,
                max_iter=10,
                handle_parsing_errors=True,
                verbose=True
            )
            security_expert = Agent(
                role="Cybersecurity Specialist",
                goal="Строгий аудит кода по стандарту Code Review (Standards & Spec)",
                backstory="Ведущий AppSec-аудитор. Проводишь двухосевую оценку: проверяешь код на соответствие спецификации (Spec) и стандартам безопасности (Standards). Отвечай строго в формате Markdown, без сырого JSON.",
                llm=llm_cyber,
                tools=TOOLS_SECURITY,
                max_iter=8,
                handle_parsing_errors=True,
                verbose=True
            )
            patch_engineer = Agent(
                role="Patch & Remediation Engineer",
                goal="Устранение уязвимостей в коде",
                backstory="Инженер по безопасности и патчингу. Исправляешь замечания аудитора, применяя безопасные практики.",
                llm=llm_patch,
                tools=TOOLS_PATCH,
                max_iter=10,
                handle_parsing_errors=True,
                verbose=True
            )
            qa_engineer = Agent(
                role="QA Test Automation Engineer",
                goal="Написание тестов pytest",
                backstory="Эксперт тестирования API.",
                llm=llm_qa,
                tools=TOOLS_QA,
                max_iter=8,
                handle_parsing_errors=True,
                verbose=True
            )
            # 6-й агент: DevOps. Dockerfile и requirements.txt уже сгенерированы
            # детерминированно (шаблон + анализ import-ов) в колбэке патч-инженера —
            # это гарантирует рабочий бейзлайн, даже если модель ничего не поправит.
            # Роль агента — свериться с реальным кодом через инструменты и починить,
            # если чего-то не хватает (системный пакет, версия и т.п.).
            devops_engineer = Agent(
                role="DevOps Engineer",
                goal="Подготовка сервиса к развёртыванию: проверка Dockerfile и requirements.txt",
                backstory="Опытный DevOps-инженер. Сверяешь Dockerfile и requirements.txt с реальным кодом сервиса и чинишь несоответствия.",
                llm=llm_patch,
                tools=TOOLS_DEVOPS,
                max_iter=6,
                handle_parsing_errors=True,
                verbose=True
            )

            t1 = Task(description=f"{recall_block}Спроектируй микросервис: {user_request}.", expected_output="План архитектуры.", agent=architect)
            t2 = Task(
                description=f"Напиши полный код на FastAPI с SQLite и SQLAlchemy. Без сокращений. "
                            f"После генерации сохрани файл через save_code_to_sandbox с file_name='{service_filename}'.",
                expected_output="Python-код сервиса в блоке ```python```.", agent=coder, context=[t1],
                callback=_save_service_cb,
            )
            t3 = Task(
                description=f"{recall_block}Проведи аудит безопасности полученного кода. Выяви уязвимости, CWE, OWASP. Строго без JSON! "
                            f"Файл сервиса уже сохранён на диске как '{service_filename}' — можешь исследовать его через explore_project_structure.",
                expected_output="Отчет безопасности в Markdown.", agent=security_expert, context=[t2],
            )
            t4 = Task(
                description=f"На основе отчета безопасности исправь весь уязвимый код в файле '{service_filename}', "
                            "примени безопасные практики. Сохрани исправленный код через save_code_to_sandbox "
                            f"с тем же file_name='{service_filename}', затем проверь его через validate_python_syntax. "
                            "Выдай итоговый безопасный код в блоке ```python```.",
                expected_output="Исправленный безопасный Python-код в блоке ```python```.", agent=patch_engineer, context=[t2, t3],
                callback=_save_patched_and_scaffold_cb,
            )
            t5 = Task(
                description=f"Напиши автономный набор тестов pytest для финального безопасного кода сервиса "
                            f"(файл '{service_filename}', импортируй `from {service_filename[:-3]} import app`). "
                            f"Сохрани тест через save_code_to_sandbox с file_name='{test_filename}' и прогони через run_pytest_suite.",
                expected_output="Python-код файла тестов pytest.", agent=qa_engineer, context=[t4],
                callback=_save_test_cb,
            )
            t6 = Task(
                description=(
                    f"ЗАДАЧА: Прочитай через read_sandbox_file файлы 'Dockerfile', 'requirements.txt' и "
                    f"'{service_filename}'. Проверь, что requirements.txt содержит все нужные для кода "
                    "зависимости, а Dockerfile корректно собирает и запускает сервис (нет ли недостающих "
                    "системных пакетов, верна ли версия Python, верна ли команда запуска). "
                    "Если нужно — исправь через save_code_to_sandbox с соответствующим file_name "
                    "('Dockerfile' или 'requirements.txt'). Если всё уже корректно — прямо подтверди это, "
                    "ничего не переписывая."
                ),
                expected_output="Подтверждение корректности либо исправленные Dockerfile/requirements.txt.",
                agent=devops_engineer, context=[t4],
            )

            crew = Crew(agents=[architect, coder, security_expert, patch_engineer, qa_engineer, devops_engineer],
                        tasks=[t1, t2, t3, t4, t5, t6], process=Process.sequential, verbose=True)
            res = crew.kickoff()

            code_out = str(t4.output.raw) if hasattr(t4, 'output') and t4.output else (str(t2.output.raw) if hasattr(t2, 'output') and t2.output else "")
            sec_out = str(t3.output.raw) if hasattr(t3, 'output') and t3.output else ""
            test_out = str(t5.output.raw) if hasattr(t5, 'output') and t5.output else ""
            devops_out = str(t6.output.raw) if hasattr(t6, 'output') and t6.output else ""

            if sec_out:
                save_note_with_embedding(f"Аудит {service_filename}", sec_out[:1500], "security_audit")
                try:
                    with open(os.path.join(WORKING_DIR, "SECURITY_AUDIT.md"), "w", encoding="utf-8") as f:
                        f.write(sec_out)
                except Exception:
                    pass

            result_queue.put({
                "status": "ok", "code": code_out, "sec": sec_out, "tests": test_out, "devops": devops_out, "raw": str(res),
                "service_filename": service_filename, "test_filename": test_filename,
            })

        elif mode == "sec_only":
            recall_notes = recall_relevant_notes(user_request, top_k=3)
            recall_block = format_recall_block(recall_notes)

            security_expert = Agent(
                role="Senior Application Security Engineer",
                goal="Глубокий аудит безопасности исходного кода",
                backstory="Ведущий AppSec-аудитор. СТРОГИЙ ЗАПРЕТ НА JSON! Выдавай ответ исключительно в читаемом формате Markdown на русском языке.",
                llm=llm_cyber,
                tools=TOOLS_SECURITY,
                max_iter=8,
                handle_parsing_errors=True,
                verbose=True
            )
            file_context_prompt = f"РЕАЛЬНОЕ СОДЕРЖИМОЕ ФАЙЛА НА ДИСКЕ ({target_filename}):\n```python\n{target_file_code}\n```\n\n" if target_file_code else ""
            t = Task(
                description=(
                    f"{recall_block}ЗАДАЧА: Проведи глубокий аудит безопасности кода по запросу: {user_request}\n\n"
                    f"{file_context_prompt}"
                    "ПРАВИЛА:\n1. Запрещено использовать JSON. Пиши отчет обычным текстом с заголовками Markdown.\n"
                    "2. Выяви уязвимости, CWE, OWASP и дай рекомендации по исправлению."
                ),
                expected_output="Отчет по аудиту безопасности в формате Markdown.",
                agent=security_expert
            )
            crew = Crew(agents=[security_expert], tasks=[t], process=Process.sequential, verbose=True)
            res = crew.kickoff()
            save_note_with_embedding(f"Аудит {target_filename or user_request[:60]}", str(res)[:1500], "security_audit")
            if target_filename:
                try:
                    with open(os.path.join(WORKING_DIR, "SECURITY_AUDIT.md"), "w", encoding="utf-8") as f:
                        f.write(str(res))
                except Exception:
                    pass
            result_queue.put({"status": "ok", "raw": str(res)})

        elif mode == "code_only":
            coder = Agent(role="Senior Backend Developer", goal="Писать чистый код", backstory="Senior разработчик.", llm=llm_coder, tools=TOOLS_CODER, max_iter=10, handle_parsing_errors=True, verbose=True)
            t = Task(
                description=f"Напиши рабочий код: {user_request}. В блоке ```python ... ```. "
                             f"Сохрани через save_code_to_sandbox с file_name='{service_filename}'.",
                expected_output="Рабочий код.", agent=coder, callback=_save_service_cb,
            )
            crew = Crew(agents=[coder], tasks=[t], process=Process.sequential, verbose=True)
            res = crew.kickoff()
            result_queue.put({"status": "ok", "raw": str(res), "service_filename": service_filename})

        else:
            researcher = Agent(role="Senior Research Analyst", goal="Анализ источников", backstory="Кибер-аналитик данных.", llm=llm_general, tools=TOOLS_RESEARCH, verbose=True)
            t = Task(
                description=f"ЗАДАЧА: Подготовь подробный отчет по теме: {user_request}\n\n{raw_web_context}",
                expected_output="Отчет со сносками.",
                agent=researcher
            )
            crew = Crew(agents=[researcher], tasks=[t], process=Process.sequential, verbose=True)
            res = crew.kickoff()
            result_queue.put({"status": "ok", "raw": str(res)})

    except Exception as e:
        import traceback
        result_queue.put({"status": "error", "error": str(e), "trace": traceback.format_exc()})
