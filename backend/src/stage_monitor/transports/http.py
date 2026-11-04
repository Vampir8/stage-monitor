import asyncio
from urllib.parse import urlsplit

import httpx

from .base import BaseTransport, TransportLimitError


class HTTPTransport(BaseTransport):
    def __init__(self, base_url, *, headers=None, auth=None, verify=True, client_transport=None, keepalive_connections=2, **kwargs):
        super().__init__(**kwargs)
        url = urlsplit(base_url)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError("HTTP requires a base URL without embedded credentials")
        if url.query or url.fragment:
            raise ValueError("Base URL must not contain query/fragment")
        self.base_url, self.headers, self.client_transport = base_url, headers, client_transport
        if verify is False:
            raise ValueError('TLS certificate verification cannot be disabled')
        self.auth, self.verify = auth, verify
        if type(keepalive_connections) is not int or not 0 <= keepalive_connections <= 4:
            raise ValueError('Invalid HTTP keepalive limit')
        self.keepalive_connections = keepalive_connections
        self.client = None

    @property
    def connected(self):
        # An initialized pool is not proof that the remote device is reachable.
        return self.client is not None and not self.client.is_closed

    async def connect(self):
        if not self.connected:
            self.client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self.headers,
                auth=self.auth, verify=self.verify,
                timeout=self.timeout,
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=self.keepalive_connections),
                follow_redirects=False,
                trust_env=False,
                transport=self.client_transport,
            )

    async def disconnect(self):
        client, self.client = self.client, None
        if client:
            await client.aclose()

    async def request(self, method, path, *, content=None, params=None):
        # Keep requests bound to the configured endpoint, including scheme-relative URLs.
        url = urlsplit(path)
        if url.scheme or url.netloc or not path.startswith("/") or path.startswith("//"):
            raise ValueError("Request path must be relative to the configured host")
        if content is not None:
            self.validate_payload(content)
        if not self.connected:
            raise ConnectionError("HTTP pool closed")
        async with asyncio.timeout(self.timeout):
            await self.record("TX", content or b"")
            async with self.client.stream(method, path, content=content, params=params) as response:
                response.raise_for_status()
                if response.is_redirect:
                    raise ConnectionError("HTTP redirects are not followed")
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=min(65536, self.max_bytes + 1)):
                    body.extend(chunk)
                    if len(body) > self.max_bytes:
                        raise TransportLimitError("HTTP response body too large")
                await self.record("RX", body)
                headers = response.headers.copy()
                for key in ["content-encoding", "transfer-encoding", "content-length"]:
                    headers.pop(key, None)
                return httpx.Response(
                    response.status_code,
                    headers=headers,
                    content=bytes(body),
                    request=response.request,
                )
