#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import base64
import html
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import quote

import aiohttp
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from database import Database
from payment_service import PaymentService
from vpn_service import VpnService

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="VPN Subscription Server")
db = Database()
payments = PaymentService(db)
vpn = VpnService(db)

SERVER_NAME = os.getenv("SERVER_NAME", "VPN Service")
SUPPORT_URL = os.getenv("SUPPORT_URL") or os.getenv("SUPPORT_ADMIN_URL", "")
BOT_USERNAME = (os.getenv("BOT_USERNAME") or os.getenv("TELEGRAM_BOT_USERNAME") or "your_bot").lstrip("@")
PUBLIC_BASE_URL = (
    os.getenv("PUBLIC_BASE_URL")
    or os.getenv("PROFILE_WEB_URL")
    or os.getenv("SUBSCRIPTION_SERVER_URL")
    or f"http://{os.getenv('SERVER_IP', '127.0.0.1')}:8000"
).rstrip("/")
WEBHOOK_SECRET = os.getenv("PAYMENT_WEBHOOK_SECRET", "").strip()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
HAPP_CRYPTO_API_URL = os.getenv("HAPP_CRYPTO_API_URL", "https://crypto.happ.su/api-v2.php").strip()


def format_traffic(value: int) -> str:
    value = int(value or 0)
    if value < 1024:
        return f"{value} B"
    if value < 1024 ** 2:
        return f"{value / 1024:.1f} KB"
    if value < 1024 ** 3:
        return f"{value / 1024 ** 2:.1f} MB"
    return f"{value / 1024 ** 3:.2f} GB"


def is_subscription_active(subscription: Dict) -> bool:
    try:
        return (
            subscription.get("status") == "active"
            and not subscription.get("is_blocked")
            and datetime.fromisoformat(subscription["end_date"]) > datetime.utcnow()
        )
    except Exception:
        return False


def profile_title(subscription: Dict) -> str:
    if subscription.get("username"):
        return f"{SERVER_NAME} · @{subscription['username']}"
    if subscription.get("first_name"):
        return f"{SERVER_NAME} · {subscription['first_name']}"
    return SERVER_NAME


def b64_text(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def subscription_url(subscription_key: str) -> str:
    return f"{PUBLIC_BASE_URL}/sub/{subscription_key}"


def device_subscription_url(device_key: str) -> str:
    return f"{PUBLIC_BASE_URL}/sub/device/{device_key}"


def hiddify_import_url(subscription_key: str, title: str) -> str:
    return f"hiddify://import/{subscription_url(subscription_key)}#{quote(title)}"


async def happ_crypto_url(subscription_key: str) -> Optional[str]:
    if not HAPP_CRYPTO_API_URL:
        return None
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
            async with session.post(HAPP_CRYPTO_API_URL, json={"url": subscription_url(subscription_key)}) as response:
                if response.status != 200:
                    logger.warning("Happ crypto API returned status=%s", response.status)
                    return None
                payload = await response.json(content_type=None)
                encrypted = payload.get("encrypted_link") if isinstance(payload, dict) else None
                if encrypted and encrypted.startswith("happ://crypt"):
                    return encrypted
    except Exception:
        logger.exception("Happ crypto link generation failed")
    return None


def dummy_client_urls() -> List[Dict]:
    names = ["Подписка истекла", "Продлите доступ", f"{SERVER_NAME} · @{BOT_USERNAME}"]
    urls = []
    for index, name in enumerate(names, start=1):
        urls.append({
            "config_url": (
                f"vless://00000000-0000-4000-8000-00000000000{index}"
                f"@127.0.0.1:9?type=tcp&security=none&encryption=none#{quote(name)}"
            ),
            "server_name": name,
            "country_name": "Service",
            "country_code": "UN",
            "traffic_up": 0,
            "traffic_down": 0,
            "traffic_limit": 0,
            "status": "inactive",
        })
    return urls


def request_meta(request: Optional[Request]) -> tuple[str, str]:
    if not request:
        return "", ""
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",", 1)[0].strip() if forwarded else (request.client.host if request.client else "")
    return ip, request.headers.get("user-agent", "")


