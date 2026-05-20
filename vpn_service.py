#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import base64
import json
import os
import secrets
from datetime import datetime
from typing import Dict, List
from urllib.parse import quote, unquote, urlparse, urlunparse

from database import Database
from geoip_service import detect_country
from security import decrypt_secret, encrypt_secret
from xui_api import XUIApi


class VpnService:
    def __init__(self, db: Database):
        self.db = db

    async def ensure_default_server_from_env(self):
        if self.db.list_servers():
            return
        xui_url = os.getenv("X_UI_URL")
        xui_user = os.getenv("X_UI_USERNAME")
        xui_pass = os.getenv("X_UI_PASSWORD")
        if not (xui_url and xui_user and xui_pass):
            return

        server_ip = os.getenv("SERVER_IP") or parse_config_url(xui_url)["host"] or "127.0.0.1"
        inbound_id = int(os.getenv("DEFAULT_INBOUND_ID", "1"))
        template = os.getenv(
            "DEFAULT_SERVER_CONFIG_URL",
            f"vless://00000000-0000-0000-0000-000000000000@{server_ip}:443?type=tcp&security=none&encryption=none#VPN",
        )
        await self.add_server(
            config_url=template,
            xui_url=xui_url,
            xui_username=xui_user,
            xui_password=xui_pass,
            inbound_id=inbound_id,
        )

    async def add_server(self, config_url: str, xui_url: str, xui_username: str,
                         xui_password: str, inbound_id: int) -> int:
        parsed = parse_config_url(config_url)
        xui = XUIApi(xui_url.strip(), xui_username.strip(), xui_password.strip())
        try:
            inbound = await xui.get_inbound(int(inbound_id))
            if not inbound:
                raise ValueError(f"Inbound {inbound_id} not found in 3X-UI")
        finally:
            await xui.close()

        geo = await detect_country(parsed["host"] or xui_url)
        country_code = geo["country_code"]
        country_name = geo["country_name"]
        flag = geo["flag"]
        existing_same_country = [
            server for server in self.db.list_servers()
            if server.get("country_code") == country_code
        ]
        number = len(existing_same_country) + 1
        name = f"{flag} {country_name} #{number}" if country_name != "Unknown" else f"{flag} Server #{number}"
        server_id = self.db.add_server(
            name=name,
            country_code=country_code,
            country_name=country_name,
            flag=flag,
            host=parsed["host"] or geo["host"],
            port=parsed["port"],
            protocol=parsed["protocol"],
            config_url=config_url.strip(),
            xui_url=xui_url.strip(),
            xui_username=xui_username.strip(),
            xui_password_encrypted=encrypt_secret(xui_password.strip()),
            inbound_id=int(inbound_id),
        )
        await self.provision_server_for_active_subscriptions(server_id)
        return server_id

    async def add_static_server(self, config_url: str) -> int:
        parsed = parse_config_url(config_url)
        geo = await detect_country(parsed["host"] or "")
        country_code = geo["country_code"]
        country_name = geo["country_name"]
        flag = geo["flag"]
        existing_same_country = [
            server for server in self.db.list_servers()
            if server.get("country_code") == country_code
        ]
        number = len(existing_same_country) + 1
        name = f"{flag} {country_name} #{number}" if country_name != "Unknown" else f"{flag} Server #{number}"
        server_id = self.db.add_server(
            name=name,
            country_code=country_code,
            country_name=country_name,
            flag=flag,
            host=parsed["host"] or geo["host"],
            port=parsed["port"],
            protocol=parsed["protocol"],
            config_url=config_url.strip(),
            inbound_id=0,
        )
        await self.provision_server_for_active_subscriptions(server_id)
        return server_id

    async def provision_subscription(self, subscription: Dict) -> List[Dict]:
        user = self.db.get_user_by_id(subscription["user_id"])
        if not user or user.get("is_blocked"):
            return []

        existing = {
            client["server_id"]: client
            for client in self.db.list_clients_for_subscription(subscription["id"], active_only=False)
            if client.get("status") in ("active", "disabled")
        }
        provisioned = []
        for server in self.db.list_servers(active_only=True):
            if server["id"] in existing:
                await self._refresh_existing_client(subscription, server, existing[server["id"]])
                continue
            provisioned.append(await self._provision_client(subscription, user, server))
        return provisioned

    async def provision_server_for_active_subscriptions(self, server_id: int) -> List[Dict]:
        server = self.db.get_server(server_id)
        if not server or server["status"] != "active":
            return []
        provisioned = []
        for subscription in self.db.list_active_subscriptions():
            clients = self.db.list_clients_for_subscription(subscription["id"], active_only=False)
            if any(client["server_id"] == server_id for client in clients):
                continue
            user = self.db.get_user_by_id(subscription["user_id"])
            if user:
                provisioned.append(await self._provision_client(subscription, user, server))
        return provisioned

    async def _provision_client(self, subscription: Dict, user: Dict, server: Dict) -> Dict:
        if is_static_server(server):
            return self._provision_static_client(subscription, user, server)

        xui = XUIApi(server["xui_url"], server["xui_username"], decrypt_secret(server["xui_password_encrypted"]))
        try:
            end_date = datetime.fromisoformat(subscription["end_date"])
            expiry_ms = int(end_date.timestamp() * 1000)
            traffic_limit = int(subscription.get("traffic_limit") or 0)
            email = f"user_{user['telegram_id']}_{server['id']}_{subscription['id']}"
            client = await xui.create_client_until(
                inbound_id=int(server["inbound_id"]),
                email=email,
                traffic_limit_bytes=traffic_limit,
                expiry_time_ms=expiry_ms,
                tg_id=str(user["telegram_id"]),
                sub_id=subscription["subscription_key"] or "",
            )
            remark = server["name"]
            config_url = personalize_config_url(server["config_url"], client["client_id"], remark)
            self.db.upsert_server_client(
                user_id=user["id"],
                subscription_id=subscription["id"],
                server_id=server["id"],
                xui_client_id=client["client_id"],
                xui_email=email,
                config_url=config_url,
                traffic_limit=traffic_limit,
            )
            return {"server_id": server["id"], "client_id": client["client_id"], "config_url": config_url}
        finally:
            await xui.close()

    def _provision_static_client(self, subscription: Dict, user: Dict, server: Dict) -> Dict:
        traffic_limit = int(subscription.get("traffic_limit") or 0)
        static_id = f"static_{server['id']}_{subscription['id']}_{secrets.token_hex(4)}"
        email = f"static_{user['telegram_id']}_{server['id']}_{subscription['id']}"
        config_url = set_config_remark(server["config_url"], server["name"])
        self.db.upsert_server_client(
            user_id=user["id"],
            subscription_id=subscription["id"],
            server_id=server["id"],
            xui_client_id=static_id,
            xui_email=email,
            config_url=config_url,
            traffic_limit=traffic_limit,
        )
        return {"server_id": server["id"], "client_id": static_id, "config_url": config_url}

    async def _refresh_existing_client(self, subscription: Dict, server: Dict, client: Dict):
        if is_static_server(server):
            traffic_limit = int(subscription.get("traffic_limit") or client.get("traffic_limit") or 0)
            self.db.upsert_server_client(
                user_id=subscription["user_id"],
                subscription_id=subscription["id"],
                server_id=server["id"],
                xui_client_id=client["xui_client_id"],
                xui_email=client["xui_email"],
                config_url=set_config_remark(server["config_url"], server["name"]),
                traffic_limit=traffic_limit,
            )
            return

        xui = XUIApi(server["xui_url"], server["xui_username"], decrypt_secret(server["xui_password_encrypted"]))
        try:
            end_date = datetime.fromisoformat(subscription["end_date"])
            expiry_ms = int(end_date.timestamp() * 1000)
            traffic_limit = int(subscription.get("traffic_limit") or client.get("traffic_limit") or 0)
            await xui.update_client(
                int(server["inbound_id"]),
                client["xui_client_id"],
                enable=True,
                expiry_time_ms=expiry_ms,
                traffic_limit_bytes=traffic_limit,
            )
            self.db.update_server_client_limit(client["id"], traffic_limit, "active")
        finally:
            await xui.close()

    async def sync_traffic(self):
        for server in self.db.list_servers(active_only=True):
            if is_static_server(server):
                continue
            clients = self.db.list_clients_for_server(server["id"], active_only=True)
            if not clients:
                continue
            xui = XUIApi(server["xui_url"], server["xui_username"], decrypt_secret(server["xui_password_encrypted"]))
            try:
                for client in clients:
                    stats = await xui.get_client_stats(client["xui_email"])
                    if stats:
                        self.db.update_client_traffic(
                            client["id"],
                            int(stats.get("up") or 0),
                            int(stats.get("down") or 0),
                            bool(stats.get("enable", True)),
                        )
            finally:
                await xui.close()

    async def disable_user_clients(self, user_id: int):
        for subscription in self.db.get_user_subscriptions(user_id):
            for client in self.db.list_clients_for_subscription(subscription["id"], active_only=False):
                server = self.db.get_server(client["server_id"])
                if not server:
                    continue
                if is_static_server(server):
                    continue
                xui = XUIApi(server["xui_url"], server["xui_username"], decrypt_secret(server["xui_password_encrypted"]))
                try:
                    await xui.disable_client(int(server["inbound_id"]), client["xui_client_id"])
                finally:
                    await xui.close()
        self.db.mark_client_status_by_user(user_id, "disabled")

    async def delete_server(self, server_id: int):
        server = self.db.get_server(server_id)
        if not server:
            raise ValueError("Server not found")
        if server.get("status") == "deleted":
            return

        if not is_static_server(server):
            clients = self.db.list_clients_for_server(server_id, active_only=True)
            xui = XUIApi(server["xui_url"], server["xui_username"], decrypt_secret(server["xui_password_encrypted"]))
            try:
                for client in clients:
                    await xui.disable_client(int(server["inbound_id"]), client["xui_client_id"])
            finally:
                await xui.close()
        self.db.soft_delete_server(server_id)

    async def rename_server(self, server_id: int, name: str):
        server = self.db.get_server(server_id)
        if not server or server.get("status") == "deleted":
            raise ValueError("Server not found")

        clean_name = " ".join((name or "").strip().split())
        if not clean_name:
            raise ValueError("Server name is empty")
        if len(clean_name) > 64:
            raise ValueError("Server name is too long")

        for client in self.db.list_clients_for_server(server_id, active_only=False):
            if is_static_server(server):
                config_url = set_config_remark(server["config_url"], clean_name)
            else:
                config_url = personalize_config_url(server["config_url"], client["xui_client_id"], clean_name)
            self.db.update_server_client_config(client["id"], config_url)
        self.db.update_server_name(server_id, clean_name)

    async def expire_subscriptions(self) -> List[Dict]:
        expired = self.db.get_expired_subscriptions()
        for subscription in expired:
            self.db.update_subscription_status(subscription["id"], "expired", "expired")
            for client in self.db.list_clients_for_subscription(subscription["id"], active_only=False):
                server = self.db.get_server(client["server_id"])
                if not server:
                    continue
                if is_static_server(server):
                    continue
                xui = XUIApi(server["xui_url"], server["xui_username"], decrypt_secret(server["xui_password_encrypted"]))
                try:
                    await xui.disable_client(int(server["inbound_id"]), client["xui_client_id"])
                finally:
                    await xui.close()
        return expired


