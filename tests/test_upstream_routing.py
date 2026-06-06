import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.responses import JSONResponse

from app.control.account.models import AccountRecord
from app.control.upstream.backends.local import LocalUpstreamProviderRepository
from app.control.upstream.commands import UpstreamUpsert
from app.dataplane.upstream import UpstreamDirectory
from app.products.openai.router import (
    chat_completions_endpoint,
    list_models,
)
from app.products.openai.schemas import ChatCompletionRequest, MessageItem


class _AccountRepo:
    def __init__(self, records=None):
        self._records = records or []

    async def runtime_snapshot(self):
        return SimpleNamespace(items=self._records)


def _request(*, account_repo=None, upstream_directory=None):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                repository=account_repo or _AccountRepo(),
                upstream_directory=upstream_directory,
            )
        )
    )


class UpstreamRoutingTests(unittest.TestCase):
    def test_repository_and_directory_select_enabled_provider(self):
        async def _run():
            with tempfile.TemporaryDirectory() as tmp:
                repo = LocalUpstreamProviderRepository(Path(tmp) / "upstreams.db")
                await repo.initialize()
                await repo.upsert_provider(
                    UpstreamUpsert(
                        name="compat",
                        base_url="https://example.test/v1",
                        api_key="sk-test",
                        models=["gpt-test"],
                    )
                )
                directory = UpstreamDirectory(repo)
                await directory.bootstrap()
                self.assertTrue(directory.has_model("gpt-test"))
                lease = await directory.reserve("gpt-test")
                self.assertIsNotNone(lease)
                self.assertEqual(lease.provider.name, "compat")
                await directory.release(lease)

        asyncio.run(_run())

    def test_models_endpoint_merges_local_and_upstream_without_duplicates(self):
        async def _run():
            with tempfile.TemporaryDirectory() as tmp:
                repo = LocalUpstreamProviderRepository(Path(tmp) / "upstreams.db")
                await repo.initialize()
                await repo.upsert_provider(
                    UpstreamUpsert(
                        name="compat",
                        base_url="https://example.test",
                        api_key="sk-test",
                        models=["grok-4.20-fast", "gpt-test"],
                    )
                )
                directory = UpstreamDirectory(repo)
                await directory.bootstrap()
                account_repo = _AccountRepo(
                    [AccountRecord(token="token-1", pool="basic")]
                )
                response = await list_models(
                    _request(
                        account_repo=account_repo,
                        upstream_directory=directory,
                    )
                )
                payload = response.body.decode()
                self.assertEqual(payload.count('"id":"grok-4.20-fast"'), 1)
                self.assertIn('"id":"gpt-test"', payload)

        asyncio.run(_run())

    def test_upstream_only_model_routes_to_forwarder(self):
        async def _run():
            with tempfile.TemporaryDirectory() as tmp:
                repo = LocalUpstreamProviderRepository(Path(tmp) / "upstreams.db")
                await repo.initialize()
                await repo.upsert_provider(
                    UpstreamUpsert(
                        name="compat",
                        base_url="https://example.test",
                        api_key="sk-test",
                        models=["gpt-test"],
                    )
                )
                directory = UpstreamDirectory(repo)
                await directory.bootstrap()
                req = ChatCompletionRequest(
                    model="gpt-test",
                    messages=[MessageItem(role="user", content="hello")],
                    stream=False,
                )
                with patch(
                    "app.dataplane.upstream.forwarder.forward_json",
                    new=AsyncMock(
                        return_value={
                            "id": "chatcmpl_test",
                            "object": "chat.completion",
                        }
                    ),
                ) as forward_json:
                    response = await chat_completions_endpoint(
                        req,
                        _request(upstream_directory=directory),
                    )
                self.assertIsInstance(response, JSONResponse)
                self.assertTrue(forward_json.await_count)
                args = forward_json.await_args.args
                self.assertEqual(args[1], "chat/completions")
                self.assertEqual(args[2]["model"], "gpt-test")
                self.assertFalse(args[2]["stream"])

        asyncio.run(_run())

    def test_local_429_falls_back_to_upstream(self):
        async def _run():
            from app.platform.errors import RateLimitError

            with tempfile.TemporaryDirectory() as tmp:
                repo = LocalUpstreamProviderRepository(Path(tmp) / "upstreams.db")
                await repo.initialize()
                await repo.upsert_provider(
                    UpstreamUpsert(
                        name="compat",
                        base_url="https://example.test",
                        api_key="sk-test",
                        models=["grok-4.20-fast"],
                    )
                )
                directory = UpstreamDirectory(repo)
                await directory.bootstrap()
                account_repo = _AccountRepo(
                    [AccountRecord(token="token-1", pool="basic")]
                )
                req = ChatCompletionRequest(
                    model="grok-4.20-fast",
                    messages=[MessageItem(role="user", content="hello")],
                    stream=False,
                )
                with (
                    patch(
                        "app.products.openai.router._dispatch_local_chat",
                        new=AsyncMock(side_effect=RateLimitError("quota")),
                    ),
                    patch(
                        "app.dataplane.upstream.forwarder.forward_json",
                        new=AsyncMock(return_value={"id": "fallback"}),
                    ) as forward_json,
                ):
                    response = await chat_completions_endpoint(
                        req,
                        _request(
                            account_repo=account_repo,
                            upstream_directory=directory,
                        ),
                    )
                self.assertIsInstance(response, JSONResponse)
                self.assertTrue(forward_json.await_count)

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
