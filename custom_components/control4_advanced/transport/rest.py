"""Director REST reads and an explicit generic POST path."""

from __future__ import annotations

from typing import Any

import aiohttp

from .auth import TokenProvider


class DirectorRestError(Exception):
    """A Director REST request failed; the message contains no response body."""


class DirectorRestClient:
    def __init__(
        self,
        host: str,
        token_provider: TokenProvider,
        session: aiohttp.ClientSession,
    ) -> None:
        self.host = host
        self.token_provider = token_provider
        self.session = session

    async def request_json(self, method: str, path: str, *, body: Any = None) -> Any:
        if not path.startswith("/api/v1/"):
            raise ValueError("Director REST path must start with /api/v1/")
        if method not in {"GET", "POST"}:
            raise ValueError("only GET and POST are supported")
        url = f"https://{self.host}{path}"
        for attempt in range(2):
            token = await self.token_provider.get_token(force_refresh=bool(attempt))
            try:
                async with self.session.request(
                    method,
                    url,
                    headers={"Authorization": f"Bearer {token.value}"},
                    json=body if method == "POST" else None,
                ) as response:
                    if response.status == 401 and attempt == 0:
                        continue
                    if response.status >= 400:
                        raise DirectorRestError(f"Director REST returned HTTP {response.status}")
                    try:
                        return await response.json()
                    except (aiohttp.ContentTypeError, ValueError):
                        raise DirectorRestError("Director REST returned invalid JSON") from None
            except aiohttp.ClientError as exc:
                raise DirectorRestError(f"Director REST transport failed ({type(exc).__name__})") from None
        raise DirectorRestError("Director REST authentication failed")

    async def get_items(self) -> list[dict[str, Any]]:
        payload = await self.request_json("GET", "/api/v1/items")
        items = payload if isinstance(payload, list) else payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise DirectorRestError("Director inventory has an unexpected shape")
        return items

    async def get_variables(self, device_id: int) -> list[dict[str, Any]]:
        payload = await self.request_json("GET", f"/api/v1/items/{int(device_id)}/variables")
        variables = payload if isinstance(payload, list) else payload.get("variables") if isinstance(payload, dict) else None
        if not isinstance(variables, list) or not all(isinstance(item, dict) for item in variables):
            raise DirectorRestError("Director variables have an unexpected shape")
        return variables

    async def get_commands(self, device_id: int) -> list[dict[str, Any]]:
        """Read Director's command metadata; this does not execute a command."""
        payload = await self.request_json("GET", f"/api/v1/items/{int(device_id)}/commands")
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise DirectorRestError("Director command metadata has an unexpected shape")
        return payload

    async def get_ui_configuration(self) -> dict[str, Any]:
        """Read Director's Watch/Listen source catalog without retaining raw media data."""
        payload = await self.request_json("GET", "/api/v1/agents/ui_configuration")
        if not isinstance(payload, dict) or not isinstance(payload.get("experiences"), list):
            raise DirectorRestError("Director UI configuration has an unexpected shape")
        return payload

    async def post_json(self, path: str, body: dict[str, Any]) -> Any:
        """Send caller-supplied REST JSON; no command schema is assumed here."""
        return await self.request_json("POST", path, body=body)