def subscription_payload(subscription_key: str = None, device_key: str = None,
                         request: Optional[Request] = None) -> Dict:
    device = None
    if device_key:
        device = db.get_subscription_device_by_key(device_key)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")
        subscription = db.get_subscription_by_key(device["subscription_key"])
    else:
        subscription = db.get_subscription_by_key(subscription_key)
        if subscription:
            device = db.ensure_primary_device(subscription)

    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")

    clients = db.list_clients_for_subscription(subscription["id"], active_only=True)
    end_date = datetime.fromisoformat(subscription["end_date"])
    used = sum(int(client["traffic_up"] or 0) + int(client["traffic_down"] or 0) for client in clients)
    total = sum(int(client["traffic_limit"] or 0) for client in clients) or int(subscription.get("traffic_limit") or 0)
    days_left = max(0, (end_date - datetime.utcnow()).days)
    device_active = bool(device and device.get("status") == "active")
    active = is_subscription_active(subscription) and device_active
    if device:
        ip, user_agent = request_meta(request)
        db.mark_device_seen(device["id"], ip, user_agent)

    return {
        "subscription": subscription,
        "device": device,
        "clients": clients if active else dummy_client_urls(),
        "end_date": end_date,
        "days_left": days_left,
        "active": active,
        "used": used,
        "total": total,
    }


@app.get("/")
async def root():
    return HTMLResponse(f"""
    <!doctype html>
    <html lang="ru">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>{html.escape(SERVER_NAME)}</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; margin: 0; background: #101418; color: #fff; }}
            main {{ max-width: 720px; margin: 0 auto; padding: 48px 20px; }}
            a {{ color: #7dd3fc; }}
            .box {{ border: 1px solid #263241; border-radius: 8px; padding: 24px; background: #151b22; }}
        </style>
    </head>
    <body>
        <main>
            <div class="box">
                <h1>{html.escape(SERVER_NAME)}</h1>
                <p>Subscription API работает. Управление подпиской доступно в Telegram-боте.</p>
                <p><a href="{html.escape(SUPPORT_URL)}">Поддержка</a></p>
            </div>
        </main>
    </body>
    </html>
    """)


@app.get("/health")
async def health():
    stats = db.get_stats()
    return {"status": "ok", "service": SERVER_NAME, "active_subscriptions": stats["active_subs"], "active_servers": stats["active_servers"]}


@app.get("/sub/{subscription_key}")
async def get_subscription(subscription_key: str, request: Request):
    data = subscription_payload(subscription_key=subscription_key, request=request)
    return build_subscription_response(data, subscription_key)


@app.get("/sub/device/{device_key}")
async def get_device_subscription(device_key: str, request: Request):
    data = subscription_payload(device_key=device_key, request=request)
    return build_subscription_response(data, data["subscription"]["subscription_key"], device_key=device_key)


def build_subscription_response(data: Dict, subscription_key: str, device_key: str = None) -> Response:
    subscription = data["subscription"]
    clients = data["clients"]
    end_date = data["end_date"]

    if data["active"]:
        announce = f"✅ Подписка активна до {end_date.strftime('%d.%m.%Y')}. Осталось {data['days_left']} дн."
    else:
        announce = "⚠️ Подписка неактивна. Продлите доступ в Telegram-боте."
    title = profile_title(subscription)

    lines = [
        f"#profile-title: base64:{b64_text(title)}",
        "#profile-update-interval: 6",
        f"#subscription-userinfo: upload=0; download={data['used']}; total={data['total']}; expire={int(end_date.timestamp())}",
        f"#support-url: {SUPPORT_URL}",
        f"#profile-web-page-url: {PUBLIC_BASE_URL}/info/{subscription_key}/html",
        f"#announce: base64:{b64_text(announce)}",
        "",
    ]
    lines.extend(client["config_url"] for client in clients)
    body = "\n".join(lines) + "\n"
    encoded = base64.b64encode(body.encode("utf-8")).decode("ascii")
    headers = {
        "profile-title": f"base64:{b64_text(title)}",
        "profile-update-interval": "6",
        "subscription-userinfo": f"upload=0; download={data['used']}; total={data['total']}; expire={int(end_date.timestamp())}",
        "support-url": SUPPORT_URL,
        "profile-web-page-url": f"{PUBLIC_BASE_URL}/info/{subscription_key}/html",
    }
    return Response(content=encoded, media_type="text/plain; charset=utf-8", headers=headers)


