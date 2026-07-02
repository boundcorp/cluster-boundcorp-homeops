FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY birdweather_ingester ./birdweather_ingester

RUN pip install --no-cache-dir .

USER 65534:65534

CMD ["birdweather-ingester"]
