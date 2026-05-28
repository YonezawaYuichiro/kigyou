FROM python:3.11-slim

WORKDIR /app

# psycopg[binary] はバンドル済み libpq を使うが、lxml 等のビルドに gcc が必要な場合に備える
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# ── 依存レイヤー（pyproject.toml が変わるときだけ再ビルド） ────────────────
COPY pyproject.toml .
# hatchling が backend パッケージを要求するのでスタブを置く
RUN mkdir -p backend && touch backend/__init__.py
RUN pip install --no-cache-dir -e .

# ── ソースレイヤー ────────────────────────────────────────────────────────────
# バインドマウント運用のため COPY はメタ情報として置く
# (compose の volumes: .:/app がこのレイヤーを上書きする)
COPY . .

EXPOSE 8000 8501
