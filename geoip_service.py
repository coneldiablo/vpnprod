#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import ipaddress
import os
import socket
from typing import Dict
from urllib.parse import urlparse

import aiohttp


COUNTRY_FLAGS = {
    "RU": "🇷🇺",
    "US": "🇺🇸",
    "DE": "🇩🇪",
    "NL": "🇳🇱",
    "FI": "🇫🇮",
    "FR": "🇫🇷",
    "GB": "🇬🇧",
    "PL": "🇵🇱",
    "TR": "🇹🇷",
    "SG": "🇸🇬",
    "JP": "🇯🇵",
}


def extract_host(url: str) -> str:
    parsed = urlparse(url)
    if parsed.hostname:
        return parsed.hostname
    if "://" not in url:
        parsed = urlparse(f"scheme://{url}")
        return parsed.hostname or url.split(":")[0]
    return url


async def resolve_ip(host: str) -> str:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return infos[0][4][0]


async def detect_country(host_or_url: str) -> Dict:
    host = extract_host(host_or_url)
    try:
        ip = host if _is_ip(host) else await resolve_ip(host)
    except Exception:
        ip = host

    result = {
        "host": host,
        "ip": ip,
        "country_code": "UN",
        "country_name": "Unknown",
        "flag": "🌍",
    }

    if not _is_public_ip(ip):
        return result

    provider_url = os.getenv("GEOIP_PROVIDER_URL", "http://ip-api.com/json/{ip}?fields=status,country,countryCode")
    if not provider_url:
        return result

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(provider_url.format(ip=ip), timeout=8) as response:
                if response.status != 200:
                    return result
                data = await response.json()
                if data.get("status") and data.get("status") != "success":
                    return result
                code = (data.get("countryCode") or "UN").upper()
                result.update({
                    "country_code": code,
                    "country_name": data.get("country") or "Unknown",
                    "flag": COUNTRY_FLAGS.get(code, "🌍"),
                })
    except Exception:
        return result

    return result


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _is_public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
        return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)
    except ValueError:
        return False
