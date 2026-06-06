"""SQLite upstream provider repository."""

import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from app.platform.runtime.clock import now_ms
from ..commands import ListUpstreamsQuery, UpstreamPatch, UpstreamUpsert
from ..models import (
    UpstreamChangeSet,
    UpstreamMutationResult,
    UpstreamPage,
    UpstreamProviderRecord,
    UpstreamRuntimeSnapshot,
    UpstreamStatus,
    new_provider_id,
)

_TBL = "upstream_providers"
_META = "upstream_meta"


class LocalUpstreamProviderRepository:
    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path)
        self._lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_sync(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(f"""
                CREATE TABLE IF NOT EXISTS {_META} (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                INSERT OR IGNORE INTO {_META} VALUES ('revision', '0');

                CREATE TABLE IF NOT EXISTS {_TBL} (
                    id             TEXT    NOT NULL PRIMARY KEY,
                    name           TEXT    NOT NULL,
                    type           TEXT    NOT NULL,
                    base_url       TEXT    NOT NULL,
                    api_key        TEXT    NOT NULL,
                    status         TEXT    NOT NULL,
                    models         TEXT    NOT NULL DEFAULT '[]',
                    weight         INTEGER NOT NULL DEFAULT 1,
                    timeout_sec    REAL    NOT NULL DEFAULT 120,
                    last_health    TEXT    NOT NULL DEFAULT 'unknown',
                    last_health_at INTEGER,
                    last_error     TEXT,
                    created_at     INTEGER NOT NULL,
                    updated_at     INTEGER NOT NULL,
                    deleted_at     INTEGER,
                    ext            TEXT    NOT NULL DEFAULT '{{}}',
                    revision       INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_upstream_revision
                    ON {_TBL} (revision);
                CREATE INDEX IF NOT EXISTS idx_upstream_status
                    ON {_TBL} (status);
                CREATE INDEX IF NOT EXISTS idx_upstream_deleted
                    ON {_TBL} (deleted_at) WHERE deleted_at IS NOT NULL;
            """)
            conn.commit()

    def _bump_revision(self, conn: sqlite3.Connection) -> int:
        conn.execute(
            f"UPDATE {_META} SET value = CAST(value AS INTEGER) + 1 WHERE key = 'revision'"
        )
        row = conn.execute(
            f"SELECT CAST(value AS INTEGER) FROM {_META} WHERE key = 'revision'"
        ).fetchone()
        return int(row[0])

    def _get_revision_sync(self, conn: sqlite3.Connection) -> int:
        row = conn.execute(
            f"SELECT CAST(value AS INTEGER) FROM {_META} WHERE key = 'revision'"
        ).fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> UpstreamProviderRecord:
        data = dict(row)
        data["models"] = json.loads(data.get("models") or "[]")
        data["ext"] = json.loads(data.get("ext") or "{}")
        return UpstreamProviderRecord.model_validate(data)

    @staticmethod
    def _record_to_params(record: UpstreamProviderRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "name": record.name,
            "type": record.type.value,
            "base_url": record.base_url,
            "api_key": record.api_key,
            "status": record.status.value,
            "models": json.dumps(record.models),
            "weight": record.weight,
            "timeout_sec": record.timeout_sec,
            "last_health": record.last_health.value,
            "last_health_at": record.last_health_at,
            "last_error": record.last_error,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "deleted_at": record.deleted_at,
            "ext": json.dumps(record.ext),
            "revision": record.revision,
        }

    def _upsert_sync(
        self,
        conn: sqlite3.Connection,
        item: UpstreamUpsert,
        revision: int,
    ) -> tuple[int, str]:
        ts = now_ms()
        provider_id = item.id or new_provider_id()
        row = conn.execute(
            f"SELECT created_at FROM {_TBL} WHERE id = ?",
            (provider_id,),
        ).fetchone()
        created_at = int(row["created_at"]) if row else ts
        record = UpstreamProviderRecord(
            id=provider_id,
            name=item.name,
            type=item.type,
            base_url=item.base_url,
            api_key=item.api_key,
            status=item.status,
            models=item.models,
            weight=item.weight,
            timeout_sec=item.timeout_sec,
            created_at=created_at,
            updated_at=ts,
            deleted_at=None,
            ext=item.ext,
            revision=revision,
        )
        params = self._record_to_params(record)
        conn.execute(
            f"""
            INSERT INTO {_TBL} (
                id, name, type, base_url, api_key, status, models, weight,
                timeout_sec, last_health, last_health_at, last_error,
                created_at, updated_at, deleted_at, ext, revision
            ) VALUES (
                :id, :name, :type, :base_url, :api_key, :status, :models,
                :weight, :timeout_sec, :last_health, :last_health_at,
                :last_error, :created_at, :updated_at, :deleted_at, :ext,
                :revision
            )
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                type = excluded.type,
                base_url = excluded.base_url,
                api_key = excluded.api_key,
                status = excluded.status,
                models = excluded.models,
                weight = excluded.weight,
                timeout_sec = excluded.timeout_sec,
                updated_at = excluded.updated_at,
                deleted_at = NULL,
                ext = excluded.ext,
                revision = excluded.revision
            """,
            params,
        )
        return int(conn.execute("SELECT changes()").fetchone()[0]), provider_id

    def _patch_sync(
        self,
        conn: sqlite3.Connection,
        patch: UpstreamPatch,
        revision: int,
    ) -> int:
        row = conn.execute(f"SELECT * FROM {_TBL} WHERE id = ?", (patch.id,)).fetchone()
        if row is None:
            return 0
        record = self._row_to_record(row)
        updates = patch.model_dump(exclude_unset=True)
        updates.pop("id", None)
        ext_merge = updates.pop("ext_merge", None)
        if ext_merge:
            updates["ext"] = {**record.ext, **ext_merge}
        updates["updated_at"] = now_ms()
        updates["revision"] = revision
        candidate = record.model_copy(update=updates)
        candidate = UpstreamProviderRecord.model_validate(candidate.model_dump())
        params = self._record_to_params(candidate)
        conn.execute(
            f"""
            UPDATE {_TBL} SET
                name = :name,
                type = :type,
                base_url = :base_url,
                api_key = :api_key,
                status = :status,
                models = :models,
                weight = :weight,
                timeout_sec = :timeout_sec,
                last_health = :last_health,
                last_health_at = :last_health_at,
                last_error = :last_error,
                updated_at = :updated_at,
                deleted_at = :deleted_at,
                ext = :ext,
                revision = :revision
            WHERE id = :id
            """,
            params,
        )
        return int(conn.execute("SELECT changes()").fetchone()[0])

    async def initialize(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._init_sync)

    async def get_revision(self) -> int:
        def _sync() -> int:
            with closing(self._connect()) as conn:
                return self._get_revision_sync(conn)

        return await asyncio.to_thread(_sync)

    async def runtime_snapshot(self) -> UpstreamRuntimeSnapshot:
        def _sync() -> UpstreamRuntimeSnapshot:
            with closing(self._connect()) as conn:
                revision = self._get_revision_sync(conn)
                rows = conn.execute(
                    f"SELECT * FROM {_TBL} WHERE deleted_at IS NULL"
                ).fetchall()
                return UpstreamRuntimeSnapshot(
                    revision=revision,
                    items=[self._row_to_record(row) for row in rows],
                )

        return await asyncio.to_thread(_sync)

    async def scan_changes(
        self,
        since_revision: int,
        *,
        limit: int = 5000,
    ) -> UpstreamChangeSet:
        def _sync() -> UpstreamChangeSet:
            with closing(self._connect()) as conn:
                revision = self._get_revision_sync(conn)
                rows = conn.execute(
                    f"SELECT * FROM {_TBL} WHERE revision > ? ORDER BY revision LIMIT ?",
                    (since_revision, limit),
                ).fetchall()
                items: list[UpstreamProviderRecord] = []
                deleted: list[str] = []
                for row in rows:
                    record = self._row_to_record(row)
                    if record.is_deleted():
                        deleted.append(record.id)
                    else:
                        items.append(record)
                return UpstreamChangeSet(
                    revision=revision,
                    items=items,
                    deleted_ids=deleted,
                    has_more=len(rows) == limit,
                )

        return await asyncio.to_thread(_sync)

    async def upsert_provider(
        self,
        item: UpstreamUpsert,
    ) -> UpstreamMutationResult:
        def _sync() -> UpstreamMutationResult:
            with closing(self._connect()) as conn:
                revision = self._bump_revision(conn)
                count, provider_id = self._upsert_sync(conn, item, revision)
                conn.commit()
                return UpstreamMutationResult(
                    upserted=count,
                    revision=revision,
                    id=provider_id,
                )

        async with self._lock:
            return await asyncio.to_thread(_sync)

    async def patch_provider(
        self,
        patch: UpstreamPatch,
    ) -> UpstreamMutationResult:
        def _sync() -> UpstreamMutationResult:
            with closing(self._connect()) as conn:
                revision = self._bump_revision(conn)
                count = self._patch_sync(conn, patch, revision)
                conn.commit()
                return UpstreamMutationResult(
                    patched=count,
                    revision=revision,
                    id=patch.id,
                )

        async with self._lock:
            return await asyncio.to_thread(_sync)

    async def delete_provider(
        self,
        provider_id: str,
    ) -> UpstreamMutationResult:
        def _sync() -> UpstreamMutationResult:
            with closing(self._connect()) as conn:
                ts = now_ms()
                revision = self._bump_revision(conn)
                conn.execute(
                    f"""
                    UPDATE {_TBL}
                    SET status = ?, deleted_at = ?, updated_at = ?, revision = ?
                    WHERE id = ? AND deleted_at IS NULL
                    """,
                    (UpstreamStatus.DELETED.value, ts, ts, revision, provider_id),
                )
                count = int(conn.execute("SELECT changes()").fetchone()[0])
                conn.commit()
                return UpstreamMutationResult(
                    deleted=count,
                    revision=revision,
                    id=provider_id,
                )

        async with self._lock:
            return await asyncio.to_thread(_sync)

    async def get_provider(
        self,
        provider_id: str,
    ) -> UpstreamProviderRecord | None:
        def _sync() -> UpstreamProviderRecord | None:
            with closing(self._connect()) as conn:
                row = conn.execute(
                    f"SELECT * FROM {_TBL} WHERE id = ?",
                    (provider_id,),
                ).fetchone()
                return self._row_to_record(row) if row else None

        return await asyncio.to_thread(_sync)

    async def list_providers(
        self,
        query: ListUpstreamsQuery,
    ) -> UpstreamPage:
        def _sync() -> UpstreamPage:
            with closing(self._connect()) as conn:
                where_parts: list[str] = []
                params: list[Any] = []
                if not query.include_deleted:
                    where_parts.append("deleted_at IS NULL")
                if query.status is not None:
                    where_parts.append("status = ?")
                    params.append(query.status.value)
                where_sql = (
                    "WHERE " + " AND ".join(where_parts) if where_parts else ""
                )
                total = int(
                    conn.execute(
                        f"SELECT COUNT(*) FROM {_TBL} {where_sql}",
                        params,
                    ).fetchone()[0]
                )
                safe_sort = (
                    query.sort_by
                    if query.sort_by
                    in {"updated_at", "created_at", "name", "weight", "id"}
                    else "updated_at"
                )
                order_dir = "DESC" if query.sort_desc else "ASC"
                offset = (query.page - 1) * query.page_size
                rows = conn.execute(
                    f"""
                    SELECT * FROM {_TBL} {where_sql}
                    ORDER BY {safe_sort} {order_dir}
                    LIMIT ? OFFSET ?
                    """,
                    params + [query.page_size, offset],
                ).fetchall()
                items = [self._row_to_record(row) for row in rows]
                if query.model:
                    items = [item for item in items if query.model in item.models]
                    total = len(items)
                revision = self._get_revision_sync(conn)
                total_pages = max(1, (total + query.page_size - 1) // query.page_size)
                return UpstreamPage(
                    items=items,
                    total=total,
                    page=query.page,
                    page_size=query.page_size,
                    total_pages=total_pages,
                    revision=revision,
                )

        return await asyncio.to_thread(_sync)

    async def close(self) -> None:
        """No-op for SQLite; connections are operation scoped."""


__all__ = ["LocalUpstreamProviderRepository"]
