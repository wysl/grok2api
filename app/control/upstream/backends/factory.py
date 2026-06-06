"""Upstream provider repository factory."""

import os
from pathlib import Path

from app.control.account.backends.factory import get_repository_backend
from app.platform.paths import data_path
from ..repository import UpstreamProviderRepository


def create_repository() -> UpstreamProviderRepository:
    backend = get_repository_backend()
    if backend == "local":
        return _make_local()
    if backend == "redis":
        return _make_redis()
    if backend in {"mysql", "postgresql"}:
        return _make_sql(backend)
    raise ValueError(f"Unknown upstream storage backend: {backend!r}")


def describe_repository_target() -> tuple[str, str]:
    backend = get_repository_backend()
    if backend == "local":
        return "local", str(_resolve_local_db_path())
    return backend, "shared account storage target"


def _get_env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _resolve_local_db_path() -> Path:
    path_str = _get_env("UPSTREAM_LOCAL_PATH", "")
    if not path_str:
        account_path = _get_env("ACCOUNT_LOCAL_PATH", "")
        if account_path:
            path_str = account_path
    db_path = Path(path_str) if path_str else data_path("accounts.db")
    if not db_path.is_absolute():
        db_path = Path(__file__).resolve().parents[4] / db_path
    return db_path


def _make_local() -> UpstreamProviderRepository:
    from .local import LocalUpstreamProviderRepository

    return LocalUpstreamProviderRepository(_resolve_local_db_path())


def _make_redis() -> UpstreamProviderRepository:
    from redis.asyncio import Redis
    from .redis import RedisUpstreamProviderRepository

    url = _get_env("UPSTREAM_REDIS_URL") or _get_env("ACCOUNT_REDIS_URL")
    if not url:
        raise ValueError("Missing required env: ACCOUNT_REDIS_URL")
    return RedisUpstreamProviderRepository(Redis.from_url(url, decode_responses=False))


def _make_sql(dialect: str) -> UpstreamProviderRepository:
    from app.control.account.backends.sql import create_mysql_engine, create_pgsql_engine
    from .sql import SqlUpstreamProviderRepository

    if dialect == "mysql":
        url = _get_env("UPSTREAM_MYSQL_URL") or _get_env("ACCOUNT_MYSQL_URL")
        engine = create_mysql_engine(url)
    else:
        url = _get_env("UPSTREAM_POSTGRESQL_URL") or _get_env(
            "ACCOUNT_POSTGRESQL_URL"
        )
        engine = create_pgsql_engine(url)
    return SqlUpstreamProviderRepository(engine, dialect=dialect, dispose_engine=False)


__all__ = ["create_repository", "describe_repository_target"]
