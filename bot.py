#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict
from urllib.parse import quote_plus

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from database import Database
from payment_service import PaymentService
from vpn_service import VpnService

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

db = Database()
payments = PaymentService(db)
vpn = VpnService(db)

PROJECT_DIR = Path(__file__).resolve().parent
SERVER_NAME = os.getenv("SERVER_NAME", "VPN Service")
MAIN_MENU_IMAGE_PATH = Path(os.getenv("MAIN_MENU_IMAGE_PATH") or PROJECT_DIR / "assets" / "main-menu.png")
PRICES_MENU_IMAGE_PATH = Path(os.getenv("PRICES_MENU_IMAGE_PATH") or PROJECT_DIR / "assets" / "prices-menu.png")
VPN_MENU_IMAGE_PATH = Path(os.getenv("VPN_MENU_IMAGE_PATH") or PROJECT_DIR / "assets" / "vpn-menu.png")
HELP_MENU_IMAGE_PATH = Path(os.getenv("HELP_MENU_IMAGE_PATH") or PROJECT_DIR / "assets" / "help-menu.png")
SERVERS_MENU_IMAGE_PATH = Path(os.getenv("SERVERS_MENU_IMAGE_PATH") or PROJECT_DIR / "assets" / "servers-menu.png")

PUBLIC_BASE_URL = (
    os.getenv("PUBLIC_BASE_URL")
    or os.getenv("SUBSCRIPTION_SERVER_URL")
    or f"http://{os.getenv('SERVER_IP', '127.0.0.1')}:8000"
).rstrip("/")
SUPPORT_URL = os.getenv("SUPPORT_URL", "")
SUPPORT_ADMIN_URL = os.getenv("SUPPORT_ADMIN_URL", "https://t.me/your_admin_username")
DEVICE_LIMIT = int(os.getenv("DEVICE_LIMIT", "3"))
TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "3"))
TRIAL_TRAFFIC_GB = int(os.getenv("TRIAL_TRAFFIC_GB") or os.getenv("TRAFFIC_LIMIT_GB", "100"))
REFERRAL_BONUS_DAYS = int(os.getenv("REFERRAL_BONUS_DAYS", "3"))
REFERRAL_FREE_LIMIT = int(os.getenv("REFERRAL_FREE_LIMIT", "5"))
PAYMENT_AUTO_CHECK_INTERVAL = int(os.getenv("PAYMENT_AUTO_CHECK_INTERVAL", "20"))
PAYMENT_AUTO_CHECK_ATTEMPTS = int(os.getenv("PAYMENT_AUTO_CHECK_ATTEMPTS", "45"))


def admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_TELEGRAM_IDS") or os.getenv("ADMIN_TELEGRAM_ID", "")
    ids = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


def is_admin(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id in admin_ids())


def fmt_traffic(value: int) -> str:
    value = int(value or 0)
    if value < 1024:
        return f"{value} B"
    if value < 1024 ** 2:
        return f"{value / 1024:.1f} KB"
    if value < 1024 ** 3:
        return f"{value / 1024 ** 2:.1f} MB"
    return f"{value / 1024 ** 3:.2f} GB"


def days_left(end_date: str) -> int:
    try:
        return max(0, (datetime.fromisoformat(end_date) - datetime.utcnow()).days)
    except Exception:
        return 0


async def send_screen(update: Update, text: str, keyboard: InlineKeyboardMarkup = None, edit: bool = True):
    query = update.callback_query
    if query and edit:
        try:
            await query.edit_message_text(text, reply_markup=keyboard, disable_web_page_preview=True)
            return
        except BadRequest:
            pass
    target = update.effective_message
    await target.reply_text(text, reply_markup=keyboard, disable_web_page_preview=True)


async def send_image_screen(update: Update, image_path: Path, text: str, keyboard: InlineKeyboardMarkup):
    if not image_path.exists():
        await send_screen(update, text, keyboard)
        return

    query = update.callback_query
    if query:
        try:
            with image_path.open("rb") as image:
                await query.edit_message_media(
                    media=InputMediaPhoto(media=image, caption=text),
                    reply_markup=keyboard,
                )
            return
        except BadRequest:
            try:
                with image_path.open("rb") as image:
                    await query.message.reply_photo(photo=image, caption=text, reply_markup=keyboard)
                return
            except BadRequest:
                await send_screen(update, text, keyboard, edit=False)
                return

    target = update.effective_message
    with image_path.open("rb") as image:
        await target.reply_photo(photo=image, caption=text, reply_markup=keyboard)


async def send_main_menu(update: Update, text: str, keyboard: InlineKeyboardMarkup):
    await send_image_screen(update, MAIN_MENU_IMAGE_PATH, text, keyboard)


def user_main_keyboard(show_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🎁 3 дня бесплатно", callback_data="u:trial")],
        [InlineKeyboardButton("💳 Купить подписку", callback_data="u:buy"), InlineKeyboardButton("🔐 Мой VPN", callback_data="u:vpn")],
        [InlineKeyboardButton("🌍 Серверы", callback_data="u:servers"), InlineKeyboardButton("🎁 Пригласить", callback_data="u:referral")],
        [InlineKeyboardButton("🆘 Поддержка", callback_data="u:support"), InlineKeyboardButton("ℹ️ Инструкция", callback_data="u:help")],
    ]
    if show_admin:
        rows.append([InlineKeyboardButton("🛠 Админ-панель", callback_data="a:menu")])
    return InlineKeyboardMarkup(rows)


def nav_keyboard(back: str = "u:menu", home: str = "u:menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Назад", callback_data=back),
        InlineKeyboardButton("🏠 Главное меню", callback_data=home),
    ]])


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Пользователи", callback_data="a:users"), InlineKeyboardButton("💬 Сообщения", callback_data="a:messages")],
        [InlineKeyboardButton("🌍 Серверы", callback_data="a:servers"), InlineKeyboardButton("💳 Платежи", callback_data="a:payments")],
        [InlineKeyboardButton("🎁 Рефералы", callback_data="a:referrals"), InlineKeyboardButton("📣 Рассылка", callback_data="a:broadcast")],
        [InlineKeyboardButton("📊 Статистика", callback_data="a:stats")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="a:settings")],
        [InlineKeyboardButton("🏠 Меню пользователя", callback_data="u:menu")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    existing = db.get_user(user.id)
    user_id = db.add_user(user.id, user.username, user.first_name)
    if not existing and context.args:
        payload = context.args[0].strip()
        if payload.startswith("ref_"):
            result = db.register_referral(user_id, payload.removeprefix("ref_"), REFERRAL_BONUS_DAYS)
            if result.get("ok"):
                await notify_referral_bonus(context, result)
    await show_user_menu(update, context)


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.add_user(update.effective_user.id, update.effective_user.username, update.effective_user.first_name)
    if not is_admin(update):
        await update.message.reply_text("🚫 Админ-панель доступна только администратору.")
        return
    await show_admin_menu(update, context)


async def show_user_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.add_user(user.id, user.username, user.first_name)
    text = (
        f"👋 Привет, {user.first_name or 'друг'}!\n\n"
        "🔐 Здесь можно купить VPN, включить пробные 3 дня и написать в поддержку.\n"
        "Выберите действие:"
    )
    await send_main_menu(update, text, user_main_keyboard(is_admin(update)))


async def show_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("state", None)
    if not is_admin(update):
        await send_screen(update, "🚫 Нет доступа.", user_main_keyboard(False))
        return
    await send_screen(update, "🛠 Админ-панель\n\nВыберите раздел:", admin_keyboard())


async def show_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    plans = db.list_plans()
    rows = [[InlineKeyboardButton(f"{plan['name']} · {int(plan['price'])} ₽", callback_data=f"u:plan:{plan['id']}")] for plan in plans]
    rows.append([InlineKeyboardButton("🎁 3 дня бесплатно", callback_data="u:trial")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="u:menu"), InlineKeyboardButton("🏠 Главное меню", callback_data="u:menu")])
    await send_image_screen(
        update,
        PRICES_MENU_IMAGE_PATH,
        f"💳 Выберите тариф\n\nДо {DEVICE_LIMIT} устройств на одну подписку. После оплаты VPN включится автоматически.",
        InlineKeyboardMarkup(rows),
    )


