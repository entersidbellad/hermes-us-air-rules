"""HTTP access and the on-disk cache.

Only two hosts are ever contacted (ALLOWED_HOSTS). Every request is an HTTPS GET,
paced to at most one request per second per host, with a 20 s socket timeout and a
10 MB response cap. Redirects are followed by hand and only to allowed hosts.

The cache lives under <HERMES_HOME>/plugin-data/us-air-rules/cache/ and holds public
regulatory text and search results only, never a facility profile. Writes are atomic
(temp file + os.replace), so parallel Hermes processes can share it.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .version import __version__

ALLOWED_HOSTS = frozenset({"www.ecfr.gov", "www.federalregister.gov"})
USER_AGENT = f"hermes-us-air-rules/{__version__} (+https://github.com/entersidbellad/hermes-us-air-rules)"
TIMEOUT_S = 20
MAX_REDIRECTS = 3
MIN_INTERVAL_S = 1.0
MAX_BYTES = 10 * 1024 * 1024


class UsairError(Exception):
    """An error a tool reports to the model as JSON (never raised past a handler)."""

    def __init__(self, kind: str, message: str, next_step: str = "", details: Optional[dict] = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.next_step = next_step
        self.details = details or {}


@dataclass
class Response:
    status: int
    body: bytes
    headers: Dict[str, str]
    url: str


Transport = Callable[[str, Dict[str, str]], Response]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def build_url(host: str, path: str, params: Optional[list] = None) -> str:
    query = urllib.parse.urlencode(params or [], doseq=True)
    return urllib.parse.urlunsplit(("https", host, path, query, ""))


def check_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise UsairError("network", f"Refused to contact {host or url!r}: not one of the plugin's source hosts.")
    if parts.scheme == "http":
        parts = parts._replace(scheme="https")
    elif parts.scheme != "https":
        raise UsairError("network", f"Refused non-HTTPS URL {url!r}.")
    return urllib.parse.urlunsplit(parts)


class Http:
    """Small urllib wrapper. ``transport`` can be replaced in tests: (url, headers) -> Response."""

    def __init__(self, transport: Optional[Transport] = None, min_interval: float = MIN_INTERVAL_S):
        self._transport = transport or self._urllib
        self._min_interval = min_interval
        self._lock = threading.Lock()
        self._last: Dict[str, float] = {}
        self.requests = 0

    def _pace(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._last.get(host, float("-inf")) + self._min_interval - now
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.monotonic()

    @staticmethod
    def _urllib(url: str, headers: Dict[str, str]) -> Response:
        opener = urllib.request.build_opener(_NoRedirect())
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            resp = opener.open(req, timeout=TIMEOUT_S)
            status = resp.status
        except urllib.error.HTTPError as err:
            resp = err
            status = err.code
        try:
            raw = resp.read(MAX_BYTES + 1) if status == 200 else resp.read(65536)
        finally:
            try:
                resp.close()
            except Exception:
                pass
        hdrs = {k.lower(): v for k, v in (resp.headers.items() if resp.headers else [])}
        return Response(status, raw, hdrs, url)

    def get(self, url: str, headers: Optional[Dict[str, str]] = None) -> Response:
        url = check_url(url)
        hdrs = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
        hdrs.update(headers or {})
        for _ in range(MAX_REDIRECTS + 1):
            host = urllib.parse.urlsplit(url).hostname or ""
            self._pace(host)
            self.requests += 1
            try:
                resp = self._transport(url, hdrs)
            except UsairError:
                raise
            except TimeoutError as err:
                raise UsairError("timeout", f"{host} did not answer within {TIMEOUT_S} s.",
                                 "Wait a moment and retry once.") from err
            except (urllib.error.URLError, OSError) as err:
                raise UsairError("network", f"Could not reach {host}: {err}",
                                 "Check the network connection, then retry once.") from err
            if resp.status in (301, 302, 303, 307, 308):
                loc = resp.headers.get("location")
                if not loc:
                    raise UsairError("bad_response", f"{host} sent a redirect without a target.")
                url = check_url(urllib.parse.urljoin(url, loc))
                continue
            body = resp.body
            if len(body) > MAX_BYTES:
                raise UsairError("too_large", f"The response from {host} is larger than {MAX_BYTES // (1024 * 1024)} MB.",
                                 "Ask for a single section instead of a whole subpart.")
            if resp.headers.get("content-encoding", "").lower() == "gzip" and body:
                body = _gunzip(body)
            return Response(resp.status, body, resp.headers, url)
        raise UsairError("bad_response", "Too many redirects.")


def _gunzip(data: bytes) -> bytes:
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
            out = gz.read(MAX_BYTES + 1)
    except (OSError, EOFError) as err:
        raise UsairError("bad_response", f"Could not unpack the compressed response: {err}") from err
    if len(out) > MAX_BYTES:
        raise UsairError("too_large", f"The unpacked response is larger than {MAX_BYTES // (1024 * 1024)} MB.",
                         "Ask for a single section instead of a whole subpart.")
    return out


def hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home  # public helper, stable across versions
        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_key(key: str) -> str:
    clean = _SAFE.sub("_", key).strip("._")[:80] or "x"
    if clean != key:
        clean = f"{clean}-{hashlib.sha1(key.encode()).hexdigest()[:10]}"
    return clean


@dataclass
class Cached:
    data: Any
    state: str  # fresh | cached | stale
    fetched_at: float
    meta: Dict[str, Any] = field(default_factory=dict)


class Cache:
    """One JSON document per key: {"meta": {...}, "data": ...}."""

    def __init__(self, root: Optional[Path] = None):
        self._root = root

    @property
    def root(self) -> Path:
        root = self._root or hermes_home() / "plugin-data" / "us-air-rules" / "cache"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _path(self, key: str) -> Path:
        return self.root / (_safe_key(key) + ".json")

    def read(self, key: str):
        try:
            doc = json.loads(self._path(key).read_text(encoding="utf-8"))
            return doc["data"], doc.get("meta") or {}
        except (OSError, ValueError, KeyError, TypeError):
            return None, None

    def write(self, key: str, data: Any, meta: Dict[str, Any]) -> None:
        payload = json.dumps({"meta": meta, "data": data}, ensure_ascii=False, separators=(",", ":"))
        _atomic_write(self._path(key), payload.encode("utf-8"))

    def entries(self) -> int:
        return sum(1 for p in self.root.glob("*.json"))

    def size_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.root.glob("*.json"))

    def prune(self, max_bytes: int, keep: str = "") -> int:
        """Delete the oldest entries until the cache fits. Returns entries removed."""
        keep_name = _safe_key(keep) + ".json" if keep else ""
        files = []
        total = 0
        for p in self.root.glob("*.json"):
            try:
                st = p.stat()
            except OSError:
                continue
            total += st.st_size
            files.append((st.st_mtime, st.st_size, p))
        removed = 0
        for _mtime, size, p in sorted(files):
            if total <= max_bytes:
                break
            if p.name == keep_name:
                continue
            try:
                p.unlink()
            except OSError:
                continue
            total -= size
            removed += 1
        return removed


def _atomic_write(path: Path, payload: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Store:
    """Fetch-through cache. Entries younger than their TTL are served from disk; on a network
    failure or a 5xx answer the last copy is served with state "stale" (and says so)."""

    def __init__(self, http: Optional[Http] = None, cache: Optional[Cache] = None,
                 settings: Optional[Callable[[str, Any], Any]] = None):
        self.http = http or Http()
        self.cache = cache or Cache()
        self._settings = settings or (lambda key, default=None: default)

    def setting(self, key: str, default: Any = None) -> Any:
        try:
            value = self._settings(key, default)
        except Exception:
            value = default
        return default if value is None else value

    def fetch(self, key: str, url: str, parse: Callable[[bytes], Any], *, ttl_s: float,
              headers: Optional[Dict[str, str]] = None) -> Cached:
        """``ttl_s`` < 0 means the document never changes (date-pinned eCFR text)."""
        data, meta = self.cache.read(key)
        now = time.time()
        if data is not None and (ttl_s < 0 or now - meta.get("fetched_at", 0) < ttl_s):
            return Cached(data, "cached", meta.get("fetched_at", now), meta)
        try:
            resp = self.http.get(url, headers)
        except UsairError as err:
            if data is not None and err.kind in ("network", "timeout"):
                return Cached(data, "stale", meta.get("fetched_at", 0), meta)
            raise
        if resp.status != 200:
            if data is not None and resp.status >= 500:
                return Cached(data, "stale", meta.get("fetched_at", 0), meta)
            raise status_error(resp)
        parsed = parse(resp.body)
        new_meta = {"url": resp.url, "fetched_at": now}
        try:
            self.cache.write(key, parsed, new_meta)
            self._maybe_prune(key)
        except OSError:
            pass
        return Cached(parsed, "fresh", now, new_meta)

    def _maybe_prune(self, key: str) -> None:
        try:
            limit_mb = float(self.setting("cache_max_mb", 100))
        except (TypeError, ValueError):
            limit_mb = 100.0
        self.cache.prune(int(max(limit_mb, 5) * 1024 * 1024), keep=key)


def status_error(resp: Response) -> UsairError:
    host = urllib.parse.urlsplit(resp.url).hostname
    snippet = resp.body[:300].decode("utf-8", "replace") if resp.body else ""
    if resp.status == 404:
        return UsairError("not_found", f"{host} has no document at this address (HTTP 404).",
                          "Check the citation; the part, subpart or section may not exist in Title 40.",
                          {"url": resp.url})
    if resp.status == 429:
        return UsairError("rate_limited", f"{host} asked us to slow down (HTTP 429).", "Wait a minute, then retry once.")
    if resp.status >= 500:
        return UsairError("upstream", f"{host} answered HTTP {resp.status}.", "Try again later.")
    return UsairError("bad_request", f"{host} answered HTTP {resp.status}: {snippet}".strip(),
                      "Check the arguments (citation format, dates).", {"url": resp.url})


def parse_json(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as err:
        raise UsairError("bad_response", f"The source sent something that is not JSON: {err}",
                         "Retry once; the source may be changing its format.") from err
