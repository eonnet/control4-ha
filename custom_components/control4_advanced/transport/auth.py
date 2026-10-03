"""Obtain Director JWTs through the Control4 account API.

The request shape follows the locally installed pyControl4 2.0.2 account
implementation, which is the method already proven in this environment.
Tokens are held only in memory and never included in exception messages.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import time
from typing import Any, Protocol

import aiohttp
from pyControl4.account import APPLICATION_KEY


AUTH_URL = "https://apis.control4.com/authentication/v1/rest"
CONTROLLERS_URL = "https://apis.control4.com/account/v3/rest/accounts"
DIRECTOR_TOKEN_URL = "https://apis.control4.com/authentication/v1/rest/authorization"


class AuthenticationError(Exception):
    """A credential, controller selection, or token exchange failed."""


class AuthenticationTransportError(AuthenticationError):
    """A temporary account API connection or server failure."""


@dataclass(frozen=True, repr=False)
class DirectorToken:
    value: str
    expires_at: float  # monotonic seconds

    def __repr__(self) -> str:
        return "DirectorToken(value=<redacted>, expires_at=<monotonic>)"


class TokenProvider(Protocol):
    async def get_token(self, *, force_refresh: bool = False) -> DirectorToken: ...


class AccountTokenProvider:
    """Refresh a Director token without depending on the production MCP project."""

    def __init__(
        self,
        username: str,
        password: str,
        controller_common_name: str | None = None,
        *,
        refresh_margin_seconds: int = 300,
    ) -> None:
        if not username or not password:
            raise ValueError("Control4 account username and password are required")
        self._username = username
        self._password = password
        self._controller_common_name = controller_common_name
        self._refresh_margin = refresh_margin_seconds
        self._token: DirectorToken | None = None
        self._lock = asyncio.Lock()

    async def get_token(self, *, force_refresh: bool = False) -> DirectorToken:
        async with self._lock:
            now = time.monotonic()
            if (
                not force_refresh
                and self._token is not None
                and now + self._refresh_margin < self._token.expires_at
            ):
                return self._token
            for attempt in range(3):
                try:
                    self._token = await self._fetch_token()
                    break
                except AuthenticationTransportError:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(0.5 * (2**attempt))
            return self._token

    async def _fetch_token(self) -> DirectorToken:
        timeout = aiohttp.ClientTimeout(total=30)
        try:
            # Cloud TLS remains verified. Director's local self-signed TLS is
            # configured separately in DirectorRestClient.
            async with aiohttp.ClientSession(timeout=timeout) as session:
                account_response = await self._json(
                    session,
                    "POST",
                    AUTH_URL,
                    json={
                        "clientInfo": {
                            "device": {
                                "deviceName": "pyControl4",
                                "deviceUUID": "0000000000000000",
                                "make": "pyControl4",
                                "model": "pyControl4",
                                "os": "Android",
                                "osVersion": "10",
                            },
                            "userInfo": {
                                "applicationKey": APPLICATION_KEY,
                                "password": self._password,
                                "userName": self._username,
                            },
                        }
                    },
                )
                account_token = _nested_token(account_response)
                common_name = self._controller_common_name
                if common_name is None:
                    controllers_response = await self._json(
                        session,
                        "GET",
                        CONTROLLERS_URL,
                        headers={"Authorization": f"Bearer {account_token}"},
                    )
                    common_names = _controller_common_names(controllers_response)
                    if len(common_names) != 1:
                        raise AuthenticationError(
                            f"expected one Control4 controller; found {len(common_names)}"
                        )
                    common_name = next(iter(common_names))
                director_response = await self._json(
                    session,
                    "POST",
                    DIRECTOR_TOKEN_URL,
                    headers={"Authorization": f"Bearer {account_token}"},
                    json={"serviceInfo": {"commonName": common_name, "services": "director"}},
                )
                token = _nested_token(director_response)
                auth_info = director_response.get("authToken")
                valid_seconds = auth_info.get("validSeconds") if isinstance(auth_info, dict) else None
                if not isinstance(valid_seconds, (int, float)) or valid_seconds <= 0:
                    raise AuthenticationError("Director token response omitted a valid lifetime")
                return DirectorToken(token, time.monotonic() + float(valid_seconds))
        except AuthenticationError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise AuthenticationTransportError(
                f"Control4 token exchange failed ({type(exc).__name__})"
            ) from None
        except (TypeError, ValueError) as exc:
            raise AuthenticationError(
                f"Control4 token response was invalid ({type(exc).__name__})"
            ) from None

    @staticmethod
    async def _json(
        session: aiohttp.ClientSession,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        async with session.request(method, url, **kwargs) as response:
            if response.status >= 500:
                raise AuthenticationTransportError(
                    f"Control4 account API returned HTTP {response.status}"
                )
            if response.status >= 400:
                raise AuthenticationError(f"Control4 account API returned HTTP {response.status}")
            try:
                payload = await response.json()
            except (aiohttp.ContentTypeError, ValueError):
                raise AuthenticationError("Control4 account API returned invalid JSON") from None
            if not isinstance(payload, dict):
                raise AuthenticationError("Control4 account API returned an unexpected shape")
            return payload


def _nested_token(payload: dict[str, Any]) -> str:
    auth_info = payload.get("authToken")
    value = auth_info.get("token") if isinstance(auth_info, dict) else None
    if not isinstance(value, str) or not value:
        raise AuthenticationError("Control4 account API did not provide a token")
    return value


def _controller_common_names(value: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        name = value.get("controllerCommonName")
        if isinstance(name, str) and name:
            names.add(name)
        for child in value.values():
            names.update(_controller_common_names(child))
    elif isinstance(value, list):
        for child in value:
            names.update(_controller_common_names(child))
    return names