async def show_plan_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, plan_id: str):
    plan = db.get_plan(plan_id)
    if not plan:
        await send_screen(update, "❌ Тариф не найден.", nav_keyboard("u:buy"))
        return
    text = (
        f"💳 {plan['name']}\n\n"
        f"Стоимость: {int(plan['price'])} ₽\n"
        f"Трафик: {plan['traffic_limit_gb']} GB\n"
        f"Устройств: до {DEVICE_LIMIT}\n"
        "Доступ: все активные серверы в одной подписке."
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💸 Оплатить", callback_data=f"u:pay:{plan['id']}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="u:buy"), InlineKeyboardButton("🏠 Главное меню", callback_data="u:menu")],
    ])
    await send_screen(update, text, keyboard)


async def create_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, plan_id: str):
    tg_user = update.effective_user
    user_id = db.add_user(tg_user.id, tg_user.username, tg_user.first_name)
    user = db.get_user_by_id(user_id)
    if user.get("is_blocked"):
        await send_screen(update, "🚫 Аккаунт заблокирован. Напишите в поддержку.", nav_keyboard("u:menu"))
        return

    plan = db.get_plan(plan_id)
    if not plan:
        await send_screen(update, "❌ Тариф не найден.", nav_keyboard("u:buy"))
        return

    try:
        payment = await payments.create_payment_for_plan(user, plan)
    except Exception as exc:
        logger.exception("Payment create failed")
        await send_screen(update, f"❌ Не удалось создать платеж.\n\nПричина: {exc}", nav_keyboard("u:buy"))
        return

    if not payment.get("confirmation_url"):
        await send_screen(
            update,
            "⚠️ Платежи пока не настроены.\n\nДобавьте PAYMENT_ACCOUNT_ID, PAYMENT_SECRET_KEY и PAYMENT_API_URL в .env.",
            nav_keyboard("u:buy"),
        )
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💸 Открыть оплату", url=payment["confirmation_url"])],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="u:menu")],
    ])
    schedule_payment_auto_check(context, payment, update.effective_chat.id)
    await send_screen(
        update,
        "💸 Платеж создан\n\nПосле оплаты бот сам проверит статус и пришлет подписку.",
        keyboard,
    )


async def activate_trial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    user_id = db.add_user(tg_user.id, tg_user.username, tg_user.first_name)
    user = db.get_user_by_id(user_id)
    if user.get("is_blocked"):
        await send_screen(update, "🚫 Аккаунт заблокирован. Напишите в поддержку.", nav_keyboard("u:menu"))
        return

    if db.get_active_subscription(user_id):
        await send_screen(update, "✅ У вас уже есть активная подписка.", nav_keyboard("u:vpn"))
        return

    if int(user.get("trial_used") or 0):
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Купить подписку", callback_data="u:buy")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="u:menu")],
        ])
        await send_screen(update, "🎁 Пробный период уже был активирован.", keyboard)
        return

    try:
        subscription = db.create_trial_subscription(user_id, days=TRIAL_DAYS, traffic_gb=TRIAL_TRAFFIC_GB)
    except ValueError as exc:
        await send_screen(update, f"❌ Не удалось включить пробный период.\n\n{exc}", nav_keyboard("u:menu"))
        return

    try:
        await vpn.provision_subscription(subscription)
    except Exception:
        logger.exception("Trial provision failed")
        await send_screen(
            update,
            "🎁 Пробный период включен.\n\nКлючи обновятся автоматически, если сервер временно недоступен.",
            nav_keyboard("u:vpn"),
        )
        return

    await show_vpn(update, context)


async def check_payment(update: Update, context: ContextTypes.DEFAULT_TYPE, provider_payment_id: str):
    try:
        result = await payments.handle_webhook({"event": "payment.manual_check", "object": {"id": provider_payment_id}})
    except Exception as exc:
        logger.exception("Payment check failed")
        await send_screen(update, f"❌ Не удалось проверить оплату.\n\n{exc}", nav_keyboard("u:vpn"))
        return

    if result.get("status") != "succeeded":
        await send_screen(update, "⏳ Оплата еще не подтверждена.", nav_keyboard("u:vpn"))
        return

    if result.get("subscription"):
        try:
            await vpn.provision_subscription(result["subscription"])
        except Exception:
            logger.exception("Provision after manual payment check failed")
        await notify_referral_payment_bonus(context, result.get("referral_bonus"))
    await show_vpn(update, context)


async def show_vpn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = db.get_user(update.effective_user.id)
    if not user:
        await send_screen(update, "❌ Нажмите /start, чтобы открыть меню.", user_main_keyboard(is_admin(update)))
        return
    subscription = db.get_active_subscription(user["id"])
    if not subscription:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎁 3 дня бесплатно", callback_data="u:trial")],
            [InlineKeyboardButton("💳 Купить подписку", callback_data="u:buy")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="u:menu")],
        ])
        await send_image_screen(update, VPN_MENU_IMAGE_PATH, "🔐 Активной подписки пока нет.", keyboard)
        return

    clients = db.list_clients_for_subscription(subscription["id"], active_only=True)
    primary_device = db.ensure_primary_device(subscription)
    devices = db.list_subscription_devices(subscription["id"], active_only=True)
    subscription_url = device_subscription_url(subscription, primary_device)
    open_url = device_open_url(subscription, primary_device)
    text = (
        "🔐 Мой VPN\n\n"
        f"Статус: ✅ активна\n"
        f"До: {datetime.fromisoformat(subscription['end_date']).strftime('%d.%m.%Y')}\n"
        f"Осталось: {days_left(subscription['end_date'])} дн.\n"
        f"Серверов: {len(clients)}\n\n"
        f"Устройств: {len(devices)} / {DEVICE_LIMIT}\n\n"
        f"Subscription URL:\n{subscription_url}"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Открыть в HAPP/Hub", url=open_url)],
        [InlineKeyboardButton("📋 Скопировать ссылку", callback_data="u:copy"), InlineKeyboardButton("🔄 Обновить", callback_data="u:vpn")],
        [InlineKeyboardButton("📱 Устройства", callback_data="u:devices")],
        [InlineKeyboardButton("💳 Продлить", callback_data="u:buy"), InlineKeyboardButton("🎁 Пригласить", callback_data="u:referral")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="u:menu")],
    ])
    await send_image_screen(update, VPN_MENU_IMAGE_PATH, text, keyboard)


def device_subscription_url(subscription: Dict, device: Dict = None) -> str:
    if device and not int(device.get("is_primary") or 0):
        return f"{PUBLIC_BASE_URL}/sub/device/{device['device_key']}"
    return f"{PUBLIC_BASE_URL}/sub/{subscription['subscription_key']}"


def device_open_url(subscription: Dict, device: Dict = None) -> str:
    if device and not int(device.get("is_primary") or 0):
        return f"{PUBLIC_BASE_URL}/open/device/{device['device_key']}"
    return f"{PUBLIC_BASE_URL}/open/{subscription['subscription_key']}"


async def show_devices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = db.get_user(update.effective_user.id)
    subscription = db.get_active_subscription(user["id"]) if user else None
    if not subscription:
        await send_screen(update, "❌ Активной подписки нет.", nav_keyboard("u:menu"))
        return
    db.ensure_primary_device(subscription)
    devices = db.list_subscription_devices(subscription["id"], active_only=True)
    lines = [f"📱 Устройства\n\nАктивно: {len(devices)} / {DEVICE_LIMIT}\n"]
    rows = []
    for device in devices:
        seen = device.get("last_seen_at") or "еще не открывалось"
        lines.append(f"{device['name']} · запросов: {int(device.get('request_count') or 0)} · {seen}")
        rows.append([InlineKeyboardButton(f"📱 {device['name']}", callback_data=f"u:device:{device['id']}")])
    if len(devices) < DEVICE_LIMIT:
        rows.append([InlineKeyboardButton("➕ Добавить устройство", callback_data="u:add_device")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="u:vpn"), InlineKeyboardButton("🏠 Главное меню", callback_data="u:menu")])
    await send_screen(update, "\n".join(lines), InlineKeyboardMarkup(rows))


