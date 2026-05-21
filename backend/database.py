from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.config import settings

_engine: Engine | None = None
_session_factory: sessionmaker | None = None


def get_engine() -> Engine:
    """エンジンを遅延生成する。テスト時にpsycopg2なしでインポートできる。"""
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.database_url,
            echo=False,
            pool_pre_ping=True,
        )
    return _engine


def _get_session_factory() -> sessionmaker:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), autocommit=False, autoflush=False)
    return _session_factory


def init_db() -> None:
    """テーブルを作成する。Alembic 導入後はこの関数を差し替える。"""
    # 循環インポート回避のためローカルインポート
    from backend.models import Base

    Base.metadata.create_all(get_engine())


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """コンテキストマネージャ版セッション。with 句で使用する。"""
    session = _get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
