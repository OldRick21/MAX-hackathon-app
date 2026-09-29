# Облачный сервис: раннер (services/runner) держит отдельный процесс этого сервиса для каждого вуза.
# Сборка из каталога services/: docker build -f administration/Dockerfile services
FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data APP_MODULE=app.main:app DB_ENV= DB_FILE=
COPY administration/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt httpx
RUN useradd --uid 10002 --create-home app && mkdir /data && chown app:app /data
COPY administration/app ./app
COPY administration/client ./client
COPY runner/runner.py ./runner.py
USER app
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
CMD ["uvicorn", "runner:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