async def add_device(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = db.get_user(update.effective_user.id)
    subscription = db.get_active_subscription(user["id"]) if user else None
    if not subscription:
        await send_screen(update, "❌ Активной подписки нет.", nav_keyboard("u:menu"))
        return
    db.ensure_primary_device(subscription)
    active_devices = db.list_subscription_devices(subscription["id"], active_only=True)
    if len(active_devices) >= DEVICE_LIMIT:
        await send_screen(update, "📱 Лимит 3 устройства. Удалите старое устройство или напишите в поддержку.", nav_keyboard("u:devices"))
        return
    used_names = {device["name"] for device in active_devices}
    number = 1
    while f"Устройство {number}" in used_names:
        number += 1
    device = db.create_subscription_device(subscription["id"], user["id"], f"Устройство {number}", limit=DEVICE_LIMIT)
    if not device:
        await send_screen(update, "📱 Лимит 3 устройства. Удалите старое устройство или напишите в поддержку.", nav_keyboard("u:devices"))
        return
    await show_device(update, context, device["id"])


async def show_device(update: Update, context: ContextTypes.DEFAULT_TYPE, device_id: int):
    user = db.get_user(update.effective_user.id)
    subscription = db.get_active_subscription(user["id"]) if user else None
    device = db.get_subscription_device_by_id(device_id)
    if not subscription or not device or int(device["subscription_id"]) != int(subscription["id"]) or device["status"] != "active":
        await send_screen(update, "❌ Устройство не найдено.", nav_keyboard("u:devices"))
        return
    sub_url = device_subscription_url(subscription, device)
    open_url = device_open_url(subscription, device)
    text = (
        f"📱 {device['name']}\n\n"
        f"Статус: ✅ активно\n"
        f"Последнее обновление: {device.get('last_seen_at') or 'еще не открывалось'}\n\n"
        f"Subscription URL:\n{sub_url}"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Открыть в HAPP/Hub", url=open_url)],
        [InlineKeyboardButton("🗑 Удалить устройство", callback_data=f"u:delete_device:{device_id}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="u:devices"), InlineKeyboardButton("🏠 Главное меню", callback_data="u:menu")],
    ])
    await send_screen(update, text, keyboard)


async def delete_device(update: Update, context: ContextTypes.DEFAULT_TYPE, device_id: int):
    user = db.get_user(update.effective_user.id)
    subscription = db.get_active_subscription(user["id"]) if user else None
    device = db.get_subscription_device_by_id(device_id)
    if not subscription or not device or int(device["subscription_id"]) != int(subscription["id"]):
        await send_screen(update, "❌ Устройство не найдено.", nav_keyboard("u:devices"))
        return
    db.delete_subscription_device(device_id)
    await send_screen(update, "✅ Устройство удалено. Его ссылка больше не будет отдавать реальные серверы.", nav_keyboard("u:devices"))


async def show_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    user_id = db.add_user(tg_user.id, tg_user.username, tg_user.first_name)
    summary = db.get_referral_summary(user_id)
    code = summary.get("referral_code")
    if not code:
        db.add_user(tg_user.id, tg_user.username, tg_user.first_name)
        summary = db.get_referral_summary(user_id)
        code = summary.get("referral_code")

    bot_user = await context.bot.get_me()
    link = f"https://t.me/{bot_user.username}?start=ref_{code}"
    invited = int(summary.get("invited_count") or 0)
    credited = int(summary.get("credited_count") or 0)
    awaiting = int(summary.get("awaiting_payment_count") or 0)
    pending_days = int(summary.get("referral_bonus_pending_days") or 0)
    applied_days = int(summary.get("referral_bonus_applied_days") or 0)
    share_text = f"Подключай {SERVER_NAME}, тут дают 3 дня бесплатно: {link}"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Поделиться", url=f"https://t.me/share/url?url={quote_plus(link)}&text={quote_plus(share_text)}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="u:menu"), InlineKeyboardButton("🏠 Главное меню", callback_data="u:menu")],
    ])
    text = (
        "🎁 Пригласить друга\n\n"
        f"Бонус: +{REFERRAL_BONUS_DAYS} дня за реферала.\n"
        f"Первые {REFERRAL_FREE_LIMIT}: после старта по ссылке.\n"
        f"Дальше: только после первой оплаты друга.\n\n"
        f"Приглашено: {invited}\n"
        f"Зачтено: {credited}\n"
        f"Ждут оплаты: {awaiting}\n"
        f"Начислено: {applied_days} дн.\n"
        f"Ожидает подписки: {pending_days} дн.\n\n"
        f"Ваша ссылка:\n{link}"
    )
    await send_screen(update, text, keyboard)


async def copy_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = db.get_user(update.effective_user.id)
    subscription = db.get_active_subscription(user["id"]) if user else None
    if not subscription:
        await send_screen(update, "❌ Активной подписки нет.", nav_keyboard("u:menu"))
        return
    device = db.ensure_primary_device(subscription)
    url = device_subscription_url(subscription, device)
    open_url = device_open_url(subscription, device)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Открыть в HAPP/Hub", url=open_url)],
        [InlineKeyboardButton("⬅️ Назад", callback_data="u:vpn"), InlineKeyboardButton("🏠 Главное меню", callback_data="u:menu")],
    ])
    await send_screen(update, f"📋 Ссылка для Hub/HAPP:\n\n{url}", keyboard)


async def show_traffic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = db.get_user(update.effective_user.id)
    subscription = db.get_active_subscription(user["id"]) if user else None
    if not subscription:
        await send_screen(update, "📊 Трафик появится после покупки подписки.", nav_keyboard("u:menu"))
        return
    clients = db.list_clients_for_subscription(subscription["id"], active_only=True)
    if not clients:
        await send_screen(update, "📊 Ключи еще не выпущены. Попробуйте обновить позже.", nav_keyboard("u:vpn"))
        return
    lines = ["📊 Трафик по серверам\n"]
    total_used = 0
    total_limit = 0
    for client in clients:
        used = int(client["traffic_up"] or 0) + int(client["traffic_down"] or 0)
        limit = int(client["traffic_limit"] or 0)
        total_used += used
        total_limit += limit
        lines.append(f"{client['server_name']}: {fmt_traffic(used)} / {fmt_traffic(limit)}")
    lines.append(f"\nИтого: {fmt_traffic(total_used)} / {fmt_traffic(total_limit)}")
    await send_screen(update, "\n".join(lines), nav_keyboard("u:vpn"))


async def show_user_servers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    servers = db.list_servers(active_only=True)
    if not servers:
        await send_image_screen(update, SERVERS_MENU_IMAGE_PATH, "🌍 Серверы скоро появятся.", nav_keyboard("u:menu"))
        return
    text = "🌍 Доступные серверы\n\n" + "\n".join(f"{server['name']} · {server['protocol'] or 'vpn'}" for server in servers)
    await send_image_screen(update, SERVERS_MENU_IMAGE_PATH, text, nav_keyboard("u:menu"))


async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "ℹ️ Как подключиться\n\n"
        "1. Купите подписку или включите пробные 3 дня.\n"
        "2. Откройте «Мой VPN».\n"
        "3. Скопируйте Subscription URL.\n"
        "4. В Hub/HAPP нажмите + и добавьте подписку.\n\n"
        "Если что-то не работает, напишите в поддержку."
    )
    await send_image_screen(update, HELP_MENU_IMAGE_PATH, text, nav_keyboard("u:menu"))


async def start_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("state", None)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ Написать в поддержку", callback_data="u:support_ticket")],
        [InlineKeyboardButton("💬 Чат с админом", url=SUPPORT_ADMIN_URL)],
        [InlineKeyboardButton("⬅️ Назад", callback_data="u:menu"), InlineKeyboardButton("🏠 Главное меню", callback_data="u:menu")],
    ])
    await send_screen(update, "🆘 Поддержка\n\nМожно написать тикет в боте или открыть прямой чат с админом.", keyboard)


