FROM python:3.13-slim

RUN apt-get update && apt-get install -y \
    pkg-config \
    libssl-dev \
    libsqlite3-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.14 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src/ ./src/

RUN mkdir -p /data/anki && \
    groupadd -r sensei && useradd -r -g sensei sensei && \
    chown -R sensei:sensei /app /data

USER sensei

CMD ["uv", "run", "python", "-m", "src.main"]
