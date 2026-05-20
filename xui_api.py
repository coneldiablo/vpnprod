#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import aiohttp
import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def device_limit() -> int:
    try:
        return max(0, int(os.getenv("DEVICE_LIMIT", "3")))
    except ValueError:
        return 3


def client_flow() -> str:
    return os.getenv("X_UI_CLIENT_FLOW", "").strip()


class XUIApi:
    def __init__(self, base_url: str, username: str, password: str):
        if not base_url:
            raise ValueError("XUI base_url is required")
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = None
        self.cookies = None

    async def _get_session(self):
        if not self.session:
            timeout = aiohttp.ClientTimeout(total=25)
            self.session = aiohttp.ClientSession(timeout=timeout)
        return self.session

    async def login(self) -> bool:
        session = await self._get_session()
        async with session.post(
            f"{self.base_url}/login",
            data={"username": self.username, "password": self.password},
        ) as response:
            text = await response.text()
            if response.status != 200:
                logger.error("3X-UI login failed: status=%s body=%s", response.status, text[:160])
                return False
            self.cookies = response.cookies
            return True

    async def _ensure_login(self):
        if not self.cookies:
            ok = await self.login()
            if not ok:
                raise RuntimeError("3X-UI login failed")

    async def get_inbounds(self) -> List[Dict]:
        await self._ensure_login()
        session = await self._get_session()
        async with session.get(f"{self.base_url}/panel/api/inbounds/list", cookies=self.cookies) as response:
            text = await response.text()
            if response.status in (401, 403):
                await self.login()
                async with session.get(f"{self.base_url}/panel/api/inbounds/list", cookies=self.cookies) as retry:
                    text = await retry.text()
                    response_status = retry.status
            else:
                response_status = response.status
            if response_status != 200:
                raise RuntimeError(f"3X-UI inbounds request failed: {response_status} {text[:160]}")
            data = json.loads(text)
            return data.get("obj", []) or []

    async def get_inbound(self, inbound_id: int) -> Optional[Dict]:
        inbounds = await self.get_inbounds()
        return next((item for item in inbounds if int(item.get("id")) == int(inbound_id)), None)

    async def create_client(self, inbound_id: int, email: str,
                            traffic_limit_gb: int, expiry_days: int,
                            tg_id: str = "", sub_id: str = "") -> Dict:
        expiry_time = int((datetime.utcnow() + timedelta(days=expiry_days)).timestamp() * 1000)
        return await self.create_client_until(
            inbound_id=inbound_id,
            email=email,
            traffic_limit_bytes=int(traffic_limit_gb) * 1024 ** 3,
            expiry_time_ms=expiry_time,
            tg_id=tg_id,
            sub_id=sub_id,
        )

    async def create_client_until(self, inbound_id: int, email: str,
                                  traffic_limit_bytes: int, expiry_time_ms: int,
                                  tg_id: str = "", sub_id: str = "") -> Dict:
        await self._ensure_login()
        session = await self._get_session()
        client_id = str(uuid.uuid4())
        client_data = {
            "id": client_id,
            "email": email,
            "enable": True,
            "flow": client_flow(),
            "limitIp": device_limit(),
            "totalGB": int(traffic_limit_bytes),
            "expiryTime": int(expiry_time_ms),
            "tgId": tg_id or "",
            "subId": sub_id or "",
        }
        form_data = aiohttp.FormData()
        form_data.add_field("id", str(inbound_id))
        form_data.add_field("settings", json.dumps({"clients": [client_data]}, ensure_ascii=False))

        async with session.post(
            f"{self.base_url}/panel/api/inbounds/addClient",
            data=form_data,
            cookies=self.cookies,
        ) as response:
            text = await response.text()
            if response.status != 200:
                raise RuntimeError(f"3X-UI addClient failed: {response.status} {text[:160]}")
            data = json.loads(text or "{}")
            if not data.get("success"):
                raise RuntimeError(f"3X-UI addClient rejected: {data.get('msg', 'unknown error')}")
            return {"client_id": client_id, "email": email}

    async def get_client_stats(self, email_or_client_id: str) -> Optional[Dict]:
        inbounds = await self.get_inbounds()
        for inbound in inbounds:
            for stat in inbound.get("clientStats", []) or []:
                if stat.get("email") == email_or_client_id or stat.get("id") == email_or_client_id:
                    return {
                        "up": int(stat.get("up") or 0),
                        "down": int(stat.get("down") or 0),
                        "total": int(stat.get("total") or stat.get("totalGB") or 0),
                        "expiryTime": stat.get("expiryTime"),
                        "enable": bool(stat.get("enable", True)),
                    }

            settings = _safe_json(inbound.get("settings"), {})
            for client in settings.get("clients", []) or []:
                if client.get("email") == email_or_client_id or client.get("id") == email_or_client_id:
                    return {
                        "up": int(client.get("up") or 0),
                        "down": int(client.get("down") or 0),
                        "total": int(client.get("totalGB") or 0),
                        "expiryTime": client.get("expiryTime"),
                        "enable": bool(client.get("enable", True)),
                    }
        return None

    async def set_client_enabled(self, inbound_id: int, client_id: str, enabled: bool) -> bool:
        return await self.update_client(inbound_id, client_id, enable=enabled)

    async def update_client(self, inbound_id: int, client_id: str, *,
                            enable: bool = None,
                            expiry_time_ms: int = None,
                            traffic_limit_bytes: int = None) -> bool:
        await self._ensure_login()
        inbound = await self.get_inbound(inbound_id)
        if not inbound:
            return False
        settings = _safe_json(inbound.get("settings"), {})
        clients = settings.get("clients", []) or []
        target = next((client for client in clients if client.get("id") == client_id), None)
        if not target:
            return False
        if enable is not None:
            target["enable"] = enable
        if expiry_time_ms is not None:
            target["expiryTime"] = int(expiry_time_ms)
        if traffic_limit_bytes is not None:
            target["totalGB"] = int(traffic_limit_bytes)
        target["limitIp"] = device_limit()

        session = await self._get_session()
        form_data = aiohttp.FormData()
        form_data.add_field("id", str(inbound_id))
        form_data.add_field("settings", json.dumps({"clients": [target]}, ensure_ascii=False))
        async with session.post(
            f"{self.base_url}/panel/api/inbounds/updateClient/{client_id}",
            data=form_data,
            cookies=self.cookies,
        ) as response:
            data = await response.json(content_type=None)
            return bool(data.get("success"))

    async def disable_client(self, inbound_id: int, client_id: str) -> bool:
        return await self.set_client_enabled(inbound_id, client_id, False)

    async def enable_client(self, inbound_id: int, client_id: str) -> bool:
        return await self.set_client_enabled(inbound_id, client_id, True)

    async def delete_client(self, inbound_id: int, client_id: str) -> bool:
        await self._ensure_login()
        session = await self._get_session()
        async with session.post(
            f"{self.base_url}/panel/api/inbounds/{inbound_id}/delClient/{client_id}",
            cookies=self.cookies,
        ) as response:
            data = await response.json(content_type=None)
            return bool(data.get("success"))

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None


def _safe_json(value, default):
    if not value:
        return default
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default
