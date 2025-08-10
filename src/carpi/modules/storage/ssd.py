from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ...event_bus import EventBus

logger = logging.getLogger(__name__)


@dataclass
class UsbPartition:
    name: str
    path: str
    fs_type: Optional[str]
    uuid: Optional[str]
    mountpoint: Optional[str]
    model: Optional[str]
    is_removable: bool
    transport: Optional[str]


def _run_lsblk_json() -> Dict[str, Any] | None:
    try:
        result = subprocess.run(
            [
                "lsblk",
                "-J",
                "-o",
                "NAME,TYPE,RM,MODEL,TRAN,MOUNTPOINT,UUID,FSTYPE,KNAME",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except Exception:
        return None


def _collect_usb_partitions() -> List[UsbPartition]:
    data = _run_lsblk_json()
    if not data:
        return []
    parts: List[UsbPartition] = []
    for disk in data.get("blockdevices", []) or []:
        if (disk.get("type") != "disk"):
            continue
        rm = bool(disk.get("rm"))
        tran = disk.get("tran")
        model = disk.get("model")
        children = disk.get("children") or []
        for ch in children:
            if ch.get("type") != "part":
                continue
            name = ch.get("name") or ch.get("kname") or ""
            path = f"/dev/{name}"
            parts.append(
                UsbPartition(
                    name=name,
                    path=path,
                    fs_type=ch.get("fstype"),
                    uuid=ch.get("uuid"),
                    mountpoint=ch.get("mountpoint"),
                    model=model,
                    is_removable=rm,
                    transport=tran,
                )
            )
    return parts


class SSDManager:
    """Hot-swap USB SSD manager.

    - Detects USB block partitions
    - Mounts the first available partition to /media/carpi-<uuid> (or by name if UUID missing)
    - Publishes status updates on `storage.usb` with device, mountpoint, and free/total bytes
    - Accepts commands on `storage.usb.command`: { action: 'eject'|'refresh' }
    """

    def __init__(self, events: EventBus, mount_base: str = "/media") -> None:
        self._events = events
        self._mount_base = mount_base
        self._task: asyncio.Task | None = None
        self._current: Optional[UsbPartition] = None
        self._mountpoint: Optional[str] = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="ssd-manager")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        logger.info("SSD manager started")

        async def handle_commands() -> None:
            async for cmd in self._events.subscribe("storage.usb.command"):
                action = (cmd.get("action") or "").lower()
                try:
                    if action == "eject":
                        await self._ensure_unmounted()
                    elif action == "refresh":
                        await self._scan_and_reconcile()
                except Exception as exc:
                    logger.warning("storage.usb.command failed: %s", exc)

        async def scanner_loop() -> None:
            while True:
                try:
                    await self._scan_and_reconcile()
                except Exception as exc:
                    logger.warning("SSD scan error: %s", exc)
                await asyncio.sleep(1.0)

        await asyncio.gather(handle_commands(), scanner_loop())

    async def _scan_and_reconcile(self) -> None:
        parts = _collect_usb_partitions()

        def _is_candidate(p: UsbPartition) -> bool:
            # Only consider USB transport devices
            if p.transport != "usb":
                return False
            # Must have a filesystem
            if not p.fs_type:
                return False
            # Ignore obvious system device prefixes
            forbidden_prefixes = ("mmcblk", "nvme", "loop", "zram", "dm-", "md", "sr")
            if any((p.name or "").startswith(pref) for pref in forbidden_prefixes):
                return False
            # If mounted, require typical external mount locations and exclude /boot
            if p.mountpoint:
                if p.mountpoint.startswith("/boot"):
                    return False
                if not any(p.mountpoint.startswith(prefix) for prefix in ("/media", "/mnt", "/run/media")):
                    return False
            return True

        candidate = next((p for p in parts if _is_candidate(p)), None)

        # If we previously had a current selection that no longer matches the criteria, drop it
        if self._current is not None and not _is_candidate(self._current):
            self._current = None
            # Do not unmount if mountpoint is a system path; only unmount mounts we created under mount_base
            if self._mountpoint and self._mountpoint.startswith(self._mount_base + "/"):
                await self._ensure_unmounted()
            self._mountpoint = None
            await self._publish_status()

        if candidate is None:
            # No device present
            if self._current is not None:
                # Device removed while mounted -> clear state
                if self._mountpoint and self._mountpoint.startswith(self._mount_base + "/"):
                    await self._ensure_unmounted()
                self._current = None
                self._mountpoint = None
                await self._publish_status()
            return

        # If different from current, switch
        if self._current and (self._current.path != candidate.path):
            await self._ensure_unmounted()
            self._mountpoint = None
            self._current = None

        self._current = candidate

        # Mount if not mounted
        if not candidate.mountpoint and not self._mountpoint:
            await self._mount(candidate)
        elif candidate.mountpoint and not self._mountpoint:
            # Adopt externally mounted
            self._mountpoint = candidate.mountpoint

        await self._publish_status()

    async def _mount(self, part: UsbPartition) -> None:
        if not part.fs_type:
            return
        name = part.uuid or part.name.replace("/", "_")
        mountpoint = os.path.join(self._mount_base, f"carpi-{name}")
        try:
            os.makedirs(mountpoint, exist_ok=True)
            options = ["rw", "sync", "noatime"]
            cmd = ["mount", "-o", ",".join(options), part.path, mountpoint]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if res.returncode != 0:
                logger.warning("Mount failed: %s", res.stderr.strip())
                return
            self._mountpoint = mountpoint
            logger.info("Mounted %s at %s", part.path, mountpoint)
        except Exception as exc:
            logger.warning("Mount exception: %s", exc)

    async def _ensure_unmounted(self) -> None:
        if self._mountpoint:
            try:
                res = subprocess.run(["umount", self._mountpoint], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if res.returncode != 0:
                    logger.warning("Umount failed: %s", res.stderr.strip())
                else:
                    logger.info("Unmounted %s", self._mountpoint)
            except Exception as exc:
                logger.warning("Umount exception: %s", exc)
            # Try cleanup directory if empty
            try:
                if (
                    self._mountpoint
                    and self._mountpoint.startswith(self._mount_base + "/")
                    and os.path.isdir(self._mountpoint)
                    and not os.listdir(self._mountpoint)
                ):
                    os.rmdir(self._mountpoint)
            except Exception:
                pass
            self._mountpoint = None

    async def _publish_status(self) -> None:
        device_present = self._current is not None
        mounted = bool(self._mountpoint)
        total = free = used = None
        if mounted and self._mountpoint:
            try:
                du = shutil.disk_usage(self._mountpoint)
                total, used, free = du.total, du.used, du.free
            except Exception:
                pass
        payload = {
            "connected": device_present,
            "mounted": mounted,
            "device": (self._current.path if self._current else None),
            "model": (self._current.model if self._current else None),
            "fs": (self._current.fs_type if self._current else None),
            "mountpoint": self._mountpoint,
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": free,
        }
        await self._events.publish("storage.usb", payload)


