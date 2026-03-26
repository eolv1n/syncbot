FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

COPY pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests
COPY .env.example ./
COPY scripts ./scripts

RUN pip install --no-cache-dir -e .

CMD ["python", "-m", "app", "dry-run"]