async def start_support_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["state"] = {"name": "support_message"}
    await send_screen(update, "🆘 Поддержка\n\nНапишите сообщение одним текстом. Админ ответит здесь.", nav_keyboard("u:menu"))


async def show_admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = db.list_recent_users(8)
    rows = [[InlineKeyboardButton(_user_label(user), callback_data=f"a:user:{user['id']}")] for user in users]
    rows.append([InlineKeyboardButton("🔎 Найти пользователя", callback_data="a:user_search")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="a:menu"), InlineKeyboardButton("🏠 Главное меню", callback_data="u:menu")])
    await send_screen(update, "👥 Пользователи\n\nПоследние регистрации:", InlineKeyboardMarkup(rows))


async def show_admin_user_card(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    summary = db.get_user_summary(user_id)
    if not summary:
        await send_screen(update, "❌ Пользователь не найден.", nav_keyboard("a:users", "a:menu"))
        return
    referral = db.get_referral_summary(user_id)
    subscriptions = db.get_user_subscriptions(user_id)
    device_count = 0
    if subscriptions:
        device_count = len(db.list_subscription_devices(subscriptions[0]["id"], active_only=True))
    referrer = "—"
    if referral.get("referred_by_user_id"):
        ref_username = referral.get("referrer_username")
        ref_name = referral.get("referrer_first_name") or "Пользователь"
        referrer = f"{ref_name} · @{ref_username}" if ref_username else ref_name
    blocked = "🚫 заблокирован" if summary["is_blocked"] else "✅ активен"
    sub_status = summary.get("subscription_status") or "нет подписки"
    text = (
        f"👤 {_user_label(summary)}\n\n"
        f"ID: {summary['telegram_id']}\n"
        f"Статус: {blocked}\n"
        f"Подписка: {sub_status}\n"
        f"До: {summary.get('end_date') or '—'}\n"
        f"Трафик: {fmt_traffic(summary.get('traffic_used'))} / {fmt_traffic(summary.get('traffic_limit'))}\n\n"
        f"Реферер: {referrer}\n"
        f"Пригласил: {int(referral.get('invited_count') or 0)}\n"
        f"Зачтено реф.: {int(referral.get('credited_count') or 0)} · ждут оплаты {int(referral.get('awaiting_payment_count') or 0)}\n"
        f"Реф. дни: +{int(referral.get('referral_bonus_applied_days') or 0)} / ожидает {int(referral.get('referral_bonus_pending_days') or 0)}\n"
        f"Реф. бонусы: {'🚫 отключены' if int(referral.get('referral_bonus_disabled') or 0) else '✅ включены'}\n"
        f"Устройства: {device_count} / {DEVICE_LIMIT}"
    )
    rows = [
        [InlineKeyboardButton("🚫 Заблокировать" if not summary["is_blocked"] else "✅ Разблокировать", callback_data=f"a:block:{user_id}")],
        [InlineKeyboardButton("❌ Забрать подписку", callback_data=f"a:revoke:{user_id}"), InlineKeyboardButton("➕ Продлить", callback_data=f"a:extend:{user_id}")],
        [InlineKeyboardButton("🔗 Отправить ссылку", callback_data=f"a:sendlink:{user_id}")],
        [InlineKeyboardButton("📱 Устройства", callback_data=f"a:user_devices:{user_id}"), InlineKeyboardButton("🎁 Рефералы", callback_data=f"a:user_refs:{user_id}")],
        [InlineKeyboardButton("➕ Добавить дни", callback_data=f"a:days_add:{user_id}"), InlineKeyboardButton("➖ Забрать дни", callback_data=f"a:days_sub:{user_id}")],
        [InlineKeyboardButton("🚫 Откл. реф. бонусы" if not int(referral.get("referral_bonus_disabled") or 0) else "✅ Вкл. реф. бонусы", callback_data=f"a:ref_toggle:{user_id}")],
        [InlineKeyboardButton("❌ Отменить pending-бонусы", callback_data=f"a:ref_cancel_pending:{user_id}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="a:users"), InlineKeyboardButton("🏠 Админ", callback_data="a:menu")],
    ]
    await send_screen(update, text, InlineKeyboardMarkup(rows))


async def show_admin_referrals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    leaders = db.list_referral_leaders(15)
    lines = ["🎁 Рефералы\n"]
    rows = []
    for index, user in enumerate(leaders, start=1):
        label = _user_label(user)
        lines.append(
            f"{index}. {label}: зачтено {int(user.get('credited_count') or 0)}, "
            f"всего {int(user.get('invited_count') or 0)}, ждут {int(user.get('awaiting_payment_count') or 0)}, "
            f"дней +{int(user.get('credited_days') or 0)}"
        )
        rows.append([InlineKeyboardButton(f"{index}. {label}", callback_data=f"a:user:{user['id']}")])
    if not leaders:
        lines.append("Рефералов пока нет.")
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="a:menu"), InlineKeyboardButton("🏠 Админ", callback_data="a:menu")])
    await send_screen(update, "\n".join(lines), InlineKeyboardMarkup(rows))


async def show_admin_user_referrals(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    user = db.get_user_by_id(user_id)
    refs = db.list_referrals_for_user(user_id, limit=15)
    lines = [f"🎁 Рефералы {_user_label(user)}\n"]
    for ref in refs:
        lines.append(
            f"{_user_label(ref)} · {ref['status']} · +{int(ref['bonus_days'] or 0)} дн."
        )
    if not refs:
        lines.append("Приглашенных пока нет.")
    await send_screen(update, "\n".join(lines), nav_keyboard(f"a:user:{user_id}", "a:menu"))


async def show_admin_user_devices(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    user = db.get_user_by_id(user_id)
    subscriptions = db.get_user_subscriptions(user_id)
    active_subscription = next((sub for sub in subscriptions if sub["status"] == "active"), subscriptions[0] if subscriptions else None)
    if not active_subscription:
        await send_screen(update, "📱 У пользователя нет подписок.", nav_keyboard(f"a:user:{user_id}", "a:menu"))
        return
    db.ensure_primary_device(active_subscription)
    devices = db.list_subscription_devices(active_subscription["id"], active_only=False)
    lines = [f"📱 Устройства {_user_label(user)}\n"]
    rows = []
    for device in devices:
        status = "✅" if device["status"] == "active" else "🗑"
        lines.append(
            f"{status} {device['id']}. {device['name']} · запросов {int(device.get('request_count') or 0)} · "
            f"{device.get('last_ip') or 'no ip'} · {device.get('last_seen_at') or 'never'}"
        )
        rows.append([
            InlineKeyboardButton(f"♻️ {device['name']}", callback_data=f"a:dev_reset:{user_id}:{device['id']}"),
            InlineKeyboardButton("🗑", callback_data=f"a:dev_delete:{user_id}:{device['id']}"),
        ])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"a:user:{user_id}"), InlineKeyboardButton("🏠 Админ", callback_data="a:menu")])
    await send_screen(update, "\n".join(lines), InlineKeyboardMarkup(rows))


async def show_admin_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    threads = db.list_open_support_threads()
    rows = [[InlineKeyboardButton(_user_label(thread), callback_data=f"a:thread:{thread['user_id']}")] for thread in threads]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="a:menu"), InlineKeyboardButton("🏠 Главное меню", callback_data="u:menu")])
    text = "💬 Сообщения\n\n" + ("Выберите диалог:" if threads else "Новых обращений нет.")
    await send_screen(update, text, InlineKeyboardMarkup(rows))


async def show_thread(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    messages = list(reversed(db.list_support_messages(user_id, limit=10)))
    user = db.get_user_by_id(user_id)
    lines = [f"💬 Диалог с {_user_label(user)}\n"]
    for message in messages:
        prefix = "👤" if message["direction"] == "user" else "🛠"
        lines.append(f"{prefix} {message['message_text']}")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ Ответить", callback_data=f"a:reply:{user_id}"), InlineKeyboardButton("✅ Закрыть", callback_data=f"a:close_thread:{user_id}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="a:messages"), InlineKeyboardButton("🏠 Админ", callback_data="a:menu")],
    ])
    await send_screen(update, "\n\n".join(lines), keyboard)


