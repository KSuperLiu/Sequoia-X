FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

WORKDIR /app
COPY pyproject.toml README.md ./
COPY sequoia_x ./sequoia_x
COPY main.py ./main.py
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 sequoia && mkdir -p /app/data /app/logs /app/backups \
    && chown -R sequoia:sequoia /app
USER sequoia

CMD ["uvicorn", "sequoia_x.app.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
