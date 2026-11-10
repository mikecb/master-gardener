"""Download OpenPlantDB, keep it as a local SQLite file, and keep it fresh.

The catalogue is fetched again when the integration version changes, and
checked for changes once a week. A failed or malformed download never replaces
a working database.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .engine import plantdb

SOURCE_URL = (
    "https://raw.githubusercontent.com/cwfrazier1/openplantdb/main/data/plants.csv"
)
CHECK_EVERY = timedelta(days=7)
DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=900)
CHUNK = 1 << 20

# What can go wrong fetching or building the catalogue without it being a bug.
REFRESH_ERRORS = (aiohttp.ClientError, TimeoutError, OSError, ValueError)


def catalogue_dir(hass: HomeAssistant) -> Path:
    return Path(hass.config.path(DOMAIN))


class PlantStore:
    def __init__(self, hass: HomeAssistant, version: str) -> None:
        self.hass = hass
        self._version = version
        self._dir = catalogue_dir(hass)
        self._db = self._dir / "openplantdb.sqlite"
        self._meta_file = self._dir / "openplantdb.json"
        self._lock = asyncio.Lock()
        # plants, etag, fetched_at, checked_at, version
        self.meta: dict = {}

    async def async_load(self) -> None:
        self.meta = await self.hass.async_add_executor_job(self._read_meta)

    def _read_meta(self) -> dict:
        if not self._db.exists() or not self._meta_file.exists():
            return {}
        try:
            return json.loads(self._meta_file.read_text(encoding="utf-8"))
        except ValueError:
            return {}

    @property
    def ready(self) -> bool:
        return bool(self.meta.get("plants"))

    def refresh_reason(self) -> str | None:
        """Why the catalogue should be fetched now, or None if it is current."""
        if not self.ready:
            return "missing"
        if self.meta.get("version") != self._version:
            return "integration updated"
        checked = datetime.fromisoformat(self.meta["checked_at"])
        if dt_util.utcnow() - checked >= CHECK_EVERY:
            return "weekly check"
        return None

    async def async_refresh(self, force: bool = False) -> bool:
        """Fetch the catalogue if due. True if the local database changed.

        Raises one of REFRESH_ERRORS on failure; the old database stays.
        """
        async with self._lock:
            reason = "requested" if force else self.refresh_reason()
            if reason is None:
                return False
            headers = {}
            if reason == "weekly check" and self.meta.get("etag"):
                # Only download if the file changed since we fetched it.
                headers["If-None-Match"] = self.meta["etag"]

            download = self._dir / "plants.csv.download"
            await self.hass.async_add_executor_job(
                lambda: self._dir.mkdir(parents=True, exist_ok=True)
            )
            session = async_get_clientsession(self.hass)
            async with session.get(
                SOURCE_URL, headers=headers, timeout=DOWNLOAD_TIMEOUT
            ) as response:
                if response.status == 304:
                    self.meta["checked_at"] = dt_util.utcnow().isoformat()
                    await self.hass.async_add_executor_job(self._write_meta)
                    return False
                response.raise_for_status()
                etag = response.headers.get("ETag")
                handle = await self.hass.async_add_executor_job(open, download, "wb")
                try:
                    async for chunk in response.content.iter_chunked(CHUNK):
                        await self.hass.async_add_executor_job(handle.write, chunk)
                finally:
                    await self.hass.async_add_executor_job(handle.close)

            count = await self.hass.async_add_executor_job(self._build, download)
            now = dt_util.utcnow().isoformat()
            self.meta = {
                "plants": count,
                "etag": etag,
                "fetched_at": now,
                "checked_at": now,
                "version": self._version,
                "source": SOURCE_URL,
            }
            await self.hass.async_add_executor_job(self._write_meta)
            return True

    def _build(self, download: Path) -> int:
        """Build beside the live database and swap it in only if it is good."""
        staging = self._dir / "openplantdb.sqlite.new"
        try:
            count = plantdb.build_database(download, staging)
            os.replace(staging, self._db)
        finally:
            download.unlink(missing_ok=True)
            staging.unlink(missing_ok=True)
        return count

    def _write_meta(self) -> None:
        self._meta_file.write_text(json.dumps(self.meta), encoding="utf-8")

    async def async_get(self, slug: str) -> dict | None:
        return await self.hass.async_add_executor_job(plantdb.get, self._db, slug)

    async def async_resolve(self, text: str) -> tuple[dict | None, list[dict]]:
        return await self.hass.async_add_executor_job(plantdb.resolve, self._db, text)

    async def async_search(
        self, query: str, zone: int | None, limit: int
    ) -> list[dict]:
        return await self.hass.async_add_executor_job(
            lambda: plantdb.search(self._db, query, zone=zone, limit=limit)
        )