async def show_admin_servers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    servers = db.list_servers()
    lines = ["🌍 Серверы\n"]
    rows = []
    for server in servers:
        status = "✅" if server["status"] == "active" else "⏸"
        lines.append(f"{status} {server['id']}. {server['name']} · inbound {server['inbound_id']}")
        rows.append([InlineKeyboardButton(f"{status} {server['id']}. {server['name']}", callback_data=f"a:server:{server['id']}")])
    if not servers:
        lines.append("Серверов пока нет.")
    rows.extend([
        [InlineKeyboardButton("➕ Готовый конфиг", callback_data="a:add_server")],
        [InlineKeyboardButton("🔐 3X-UI сервер", callback_data="a:add_xui_server")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="a:menu"), InlineKeyboardButton("🏠 Главное меню", callback_data="u:menu")],
    ])
    keyboard = InlineKeyboardMarkup(rows)
    await send_screen(update, "\n".join(lines), keyboard)


async def show_admin_server_card(update: Update, context: ContextTypes.DEFAULT_TYPE, server_id: int):
    server = db.get_server(server_id)
    if not server or server.get("status") == "deleted":
        await send_screen(update, "❌ Сервер не найден или уже удален.", nav_keyboard("a:servers", "a:menu"))
        return
    counts = db.count_clients_for_server(server_id)
    status = "✅ активен" if server["status"] == "active" else "⏸ выключен"
    server_type = "готовый конфиг" if int(server.get("inbound_id") or 0) <= 0 else "3X-UI"
    text = (
        f"🌍 {server['name']}\n\n"
        f"ID: {server['id']}\n"
        f"Статус: {status}\n"
        f"Тип: {server_type}\n"
        f"Страна: {server.get('country_name') or 'Unknown'}\n"
        f"Host: {server.get('host') or '—'}\n"
        f"Inbound: {server.get('inbound_id') or 0}\n"
        f"Клиентов: {int(counts.get('active') or 0)} активных / {int(counts.get('total') or 0)} всего"
    )
    toggle_text = "⏸ Выключить" if server["status"] == "active" else "✅ Включить"
    rows = [
        [InlineKeyboardButton("✏️ Изменить название", callback_data=f"a:server_rename:{server_id}")],
        [InlineKeyboardButton(toggle_text, callback_data=f"a:server_toggle:{server_id}")],
        [InlineKeyboardButton("🗑 Удалить сервер", callback_data=f"a:server_delete_confirm:{server_id}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="a:servers"), InlineKeyboardButton("🏠 Админ", callback_data="a:menu")],
    ]
    await send_screen(update, text, InlineKeyboardMarkup(rows))


async def confirm_delete_server(update: Update, context: ContextTypes.DEFAULT_TYPE, server_id: int):
    server = db.get_server(server_id)
    if not server or server.get("status") == "deleted":
        await send_screen(update, "❌ Сервер не найден или уже удален.", nav_keyboard("a:servers", "a:menu"))
        return
    counts = db.count_clients_for_server(server_id)
    text = (
        f"🗑 Удалить сервер?\n\n"
        f"{server['name']}\n"
        f"Активных клиентов: {int(counts.get('active') or 0)}\n\n"
        "Сервер пропадет из всех подписок и больше не будет выдаваться новым пользователям."
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да, удалить", callback_data=f"a:server_delete:{server_id}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"a:server:{server_id}"), InlineKeyboardButton("🏠 Админ", callback_data="a:menu")],
    ])
    await send_screen(update, text, keyboard)


async def show_admin_payments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    recent = db.list_recent_payments(10)
    lines = ["💳 Платежи\n"]
    for payment in recent:
        user = payment.get("username") or payment.get("first_name") or payment.get("telegram_id")
        provider_id = payment.get("provider_payment_id") or payment.get("payment_id") or "—"
        if len(str(provider_id)) > 14:
            provider_id = f"{str(provider_id)[:8]}..."
        lines.append(f"{payment['status']} · {int(payment['amount'])} ₽ · {user} · {provider_id}")
    if not recent:
        lines.append("Платежей пока нет.")
    await send_screen(update, "\n".join(lines), nav_keyboard("a:menu", "a:menu"))


async def show_admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = db.get_stats()
    text = (
        "📊 Статистика\n\n"
        f"Пользователей: {stats['total_users']}\n"
        f"Заблокировано: {stats['blocked_users']}\n"
        f"Активных подписок: {stats['active_subs']}\n"
        f"Активных серверов: {stats['active_servers']}\n"
        f"Открытых сообщений: {stats['open_messages']}\n"
        f"Рефералов: {stats['total_referrals']}\n"
        f"Бонусных дней: {stats['referral_bonus_days']}\n"
        f"Доход: {int(stats['total_revenue'])} ₽"
    )
    await send_screen(update, text, nav_keyboard("a:menu", "a:menu"))


async def show_admin_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "⚙️ Настройки\n\n"
        f"Платежи: {'✅ настроены' if payments.enabled else '⚠️ не настроены'}\n"
        f"Public URL: {PUBLIC_BASE_URL}\n"
        f"Админов: {len(admin_ids())}\n\n"
        f"Устройств на подписку: {DEVICE_LIMIT}\n"
        f"Пробный период: {TRIAL_DAYS} дня\n\n"
        f"Реф. бонус: {REFERRAL_BONUS_DAYS} дня\n"
        f"Без оплаты до рефералов: {REFERRAL_FREE_LIMIT}\n"
        f"Чат поддержки: {SUPPORT_ADMIN_URL}\n\n"
        "Основные параметры меняются в .env."
    )
    await send_screen(update, text, nav_keyboard("a:menu", "a:menu"))


async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count = db.count_broadcast_users()
    context.user_data["state"] = {"name": "broadcast_message"}
    text = (
        "📣 Рассылка всем пользователям\n\n"
        f"Получателей: {count}\n"
        "Отправьте одним сообщением:\n"
        "• текст\n"
        "• фото с подписью или без\n"
        "• видео с подписью или без"
    )
    await send_screen(update, text, nav_keyboard("a:menu", "a:menu"))


async def confirm_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state") or {}
    payload = state.get("broadcast")
    if state.get("name") != "broadcast_confirm" or not payload:
        await start_broadcast(update, context)
        return

    recipients = db.list_broadcast_users()
    sent = 0
    failed = 0
    await send_screen(update, f"📣 Рассылка началась\n\nПолучателей: {len(recipients)}", nav_keyboard("a:menu", "a:menu"))

    for user in recipients:
        try:
            await send_broadcast_payload(context, user["telegram_id"], payload)
            sent += 1
        except Exception:
            failed += 1
            logger.exception("Broadcast delivery failed for user_id=%s telegram_id=%s", user.get("id"), user.get("telegram_id"))
        if (sent + failed) % 20 == 0:
            await asyncio.sleep(1)

    context.user_data.pop("state", None)
    await send_screen(
        update,
        f"✅ Рассылка завершена\n\nОтправлено: {sent}\nОшибок: {failed}",
        nav_keyboard("a:menu", "a:menu"),
    )


async def send_broadcast_preview(update: Update, payload: Dict, count: int):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📣 Отправить всем", callback_data="a:broadcast_confirm")],
        [InlineKeyboardButton("✏️ Изменить", callback_data="a:broadcast_edit"), InlineKeyboardButton("❌ Отмена", callback_data="a:broadcast_cancel")],
    ])
    message = update.effective_message
    kind = payload.get("kind")
    if kind == "photo":
        await message.reply_photo(photo=payload["file_id"], caption=payload.get("caption") or None)
        await message.reply_text(f"📣 Предпросмотр фото\n\nПолучателей: {count}", reply_markup=keyboard)
        return
    if kind == "video":
        await message.reply_video(video=payload["file_id"], caption=payload.get("caption") or None)
        await message.reply_text(f"📣 Предпросмотр видео\n\nПолучателей: {count}", reply_markup=keyboard)
        return
    await message.reply_text(
        f"📣 Предпросмотр рассылки\n\n{payload['text']}\n\nПолучателей: {count}",
        reply_markup=keyboard,
    )


