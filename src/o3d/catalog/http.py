"""Bounded, identified HTTP requests. No arbitrary asset/proxy endpoint."""
from __future__ import annotations

import time
from urllib.parse import urljoin, urlsplit

import httpx

USER_AGENT = "OxygenPartsCatalog/0.1 (+https://github.com/OXYGEN-CRO/oxygen-cad)"


class Fetcher:
    def __init__(self, interval: float = 1.0):
        self.client = httpx.Client(timeout=60, headers={"User-Agent": USER_AGENT}, follow_redirects=False)
        self.interval = interval
        self.last = 0.0

    def close(self):
        self.client.close()

    def get(self, url: str, *, allowed_hosts: set[str], max_bytes=25_000_000):
        for attempt in range(5):
            parsed = urlsplit(url)
            if parsed.scheme != "https" or parsed.hostname not in allowed_hosts or parsed.username or parsed.port not in (None, 443):
                raise ValueError("URL is outside this connector's HTTPS host allowlist")
            time.sleep(max(0, self.interval - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            with self.client.stream("GET", url) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    url = urljoin(url, response.headers["location"])
                    continue
                if response.status_code in (429, 502, 503, 504) and attempt < 4:
                    try:
                        delay = float(response.headers.get("Retry-After", 2 ** attempt))
                    except ValueError:
                        delay = 2 ** attempt
                    if delay > 30:
                        raise ValueError("Provider requests a longer backoff; resume this run later")
                    time.sleep(max(0, delay))
                    continue
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError(f"Response exceeds {max_bytes} bytes")
                    chunks.append(chunk)
                return b"".join(chunks)
        raise ValueError("Redirect/retry limit reached")
