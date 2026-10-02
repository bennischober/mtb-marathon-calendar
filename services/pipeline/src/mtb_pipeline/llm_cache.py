"""Durable cache for LLM answers, keyed by content, model and prompt version.

Re-running a collection must not re-ask the LLM about the same event text:
that would burn tokens and make runs non-reproducible. Answers are stored in
SQLite, keyed on ``(content_hash, model, prompt_version)``, so a prompt or
model change invalidates stale verdicts automatically.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import String, select
from sqlalchemy.orm import Mapped, mapped_column

from .database import create_db_engine, init_db, session_scope
from .llm import PROMPT_VERSION, cache_key
from .orm import Base


class LLMCacheRecord(Base):
    __tablename__ = "llm_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(120))
    prompt_version: Mapped[int]
    answer: Mapped[str]  # raw JSON the provider returned
    created_at: Mapped[str] = mapped_column(String(32))


def get_cached(cache_path: Path, content_hash: str, model: str) -> Any | None:
    """Return the cached parsed answer, or ``None`` on a miss."""
    key = cache_key(content_hash, model)
    engine = create_db_engine(cache_path)
    init_db(engine)
    try:
        with session_scope(engine) as session:
            record = session.get(LLMCacheRecord, key)
            if record is None:
                return None
            return _loads(record.answer)
    finally:
        engine.dispose()


def put_cached(cache_path: Path, content_hash: str, model: str, answer: Any) -> None:
    key = cache_key(content_hash, model)
    engine = create_db_engine(cache_path)
    init_db(engine)
    try:
        with session_scope(engine) as session:
            existing = session.get(LLMCacheRecord, key)
            payload = _dumps(answer)
            stamp = datetime.now(UTC).isoformat()
            if existing is None:
                session.add(
                    LLMCacheRecord(
                        key=key,
                        content_hash=content_hash,
                        model=model,
                        prompt_version=PROMPT_VERSION,
                        answer=payload,
                        created_at=stamp,
                    )
                )
            else:
                existing.answer = payload
                existing.created_at = stamp
            session.commit()
    finally:
        engine.dispose()


def cached_entry_count(cache_path: Path) -> int:
    engine = create_db_engine(cache_path)
    init_db(engine)
    try:
        with session_scope(engine) as session:
            return len(session.scalars(select(LLMCacheRecord.key)).all())
    finally:
        engine.dispose()


def _loads(raw: str) -> Any:
    import json

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _dumps(answer: Any) -> str:
    import json

    return json.dumps(answer, ensure_ascii=False, sort_keys=True)