async def send_broadcast_payload(context: ContextTypes.DEFAULT_TYPE, chat_id: int, payload: Dict):
    kind = payload.get("kind")
    caption = payload.get("caption") or None
    if kind == "photo":
        await context.bot.send_photo(chat_id, photo=payload["file_id"], caption=caption)
        return
    if kind == "video":
        await context.bot.send_video(chat_id, video=payload["file_id"], caption=caption)
        return
    await context.bot.send_message(chat_id, f"📣 Сообщение от {SERVER_NAME}\n\n{payload['text']}")


def broadcast_payload_from_message(message) -> Dict:
    text = (message.text or "").strip()
    if text:
        return {"kind": "text", "text": text}
    if message.photo:
        return {
            "kind": "photo",
            "file_id": message.photo[-1].file_id,
            "caption": message.caption or "",
        }
    if message.video:
        return {
            "kind": "video",
            "file_id": message.video.file_id,
            "caption": message.caption or "",
        }
    return {}


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data == "u:menu":
        await show_user_menu(update, context)
    elif data == "u:trial":
        await activate_trial(update, context)
    elif data == "u:buy":
        await show_plans(update, context)
    elif data.startswith("u:plan:"):
        await show_plan_confirm(update, context, data.split(":", 2)[2])
    elif data.startswith("u:pay:"):
        await create_payment(update, context, data.split(":", 2)[2])
    elif data.startswith("u:check:"):
        await check_payment(update, context, data.split(":", 2)[2])
    elif data == "u:vpn":
        await show_vpn(update, context)
    elif data == "u:copy":
        await copy_subscription(update, context)
    elif data == "u:devices":
        await show_devices(update, context)
    elif data == "u:add_device":
        await add_device(update, context)
    elif data.startswith("u:device:"):
        await show_device(update, context, int(data.split(":")[2]))
    elif data.startswith("u:delete_device:"):
        await delete_device(update, context, int(data.split(":")[2]))
    elif data == "u:traffic":
        await show_traffic(update, context)
    elif data == "u:servers":
        await show_user_servers(update, context)
    elif data == "u:referral":
        await show_referral(update, context)
    elif data == "u:help":
        await show_help(update, context)
    elif data == "u:support":
        await start_support(update, context)
    elif data == "u:support_ticket":
        await start_support_ticket(update, context)
    elif data == "a:menu":
        await show_admin_menu(update, context)
    elif is_admin(update):
        await handle_admin_callback(update, context, data)
    else:
        await send_screen(update, "🚫 Нет доступа.", user_main_keyboard(False))


async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    if data == "a:users":
        await show_admin_users(update, context)
    elif data == "a:user_search":
        context.user_data["state"] = {"name": "admin_user_search"}
        await send_screen(update, "🔎 Напишите Telegram ID, username или имя пользователя.", nav_keyboard("a:users", "a:menu"))
    elif data.startswith("a:user:"):
        await show_admin_user_card(update, context, int(data.split(":")[2]))
    elif data.startswith("a:block:"):
        user_id = int(data.split(":")[2])
        user = db.get_user_by_id(user_id)
        blocked = not bool(user["is_blocked"])
        db.set_user_blocked(user_id, blocked)
        if blocked:
            db.revoke_active_subscription(user_id, "blocked")
            try:
                await vpn.disable_user_clients(user_id)
            except Exception:
                logger.exception("Disable blocked user clients failed")
        await show_admin_user_card(update, context, user_id)
    elif data.startswith("a:revoke:"):
        user_id = int(data.split(":")[2])
        db.revoke_active_subscription(user_id, "admin_revoked")
        try:
            await vpn.disable_user_clients(user_id)
        except Exception:
            logger.exception("Disable revoked user clients failed")
        await show_admin_user_card(update, context, user_id)
    elif data.startswith("a:extend:"):
        user_id = int(data.split(":")[2])
        rows = [[InlineKeyboardButton(f"{plan['name']} · {int(plan['price'])} ₽", callback_data=f"a:extend_plan:{user_id}:{plan['id']}")] for plan in db.list_plans()]
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"a:user:{user_id}"), InlineKeyboardButton("🏠 Админ", callback_data="a:menu")])
        await send_screen(update, "➕ Выберите срок продления:", InlineKeyboardMarkup(rows))
    elif data.startswith("a:extend_plan:"):
        _, _, user_id, plan_id = data.split(":")
        payment = db.create_manual_payment(int(user_id), plan_id)
        subscription = db.create_or_extend_subscription(payment)
        try:
            await vpn.provision_subscription(subscription)
        except Exception:
            logger.exception("Manual provision failed")
        await show_admin_user_card(update, context, int(user_id))
    elif data.startswith("a:sendlink:"):
        user_id = int(data.split(":")[2])
        await admin_send_subscription_link(update, context, user_id)
    elif data.startswith("a:user_devices:"):
        await show_admin_user_devices(update, context, int(data.split(":")[2]))
    elif data.startswith("a:user_refs:"):
        await show_admin_user_referrals(update, context, int(data.split(":")[2]))
    elif data.startswith("a:dev_reset:"):
        _, _, user_id, device_id = data.split(":")
        db.reset_subscription_device(int(device_id))
        await show_admin_user_devices(update, context, int(user_id))
    elif data.startswith("a:dev_delete:"):
        _, _, user_id, device_id = data.split(":")
        db.delete_subscription_device(int(device_id))
        await show_admin_user_devices(update, context, int(user_id))
    elif data.startswith("a:days_add:"):
        user_id = int(data.split(":")[2])
        context.user_data["state"] = {"name": "admin_adjust_days", "user_id": user_id, "direction": 1}
        await send_screen(update, "➕ Сколько дней добавить? Отправьте число.", nav_keyboard(f"a:user:{user_id}", "a:menu"))
    elif data.startswith("a:days_sub:"):
        user_id = int(data.split(":")[2])
        context.user_data["state"] = {"name": "admin_adjust_days", "user_id": user_id, "direction": -1}
        await send_screen(update, "➖ Сколько дней забрать? Отправьте число.", nav_keyboard(f"a:user:{user_id}", "a:menu"))
    elif data.startswith("a:ref_toggle:"):
        user_id = int(data.split(":")[2])
        summary = db.get_referral_summary(user_id)
        db.set_referral_bonus_disabled(user_id, not bool(int(summary.get("referral_bonus_disabled") or 0)))
        await show_admin_user_card(update, context, user_id)
    elif data.startswith("a:ref_cancel_pending:"):
        user_id = int(data.split(":")[2])
        admin_db_id = db.add_user(update.effective_user.id, update.effective_user.username, update.effective_user.first_name)
        days = db.cancel_pending_referral_bonuses(user_id, admin_db_id)
        await send_screen(update, f"✅ Pending-бонусы отменены. Снято из ожидания: {days} дн.", nav_keyboard(f"a:user:{user_id}", "a:menu"))
    elif data == "a:messages":
        await show_admin_messages(update, context)
    elif data.startswith("a:thread:"):
        await show_thread(update, context, int(data.split(":")[2]))
    elif data.startswith("a:reply:"):
        user_id = int(data.split(":")[2])
        context.user_data["state"] = {"name": "admin_reply", "user_id": user_id}
        await send_screen(update, "✍️ Напишите ответ пользователю одним сообщением.", nav_keyboard(f"a:thread:{user_id}", "a:menu"))
    elif data.startswith("a:close_thread:"):
        user_id = int(data.split(":")[2])
        db.close_support_thread(user_id)
        await show_admin_messages(update, context)
    elif data == "a:servers":
        await show_admin_servers(update, context)
    elif data.startswith("a:server:"):
        await show_admin_server_card(update, context, int(data.split(":")[2]))
    elif data.startswith("a:server_rename:"):
        server_id = int(data.split(":")[2])
        server = db.get_server(server_id)
        if not server or server.get("status") == "deleted":
            await send_screen(update, "❌ Сервер не найден или уже удален.", nav_keyboard("a:servers", "a:menu"))
            return
        context.user_data["state"] = {"name": "server_rename", "server_id": server_id}
        await send_screen(
            update,
            f"✏️ Новое название сервера\n\nТекущее: {server['name']}\n\nНапишите новое название одним сообщением.",
            nav_keyboard(f"a:server:{server_id}", "a:menu"),
        )
    elif data.startswith("a:server_toggle:"):
        server_id = int(data.split(":")[2])
        server = db.get_server(server_id)
        if server and server.get("status") != "deleted":
            db.set_server_status(server_id, "disabled" if server["status"] == "active" else "active")
        await show_admin_server_card(update, context, server_id)
    elif data.startswith("a:server_delete_confirm:"):
        await confirm_delete_server(update, context, int(data.split(":")[2]))
    elif data.startswith("a:server_delete:"):
        server_id = int(data.split(":")[2])
        try:
            await vpn.delete_server(server_id)
        except Exception:
            logger.exception("Delete server failed")
            await send_screen(update, "❌ Не удалось удалить сервер. Проверьте логи.", nav_keyboard(f"a:server:{server_id}", "a:menu"))
            return
        await send_screen(update, "✅ Сервер удален из подписок.", nav_keyboard("a:servers", "a:menu"))
    elif data == "a:add_server":
        context.user_data["state"] = {"name": "add_server_config_static"}
        await send_screen(update, "➕ Готовый конфиг\n\nОтправьте VLESS/VMess/Trojan/SS ссылку. Она сразу попадет во все активные подписки.", nav_keyboard("a:servers", "a:menu"))
    elif data == "a:add_xui_server":
        context.user_data["state"] = {"name": "add_server_config_xui"}
        await send_screen(update, "🔐 3X-UI сервер\n\nОтправьте URL-шаблон конфига. После этого бот попросит доступ к 3X-UI.", nav_keyboard("a:servers", "a:menu"))
    elif data == "a:payments":
        await show_admin_payments(update, context)
    elif data == "a:referrals":
        await show_admin_referrals(update, context)
    elif data == "a:broadcast":
        await start_broadcast(update, context)
    elif data == "a:broadcast_confirm":
        await confirm_broadcast(update, context)
    elif data == "a:broadcast_edit":
        context.user_data["state"] = {"name": "broadcast_message"}
        await send_screen(update, "✏️ Отправьте новый текст рассылки.", nav_keyboard("a:menu", "a:menu"))
    elif data == "a:broadcast_cancel":
        context.user_data.pop("state", None)
        await show_admin_menu(update, context)
    elif data == "a:stats":
        await show_admin_stats(update, context)
    elif data == "a:settings":
        await show_admin_settings(update, context)


