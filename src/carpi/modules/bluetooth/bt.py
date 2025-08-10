from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from ...event_bus import EventBus
from ...storage.db import Database

from dbus_next.aio import MessageBus  # type: ignore[reportMissingImports]
from dbus_next import Message, MessageType, Variant, BusType  # type: ignore[reportMissingImports]
from dbus_next.service import ServiceInterface, method  # type: ignore[reportMissingImports]

logger = logging.getLogger(__name__)


def _device_path_for(address: str) -> str:
    return "/org/bluez/hci0/dev_" + address.replace(":", "_")


class BluetoothAgent(ServiceInterface):
    """BlueZ Agent implementation to confirm/authorize pairing.

    Strategy: auto-accept if device is trusted in DB, otherwise publish an
    approval event and wait briefly for response. If approved, mark trusted.
    """

    def __init__(self, manager: "BluetoothManager") -> None:
        super().__init__("org.bluez.Agent1")
        self._manager = manager

    async def _get_device_props(self, device_path: str) -> Dict[str, Any]:
        bus = self._manager._sysbus
        assert bus is not None
        msg = Message(
            destination="org.bluez",
            path=device_path,
            interface="org.freedesktop.DBus.Properties",
            member="GetAll",
            signature="s",
            body=["org.bluez.Device1"],
        )
        reply = await bus.call(msg)
        if reply.message_type == MessageType.ERROR:
            return {}
        # reply.body[0] is dict of str->Variant
        props: Dict[str, Any] = {}
        for key, variant in reply.body[0].items():
            props[key] = variant.value if isinstance(variant, Variant) else variant
        return props

    async def _await_approval(self, address: str, name: Optional[str]) -> bool:
        # Publish an approval request and wait for a response on bt.approval
        await self._manager._events.publish(
            "bt.pair_request",
            {"address": address, "name": name},
        )
        try:
            # Wait for one response for up to 30s
            async for ev in self._manager._events.subscribe("bt.pair_response"):
                if ev.get("address") == address:
                    return bool(ev.get("approved", False))
        except asyncio.CancelledError:
            return False
        return False

    async def _approve_or_reject(self, device_path: str) -> None:
        # Called by agent hooks to gate pairing/authorization
        props = await self._get_device_props(device_path)
        address = props.get("Address")
        name = props.get("Name")
        if not isinstance(address, str):
            raise Exception("org.bluez.Error.Rejected")
        trusted = await self._manager._db.is_bt_trusted(address)
        if trusted:
            return
        approved = await self._await_approval(address, name if isinstance(name, str) else None)
        if approved:
            await self._manager._db.set_bt_trusted(address, True)
            return
        raise Exception("org.bluez.Error.Rejected")

    # Agent methods
    @method()
    async def Release(self) -> None:  # noqa: N802 (DBus name)
        return None

    @method(in_signature="ou")
    async def RequestConfirmation(self, device, passkey) -> None:  # noqa: N802
        await self._approve_or_reject(device)
        return None

    @method(in_signature="o")
    async def RequestAuthorization(self, device) -> None:  # noqa: N802
        await self._approve_or_reject(device)
        return None

    @method(in_signature="os")
    async def AuthorizeService(self, device, uuid) -> None:  # noqa: N802
        await self._approve_or_reject(device)
        return None

    # Provide basic NoInputNoOutput behavior for other methods
    @method(in_signature="o", out_signature="s")
    async def RequestPinCode(self, device):  # noqa: N802
        await self._approve_or_reject(device)
        return "0000"

    @method(in_signature="o", out_signature="u")
    async def RequestPasskey(self, device):  # noqa: N802
        await self._approve_or_reject(device)
        return 0

    @method(in_signature="os")
    async def DisplayPinCode(self, device, pincode) -> None:  # noqa: N802
        return None

    @method(in_signature="ouq")
    async def DisplayPasskey(self, device, passkey, entered) -> None:  # noqa: N802
        return None

    @method()
    async def Cancel(self) -> None:  # noqa: N802
        return None


