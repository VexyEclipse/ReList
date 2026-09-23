"""Jikan v4 metadata with complete-response caching and bounded retries."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from email.utils import parsedate_to_datetime


class JikanError(RuntimeError):
    pass


class JikanUnavailable(JikanError):
    pass


class JikanClient:
    base = "https://api.jikan.moe/v4"
    ttl = 24 * 3600
    stale_ttl = 7 * 24 * 3600
    _lock = threading.Lock()
    _next_request = 0.0

    def __init__(self, cache_dir: Path, check_cancelled=lambda: None,
                 wait=time.sleep, log=lambda message: None):
        self.cache_dir = cache_dir
        self.check_cancelled = check_cancelled
        self.wait = wait
        self.log = log
        self.memory = {}

    def _get(self, path, params=None):
        url = self.base + path + "?" + urllib.parse.urlencode(params or {})
        for attempt in range(3):
            self.check_cancelled()
            # Shared across clients/scans: a little under one request per second.
            with self._lock:
                delay = max(0, self._next_request - time.monotonic())
                if delay > 60:
                    raise JikanUnavailable("Jikan requested a longer cooldown; try scanning later")
                type(self)._next_request = time.monotonic() + delay + 1.1
            self.wait(delay)
            self.check_cancelled()
            request = urllib.request.Request(url, headers={
                "Accept": "application/json", "User-Agent": "ReList/1.0"})
            try:
                with urllib.request.urlopen(request, timeout=25) as response:
                    data = json.loads(response.read().decode("utf-8"))
                if not isinstance(data, dict) or "data" not in data:
                    raise JikanError("Jikan returned an invalid response")
                return data
            except urllib.error.HTTPError as error:
                error.close()
                if error.code != 429 and not 500 <= error.code < 600:
                    raise JikanError(f"Jikan HTTP {error.code}") from error
                delay = 2 ** (attempt + 1)
                retry = error.headers.get("Retry-After") if error.headers else None
                if retry:
                    try:
                        seconds = float(retry)
                    except ValueError:
                        try:
                            seconds = parsedate_to_datetime(retry).timestamp() - time.time()
                        except (ValueError, TypeError, OverflowError):
                            seconds = delay
                    if math.isfinite(seconds):
                        delay = max(delay, seconds)
                with self._lock:
                    type(self)._next_request = max(self._next_request, time.monotonic() + delay)
                if delay > 60 or attempt == 2:
                    raise JikanUnavailable(f"Jikan HTTP {error.code}; try scanning later") from error
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                if attempt == 2:
                    raise JikanUnavailable("Jikan could not be reached") from error
                delay = 2 ** (attempt + 1)
            except (ValueError, UnicodeError) as error:
                raise JikanError("Jikan returned invalid JSON") from error
            self.log(f"  Jikan temporarily unavailable; retrying in {delay:g}s...")
            self.wait(delay)

    def _cached(self, key, fetch, validate):
        self.check_cancelled()
        path = self.cache_dir / (hashlib.sha256(key.encode()).hexdigest() + ".json")
        cached = self.memory.get(key)
        if cached is None:
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        try:
            valid = (isinstance(cached, dict) and cached.get("version") == 1
                     and cached.get("key") == key and validate(cached.get("data")))
            age = time.time() - cached["saved_at"] if valid else float("inf")
            valid = valid and 0 <= age
        except (KeyError, TypeError, ValueError):
            valid, age = False, float("inf")
        if valid and age < self.ttl:
            return cached["data"]
        try:
            data = fetch()
        except JikanUnavailable:
            if valid and age < self.stale_ttl:
                self.log("  Jikan unavailable: using previously complete cached metadata (under 7 days old).")
                return cached["data"]
            raise
        if not validate(data):
            raise JikanError("Jikan returned malformed metadata")
        payload = {"version": 1, "key": key, "saved_at": time.time(), "data": data}
        self.memory[key] = payload
        temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(temporary, path)
        except OSError as error:
            self.log(f"  Could not save Jikan cache: {error}")
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return data

    def _pages(self, path, params=None):
        records = []
        seen = set()
        for page in range(1, 1001):
            self.check_cancelled()
            result = self._get(path, {**(params or {}), "page": page})
            data, pagination = result.get("data"), result.get("pagination")
            if (not isinstance(data, list) or not isinstance(pagination, dict)
                    or type(pagination.get("has_next_page")) is not bool):
                raise JikanError("Jikan pagination is incomplete")
            if pagination.get("current_page", page) != page:
                raise JikanError("Jikan returned the wrong page")
            for item in data:
                if not isinstance(item, dict) or type(item.get("mal_id")) is not int or item["mal_id"] <= 0:
                    raise JikanError("Jikan returned an invalid MAL ID")
                if item["mal_id"] in seen:
                    raise JikanError("Jikan returned duplicate IDs across pages")
                seen.add(item["mal_id"])
            records.extend(data)
            if not pagination["has_next_page"]:
                return records
            if not data:
                raise JikanError("Jikan returned an empty page with more pages pending")
        raise JikanError("Jikan pagination exceeded the safety limit")

    @staticmethod
    def _valid_records(data):
        return (isinstance(data, list)
                and all(isinstance(e, dict) and type(e.get("mal_id")) is int
                        and e["mal_id"] > 0 for e in data)
                and len({e["mal_id"] for e in data}) == len(data))

    def search_anime(self, query):
        return self._cached("search:" + query,
                            lambda: self._pages("/anime", {"q": query, "type": "tv"}),
                            self._valid_records)

    def anime(self, mal_id):
        return self._cached(f"anime:{mal_id}", lambda: self._get(f"/anime/{mal_id}")["data"],
                            lambda d: isinstance(d, dict) and d.get("mal_id") == mal_id)

    def episodes(self, mal_id):
        # Cache only after every page succeeds; mal_id is the absolute episode number.
        return self._cached(f"episodes:{mal_id}", lambda: self._pages(f"/anime/{mal_id}/episodes"),
                            self._valid_records)
