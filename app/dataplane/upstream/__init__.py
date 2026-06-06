"""UpstreamDirectory — runtime provider selection for OpenAI-compatible upstreams."""

import asyncio

from app.control.upstream.repository import UpstreamProviderRepository
from app.platform.logging.logger import logger
from app.platform.runtime.clock import now_s
from .lease import UpstreamLease, new_lease
from .selector import current_strategy, select_provider
from .sync import apply_changes, bootstrap as _bootstrap
from .table import UpstreamRuntimeTable

_directory: "UpstreamDirectory | None" = None


class UpstreamDirectory:
    def __init__(self, repository: UpstreamProviderRepository) -> None:
        self._repo = repository
        self._table: UpstreamRuntimeTable | None = None
        self._lock = asyncio.Lock()
        self._sync_lock = asyncio.Lock()

    async def bootstrap(self) -> None:
        table = await _bootstrap(self._repo)
        async with self._lock:
            self._table = table
        logger.info("upstream directory ready: size={}", table.size)

    async def sync_if_changed(self) -> bool:
        if self._table is None:
            return False
        async with self._sync_lock:
            async with self._lock:
                table = self._table
            changed = await apply_changes(table, self._repo)
            if changed:
                logger.debug(
                    "upstream directory synced: revision={} size={}",
                    table.revision,
                    table.size,
                )
            return changed

    async def reserve(self, model: str) -> UpstreamLease | None:
        table = self._table
        if table is None:
            return None
        async with self._lock:
            providers = table.providers_for_model(model)
            provider, cursor = select_provider(
                providers,
                table.inflight,
                cursor=table.rr_cursor,
            )
            table.rr_cursor = cursor
            if provider is None:
                return None
            table.inflight[provider.id] = table.inflight.get(provider.id, 0) + 1
            return new_lease(provider, now_s())

    async def release(self, lease: UpstreamLease) -> None:
        table = self._table
        if table is None:
            return
        async with self._lock:
            provider_id = lease.provider.id
            table.inflight[provider_id] = max(
                0,
                table.inflight.get(provider_id, 0) - 1,
            )

    def has_model(self, model: str) -> bool:
        table = self._table
        return bool(table and model in table.model_index)

    def models(self) -> list[str]:
        table = self._table
        return table.models() if table else []

    @property
    def revision(self) -> int:
        return self._table.revision if self._table else 0

    @property
    def size(self) -> int:
        return self._table.size if self._table else 0


async def get_upstream_directory(
    repository: UpstreamProviderRepository,
) -> UpstreamDirectory:
    global _directory
    if _directory is None:
        directory = UpstreamDirectory(repository)
        await directory.bootstrap()
        _directory = directory
    return _directory


def set_upstream_directory(directory: UpstreamDirectory | None) -> None:
    global _directory
    _directory = directory


__all__ = [
    "UpstreamDirectory",
    "UpstreamLease",
    "current_strategy",
    "get_upstream_directory",
    "set_upstream_directory",
    "_directory",
]