@app.get("/open/{subscription_key}")
async def open_subscription(subscription_key: str):
    data = subscription_payload(subscription_key=subscription_key)
    if not data["active"]:
        return expired_subscription_page(data)
    title = profile_title(data["subscription"])
    sub_url = subscription_url(subscription_key)
    hiddify_url = hiddify_import_url(subscription_key, title)
    happ_url = await happ_crypto_url(subscription_key)
    happ_href = happ_url or sub_url
    happ_button_text = "Открыть в HAPP" if happ_url else "Открыть ссылку подписки"
    auto_redirect = f'window.location.href = "{html.escape(happ_url)}";' if happ_url else ""
    return HTMLResponse(f"""
    <!doctype html>
    <html lang="ru">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Открыть {html.escape(SERVER_NAME)}</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; margin: 0; background: #07111f; color: #fff; }}
            main {{ max-width: 560px; margin: 0 auto; padding: 32px 18px; }}
            a, button {{ display: block; width: 100%; box-sizing: border-box; margin: 10px 0; padding: 14px 16px; border-radius: 8px; border: 1px solid #1e88ff; background: #0b63ce; color: #fff; text-align: center; text-decoration: none; font-size: 16px; }}
            button {{ cursor: pointer; }}
            .secondary {{ background: transparent; color: #9fd0ff; }}
            .box {{ margin-top: 18px; padding: 14px; border-radius: 8px; background: #0d1b2d; word-break: break-all; color: #cfe6ff; }}
            p {{ color: #cbd5e1; line-height: 1.45; }}
        </style>
    </head>
    <body>
        <main>
            <h1>{html.escape(title)}</h1>
            <p>Если приложение не открылось автоматически, нажмите кнопку ниже или скопируйте ссылку и добавьте ее в HAPP/Hub через плюс.</p>
            <a href="{html.escape(happ_href)}">{happ_button_text}</a>
            <a href="{html.escape(hiddify_url)}">Открыть в Hub/Hiddify</a>
            <button class="secondary" onclick="navigator.clipboard.writeText('{html.escape(sub_url)}').then(() => this.textContent='Ссылка скопирована')">Скопировать ссылку</button>
            <div class="box">{html.escape(sub_url)}</div>
        </main>
        <script>
            setTimeout(function() {{
                {auto_redirect}
            }}, 300);
        </script>
    </body>
    </html>
    """)


@app.get("/open/device/{device_key}")
async def open_device_subscription(device_key: str):
    data = subscription_payload(device_key=device_key)
    if not data["active"]:
        return expired_subscription_page(data)
    title = profile_title(data["subscription"])
    sub_url = device_subscription_url(device_key)
    hiddify_url = f"hiddify://import/{sub_url}#{quote(title)}"
    happ_url = await happ_crypto_url_for_url(sub_url)
    happ_href = happ_url or sub_url
    happ_button_text = "Открыть в HAPP" if happ_url else "Открыть ссылку подписки"
    auto_redirect = f'window.location.href = "{html.escape(happ_url)}";' if happ_url else ""
    return HTMLResponse(f"""
    <!doctype html>
    <html lang="ru">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Открыть {html.escape(SERVER_NAME)}</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; margin: 0; background: #07111f; color: #fff; }}
            main {{ max-width: 560px; margin: 0 auto; padding: 32px 18px; }}
            a, button {{ display: block; width: 100%; box-sizing: border-box; margin: 10px 0; padding: 14px 16px; border-radius: 8px; border: 1px solid #1e88ff; background: #0b63ce; color: #fff; text-align: center; text-decoration: none; font-size: 16px; }}
            button {{ cursor: pointer; }}
            .secondary {{ background: transparent; color: #9fd0ff; }}
            .box {{ margin-top: 18px; padding: 14px; border-radius: 8px; background: #0d1b2d; word-break: break-all; color: #cfe6ff; }}
            p {{ color: #cbd5e1; line-height: 1.45; }}
        </style>
    </head>
    <body>
        <main>
            <h1>{html.escape(title)}</h1>
            <p>Если приложение не открылось автоматически, нажмите кнопку ниже или скопируйте ссылку и добавьте ее в HAPP/Hub через плюс.</p>
            <a href="{html.escape(happ_href)}">{happ_button_text}</a>
            <a href="{html.escape(hiddify_url)}">Открыть в Hub/Hiddify</a>
            <button class="secondary" onclick="navigator.clipboard.writeText('{html.escape(sub_url)}').then(() => this.textContent='Ссылка скопирована')">Скопировать ссылку</button>
            <div class="box">{html.escape(sub_url)}</div>
        </main>
        <script>
            setTimeout(function() {{
                {auto_redirect}
            }}, 300);
        </script>
    </body>
    </html>
    """)


