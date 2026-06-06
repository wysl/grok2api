"""Synchronise upstream runtime table from repository snapshots."""

from app.control.upstream.repository import UpstreamProviderRepository
from app.platform.logging.logger import logger
from .table import UpstreamRuntimeTable, make_empty_table


async def bootstrap(repository: UpstreamProviderRepository) -> UpstreamRuntimeTable:
    snapshot = await repository.runtime_snapshot()
    table = make_empty_table()
    for record in snapshot.items:
        if not record.is_deleted():
            table.upsert(record)
    table.revision = snapshot.revision
    logger.info(
        "upstream runtime table bootstrapped: revision={} provider_count={} model_count={}",
        table.revision,
        table.size,
        len(table.model_index),
    )
    return table


async def apply_changes(
    table: UpstreamRuntimeTable,
    repository: UpstreamProviderRepository,
    *,
    batch_limit: int = 5000,
) -> bool:
    changed = False
    while True:
        changeset = await repository.scan_changes(table.revision, limit=batch_limit)
        for provider_id in changeset.deleted_ids:
            table.delete(provider_id)
            changed = True
        for record in changeset.items:
            if record.is_deleted():
                table.delete(record.id)
            else:
                table.upsert(record)
            changed = True
        if changeset.revision > table.revision:
            table.revision = changeset.revision
        if not changeset.has_more:
            break
    return changed


__all__ = ["bootstrap", "apply_changes"]
