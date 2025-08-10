from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from aiohttp import web  # type: ignore[reportMissingImports]

from ...event_bus import EventBus
from ...storage.db import Database

logger = logging.getLogger(__name__)


class WebServer:
    def __init__(self, events: EventBus, db: Database, host: str = "0.0.0.0", port: int = 8080) -> None:
        self._events = events
        self._db = db
        self._host = host
        self._port = port
        self._task: asyncio.Task | None = None
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="web-server")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        app = web.Application()
        app.add_routes([
            web.get('/', self._handle_index),
            web.get('/api/sse', self._handle_sse),
            web.get('/api/contacts', self._handle_contacts),
            web.get('/api/bt_devices', self._handle_bt_devices),
        ])
        self._app = app
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self._host, self._port)
        await self._site.start()
        logger.info("Web server listening on http://%s:%d", self._host, self._port)

        try:
            while True:
                await asyncio.sleep(60)
        finally:
            try:
                if self._runner is not None:
                    await self._runner.cleanup()
            except Exception:
                pass

    async def _handle_index(self, request: web.Request) -> web.Response:
        html = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>CarPi</title>
    <style>
      body{font-family:system-ui,Arial;margin:20px}
      pre{background:#f5f5f5;padding:10px}
      .grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
    </style>
  </head>
  <body>
    <h2>CarPi Dashboard</h2>
    <div class="grid">
      <div>
        <h3>Storage (USB)</h3>
        <pre id="storage">Loading...</pre>
      </div>
      <div>
        <h3>BME280</h3>
        <pre id="bme">Loading...</pre>
      </div>
      <div>
        <h3>ICM-20948</h3>
        <pre id="icm">Loading...</pre>
      </div>
      <div>
        <h3>GPS</h3>
        <pre id="gps">Loading...</pre>
      </div>
      <div>
        <h3>Bluetooth Current</h3>
        <pre id="btcur">Loading...</pre>
      </div>
      <div>
        <h3>Bluetooth Devices</h3>
        <pre id="btdev">Loading...</pre>
      </div>
    </div>
    <script>
      const es = new EventSource('/api/sse');
      es.onmessage = (ev) => {
        try{
          const msg = JSON.parse(ev.data);
          if(msg.topic === 'storage.usb'){
            document.getElementById('storage').textContent = JSON.stringify(msg.data,null,2);
          }
          if(msg.topic === 'sensor.bme280'){
            document.getElementById('bme').textContent = JSON.stringify(msg.data,null,2);
          }
          if(msg.topic === 'sensor.icm20948'){
            document.getElementById('icm').textContent = JSON.stringify(msg.data,null,2);
          }
          if(msg.topic === 'sensor.gps'){
            document.getElementById('gps').textContent = JSON.stringify(msg.data,null,2);
          }
          if(msg.topic === 'bt.status'){
            document.getElementById('btcur').textContent = JSON.stringify(msg.data.current,null,2);
          }
        }catch(e){console.error(e)}
      };
      async function loadDevices(){
        const r = await fetch('/api/bt_devices');
        const j = await r.json();
        document.getElementById('btdev').textContent = JSON.stringify(j,null,2);
      }
      loadDevices();
      setInterval(loadDevices, 5000);
    </script>
  </body>
</html>
        """
        return web.Response(text=html, content_type='text/html')

    async def _handle_sse(self, request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(status=200, reason='OK', headers={'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache'})
        await resp.prepare(request)

        queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=100)

        async def forward(topic: str) -> None:
            async for ev in self._events.subscribe(topic):
                await queue.put({"topic": topic, "data": ev})

        tasks = [
            asyncio.create_task(forward("storage.usb")),
            asyncio.create_task(forward("sensor.bme280")),
            asyncio.create_task(forward("sensor.icm20948")),
            asyncio.create_task(forward("sensor.gps")),
            asyncio.create_task(forward("bt.status")),
        ]

        async def sender() -> None:
            try:
                while True:
                    item = await queue.get()
                    data = json.dumps(item)
                    await resp.write(f"data: {data}\n\n".encode())
                    await resp.drain()
            except asyncio.CancelledError:
                pass

        send_task = asyncio.create_task(sender())
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            for t in tasks:
                t.cancel()
            send_task.cancel()
        return resp

    async def _handle_contacts(self, request: web.Request) -> web.Response:
        # For now, this endpoint would ideally return full contacts for the current device.
        # Keeping it simple; future improvement: query by current connected address from bt.status cache.
        return web.json_response({"ok": True})

    async def _handle_bt_devices(self, request: web.Request) -> web.Response:
        devices = await self._db.list_bt_devices()
        return web.json_response(devices)