async def happ_crypto_url_for_url(url: str) -> Optional[str]:
    if not HAPP_CRYPTO_API_URL:
        return None
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
            async with session.post(HAPP_CRYPTO_API_URL, json={"url": url}) as response:
                if response.status != 200:
                    return None
                payload = await response.json(content_type=None)
                encrypted = payload.get("encrypted_link") if isinstance(payload, dict) else None
                if encrypted and encrypted.startswith("happ://crypt"):
                    return encrypted
    except Exception:
        logger.exception("Happ crypto link generation failed")
    return None


def expired_subscription_page(data: Dict) -> HTMLResponse:
    subscription = data["subscription"]
    title = profile_title(subscription)
    bot_url = f"https://t.me/{BOT_USERNAME}"
    return HTMLResponse(f"""
    <!doctype html>
    <html lang="ru">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Подписка истекла</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; margin: 0; background: #07111f; color: #fff; }}
            main {{ max-width: 560px; margin: 0 auto; padding: 36px 18px; }}
            .card {{ border: 1px solid #14335a; background: #0d1b2d; border-radius: 8px; padding: 22px; }}
            a {{ display: block; width: 100%; box-sizing: border-box; margin: 14px 0 0; padding: 14px 16px; border-radius: 8px; background: #0b63ce; color: #fff; text-align: center; text-decoration: none; }}
            p {{ color: #cbd5e1; line-height: 1.45; }}
        </style>
    </head>
    <body>
        <main>
            <section class="card">
                <h1>{html.escape(title)}</h1>
                <p>Подписка истекла или доступ отозван. Продлите доступ в Telegram-боте, после этого серверы снова появятся в подписке.</p>
                <a href="{html.escape(bot_url)}">Открыть Telegram-бота</a>
            </section>
        </main>
    </body>
    </html>
    """)


@app.get("/info/{subscription_key}")
async def get_subscription_info(subscription_key: str):
    data = subscription_payload(subscription_key)
    subscription = data["subscription"]
    return JSONResponse({
        "service": SERVER_NAME,
        "active": data["active"],
        "status": subscription["status"],
        "user": {
            "telegram_id": subscription["telegram_id"],
            "username": subscription.get("username"),
            "name": subscription.get("first_name"),
        },
        "subscription": {
            "end_date": data["end_date"].strftime("%Y-%m-%d"),
            "days_left": data["days_left"],
            "url": subscription_url(subscription_key),
        },
        "traffic": {
            "used": format_traffic(data["used"]),
            "used_bytes": data["used"],
            "limit": format_traffic(data["total"]),
            "limit_bytes": data["total"],
            "percentage": round(data["used"] / data["total"] * 100, 2) if data["total"] else 0,
        },
        "servers": [
            {
                "name": client["server_name"],
                "country": client["country_name"],
                "traffic_used": format_traffic(int(client["traffic_up"] or 0) + int(client["traffic_down"] or 0)),
                "traffic_used_bytes": int(client["traffic_up"] or 0) + int(client["traffic_down"] or 0),
                "traffic_limit": format_traffic(client["traffic_limit"]),
                "status": client["status"],
            }
            for client in data["clients"]
        ],
        "support_url": SUPPORT_URL,
    })


