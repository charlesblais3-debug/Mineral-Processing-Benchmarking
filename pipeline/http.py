"""Small polite HTTP client (stdlib only): honest User-Agent, per-domain rate limit, retries."""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path = ROOT / ".env") -> dict[str, str]:
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def user_agent() -> str:
    env = {**load_env(), **os.environ}
    name, email = env.get("CONTACT_NAME"), env.get("CONTACT_EMAIL")
    if not (name and email):
        raise RuntimeError("Set CONTACT_NAME and CONTACT_EMAIL in .env (SEC EDGAR requires a contact in the User-Agent).")
    return f"MPBenchmark/0.1 {name} {email}"


class PoliteClient:
    def __init__(self, min_interval: dict[str, float] | None = None, default_interval: float = 3.0, retries: int = 4):
        self.min_interval = min_interval or {}
        self.default_interval = default_interval
        self.retries = retries
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self.ua = user_agent()

    def _wait(self, host: str) -> None:
        """Reserve the next request slot for this host (thread-safe), then sleep until it."""
        interval = self.min_interval.get(host, self.default_interval)
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._last.get(host, 0.0) + interval)
            self._last[host] = slot
        if slot > now:
            time.sleep(slot - now)

    def get(self, url: str, timeout: float = 60) -> bytes:
        host = urlparse(url).netloc
        delay = 2.0
        for attempt in range(self.retries + 1):
            self._wait(host)
            req = urllib.request.Request(url, headers={"User-Agent": self.ua, "Accept-Encoding": "identity"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise
            except (urllib.error.URLError, TimeoutError):
                if attempt < self.retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise
        raise RuntimeError("unreachable")

    def get_head(self, url: str, nbytes: int = 60_000, timeout: float = 60) -> bytes:
        """First `nbytes` of a document (servers that ignore Range headers still work: we stop reading)."""
        host = urlparse(url).netloc
        delay = 2.0
        for attempt in range(self.retries + 1):
            self._wait(host)
            req = urllib.request.Request(url, headers={"User-Agent": self.ua, "Accept-Encoding": "identity"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return r.read(nbytes)
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise
            except (urllib.error.URLError, TimeoutError):
                if attempt < self.retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise
        raise RuntimeError("unreachable")

    def get_json(self, url: str) -> dict:
        return json.loads(self.get(url))
