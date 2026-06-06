"""Redis upstream provider repository."""

import json

from redis.asyncio import Redis

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

_KEY_REV = "upstreams:rev"
_KEY_RECORD = "upstreams:record:{provider_id}"
_KEY_REV_LOG = "upstreams:revision_log"


def _record_key(provider_id: str) -> str:
    return _KEY_RECORD.format(provider_id=provider_id)


class RedisUpstreamProviderRepository:
    def __init__(self, redis: Redis) -> None:
        self._r = redis

    @staticmethod
    def _to_hash(record: UpstreamProviderRecord) -> dict[str, str]:
        return {
            "name": record.name,
            "type": record.type.value,
            "base_url": record.base_url,
            "api_key": record.api_key,
            "status": record.status.value,
            "models": json.dumps(record.models),
            "weight": str(record.weight),
            "timeout_sec": str(record.timeout_sec),
            "last_health": record.last_health.value,
            "last_health_at": str(record.last_health_at or ""),
            "last_error": record.last_error or "",
            "created_at": str(record.created_at),
            "updated_at": str(record.updated_at),
            "deleted_at": str(record.deleted_at or ""),
            "ext": json.dumps(record.ext),
            "revision": str(record.revision),
        }

    @staticmethod
    def _from_hash(
        provider_id: str,
        data: dict[bytes | str, bytes | str],
    ) -> UpstreamProviderRecord:
        def _s(key: str) -> str:
            value = data.get(key) or data.get(key.encode())
            return value.decode() if isinstance(value, bytes) else str(value or "")

        def _i(key: str) -> int | None:
            text = _s(key)
            return int(text) if text else None

        return UpstreamProviderRecord.model_validate(
            {
                "id": provider_id,
                "name": _s("name"),
                "type": _s("type") or "openai_compatible",
                "base_url": _s("base_url"),
                "api_key": _s("api_key"),
                "status": _s("status") or "enabled",
                "models": json.loads(_s("models") or "[]"),
                "weight": int(_s("weight") or 1),
                "timeout_sec": float(_s("timeout_sec") or 120),
                "last_health": _s("last_health") or "unknown",
                "last_health_at": _i("last_health_at"),
                "last_error": _s("last_error") or None,
                "created_at": _i("created_at") or now_ms(),
                "updated_at": _i("updated_at") or now_ms(),
                "deleted_at": _i("deleted_at"),
                "ext": json.loads(_s("ext") or "{}"),
                "revision": int(_s("revision") or 0),
            }
        )

    async def _bump_revision(self) -> int:
        return int(await self._r.incr(_KEY_REV))

    async def initialize(self) -> None:
        await self._r.setnx(_KEY_REV, "0")

    async def get_revision(self) -> int:
        value = await self._r.get(_KEY_REV)
        return int(value) if value else 0

    async def runtime_snapshot(self) -> UpstreamRuntimeSnapshot:
        revision = await self.get_revision()
        items: list[UpstreamProviderRecord] = []
        async for raw_key in self._r.scan_iter("upstreams:record:*"):
            key = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
            provider_id = key.split(":", 2)[-1]
            data = await self._r.hgetall(key)
            if not data:
                continue
            record = self._from_hash(provider_id, data)
            if not record.is_deleted():
                items.append(record)
        return UpstreamRuntimeSnapshot(revision=revision, items=items)

    async def scan_changes(
        self,
        since_revision: int,
        *,
        limit: int = 5000,
    ) -> UpstreamChangeSet:
        revision = await self.get_revision()
        entries = await self._r.zrangebyscore(
            _KEY_REV_LOG,
            since_revision + 1,
            "+inf",
            withscores=False,
            start=0,
            num=limit,
        )
        ids = [entry.decode() if isinstance(entry, bytes) else entry for entry in entries]
        items: list[UpstreamProviderRecord] = []
        deleted: list[str] = []
        for provider_id in ids:
            data = await self._r.hgetall(_record_key(provider_id))
            if not data:
                deleted.append(provider_id)
                continue
            record = self._from_hash(provider_id, data)
            if record.is_deleted():
                deleted.append(provider_id)
            else:
                items.append(record)
        return UpstreamChangeSet(
            revision=revision,
            items=items,
            deleted_ids=deleted,
            has_more=len(entries) == limit,
        )

    async def upsert_provider(
        self,
        item: UpstreamUpsert,
    ) -> UpstreamMutationResult:
        revision = await self._bump_revision()
        ts = now_ms()
        provider_id = item.id or new_provider_id()
        existing = await self.get_provider(provider_id)
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
            created_at=existing.created_at if existing else ts,
            updated_at=ts,
            deleted_at=None,
            ext=item.ext,
            revision=revision,
        )
        await self._r.hset(_record_key(provider_id), mapping=self._to_hash(record))
        await self._r.zadd(_KEY_REV_LOG, {provider_id: revision})
        return UpstreamMutationResult(upserted=1, revision=revision, id=provider_id)

    async def patch_provider(
        self,
        patch: UpstreamPatch,
    ) -> UpstreamMutationResult:
        existing = await self.get_provider(patch.id)
        if existing is None:
            return UpstreamMutationResult(revision=await self.get_revision(), id=patch.id)
        revision = await self._bump_revision()
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
        await self._r.hset(_record_key(patch.id), mapping=self._to_hash(record))
        await self._r.zadd(_KEY_REV_LOG, {patch.id: revision})
        return UpstreamMutationResult(patched=1, revision=revision, id=patch.id)

    async def delete_provider(
        self,
        provider_id: str,
    ) -> UpstreamMutationResult:
        existing = await self.get_provider(provider_id)
        if existing is None or existing.is_deleted():
            return UpstreamMutationResult(revision=await self.get_revision(), id=provider_id)
        revision = await self._bump_revision()
        ts = now_ms()
        record = existing.model_copy(
            update={
                "status": UpstreamStatus.DELETED,
                "deleted_at": ts,
                "updated_at": ts,
                "revision": revision,
            }
        )
        await self._r.hset(_record_key(provider_id), mapping=self._to_hash(record))
        await self._r.zadd(_KEY_REV_LOG, {provider_id: revision})
        return UpstreamMutationResult(deleted=1, revision=revision, id=provider_id)

    async def get_provider(
        self,
        provider_id: str,
    ) -> UpstreamProviderRecord | None:
        data = await self._r.hgetall(_record_key(provider_id))
        if not data:
            return None
        return self._from_hash(provider_id, data)

    async def list_providers(
        self,
        query: ListUpstreamsQuery,
    ) -> UpstreamPage:
        snapshot = await self.runtime_snapshot()
        items = list(snapshot.items)
        if query.include_deleted:
            items = []
            async for raw_key in self._r.scan_iter("upstreams:record:*"):
                key = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
                provider_id = key.split(":", 2)[-1]
                data = await self._r.hgetall(key)
                if data:
                    items.append(self._from_hash(provider_id, data))
        if query.status is not None:
            items = [item for item in items if item.status == query.status]
        if query.model:
            items = [item for item in items if query.model in item.models]
        reverse = query.sort_desc
        sort_by = query.sort_by if query.sort_by in {"updated_at", "created_at", "name", "weight", "id"} else "updated_at"
        items.sort(key=lambda item: getattr(item, sort_by), reverse=reverse)
        total = len(items)
        start = (query.page - 1) * query.page_size
        page_items = items[start : start + query.page_size]
        return UpstreamPage(
            items=page_items,
            total=total,
            page=query.page,
            page_size=query.page_size,
            total_pages=max(1, (total + query.page_size - 1) // query.page_size),
            revision=snapshot.revision,
        )

    async def close(self) -> None:
        await self._r.aclose()


__all__ = ["RedisUpstreamProviderRepository"]
