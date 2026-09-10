"""Explicit FastAPI preview launcher. Run only code you trust.
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
