"""OpenAI-compatible upstream forwarder."""

from collections.abc import AsyncGenerator
from urllib.parse import urljoin

import aiohttp
import orjson

from app.platform.errors import UpstreamError
from app.platform.logging.logger import logger
from .lease import UpstreamLease

_JSON_HEADERS = {"content-type": "application/json"}


def _endpoint_url(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        root = f"{base}/"
    else:
        root = f"{base}/v1/"
    return urljoin(root, path.lstrip("/"))


async def forward_json(
    lease: UpstreamLease,
    path: str,
    payload: dict,
) -> dict:
    provider = lease.provider
    url = _endpoint_url(provider.base_url, path)
    headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=provider.timeout_sec)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.post(
                url,
                headers=headers,
                data=orjson.dumps(payload),
            ) as response:
                body = await response.read()
                if response.status >= 400:
                    raise UpstreamError(
                        f"Upstream provider {provider.name!r} returned {response.status}",
                        status=response.status,
                        body=body.decode("utf-8", "replace")[:1000],
                    )
                try:
                    return orjson.loads(body)
                except orjson.JSONDecodeError as exc:
                    raise UpstreamError(
                        f"Upstream provider {provider.name!r} returned invalid JSON",
                        status=502,
                        body=body.decode("utf-8", "replace")[:1000],
                    ) from exc
        except UpstreamError:
            raise
        except Exception as exc:
            raise UpstreamError(
                f"Upstream provider {provider.name!r} transport failed: {exc}",
                status=502,
                body=str(exc)[:1000],
            ) from exc


async def forward_stream(
    lease: UpstreamLease,
    path: str,
    payload: dict,
) -> AsyncGenerator[bytes, None]:
    provider = lease.provider
    url = _endpoint_url(provider.base_url, path)
    headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    timeout = aiohttp.ClientTimeout(total=None, sock_read=provider.timeout_sec)
    session = aiohttp.ClientSession(timeout=timeout)
    response: aiohttp.ClientResponse | None = None
    try:
        response = await session.post(url, headers=headers, data=orjson.dumps(payload))
        if response.status >= 400:
            body = await response.text()
            raise UpstreamError(
                f"Upstream provider {provider.name!r} returned {response.status}",
                status=response.status,
                body=body[:1000],
        )
        async for chunk in response.content.iter_any():
            if chunk:
                yield chunk
    except UpstreamError:
        raise
    except Exception as exc:
        logger.warning(
            "upstream stream failed: provider={} error={}",
            provider.id,
            exc,
        )
        raise UpstreamError(
            f"Upstream provider {provider.name!r} stream failed: {exc}",
            status=502,
            body=str(exc)[:1000],
        ) from exc
    finally:
        if response is not None:
            response.close()
        await session.close()


async def fetch_models(provider, *, timeout_sec: float | None = None) -> list[str]:
    url = _endpoint_url(provider.base_url, "models")
    headers = {"Authorization": f"Bearer {provider.api_key}"}
    timeout = aiohttp.ClientTimeout(total=timeout_sec or provider.timeout_sec)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.get(url, headers=headers) as response:
                body = await response.read()
                if response.status >= 400:
                    raise UpstreamError(
                        f"Upstream provider {provider.name!r} returned {response.status}",
                        status=response.status,
                        body=body.decode("utf-8", "replace")[:1000],
                    )
                data = orjson.loads(body)
        except UpstreamError:
            raise
        except Exception as exc:
            raise UpstreamError(
                f"Upstream provider {provider.name!r} model refresh failed: {exc}",
                status=502,
                body=str(exc)[:1000],
            ) from exc
    raw_items = data.get("data", []) if isinstance(data, dict) else []
    models: list[str] = []
    for item in raw_items:
        model_id = item.get("id") if isinstance(item, dict) else None
        if model_id and model_id not in models:
            models.append(str(model_id))
    return models


__all__ = ["forward_json", "forward_stream", "fetch_models"]
