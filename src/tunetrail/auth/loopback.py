"""A temporary local callback receiver for Spotify's OAuth redirect."""

import asyncio
from urllib.parse import parse_qs, urlsplit

from tunetrail.auth.models import AuthorizationCallback


class InvalidRedirectUriError(ValueError):
    """Raised when a callback URI is not a local HTTP loopback address."""


class OAuthCallbackTimeoutError(TimeoutError):
    """Raised when Spotify does not redirect to the local callback in time."""


def parse_callback_target(target: str, expected_path: str) -> AuthorizationCallback:
    """Parse one callback target without trusting unexpected paths or parameters."""
    parsed = urlsplit(target)
    if parsed.path != expected_path:
        return AuthorizationCallback(
            error="invalid_request", error_description="Unexpected callback path"
        )
    query = parse_qs(parsed.query)
    return AuthorizationCallback(
        code=_first_query_value(query, "code"),
        state=_first_query_value(query, "state"),
        error=_first_query_value(query, "error"),
        error_description=_first_query_value(query, "error_description"),
    )


def _first_query_value(query: dict[str, list[str]], name: str) -> str | None:
    """Return a query parameter only when it has exactly one supplied value."""
    values = query.get(name)
    return values[0] if values is not None and len(values) == 1 else None


class LoopbackCallbackReceiver:
    """Listen once on the configured `127.0.0.1` OAuth redirect URI."""

    def __init__(self, redirect_uri: str) -> None:
        parsed = urlsplit(redirect_uri)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port is None:
            raise InvalidRedirectUriError(
                "Spotify callback URI must use http://127.0.0.1:<port>/..."
            )
        self._host = parsed.hostname
        self._port = parsed.port
        self._path = parsed.path or "/"

    async def receive(
        self, *, timeout_seconds: float = 180.0, ready: asyncio.Future[None] | None = None
    ) -> AuthorizationCallback:
        """Wait for one browser redirect, then close the listener."""
        loop = asyncio.get_running_loop()
        result: asyncio.Future[AuthorizationCallback] = loop.create_future()

        async def handle_connection(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            callback = await self._read_callback(reader)
            if not result.done():
                result.set_result(callback)
            body = "TuneTrail received the callback. Return to TuneTrail to check the result."
            writer.write(
                (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: text/plain; charset=utf-8\r\n"
                    f"Content-Length: {len(body.encode())}\r\n"
                    "Connection: close\r\n\r\n"
                    f"{body}"
                ).encode()
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handle_connection, self._host, self._port)
        async with server:
            if ready is not None and not ready.done():
                ready.set_result(None)
            try:
                return await asyncio.wait_for(result, timeout_seconds)
            except TimeoutError as error:
                raise OAuthCallbackTimeoutError(
                    "Timed out waiting for Spotify authorization"
                ) from error

    async def _read_callback(self, reader: asyncio.StreamReader) -> AuthorizationCallback:
        """Read enough of a HTTP request to validate its callback target."""
        request_line = await reader.readline()
        while await reader.readline() not in {b"\r\n", b"\n", b""}:
            pass
        try:
            method, target, _version = request_line.decode("ascii").rstrip("\r\n").split(" ", 2)
        except ValueError:
            return AuthorizationCallback(
                error="invalid_request", error_description="Malformed callback request"
            )
        if method != "GET":
            return AuthorizationCallback(
                error="invalid_request", error_description="Callback must use GET"
            )
        return parse_callback_target(target, self._path)
