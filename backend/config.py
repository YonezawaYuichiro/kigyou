from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
PROMPTS_DIR = PROJECT_ROOT / "prompts"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # API キー
    anthropic_api_key: str = Field(..., description="Anthropic API キー")

    # DB
    database_url: str = Field(
        default="postgresql+psycopg://gradmatch:gradmatch_dev@localhost:5433/gradmatch"
    )

    # ログ
    log_level: str = Field(default="INFO")

    # LLM モデル名
    sonnet_model: str = "claude-sonnet-4-6"
    haiku_model: str = "claude-haiku-4-5"
    # Gemini: 2.5-flash はthinkingモードで高額になりやすい
    # 2.0-flash: $0.075/1M input, $0.30/1M output（グラウンディング検索対応）
    # 2.0-flash-lite: $0.0375/1M input, $0.15/1M output（さらに安い・検索対応）
    gemini_model: str = "gemini-2.0-flash"

    # Gemini API キー（空の場合は Claude のみ使用）
    gemini_api_key: str = Field(default="", description="Gemini API キー")

    # Phase 1 プロバイダー: "claude" | "gemini" | "both"（交互）
    phase1_provider: str = Field(default="both", description="Phase1 LLM プロバイダー")

    # Phase 1 定数
    phase1_batch_count: int = 6
    phase1_companies_per_batch: int = 50
    phase1_max_tokens: int = 8000
    phase1_temperature: float = 0.7
    sonnet_sleep_seconds: float = 1.0

    # Phase 2 定数
    http_timeout_seconds: float = 5.0
    http_user_agent: str = "Mozilla/5.0 (compatible; GradMatch-AI/0.1)"
    url_sleep_seconds: float = 0.5

    # Phase 3 定数
    html_text_max_chars: int = 2000
    haiku_max_tokens: int = 2000
    haiku_sleep_seconds: float = 0.5

    # Phase 2.5 定数
    houjin_sleep_seconds: float = 2.0  # 法人番号公表サイトへのアクセス間隔

    # Phase 3a 定数
    openwork_sleep_seconds: float = 4.0  # Playwright 分の余裕を持たせる

    # Phase 3b 定数
    green_sleep_seconds: float = 3.0

    # 連続失敗上限
    max_consecutive_failures: int = 3


settings = Settings()
