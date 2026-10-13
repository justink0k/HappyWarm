"""Verbinding met Home Assistant via de Supervisor-proxy. Bevat een strikte whitelist:
de add-on kan alleen lezen, notify aanroepen, eigen sensoren zetten en de SAT options flow gebruiken.
Er is bewust geen MQTT en geen directe toegang tot de ketel."""
from __future__ import annotations

import asyncio
import itertools
import logging
import os

import aiohttp

log = logging.getLogger("ha")

WS_ALLOWED = {
    "get_states", "get_config", "history/history_during_period",
    "recorder/statistics_during_period", "config_entries/get",
    "input_boolean/create", "input_boolean/list", "subscribe_events",
}
REST_ALLOWED_PREFIXES = (
    "states/", "services/notify/", "config/config_entries/options/flow",
)


class HAError(Exception):
    pass


class HA:
    def __init__(self, token=None, base="http://supervisor/core"):
        self.token = token or os.environ.get("SUPERVISOR_TOKEN", "")
        self.base = base
        self.session = None
        self.ws = None
        self._ids = itertools.count(1)
        self._pending = {}
        self._subs = {}
        self._reader = None

    async def connect(self):
        self.session = aiohttp.ClientSession(headers={"Authorization": f"Bearer {self.token}"})
        url = self.base.replace("http", "ws", 1) + "/websocket"
        self.ws = await self.session.ws_connect(url, heartbeat=30, max_msg_size=0)
        msg = await self.ws.receive_json()
        if msg.get("type") != "auth_required":
            raise HAError(f"Onverwacht websocket-bericht: {msg.get('type')}")
        await self.ws.send_json({"type": "auth", "access_token": self.token})
        msg = await self.ws.receive_json()
        if msg.get("type") != "auth_ok":
            raise HAError("Websocket-authenticatie mislukt")
        self._reader = asyncio.create_task(self._read())

    async def close(self):
        if self._reader:
            self._reader.cancel()
        if self.ws:
            await self.ws.close()
        if self.session:
            await self.session.close()

    async def _read(self):
        async for m in self.ws:
            if m.type != aiohttp.WSMsgType.TEXT:
                continue
            msg = m.json()
            if msg.get("type") == "result" and msg["id"] in self._pending:
                fut = self._pending.pop(msg["id"])
                if msg.get("success"):
                    fut.set_result(msg.get("result"))
                else:
                    fut.set_exception(HAError(str(msg.get("error"))))
            elif msg.get("type") == "event" and msg["id"] in self._subs:
                try:
                    await self._subs[msg["id"]](msg["event"])
                except Exception:
                    log.exception("event-handler faalde")
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(HAError("websocket gesloten"))
        raise HAError("websocket gesloten")

    async def ws_call(self, payload, timeout=120):
        if payload["type"] not in WS_ALLOWED:
            raise HAError(f"Websocket-call niet toegestaan: {payload['type']}")
        i = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._pending[i] = fut
        await self.ws.send_json({"id": i, **payload})
        return await asyncio.wait_for(fut, timeout)

    async def subscribe(self, event_type, cb):
        i = next(self._ids)
        self._subs[i] = cb
        fut = asyncio.get_running_loop().create_future()
        self._pending[i] = fut
        await self.ws.send_json({"id": i, "type": "subscribe_events", "event_type": event_type})
        await fut

    async def rest(self, method, path, body=None):
        if not path.startswith(REST_ALLOWED_PREFIXES) and not (method == "GET" and path == "config"):
            raise HAError(f"REST-pad niet toegestaan: {path}")
        if path.startswith("states/") and method != "GET" and not path.startswith("states/sensor.sat_analyse_"):
            raise HAError("Alleen eigen sensor.sat_analyse_* mag worden geschreven")
        async with self.session.request(method, f"{self.base}/api/{path}", json=body) as r:
            txt = await r.text()
            if r.status >= 400:
                raise HAError(f"{method} {path}: HTTP {r.status} {txt[:300]}")
            try:
                return await r.json(content_type=None) if txt else None
            except Exception:
                return txt

    async def get_state(self, entity_id):
        try:
            return await self.rest("GET", f"states/{entity_id}")
        except HAError as e:
            if "404" in str(e):
                return None
            raise

    async def notify(self, service, title, message, actions=None, tag=None):
        data = {}
        if actions:
            data["actions"] = actions
        if tag:
            data["tag"] = tag
        await self.rest("POST", f"services/notify/{service}", {"title": title, "message": message, "data": data})