class BluetoothManager:
    def __init__(self, events: EventBus, db: Database, alias: Optional[str] = None, make_discoverable: bool = True, make_pairable: bool = True) -> None:
        self._events = events
        self._db = db
        self._task: asyncio.Task | None = None
        self._sysbus: MessageBus | None = None
        self._session_bus: MessageBus | None = None
        self._alias = alias
        self._make_discoverable = make_discoverable
        self._make_pairable = make_pairable

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="bluetooth-manager")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _register_agent(self) -> None:
        assert self._sysbus is not None
        agent = BluetoothAgent(self)
        path = "/carpi/agent"
        self._sysbus.export(path, agent)
        # Register with BlueZ
        msg = Message(
            destination="org.bluez",
            path="/org/bluez",
            interface="org.bluez.AgentManager1",
            member="RegisterAgent",
            signature="os",
            body=[path, "DisplayYesNo"],
        )
        reply = await self._sysbus.call(msg)
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f"RegisterAgent failed: {reply.body}")
        # Make default agent
        msg2 = Message(
            destination="org.bluez",
            path="/org/bluez",
            interface="org.bluez.AgentManager1",
            member="RequestDefaultAgent",
            signature="o",
            body=[path],
        )
        reply2 = await self._sysbus.call(msg2)
        if reply2.message_type == MessageType.ERROR:
            logger.warning("RequestDefaultAgent failed: %s", reply2.body)

    async def _run(self) -> None:
        logger.info("Bluetooth manager starting (BlueZ Agent, PBAP, HFP)")
        try:
            self._sysbus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        except Exception as exc:
            logger.warning("System DBus connect failed: %s", exc)
            while True:
                await asyncio.sleep(5)

        # Session bus for OBEX (PBAP)
        try:
            self._session_bus = await MessageBus(bus_type=BusType.SESSION).connect()
        except Exception as exc:
            logger.warning("Session DBus connect failed (OBEX may not work): %s", exc)
            self._session_bus = None

        # Register pairing/authorization agent
        try:
            await self._register_agent()
        except Exception as exc:
            logger.warning("Agent registration failed: %s", exc)

        # Power on and configure adapter
        try:
            await self._set_adapter_property("Powered", True)
            if isinstance(self._alias, str) and self._alias:
                await self._set_adapter_property("Alias", self._alias)
            if self._make_pairable:
                await self._set_adapter_property("Pairable", True)
            if self._make_discoverable:
                # Optional: make discoverable indefinitely
                await self._set_adapter_property("DiscoverableTimeout", 0)
                await self._set_adapter_property("Discoverable", True)
        except Exception as exc:
            logger.warning("Adapter configuration failed: %s", exc)

        async def handle_bt_commands() -> None:
            async for cmd in self._events.subscribe("bt.command"):
                action = (cmd.get("action") or "").lower()
                try:
                    if action == "discoverable":
                        await self._set_adapter_property("Discoverable", True)
                    elif action == "pairable":
                        await self._set_adapter_property("Pairable", True)
                    elif action == "alias":
                        alias = cmd.get("alias")
                        if isinstance(alias, str) and alias:
                            await self._set_adapter_property("Alias", alias)
                    elif action == "connect":
                        addr = cmd.get("address")
                        if addr:
                            await self._connect_device(addr)
                    elif action == "disconnect":
                        addr = cmd.get("address")
                        if addr:
                            await self._disconnect_device(addr)
                    elif action == "trust":
                        addr = cmd.get("address")
                        trusted = bool(cmd.get("trusted", True))
                        if addr:
                            await self._db.set_bt_trusted(addr, trusted)
                    elif action == "sync_contacts":
                        addr = cmd.get("address")
                        if addr:
                            await self._sync_contacts(addr)
                except Exception as exc:
                    logger.warning("bt.command failed: %s", exc)

        async def handle_call_commands() -> None:
            async for cmd in self._events.subscribe("bt.call"):
                action = (cmd.get("action") or "").lower()
                try:
                    if action == "answer":
                        await self._ofono_action("Answer")
                    elif action == "hangup":
                        await self._ofono_action("Hangup")
                    elif action == "dial":
                        number = cmd.get("number")
                        if number:
                            await self._ofono_dial(number)
                    elif action == "decline":
                        await self._ofono_action("Hangup")
                except Exception as exc:
                    logger.warning("bt.call failed: %s", exc)

        async def handle_bt_status() -> None:
            # Periodically publish connected device status
            while True:
                try:
                    status = await self._get_bt_status()
                    await self._events.publish("bt.status", status)
                except Exception as exc:
                    logger.debug("bt.status poll failed: %s", exc)
                await asyncio.sleep(3.0)

        await asyncio.gather(handle_bt_commands(), handle_call_commands(), handle_bt_status())

    async def _set_adapter_property(self, prop: str, value: Any) -> None:
        assert self._sysbus is not None
        # Pick DBus signature based on Python type
        if isinstance(value, bool):
            variant = Variant("b", value)
        elif isinstance(value, int):
            variant = Variant("u", value)
        else:
            variant = Variant("s", str(value))
        msg = Message(
            destination="org.bluez",
            path="/org/bluez/hci0",
            interface="org.freedesktop.DBus.Properties",
            member="Set",
            signature="ssv",
            body=["org.bluez.Adapter1", prop, variant],
        )
        reply = await self._sysbus.call(msg)
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f"Failed to set adapter property {prop}: {reply.body}")

    async def _connect_device(self, address: str) -> None:
        assert self._sysbus is not None
        path = _device_path_for(address)
        msg = Message(destination="org.bluez", path=path, interface="org.bluez.Device1", member="Connect")
        reply = await self._sysbus.call(msg)
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f"Failed to connect {address}: {reply.body}")

    async def _disconnect_device(self, address: str) -> None:
        assert self._sysbus is not None
        path = _device_path_for(address)
        msg = Message(destination="org.bluez", path=path, interface="org.bluez.Device1", member="Disconnect")
        reply = await self._sysbus.call(msg)
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f"Failed to disconnect {address}: {reply.body}")

    async def _ofono_action(self, method: str) -> None:
        # Minimal ofono calls; assumes ofono/hsphfpd is set up on SYSTEM bus
        assert self._sysbus is not None
        path = "/"
        msg = Message(destination="org.ofono", path=path, interface="org.ofono.VoiceCallManager", member=method)
        reply = await self._sysbus.call(msg)
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f"ofono {method} failed: {reply.body}")

    async def _ofono_dial(self, number: str) -> None:
        assert self._sysbus is not None
        path = "/"
        msg = Message(
            destination="org.ofono",
            path=path,
            interface="org.ofono.VoiceCallManager",
            member="Dial",
            signature="ss",
            body=[number, "default"],
        )
        reply = await self._sysbus.call(msg)
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f"ofono Dial failed: {reply.body}")

    async def _get_bt_status(self) -> Dict[str, Any]:
        """List connected devices using ObjectManager."""
        assert self._sysbus is not None
        msg = Message(
            destination="org.bluez",
            path="/",
            interface="org.freedesktop.DBus.ObjectManager",
            member="GetManagedObjects",
        )
        reply = await self._sysbus.call(msg)
        devices: List[Dict[str, Any]] = []
        current: Optional[Dict[str, Any]] = None
        if reply.message_type != MessageType.ERROR:
            managed = reply.body[0]
            for obj_path, ifaces in managed.items():
                dev = ifaces.get("org.bluez.Device1")
                if not dev:
                    continue
                props: Dict[str, Any] = {}
                for k, v in dev.items():
                    props[k] = v.value if isinstance(v, Variant) else v
                if props.get("Connected"):
                    entry = {
                        "address": props.get("Address"),
                        "name": props.get("Name"),
                        "paired": bool(props.get("Paired", False)),
                        "trusted": bool(props.get("Trusted", False)),
                        "uuids": props.get("UUIDs", []),
                        "rssi": props.get("RSSI"),
                    }
                    devices.append(entry)
        if devices:
            current = devices[0]
        return {"connected_devices": devices, "current": current}

    # ---- Contacts via PBAP (OBEX) ----
    async def _sync_contacts(self, address: str) -> None:
        if self._session_bus is None:
            logger.warning("OBEX session bus not available; cannot sync contacts")
            return
        tmp_file = f"/tmp/carpi_contacts_{address.replace(':', '')}.vcf"
        try:
            # Create PBAP session
            create = Message(
                destination="org.bluez.obex",
                path="/org/bluez/obex",
                interface="org.bluez.obex.Client1",
                member="CreateSession",
                signature="a{sv}",
                body=[{
                    "Destination": Variant("s", address),
                    "Target": Variant("s", "PBAP"),
                }],
            )
            reply = await self._session_bus.call(create)
            if reply.message_type == MessageType.ERROR:
                logger.warning("CreateSession failed: %s", reply.body)
                return
            session_path = reply.body[0]

            # PullAll into tmp file
            pull = Message(
                destination="org.bluez.obex",
                path=session_path,
                interface="org.bluez.obex.PhonebookAccess1",
                member="PullAll",
                signature="a{sv}",
                body=[{
                    "TargetFile": Variant("s", tmp_file),
                    "Format": Variant("s", "vcard30"),
                }],
            )
            t_reply = await self._session_bus.call(pull)
            if t_reply.message_type == MessageType.ERROR:
                logger.warning("PBAP PullAll failed: %s", t_reply.body)
                # Try RemoveSession before return
                await self._remove_obex_session(session_path)
                return
            transfer_path = t_reply.body[0]

            # Wait for Transfer1.Status == 'complete'
            await self._wait_transfer_complete(transfer_path)

            # Parse and store
            contacts = self._parse_vcf_file(tmp_file)
            await self._db.replace_contacts(address, contacts)
            logger.info("Synced %d contacts from %s", len(contacts), address)
        except Exception as exc:
            logger.warning("Sync contacts failed: %s", exc)
        finally:
            try:
                if os.path.exists(tmp_file):
                    os.remove(tmp_file)
            except Exception:
                pass

    async def _wait_transfer_complete(self, transfer_path: str) -> None:
        assert self._session_bus is not None
        # Poll properties for simplicity
        for _ in range(0, 200):  # up to ~20s
            msg = Message(
                destination="org.bluez.obex",
                path=transfer_path,
                interface="org.freedesktop.DBus.Properties",
                member="Get",
                signature="ss",
                body=["org.bluez.obex.Transfer1", "Status"],
            )
            reply = await self._session_bus.call(msg)
            if reply.message_type != MessageType.ERROR:
                status_variant: Variant = reply.body[0]
                status = status_variant.value if isinstance(status_variant, Variant) else status_variant
                if status in ("complete", "error", "cancelled"):
                    return
            await asyncio.sleep(0.1)

    async def _remove_obex_session(self, session_path: str) -> None:
        assert self._session_bus is not None
        msg = Message(
            destination="org.bluez.obex",
            path="/org/bluez/obex",
            interface="org.bluez.obex.Client1",
            member="RemoveSession",
            signature="o",
            body=[session_path],
        )
        await self._session_bus.call(msg)

    def _parse_vcf_file(self, path: str) -> List[Tuple[Optional[str], Optional[str], Optional[str]]]:
        contacts: List[Tuple[Optional[str], Optional[str], Optional[str]]] = []
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                raw = f.read()
            # Split by BEGIN:VCARD ... END:VCARD
            cards = re.split(r"(?im)^END:VCARD\s*$", raw)
            for card in cards:
                if "BEGIN:VCARD" not in card:
                    continue
                name = None
                number = None
                for line in card.splitlines():
                    if line.upper().startswith("FN:"):
                        name = line[3:].strip()
                    if line.upper().startswith("TEL"):
                        # TEL;TYPE=CELL:12345 or TEL:12345
                        parts = line.split(":", 1)
                        if len(parts) == 2:
                            number = parts[1].strip()
                            break
                trimmed = card.strip() + "\nEND:VCARD\n"
                contacts.append((name, number, trimmed))
        except Exception:
            pass
        return contacts





