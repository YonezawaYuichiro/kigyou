"""FastAPI アプリケーションエントリーポイント（V3 Phase 6）。

起動:
  uvicorn backend.main:app --reload --port 8000

Swagger UI:
  http://localhost:8000/docs

エンドポイント一覧:
  GET  /api/v2/health
  POST /api/v2/profile
  GET  /api/v2/matches?session_id=...
  GET  /api/v2/companies?category=...&prefecture=...&page=1&limit=20
  GET  /api/v2/companies/{id}
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import router
from backend.config import settings

logging.basicConfig(level=settings.log_level)

app = FastAPI(
    title="GradMatch-AI API",
    description="新卒就活向け企業マッチング・推薦システム REST API",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501"],  # Streamlit dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
