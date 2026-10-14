"""JSON file cache for synced Zoho Books data.

Stores each data type in a separate file with metadata:
  - last_synced: ISO timestamp
  - count: number of records
  - data: the actual records

Cache location: config/cache/
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "config" / "cache"


class SyncCache:
    """Read/write cached Zoho data to JSON files."""

    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = cache_dir or CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get(self, key: str) -> dict | None:
        """Get cached data by key.

        Returns dict with keys: last_synced, count, data
        or None if not cached.
        """
        path = self._path(key)

        if not path.exists():
            return None

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return raw
        except Exception:
            logger.warning("Failed to read cache: %s", path)
            return None

    def get_data(self, key: str) -> list[dict]:
        """Get just the data array from cache."""
        cached = self.get(key)
        if cached is None:
            return []
        return cached.get("data", [])

    def put(self, key: str, data: list[dict]):
        """Write data to cache with metadata."""
        now = datetime.now(timezone.utc).isoformat()

        payload = {
            "last_synced": now,
            "count": len(data),
            "data": data,
        }

        path = self._path(key)
        path.write_text(
            json.dumps(payload, indent=2, default=str),
            encoding="utf-8",
        )

        logger.info(
            "Cache written: %s (%d records)", key, len(data)
        )

    def get_metadata(self, key: str) -> dict | None:
        """Get just the metadata (no data) for a cached key."""
        path = self._path(key)

        if not path.exists():
            return None

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return {
                "last_synced": raw.get("last_synced"),
                "count": raw.get("count", 0),
                "file_size_bytes": path.stat().st_size,
            }
        except Exception:
            return None

    def list_keys(self) -> list[str]:
        """List all cached data keys."""
        keys = []
        for f in sorted(self.cache_dir.glob("*.json")):
            keys.append(f.stem)
        return keys

    def clear(self, key: str | None = None):
        """Clear one or all cache files."""
        if key:
            path = self._path(key)
            if path.exists():
                path.unlink()
                logger.info("Cache cleared: %s", key)
        else:
            for f in self.cache_dir.glob("*.json"):
                f.unlink()
            logger.info("All cache cleared")

    def get_all_metadata(self) -> dict[str, dict]:
        """Get metadata for all cached keys."""
        result = {}
        for key in self.list_keys():
            meta = self.get_metadata(key)
            if meta:
                result[key] = meta
        return result

    def _path(self, key: str) -> Path:
        """Get file path for a cache key."""
        # Sanitize key for filesystem
        safe_key = key.replace("/", "_").replace("\\", "_")
        return self.cache_dir / f"{safe_key}.json"
