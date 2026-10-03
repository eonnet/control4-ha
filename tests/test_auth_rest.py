from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import AsyncMock

from control4_transport.auth import (
    AccountTokenProvider,
    AuthenticationTransportError,
    DirectorToken,
    _controller_common_names,
)
from control4_transport.rest import DirectorRestClient, DirectorRestError


class FakeResponse:
    def __init__(self, status: int, payload) -> None:
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.headers = []

    def request(self, method, url, **kwargs):
        self.headers.append(kwargs["headers"])
        return self.responses.pop(0)


class FakeTokenProvider:
    def __init__(self) -> None:
        self.refresh_flags = []

    async def get_token(self, *, force_refresh=False):
        self.refresh_flags.append(force_refresh)
        return DirectorToken("private-token", time.monotonic() + 3600)


class AuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_cached_token_and_forced_refresh(self) -> None:
        provider = AccountTokenProvider("user", "password", "controller")
        provider._fetch_token = AsyncMock(
            side_effect=[
                DirectorToken("first", time.monotonic() + 3600),
                DirectorToken("second", time.monotonic() + 3600),
            ]
        )
        first, same = await asyncio.gather(provider.get_token(), provider.get_token())
        self.assertEqual(first.value, same.value)
        self.assertEqual(provider._fetch_token.await_count, 1)
        refreshed = await provider.get_token(force_refresh=True)
        self.assertEqual(refreshed.value, "second")
        self.assertNotIn("first", repr(first))

    async def test_account_transport_error_is_retried(self) -> None:
        provider = AccountTokenProvider("user", "password", "controller")
        provider._fetch_token = AsyncMock(
            side_effect=[
                AuthenticationTransportError("temporary disconnect"),
                DirectorToken("second", time.monotonic() + 3600),
            ]
        )
        token = await provider.get_token()
        self.assertEqual(token.value, "second")
        self.assertEqual(provider._fetch_token.await_count, 2)

    async def test_rest_retries_401_once_with_fresh_token(self) -> None:
        tokens = FakeTokenProvider()
        session = FakeSession([FakeResponse(401, {}), FakeResponse(200, [{"id": 1}])])
        rest = DirectorRestClient("127.0.0.1", tokens, session)
        self.assertEqual(await rest.get_items(), [{"id": 1}])
        self.assertEqual(tokens.refresh_flags, [False, True])
        self.assertEqual(len(session.headers), 2)

    async def test_rest_error_omits_response_body(self) -> None:
        tokens = FakeTokenProvider()
        session = FakeSession([FakeResponse(500, {"secret": "not-for-logs"})])
        rest = DirectorRestClient("127.0.0.1", tokens, session)
        with self.assertRaises(DirectorRestError) as caught:
            await rest.get_items()
        self.assertEqual(str(caught.exception), "Director REST returned HTTP 500")

    def test_nested_controller_selection_is_deduplicated(self) -> None:
        payload = {"account": [{"controllerCommonName": "one"}, {"child": {"controllerCommonName": "one"}}]}
        self.assertEqual(_controller_common_names(payload), {"one"})


if __name__ == "__main__":
    unittest.main()
