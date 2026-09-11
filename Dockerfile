FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TRADING_MODE=PAPER
WORKDIR /app
RUN groupadd --gid 10001 aegis && useradd --uid 10001 --gid aegis --create-home aegis
COPY requirements.lock pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.lock
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
COPY config ./config
RUN pip install --no-deps . && mkdir -p /app/var && chown -R aegis:aegis /app
USER aegis
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"
CMD ["aegis", "serve", "--host", "0.0.0.0", "--port", "8000"]