async def admin_send_subscription_link(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    user = db.get_user_by_id(user_id)
    subscription = db.get_active_subscription(user_id)
    if not user or not subscription:
        await send_screen(update, "❌ У пользователя нет активной подписки.", nav_keyboard(f"a:user:{user_id}", "a:menu"))
        return
    url = f"{PUBLIC_BASE_URL}/sub/{subscription['subscription_key']}"
    open_url = f"{PUBLIC_BASE_URL}/open/{subscription['subscription_key']}"
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Открыть в HAPP/Hub", url=open_url)]])
    await context.bot.send_message(user["telegram_id"], f"🔗 Ваша VPN подписка:\n\n{url}", reply_markup=keyboard)
    await send_screen(update, "✅ Ссылка отправлена пользователю.", nav_keyboard(f"a:user:{user_id}", "a:menu"))


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = db.add_user(user.id, user.username, user.first_name)
    state = context.user_data.get("state") or {}
    text = update.message.text or ""

    if state.get("name") == "support_message":
        if not text.strip():
            await update.message.reply_text("✍️ Для тикета отправьте текст одним сообщением.", reply_markup=nav_keyboard("u:support", "u:menu"))
            return
        db.add_support_message(user_id, "user", text)
        context.user_data.pop("state", None)
        for admin_id in admin_ids():
            try:
                await context.bot.send_message(admin_id, f"💬 Новое сообщение от {_user_label(db.get_user_by_id(user_id))}:\n\n{text}")
            except Exception:
                logger.exception("Admin support notify failed")
        await update.message.reply_text("✅ Сообщение отправлено. Ответ придет сюда.", reply_markup=user_main_keyboard(is_admin(update)))
        return

    if is_admin(update):
        handled = await handle_admin_text(update, context, state, text, user_id)
        if handled:
            return

    await update.message.reply_text("👇 Выберите действие кнопками:", reply_markup=user_main_keyboard(is_admin(update)))


async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE, state: Dict, text: str, admin_user_id: int) -> bool:
    name = state.get("name")
    if name == "admin_user_search":
        context.user_data.pop("state", None)
        users = db.search_users(text, limit=10)
        rows = [[InlineKeyboardButton(_user_label(user), callback_data=f"a:user:{user['id']}")] for user in users]
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="a:users"), InlineKeyboardButton("🏠 Админ", callback_data="a:menu")])
        await update.message.reply_text("🔎 Результаты поиска:" if users else "Ничего не найдено.", reply_markup=InlineKeyboardMarkup(rows))
        return True

    if name == "admin_reply":
        user_id = int(state["user_id"])
        target = db.get_user_by_id(user_id)
        db.add_support_message(user_id, "admin", text, admin_id=admin_user_id)
        context.user_data.pop("state", None)
        if target:
            await context.bot.send_message(target["telegram_id"], f"🛠 Ответ поддержки:\n\n{text}")
        await update.message.reply_text("✅ Ответ отправлен.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Диалог", callback_data=f"a:thread:{user_id}")]]))
        return True

    if name == "admin_adjust_days":
        raw = (text or "").strip()
        if not raw.isdigit():
            await update.message.reply_text("❌ Отправьте число дней.", reply_markup=nav_keyboard(f"a:user:{state['user_id']}", "a:menu"))
            return True
        days = int(raw) * int(state.get("direction") or 1)
        user_id = int(state["user_id"])
        subscription = db.adjust_subscription_days(user_id, days, admin_user_id, "admin_referral_adjust")
        context.user_data.pop("state", None)
        if subscription and subscription.get("status") == "expired":
            try:
                await vpn.disable_user_clients(user_id)
            except Exception:
                logger.exception("Disable clients after day adjustment failed")
        elif subscription and subscription.get("status") == "active" and days > 0:
            try:
                await vpn.provision_subscription(subscription)
            except Exception:
                logger.exception("Provision after day adjustment failed")
        await update.message.reply_text(f"✅ Дни изменены: {days:+d}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👤 Карточка", callback_data=f"a:user:{user_id}")]]))
        return True

    if name == "broadcast_message":
        payload = broadcast_payload_from_message(update.message)
        if not payload:
            await update.message.reply_text("❌ Отправьте текст, фото или видео одним сообщением.", reply_markup=nav_keyboard("a:menu", "a:menu"))
            return True
        if payload["kind"] == "text" and len(payload["text"]) > 3800:
            await update.message.reply_text("❌ Текст слишком длинный. Сделайте до 3800 символов.", reply_markup=nav_keyboard("a:menu", "a:menu"))
            return True

        count = db.count_broadcast_users()
        context.user_data["state"] = {"name": "broadcast_confirm", "broadcast": payload}
        await send_broadcast_preview(update, payload, count)
        return True

    if name == "server_rename":
        server_id = int(state["server_id"])
        new_name = " ".join((text or "").strip().split())
        if not new_name:
            await update.message.reply_text("❌ Название пустое. Отправьте новое название.", reply_markup=nav_keyboard(f"a:server:{server_id}", "a:menu"))
            return True
        if len(new_name) > 64:
            await update.message.reply_text("❌ Название слишком длинное. Сделайте до 64 символов.", reply_markup=nav_keyboard(f"a:server:{server_id}", "a:menu"))
            return True
        try:
            await vpn.rename_server(server_id, new_name)
        except Exception as exc:
            logger.exception("Rename server failed")
            await update.message.reply_text(f"❌ Не удалось переименовать сервер: {exc}", reply_markup=nav_keyboard(f"a:server:{server_id}", "a:menu"))
            return True
        context.user_data.pop("state", None)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌍 Открыть сервер", callback_data=f"a:server:{server_id}")],
            [InlineKeyboardButton("⬅️ Серверы", callback_data="a:servers"), InlineKeyboardButton("🏠 Админ", callback_data="a:menu")],
        ])
        await update.message.reply_text(f"✅ Название обновлено:\n{new_name}", reply_markup=keyboard)
        return True

    if name in ("add_server_config_static", "add_server_config"):
        try:
            server_id = await vpn.add_static_server(text.strip())
            context.user_data.pop("state", None)
            await update.message.reply_text(f"✅ Готовый конфиг добавлен: #{server_id}\n\nОн уже добавлен во все активные подписки.", reply_markup=admin_keyboard())
        except Exception as exc:
            logger.exception("Add static server failed")
            await update.message.reply_text(f"❌ Не удалось добавить конфиг: {exc}", reply_markup=admin_keyboard())
        return True

    if name == "add_server_config_xui":
        context.user_data["state"] = {"name": "add_server_xui_url", "config_url": text.strip()}
        await update.message.reply_text("Теперь отправьте URL панели 3X-UI, например http://127.0.0.1:54321/path")
        return True

    if name == "add_server_xui_url":
        state["xui_url"] = text.strip()
        state["name"] = "add_server_username"
        context.user_data["state"] = state
        await update.message.reply_text("Логин 3X-UI:")
        return True

    if name == "add_server_username":
        state["xui_username"] = text.strip()
        state["name"] = "add_server_password"
        context.user_data["state"] = state
        await update.message.reply_text("Пароль 3X-UI:")
        return True

    if name == "add_server_password":
        state["xui_password"] = text.strip()
        state["name"] = "add_server_inbound"
        context.user_data["state"] = state
        await update.message.reply_text("Inbound ID:")
        return True

    if name == "add_server_inbound":
        try:
            inbound_id = int(text.strip())
            server_id = await vpn.add_server(
                config_url=state["config_url"],
                xui_url=state["xui_url"],
                xui_username=state["xui_username"],
                xui_password=state["xui_password"],
                inbound_id=inbound_id,
            )
            context.user_data.pop("state", None)
            await update.message.reply_text(f"✅ Сервер добавлен: #{server_id}", reply_markup=admin_keyboard())
        except Exception as exc:
            logger.exception("Add server failed")
            await update.message.reply_text(f"❌ Не удалось добавить сервер: {exc}", reply_markup=admin_keyboard())
        return True

    return False