def parse_config_url(config_url: str) -> Dict:
    config_url = (config_url or "").strip()
    if config_url.startswith("vmess://"):
        try:
            raw = config_url.removeprefix("vmess://")
            padded = raw + "=" * (-len(raw) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
            return {
                "protocol": "vmess",
                "host": data.get("add"),
                "port": int(data["port"]) if data.get("port") else None,
            }
        except Exception:
            return {"protocol": "vmess", "host": None, "port": None}

    parsed = urlparse(config_url)
    return {
        "protocol": parsed.scheme or "vless",
        "host": parsed.hostname,
        "port": parsed.port,
    }


def is_static_server(server: Dict) -> bool:
    return not server.get("xui_url") or int(server.get("inbound_id") or 0) <= 0


def personalize_config_url(template_url: str, client_id: str, remark: str) -> str:
    template_url = template_url.strip()
    if template_url.startswith("vmess://"):
        return _personalize_vmess(template_url, client_id, remark)

    parsed = urlparse(template_url)
    if parsed.scheme in ("vless", "trojan"):
        host = parsed.hostname or ""
        userinfo = quote(client_id)
        if parsed.port:
            netloc = f"{userinfo}@{host}:{parsed.port}"
        else:
            netloc = f"{userinfo}@{host}"
        return urlunparse((
            parsed.scheme,
            netloc,
            parsed.path,
            parsed.params,
            parsed.query,
            quote(remark),
        ))

    separator = "&" if "#" in template_url else "#"
    return f"{template_url}{separator}{quote(remark)}"


def set_config_remark(config_url: str, remark: str) -> str:
    config_url = config_url.strip()
    if config_url.startswith("vmess://"):
        return _personalize_vmess_remark(config_url, remark)

    parsed = urlparse(config_url)
    if parsed.scheme in ("vless", "trojan", "ss", "socks"):
        return urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            parsed.query,
            quote(remark),
        ))

    separator = "&" if "#" in config_url else "#"
    return f"{config_url}{separator}{quote(remark)}"


def _personalize_vmess(template_url: str, client_id: str, remark: str) -> str:
    try:
        raw = template_url.removeprefix("vmess://")
        padded = raw + "=" * (-len(raw) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        data["id"] = client_id
        data["ps"] = unquote(remark)
        encoded = base64.urlsafe_b64encode(json.dumps(data, ensure_ascii=False).encode("utf-8")).decode("ascii").rstrip("=")
        return f"vmess://{encoded}"
    except Exception:
        return template_url


def _personalize_vmess_remark(template_url: str, remark: str) -> str:
    try:
        raw = template_url.removeprefix("vmess://")
        padded = raw + "=" * (-len(raw) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        data["ps"] = unquote(remark)
        encoded = base64.urlsafe_b64encode(json.dumps(data, ensure_ascii=False).encode("utf-8")).decode("ascii").rstrip("=")
        return f"vmess://{encoded}"
    except Exception:
        return template_url
