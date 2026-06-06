"""Admin upstream provider API."""

from typing import TYPE_CHECKING, Any

import orjson
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.control.upstream.commands import ListUpstreamsQuery, UpstreamPatch, UpstreamUpsert
from app.control.upstream.models import UpstreamStatus, UpstreamType
from app.dataplane.upstream.forwarder import fetch_models
from app.platform.errors import AppError, ErrorKind, ValidationError
from app.platform.runtime.clock import now_ms

if TYPE_CHECKING:
    from app.control.upstream.repository import UpstreamProviderRepository

router = APIRouter(tags=["Admin - Upstreams"])


class UpstreamCreateRequest(BaseModel):
    name: str
    type: UpstreamType = UpstreamType.OPENAI_COMPATIBLE
    base_url: str
    api_key: str
    status: UpstreamStatus = UpstreamStatus.ENABLED
    models: list[str] = Field(default_factory=list)
    weight: int = Field(default=1, ge=1)
    timeout_sec: float = Field(default=120.0, gt=0)
    ext: dict[str, Any] = Field(default_factory=dict)


class UpstreamPatchRequest(BaseModel):
    name: str | None = None
    type: UpstreamType | None = None
    base_url: str | None = None
    api_key: str | None = None
    status: UpstreamStatus | None = None
    models: list[str] | None = None
    weight: int | None = Field(default=None, ge=1)
    timeout_sec: float | None = Field(default=None, gt=0)
    ext_merge: dict[str, Any] | None = None


def get_upstream_repo(request: Request) -> "UpstreamProviderRepository":
    repo = getattr(request.app.state, "upstream_repository", None)
    if repo is None:
        raise AppError(
            "Upstream repository not initialised",
            kind=ErrorKind.SERVER,
            code="upstream_repository_not_initialised",
            status=503,
        )
    return repo


def _mask_api_key(api_key: str) -> str:
    if len(api_key) <= 12:
        return "***"
    return f"{api_key[:6]}...{api_key[-4:]}"


def _serialize_provider(record) -> dict:
    data = record.model_dump(mode="json")
    data["api_key"] = _mask_api_key(record.api_key)
    return data


def _json(data) -> Response:
    return Response(content=orjson.dumps(data), media_type="application/json")


async def _sync_directory(request: Request) -> None:
    directory = getattr(request.app.state, "upstream_directory", None)
    if directory is not None:
        await directory.sync_if_changed()


@router.get("/upstreams")
async def list_upstreams(
    status: UpstreamStatus | None = None,
    model: str | None = None,
    include_deleted: bool = False,
    repo: "UpstreamProviderRepository" = Depends(get_upstream_repo),
):
    page = await repo.list_providers(
        ListUpstreamsQuery(
            page=1,
            page_size=2000,
            status=status,
            model=model,
            include_deleted=include_deleted,
        )
    )
    return _json(
        {
            "object": "list",
            "data": [_serialize_provider(item) for item in page.items],
            "total": page.total,
            "revision": page.revision,
        }
    )


@router.post("/upstreams")
async def create_upstream(
    req: UpstreamCreateRequest,
    request: Request,
    repo: "UpstreamProviderRepository" = Depends(get_upstream_repo),
):
    result = await repo.upsert_provider(UpstreamUpsert(**req.model_dump()))
    await _sync_directory(request)
    record = await repo.get_provider(result.id or "")
    return _json({"status": "success", "provider": _serialize_provider(record)})


@router.patch("/upstreams/{provider_id}")
async def patch_upstream(
    provider_id: str,
    req: UpstreamPatchRequest,
    request: Request,
    repo: "UpstreamProviderRepository" = Depends(get_upstream_repo),
):
    payload = req.model_dump(exclude_unset=True)
    if not payload:
        raise ValidationError("No patch fields provided", param="body")
    result = await repo.patch_provider(UpstreamPatch(id=provider_id, **payload))
    if result.patched == 0:
        raise ValidationError("Upstream provider not found", param="provider_id", code="not_found")
    await _sync_directory(request)
    record = await repo.get_provider(provider_id)
    return _json({"status": "success", "provider": _serialize_provider(record)})


@router.delete("/upstreams/{provider_id}")
async def delete_upstream(
    provider_id: str,
    request: Request,
    repo: "UpstreamProviderRepository" = Depends(get_upstream_repo),
):
    result = await repo.delete_provider(provider_id)
    if result.deleted == 0:
        raise ValidationError("Upstream provider not found", param="provider_id", code="not_found")
    await _sync_directory(request)
    return _json({"status": "success", "deleted": result.deleted})


@router.post("/upstreams/{provider_id}/test")
async def test_upstream(
    provider_id: str,
    repo: "UpstreamProviderRepository" = Depends(get_upstream_repo),
):
    record = await repo.get_provider(provider_id)
    if record is None or record.is_deleted():
        raise ValidationError("Upstream provider not found", param="provider_id", code="not_found")
    models = await fetch_models(record, timeout_sec=min(record.timeout_sec, 30.0))
    await repo.patch_provider(
        UpstreamPatch(
            id=provider_id,
            last_health="healthy",
            last_health_at=now_ms(),
            last_error="",
        )
    )
    return _json({"status": "success", "model_count": len(models)})


@router.post("/upstreams/{provider_id}/refresh-models")
async def refresh_upstream_models(
    provider_id: str,
    request: Request,
    repo: "UpstreamProviderRepository" = Depends(get_upstream_repo),
):
    record = await repo.get_provider(provider_id)
    if record is None or record.is_deleted():
        raise ValidationError("Upstream provider not found", param="provider_id", code="not_found")
    models = await fetch_models(record)
    await repo.patch_provider(
        UpstreamPatch(
            id=provider_id,
            models=models,
            last_health="healthy",
            last_health_at=now_ms(),
            last_error="",
        )
    )
    await _sync_directory(request)
    return _json({"status": "success", "models": models, "model_count": len(models)})


@router.get("/upstreams/models")
async def upstream_models(request: Request):
    directory = getattr(request.app.state, "upstream_directory", None)
    models = directory.models() if directory is not None else []
    return _json(
        {
            "object": "list",
            "data": [{"id": model, "object": "model"} for model in models],
        }
    )


__all__ = ["router", "get_upstream_repo"]
