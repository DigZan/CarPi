from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import aiosqlite  # type: ignore[reportMissingImports]
except Exception:  # pragma: no cover
    aiosqlite = None  # type: ignore

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: Any | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        assert aiosqlite is not None, "aiosqlite is required"
        conn = await aiosqlite.connect(self._path)
        self._conn = conn
        # Pragmas for reliability
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA synchronous=NORMAL;")

        # Sensor readings
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sensor_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_utc TEXT NOT NULL,
                sensor TEXT NOT NULL,
                data_json TEXT NOT NULL
            )
            """
        )

        # Bluetooth devices allowlist
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bt_devices (
                address TEXT PRIMARY KEY,
                name TEXT,
                trusted INTEGER NOT NULL DEFAULT 0,
                first_seen_utc TEXT NOT NULL,
                last_seen_utc TEXT NOT NULL
            )
            """
        )

        # Contacts (very lightweight schema)
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_address TEXT NOT NULL,
                name TEXT,
                number TEXT,
                raw_vcard TEXT,
                UNIQUE(device_address, name, number)
            )
            """
        )

        await conn.commit()
        logger.info("Database initialized at %s", self._path)

    async def stop(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def insert_sensor_reading(self, sensor: str, ts_utc_iso: str, data: Dict[str, Any]) -> None:
        assert self._conn is not None
        payload = json.dumps(data, separators=(",", ":"))
        async with self._lock:
            await self._conn.execute(
                "INSERT INTO sensor_readings (ts_utc, sensor, data_json) VALUES (?, ?, ?)",
                (ts_utc_iso, sensor, payload),
            )
            await self._conn.commit()

    # --- Bluetooth device trust storage ---
    async def upsert_bt_device(self, address: str, name: Optional[str], trusted: bool, ts_utc_iso: str) -> None:
        assert self._conn is not None
        async with self._lock:
            await self._conn.execute(
                """
                INSERT INTO bt_devices(address, name, trusted, first_seen_utc, last_seen_utc)
                VALUES(?,?,?,?,?)
                ON CONFLICT(address) DO UPDATE SET
                    name=COALESCE(excluded.name, bt_devices.name),
                    trusted=excluded.trusted,
                    last_seen_utc=excluded.last_seen_utc
                """,
                (address, name, 1 if trusted else 0, ts_utc_iso, ts_utc_iso),
            )
            await self._conn.commit()

    async def set_bt_trusted(self, address: str, trusted: bool) -> None:
        assert self._conn is not None
        async with self._lock:
            await self._conn.execute(
                "UPDATE bt_devices SET trusted=? WHERE address=?",
                (1 if trusted else 0, address),
            )
            await self._conn.commit()

    async def is_bt_trusted(self, address: str) -> bool:
        assert self._conn is not None
        async with self._lock:
            async with self._conn.execute("SELECT trusted FROM bt_devices WHERE address=?", (address,)) as cursor:
                row = await cursor.fetchone()
                return bool(row[0]) if row else False

    # --- Contacts storage ---
    async def replace_contacts(self, device_address: str, contacts: Iterable[Tuple[Optional[str], Optional[str], Optional[str]]]) -> None:
        """Replace contacts for a given device.

        Each tuple is (name, number, raw_vcard).
        """
        assert self._conn is not None
        async with self._lock:
            await self._conn.execute("DELETE FROM contacts WHERE device_address=?", (device_address,))
            await self._conn.executemany(
                "INSERT OR IGNORE INTO contacts(device_address, name, number, raw_vcard) VALUES (?,?,?,?)",
                ((device_address, name, number, raw) for (name, number, raw) in contacts),
            )
            await self._conn.commit()

    async def list_bt_devices(self) -> List[Dict[str, Any]]:
        assert self._conn is not None
        async with self._lock:
            async with self._conn.execute(
                "SELECT address, name, trusted, first_seen_utc, last_seen_utc FROM bt_devices ORDER BY last_seen_utc DESC"
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    {
                        "address": r[0],
                        "name": r[1],
                        "trusted": bool(r[2]),
                        "first_seen_utc": r[3],
                        "last_seen_utc": r[4],
                    }
                    for r in rows
                ]