async def check_jobs(context: ContextTypes.DEFAULT_TYPE):
    try:
        expired = await vpn.expire_subscriptions()
        for sub in expired:
            user = db.get_user_by_id(sub["user_id"])
            if user:
                await context.bot.send_message(user["telegram_id"], "⏰ Подписка истекла. Можно продлить в меню.")
        await vpn.sync_traffic()
    except Exception:
        logger.exception("Background jobs failed")


async def check_pending_payments(context: ContextTypes.DEFAULT_TYPE):
    if not payments.enabled:
        return
    for payment in db.list_pending_provider_payments(provider=payments.provider, limit=30):
        try:
            result = await payments.handle_webhook({
                "event": "payment.background_check",
                "object": {"id": payment["provider_payment_id"]},
            })
        except Exception:
            logger.exception("Pending payment background check failed")
            continue

        if result.get("status") == "succeeded" and result.get("activated") and result.get("subscription"):
            try:
                await vpn.provision_subscription(result["subscription"])
            except Exception:
                logger.exception("Provision after pending payment background check failed")
            await send_payment_ready_message(context, payment["telegram_id"], result["subscription"])
            await notify_referral_payment_bonus(context, result.get("referral_bonus"))


async def notify_referral_bonus(context: ContextTypes.DEFAULT_TYPE, result: Dict):
    status = result.get("status")
    bonus_days = int(result.get("bonus_days") or REFERRAL_BONUS_DAYS)
    if status == "credited_on_signup":
        text = (
            f"🎁 По вашей ссылке пришел новый пользователь.\n\n"
            f"+{bonus_days} дня начислены. Если подписки нет, дни добавятся при первой подписке."
        )
    elif status == "awaiting_paid_payment":
        text = (
            "🎁 По вашей ссылке пришел новый пользователь.\n\n"
            "Это уже 6-й или следующий реферал: бонус придет после его первой оплаты."
        )
    elif status == "bonus_disabled":
        text = "🎁 Новый реферал записан, но реферальные бонусы для аккаунта отключены админом."
    else:
        text = f"🎁 По вашей ссылке пришел новый пользователь. Статус: {status}"
    try:
        await context.bot.send_message(result["referrer_telegram_id"], text)
    except Exception:
        logger.exception("Referral bonus notify failed")


def schedule_payment_auto_check(context: ContextTypes.DEFAULT_TYPE, payment: Dict, chat_id: int):
    provider_payment_id = payment.get("provider_payment_id")
    if not provider_payment_id or not context.job_queue:
        return
    context.job_queue.run_repeating(
        auto_check_payment_job,
        interval=PAYMENT_AUTO_CHECK_INTERVAL,
        first=15,
        name=f"payment:{provider_payment_id}",
        data={
            "provider_payment_id": provider_payment_id,
            "chat_id": chat_id,
            "attempts": 0,
        },
    )


async def auto_check_payment_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    data = job.data or {}
    data["attempts"] = int(data.get("attempts") or 0) + 1
    provider_payment_id = data.get("provider_payment_id")
    chat_id = data.get("chat_id")
    if not provider_payment_id:
        job.schedule_removal()
        return

    try:
        result = await payments.handle_webhook({"event": "payment.auto_check", "object": {"id": provider_payment_id}})
    except Exception:
        logger.exception("Auto payment check failed")
        if data["attempts"] >= PAYMENT_AUTO_CHECK_ATTEMPTS:
            job.schedule_removal()
        return

    status = result.get("status")
    if status == "succeeded":
        subscription = result.get("subscription")
        if result.get("activated") and subscription:
            try:
                await vpn.provision_subscription(subscription)
            except Exception:
                logger.exception("Provision after auto payment check failed")
            await send_payment_ready_message(context, chat_id, subscription)
            await notify_referral_payment_bonus(context, result.get("referral_bonus"))
        job.schedule_removal()
        return

    if status in {"canceled", "failed"}:
        if chat_id:
            await context.bot.send_message(chat_id, "❌ Платеж отменен. Подписка не выдана.")
        job.schedule_removal()
        return

    if data["attempts"] >= PAYMENT_AUTO_CHECK_ATTEMPTS:
        job.schedule_removal()


async def send_payment_ready_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, subscription: Dict):
    if not chat_id or not subscription:
        return
    url = f"{PUBLIC_BASE_URL}/sub/{subscription['subscription_key']}"
    open_url = f"{PUBLIC_BASE_URL}/open/{subscription['subscription_key']}"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Открыть в HAPP/Hub", url=open_url)],
        [InlineKeyboardButton("🔐 Мой VPN", callback_data="u:vpn")],
    ])
    await context.bot.send_message(
        chat_id,
        "✅ Оплата прошла\n\n"
        "🔗 Ваша VPN подписка готова:\n"
        f"{url}",
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


async def notify_referral_payment_bonus(context: ContextTypes.DEFAULT_TYPE, referral_bonus: Dict):
    if not referral_bonus or not referral_bonus.get("ok"):
        return
    try:
        await context.bot.send_message(
            referral_bonus["referrer_telegram_id"],
            f"🎁 Реферал оплатил подписку.\n\n+{int(referral_bonus.get('bonus_days') or REFERRAL_BONUS_DAYS)} дня начислены.",
        )
    except Exception:
        logger.exception("Paid referral bonus notify failed")


def _user_label(user: Dict) -> str:
    if not user:
        return "unknown"
    username = f"@{user['username']}" if user.get("username") else "без username"
    name = user.get("first_name") or "Пользователь"
    return f"{name} · {username}"


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(vpn.ensure_default_server_from_env())
    except Exception:
        logger.exception("Default server bootstrap failed")

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.VIDEO) & ~filters.COMMAND, handle_message))

    if application.job_queue:
        application.job_queue.run_repeating(check_jobs, interval=3600, first=30)
        application.job_queue.run_repeating(check_pending_payments, interval=60, first=20)

    logger.info("VPN bot started")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
