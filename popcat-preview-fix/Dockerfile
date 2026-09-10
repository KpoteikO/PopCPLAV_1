FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip install --no-cache-dir 'uvicorn[standard]' fastapi \
    && python -m pip check \
    && python -c "import uvicorn, fastapi"
RUN useradd --create-home appuser && chown appuser /app
COPY --chown=appuser:appuser service.py .
USER appuser
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "service:app", "--host", "0.0.0.0", "--port", "8000"]