@app.get("/info/{subscription_key}/html")
async def get_subscription_info_html(subscription_key: str):
    data = subscription_payload(subscription_key)
    subscription = data["subscription"]
    percent = round(data["used"] / data["total"] * 100, 2) if data["total"] else 0
    server_rows = "".join(
        f"""
        <div class="row">
            <strong>{html.escape(client['server_name'])}</strong>
            <span>{format_traffic(int(client['traffic_up'] or 0) + int(client['traffic_down'] or 0))} / {format_traffic(client['traffic_limit'])}</span>
        </div>
        """
        for client in data["clients"]
    )
    status = "Активна" if data["active"] else "Неактивна"
    return HTMLResponse(f"""
    <!doctype html>
    <html lang="ru">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>{html.escape(SERVER_NAME)} · Подписка</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; margin: 0; background: #f5f7fa; color: #111827; }}
            main {{ max-width: 720px; margin: 0 auto; padding: 28px 16px; }}
            .card {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 20px; margin-bottom: 14px; }}
            .status {{ display: inline-block; padding: 6px 10px; border-radius: 6px; background: {'#dcfce7' if data['active'] else '#fee2e2'}; }}
            .bar {{ height: 16px; background: #e5e7eb; border-radius: 999px; overflow: hidden; }}
            .fill {{ height: 100%; width: {min(percent, 100)}%; background: #2563eb; }}
            .row {{ display: flex; justify-content: space-between; gap: 16px; padding: 10px 0; border-top: 1px solid #eef2f7; }}
            a {{ color: #2563eb; }}
        </style>
    </head>
    <body>
        <main>
            <section class="card">
                <h1>{html.escape(SERVER_NAME)}</h1>
                <p class="status">{status}</p>
                <p>Пользователь: {html.escape(subscription.get('first_name') or 'VPN user')}</p>
                <p>Действует до: {data['end_date'].strftime('%d.%m.%Y')} · осталось {data['days_left']} дн.</p>
            </section>
            <section class="card">
                <h2>Трафик</h2>
                <p>{format_traffic(data['used'])} / {format_traffic(data['total'])}</p>
                <div class="bar"><div class="fill"></div></div>
            </section>
            <section class="card">
                <h2>Серверы</h2>
                {server_rows or '<p>Ключи еще не выпущены.</p>'}
            </section>
            <section class="card">
                <a href="{html.escape(SUPPORT_URL)}">Поддержка</a>
            </section>
        </main>
    </body>
    </html>
    """)


@app.get("/pay/return/{local_payment_id}")
async def payment_return(local_payment_id: int):
    try:
        result = await payments.verify_local_payment(local_payment_id)
    except Exception:
        logger.exception("Payment return verification failed")
        return payment_return_page(
            title="Не удалось проверить оплату",
            message="Вернитесь в бот и нажмите «Проверить оплату». Если деньги списались, напишите в поддержку.",
            status="error",
        )

    if not result.get("ok"):
        return payment_return_page(
            title="Платеж не найден",
            message="Вернитесь в бот и создайте новый платеж.",
            status="error",
        )

    if result.get("activated") and result.get("subscription"):
        try:
            await vpn.provision_subscription(result["subscription"])
        except Exception:
            logger.exception("Provisioning after payment return failed")
        await notify_subscription_ready(result["subscription"])
        await notify_referral_bonus_ready(result.get("referral_bonus"))

    status = result.get("status")
    subscription = result.get("subscription")
    if status == "succeeded" and subscription:
        open_url = f"{PUBLIC_BASE_URL}/open/{subscription['subscription_key']}"
        sub_url = subscription_url(subscription["subscription_key"])
        return payment_return_page(
            title="Оплата прошла",
            message="Подписка активирована. Откройте ее в HAPP/Hub или скопируйте ссылку.",
            status="success",
            primary_url=open_url,
            primary_text="Открыть в HAPP/Hub",
            copy_text=sub_url,
        )

    if status == "canceled":
        return payment_return_page(
            title="Платеж отменен",
            message="Подписка не выдана. Можно вернуться в бот и оплатить заново.",
            status="warning",
        )

    return payment_return_page(
        title="Оплата еще проверяется",
        message="Если вы уже оплатили, вернитесь в бот и нажмите «Проверить оплату» через несколько секунд.",
        status="pending",
    )


