"""Upstream provider repository protocol."""

from typing import Protocol, runtime_checkable

from .commands import ListUpstreamsQuery, UpstreamPatch, UpstreamUpsert
from .models import (
    UpstreamChangeSet,
    UpstreamMutationResult,
    UpstreamPage,
    UpstreamProviderRecord,
    UpstreamRuntimeSnapshot,
)


@runtime_checkable
class UpstreamProviderRepository(Protocol):
    async def initialize(self) -> None:
        ...

    async def get_revision(self) -> int:
        ...

    async def runtime_snapshot(self) -> UpstreamRuntimeSnapshot:
        ...

    async def scan_changes(
        self,
        since_revision: int,
        *,
        limit: int = 5000,
    ) -> UpstreamChangeSet:
        ...

    async def upsert_provider(
        self,
        item: UpstreamUpsert,
    ) -> UpstreamMutationResult:
        ...

    async def patch_provider(
        self,
        patch: UpstreamPatch,
    ) -> UpstreamMutationResult:
        ...

    async def delete_provider(
        self,
        provider_id: str,
    ) -> UpstreamMutationResult:
        ...

    async def get_provider(
        self,
        provider_id: str,
    ) -> UpstreamProviderRecord | None:
        ...

    async def list_providers(
        self,
        query: ListUpstreamsQuery,
    ) -> UpstreamPage:
        ...

    async def close(self) -> None:
        ...


__all__ = ["UpstreamProviderRepository"]
