"""SQLAlchemy upstream provider repository for MySQL/PostgreSQL."""

import json
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

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

metadata = sa.MetaData()

upstreams_table = sa.Table(
    "upstream_providers",
    metadata,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("type", sa.Text, nullable=False),
    sa.Column("base_url", sa.Text, nullable=False),
    sa.Column("api_key", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("models", sa.Text, nullable=False, default="[]"),
    sa.Column("weight", sa.Integer, nullable=False, default=1),
    sa.Column("timeout_sec", sa.Float, nullable=False, default=120.0),
    sa.Column("last_health", sa.Text, nullable=False, default="unknown"),
    sa.Column("last_health_at", sa.BigInteger),
    sa.Column("last_error", sa.Text),
    sa.Column("created_at", sa.BigInteger, nullable=False),
    sa.Column("updated_at", sa.BigInteger, nullable=False),
    sa.Column("deleted_at", sa.BigInteger),
    sa.Column("ext", sa.Text, nullable=False, default="{}"),
    sa.Column("revision", sa.BigInteger, nullable=False, default=0),
)

meta_table = sa.Table(
    "upstream_meta",
    metadata,
    sa.Column("key", sa.String(128), primary_key=True),
    sa.Column("value", sa.Text, nullable=False),
)


class SqlUpstreamProviderRepository:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        dialect: str,
        dispose_engine: bool = False,
    ) -> None:
        self._engine = engine
        self._dialect = dialect
        self._dispose_engine = dispose_engine
        self._Session = async_sessionmaker(engine, expire_on_commit=False)

    @staticmethod
    def _row_to_record(row: Any) -> UpstreamProviderRecord:
        data = dict(row._mapping if hasattr(row, "_mapping") else row)
        data["models"] = json.loads(data.get("models") or "[]")
        data["ext"] = json.loads(data.get("ext") or "{}")
        return UpstreamProviderRecord.model_validate(data)

    @staticmethod
    def _record_values(record: UpstreamProviderRecord) -> dict[str, Any]:
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

    async def _bump_revision(self, session) -> int:
        result = await session.execute(
            sa.select(meta_table.c.value).where(meta_table.c.key == "revision")
        )
        current = result.scalar_one_or_none()
        revision = int(current or 0) + 1
        await session.execute(
            sa.update(meta_table)
            .where(meta_table.c.key == "revision")
            .values(value=str(revision))
        )
        return revision

    async def initialize(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
            existing = await conn.execute(
                sa.select(meta_table.c.value).where(meta_table.c.key == "revision")
            )
            if existing.scalar_one_or_none() is None:
                await conn.execute(
                    sa.insert(meta_table).values(key="revision", value="0")
                )

    async def get_revision(self) -> int:
        async with self._Session() as session:
            result = await session.execute(
                sa.select(meta_table.c.value).where(meta_table.c.key == "revision")
            )
            return int(result.scalar_one_or_none() or 0)

    async def runtime_snapshot(self) -> UpstreamRuntimeSnapshot:
        async with self._Session() as session:
            revision = await self.get_revision()
            result = await session.execute(
                sa.select(upstreams_table).where(upstreams_table.c.deleted_at.is_(None))
            )
            return UpstreamRuntimeSnapshot(
                revision=revision,
                items=[self._row_to_record(row) for row in result.fetchall()],
            )

    async def scan_changes(
        self,
        since_revision: int,
        *,
        limit: int = 5000,
    ) -> UpstreamChangeSet:
        async with self._Session() as session:
            revision = await self.get_revision()
            result = await session.execute(
                sa.select(upstreams_table)
                .where(upstreams_table.c.revision > since_revision)
                .order_by(upstreams_table.c.revision.asc())
                .limit(limit)
            )
            items: list[UpstreamProviderRecord] = []
            deleted: list[str] = []
            rows = result.fetchall()
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

    async def upsert_provider(
        self,
        item: UpstreamUpsert,
    ) -> UpstreamMutationResult:
        async with self._Session.begin() as session:
            revision = await self._bump_revision(session)
            ts = now_ms()
            provider_id = item.id or new_provider_id()
            existing_result = await session.execute(
                sa.select(upstreams_table).where(upstreams_table.c.id == provider_id)
            )
            existing_row = existing_result.fetchone()
            created_at = (
                int(existing_row._mapping["created_at"]) if existing_row else ts
            )
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
            values = self._record_values(record)
            if existing_row:
                await session.execute(
                    sa.update(upstreams_table)
                    .where(upstreams_table.c.id == provider_id)
                    .values(**values)
                )
            else:
                await session.execute(sa.insert(upstreams_table).values(**values))
            return UpstreamMutationResult(upserted=1, revision=revision, id=provider_id)

    async def patch_provider(
        self,
        patch: UpstreamPatch,
    ) -> UpstreamMutationResult:
        async with self._Session.begin() as session:
            existing_result = await session.execute(
                sa.select(upstreams_table).where(upstreams_table.c.id == patch.id)
            )
            row = existing_result.fetchone()
            if row is None:
                revision = await self.get_revision()
                return UpstreamMutationResult(revision=revision, id=patch.id)
            existing = self._row_to_record(row)
            revision = await self._bump_revision(session)
            updates = patch.model_dump(exclude_unset=True)
            updates.pop("id", None)
            ext_merge = updates.pop("ext_merge", None)
            if ext_merge:
                updates["ext"] = {**existing.ext, **ext_merge}
            updates["updated_at"] = now_ms()
            updates["revision"] = revision
            record = UpstreamProviderRecord.model_validate(
                existing.model_copy(update=updates).model_dump()
            )
            await session.execute(
                sa.update(upstreams_table)
                .where(upstreams_table.c.id == patch.id)
                .values(**self._record_values(record))
            )
            return UpstreamMutationResult(patched=1, revision=revision, id=patch.id)

    async def delete_provider(
        self,
        provider_id: str,
    ) -> UpstreamMutationResult:
        async with self._Session.begin() as session:
            row = (
                await session.execute(
                    sa.select(upstreams_table).where(upstreams_table.c.id == provider_id)
                )
            ).fetchone()
            if row is None:
                revision = await self.get_revision()
                return UpstreamMutationResult(revision=revision, id=provider_id)
            revision = await self._bump_revision(session)
            ts = now_ms()
            await session.execute(
                sa.update(upstreams_table)
                .where(upstreams_table.c.id == provider_id)
                .values(
                    status=UpstreamStatus.DELETED.value,
                    deleted_at=ts,
                    updated_at=ts,
                    revision=revision,
                )
            )
            return UpstreamMutationResult(deleted=1, revision=revision, id=provider_id)

    async def get_provider(
        self,
        provider_id: str,
    ) -> UpstreamProviderRecord | None:
        async with self._Session() as session:
            row = (
                await session.execute(
                    sa.select(upstreams_table).where(upstreams_table.c.id == provider_id)
                )
            ).fetchone()
            return self._row_to_record(row) if row else None

    async def list_providers(
        self,
        query: ListUpstreamsQuery,
    ) -> UpstreamPage:
        async with self._Session() as session:
            conditions = []
            if not query.include_deleted:
                conditions.append(upstreams_table.c.deleted_at.is_(None))
            if query.status is not None:
                conditions.append(upstreams_table.c.status == query.status.value)
            stmt = sa.select(upstreams_table)
            count_stmt = sa.select(sa.func.count()).select_from(upstreams_table)
            if conditions:
                stmt = stmt.where(*conditions)
                count_stmt = count_stmt.where(*conditions)
            safe_sort = (
                query.sort_by
                if query.sort_by in {"updated_at", "created_at", "name", "weight", "id"}
                else "updated_at"
            )
            order_col = getattr(upstreams_table.c, safe_sort)
            stmt = stmt.order_by(
                order_col.desc() if query.sort_desc else order_col.asc()
            )
            stmt = stmt.limit(query.page_size).offset((query.page - 1) * query.page_size)
            rows = (await session.execute(stmt)).fetchall()
            items = [self._row_to_record(row) for row in rows]
            total = int((await session.execute(count_stmt)).scalar_one() or 0)
            if query.model:
                items = [item for item in items if query.model in item.models]
                total = len(items)
            return UpstreamPage(
                items=items,
                total=total,
                page=query.page,
                page_size=query.page_size,
                total_pages=max(1, (total + query.page_size - 1) // query.page_size),
                revision=await self.get_revision(),
            )

    async def close(self) -> None:
        if self._dispose_engine:
            await self._engine.dispose()


__all__ = ["SqlUpstreamProviderRepository"]
