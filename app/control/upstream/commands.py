"""Command/query objects for upstream provider storage."""

from typing import Any

from pydantic import BaseModel, Field

from .models import UpstreamHealth, UpstreamStatus, UpstreamType


class UpstreamUpsert(BaseModel):
    id: str | None = None
    name: str
    type: UpstreamType = UpstreamType.OPENAI_COMPATIBLE
    base_url: str
    api_key: str
    status: UpstreamStatus = UpstreamStatus.ENABLED
    models: list[str] = Field(default_factory=list)
    weight: int = Field(default=1, ge=1)
    timeout_sec: float = Field(default=120.0, gt=0)
    ext: dict[str, Any] = Field(default_factory=dict)


class UpstreamPatch(BaseModel):
    id: str
    name: str | None = None
    type: UpstreamType | None = None
    base_url: str | None = None
    api_key: str | None = None
    status: UpstreamStatus | None = None
    models: list[str] | None = None
    weight: int | None = Field(default=None, ge=1)
    timeout_sec: float | None = Field(default=None, gt=0)
    last_health: UpstreamHealth | None = None
    last_health_at: int | None = None
    last_error: str | None = None
    ext_merge: dict[str, Any] | None = None


class ListUpstreamsQuery(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=2000)
    status: UpstreamStatus | None = None
    model: str | None = None
    include_deleted: bool = False
    sort_by: str = "updated_at"
    sort_desc: bool = True


__all__ = ["UpstreamUpsert", "UpstreamPatch", "ListUpstreamsQuery"]