def payment_return_page(
    title: str,
    message: str,
    status: str,
    primary_url: str = "",
    primary_text: str = "",
    copy_text: str = "",
) -> HTMLResponse:
    colors = {
        "success": "#22c55e",
        "pending": "#38bdf8",
        "warning": "#f59e0b",
        "error": "#ef4444",
    }
    accent = colors.get(status, "#38bdf8")
    primary_button = (
        f'<a class="primary" href="{html.escape(primary_url)}">{html.escape(primary_text or "Открыть")}</a>'
        if primary_url else ""
    )
    copy_block = (
        f"""
        <button onclick="navigator.clipboard.writeText('{html.escape(copy_text)}').then(() => this.textContent='Ссылка скопирована')">Скопировать ссылку</button>
        <div class="box">{html.escape(copy_text)}</div>
        """
        if copy_text else ""
    )
    return HTMLResponse(f"""
    <!doctype html>
    <html lang="ru">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>{html.escape(title)}</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; margin: 0; background: #07111f; color: #fff; }}
            main {{ max-width: 560px; margin: 0 auto; padding: 36px 18px; }}
            .card {{ border: 1px solid #14335a; background: #0d1b2d; border-radius: 8px; padding: 22px; }}
            .badge {{ display: inline-block; width: 14px; height: 14px; border-radius: 50%; background: {accent}; margin-right: 8px; }}
            p {{ color: #cbd5e1; line-height: 1.45; }}
            a, button {{ display: block; width: 100%; box-sizing: border-box; margin: 10px 0; padding: 14px 16px; border-radius: 8px; border: 1px solid #1e88ff; background: #0b63ce; color: #fff; text-align: center; text-decoration: none; font-size: 16px; }}
            button {{ cursor: pointer; background: transparent; color: #9fd0ff; }}
            .box {{ margin-top: 12px; padding: 14px; border-radius: 8px; background: #071525; word-break: break-all; color: #cfe6ff; }}
        </style>
    </head>
    <body>
        <main>
            <section class="card">
                <h1><span class="badge"></span>{html.escape(title)}</h1>
                <p>{html.escape(message)}</p>
                {primary_button}
                {copy_block}
            </section>
        </main>
    </body>
    </html>
    """)


@app.post("/webhook/payment")
@app.post("/webhook/payment/{path_secret}")
async def payment_webhook(request: Request, path_secret: Optional[str] = None, secret: str = Query(default="")):
    if WEBHOOK_SECRET:
        received = path_secret or secret or request.headers.get("X-Webhook-Secret", "")
        if received != WEBHOOK_SECRET:
            raise HTTPException(status_code=403, detail="Bad webhook secret")

    payload = await request.json()
    result = await payments.handle_webhook(payload)
    if result.get("activated") and result.get("subscription"):
        try:
            await vpn.provision_subscription(result["subscription"])
        except Exception:
            logger.exception("Provisioning after payment webhook failed")
        await notify_subscription_ready(result["subscription"])
        await notify_referral_bonus_ready(result.get("referral_bonus"))
    return {"ok": True}


async def notify_subscription_ready(subscription: Dict):
    if not BOT_TOKEN:
        return
    user = db.get_user_by_id(subscription["user_id"])
    if not user:
        return
    url = f"{PUBLIC_BASE_URL}/sub/{subscription['subscription_key']}"
    open_url = f"{PUBLIC_BASE_URL}/open/{subscription['subscription_key']}"
    text = (
        "✅ Оплата прошла\n\n"
        "🔗 Ваша VPN подписка готова:\n"
        f"{url}\n\n"
        "Откройте Hub/HAPP, нажмите + и добавьте эту ссылку как подписку."
    )
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": user["telegram_id"],
                    "text": text,
                    "disable_web_page_preview": True,
                    "reply_markup": {"inline_keyboard": [[{"text": "🚀 Открыть в HAPP/Hub", "url": open_url}]]},
                },
            )
    except Exception:
        logger.exception("Telegram payment notification failed")


async def notify_referral_bonus_ready(referral_bonus: Dict):
    if not BOT_TOKEN or not referral_bonus or not referral_bonus.get("ok"):
        return
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": referral_bonus["referrer_telegram_id"],
                    "text": f"🎁 Реферал оплатил подписку.\n\n+{int(referral_bonus.get('bonus_days') or 3)} дня начислены.",
                },
            )
    except Exception:
        logger.exception("Telegram referral notification failed")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8000")),
        ssl_certfile=os.getenv("API_SSL_CERTFILE") or None,
        ssl_keyfile=os.getenv("API_SSL_KEYFILE") or None,
    )
