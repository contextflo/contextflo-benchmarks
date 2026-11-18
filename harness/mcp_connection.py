"""Connect to an MCP server over Streamable HTTP, logged in with OAuth.

The first connection for a profile prints the server's consent URL; open it in a browser
logged in as that profile's user, and the redirect is caught on a localhost port. After that the access and refresh tokens are
kept in ~/.config/contextflo-benchmarks/<profile>.json (outside the repo) and refreshed
automatically, so unattended runs never need the browser again.

One profile = one MCP identity. For Contextflo that is one user, and the token decides
which org (and so which context state) every tool call runs against.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from typing import AsyncIterator
from urllib.parse import parse_qs, urlparse

import httpx2
from mcp import Client
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

TOKEN_DIR = Path.home() / ".config" / "contextflo-benchmarks"
CALLBACK_PORT = 8765
CALLBACK_URL = f"http://localhost:{CALLBACK_PORT}/callback"
# Generous: a warehouse query through the MCP can take a while
HTTP_TIMEOUT_SECONDS = 300


class FileTokenStorage(TokenStorage):
    def __init__(self, *, profile: str):
        TOKEN_DIR.mkdir(parents=True, exist_ok=True)
        self.path = TOKEN_DIR / f"{profile}.json"

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text())

    def _write(self, *, data: dict) -> None:
        self.path.write_text(json.dumps(data, indent=2))
        self.path.chmod(0o600)

    async def get_tokens(self) -> OAuthToken | None:
        tokens = self._read().get("tokens")
        return OAuthToken.model_validate(tokens) if tokens is not None else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self._write(data={**self._read(), "tokens": tokens.model_dump(mode="json")})

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        client = self._read().get("client")
        return OAuthClientInformationFull.model_validate(client) if client is not None else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self._write(data={**self._read(), "client": client_info.model_dump(mode="json")})


class _CallbackCatcher:
    """One-shot localhost server that receives the OAuth redirect."""

    def __init__(self):
        self.result: AuthorizationCodeResult | None = None
        self.error: str | None = None
        catcher = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                query = parse_qs(urlparse(self.path).query)
                if "code" in query:
                    catcher.result = AuthorizationCodeResult(
                        code=query["code"][0],
                        state=query.get("state", [None])[0],
                        iss=query.get("iss", [None])[0],
                    )
                    body = b"Logged in. You can close this tab."
                else:
                    catcher.error = query.get("error_description", query.get("error", ["no code in redirect"]))[0]
                    body = f"Login failed: {catcher.error}".encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                pass

        self.server = HTTPServer(("localhost", CALLBACK_PORT), Handler)

    def start(self) -> None:
        Thread(target=self.server.handle_request, daemon=True).start()

    async def wait(self) -> AuthorizationCodeResult:
        while self.result is None and self.error is None:
            await asyncio.sleep(0.2)
        self.server.server_close()
        if self.error is not None:
            raise RuntimeError(f"OAuth login failed: {self.error}")
        return self.result


def oauth_provider(*, server_url: str, profile: str) -> OAuthClientProvider:
    catcher: dict[str, _CallbackCatcher] = {}

    async def redirect_handler(authorization_url: str) -> None:
        catcher["current"] = _CallbackCatcher()
        catcher["current"].start()
        # Not opened automatically: the default browser is usually logged in as you, and
        # consenting there would bind this profile to your org instead of the benchmark one.
        print(
            f"[{profile}] open this URL in a browser logged in as the profile's user "
            f"(waiting for the redirect to {CALLBACK_URL}):\n{authorization_url}",
            flush=True,
        )

    async def callback_handler() -> AuthorizationCodeResult:
        return await catcher["current"].wait()

    return OAuthClientProvider(
        server_url=server_url,
        client_metadata=OAuthClientMetadata(
            client_name=f"contextflo-benchmarks ({profile})",
            redirect_uris=[CALLBACK_URL],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        ),
        storage=FileTokenStorage(profile=profile),
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )


@asynccontextmanager
async def connect(*, server_url: str, profile: str) -> AsyncIterator[Client]:
    http_client = httpx2.AsyncClient(
        auth=oauth_provider(server_url=server_url, profile=profile),
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    async with http_client:
        # terminate_on_close off: Contextflo answers the session DELETE with 404, which only adds noise
        async with Client(streamable_http_client(server_url, http_client=http_client, terminate_on_close=False)) as client:
            yield client
