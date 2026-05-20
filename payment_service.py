#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
from typing import Dict, Optional

import aiohttp

from database import Database


class PaymentService:
    def __init__(self, db: Database):
        self.db = db
        self.provider = os.getenv("PAYMENT_PROVIDER", "payment_provider").strip() or "payment_provider"
        self.provider_name = os.getenv("PAYMENT_PROVIDER_NAME", "Payment provider").strip() or "Payment provider"
        self.account_id = os.getenv("PAYMENT_ACCOUNT_ID", "").strip()
        self.secret_key = os.getenv("PAYMENT_SECRET_KEY", "").strip()
        self.api_url = os.getenv("PAYMENT_API_URL", "").rstrip("/")
        self.currency = os.getenv("PAYMENT_CURRENCY", "RUB").strip() or "RUB"
        self.return_url = os.getenv("PAYMENT_RETURN_URL") or os.getenv("PUBLIC_BASE_URL") or "https://t.me/"

    @property
    def enabled(self) -> bool:
        return bool(self.account_id and self.secret_key and self.api_url)

    async def create_payment_for_plan(self, user: Dict, plan: Dict) -> Dict:
        local_id = self.db.create_payment(user["id"], plan, provider=self.provider)
        payment = self.db.get_payment_by_id(local_id)
        return_url = self.local_payment_return_url(local_id)

        if not self.enabled:
            return {
                "local_payment_id": local_id,
                "provider_payment_id": None,
                "confirmation_url": None,
                "status": "pending",
                "error": "Payment provider is not configured",
            }

        payload = {
            "amount": {
                "value": f"{float(plan['price']):.2f}",
                "currency": self.currency,
            },
            "capture": True,
            "confirmation": {
                "type": "redirect",
                "return_url": return_url,
            },
            "description": f"VPN подписка: {plan['name']}",
            "metadata": {
                "local_payment_id": str(local_id),
                "user_id": str(user["id"]),
                "telegram_id": str(user["telegram_id"]),
                "plan_id": plan["id"],
            },
        }
        headers = {
            "Idempotence-Key": payment["idempotence_key"],
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.api_url}/payments",
                auth=aiohttp.BasicAuth(self.account_id, self.secret_key),
                headers=headers,
                json=payload,
            ) as response:
                data = await response.json(content_type=None)
                if response.status >= 400:
                    self.db.mark_payment_status(payment["payment_id"], "failed", json.dumps(data, ensure_ascii=False))
                    raise RuntimeError(f"{self.provider_name} create payment failed: {response.status} {data}")

        confirmation_url = (data.get("confirmation") or {}).get("confirmation_url")
        self.db.attach_provider_payment(local_id, data["id"], confirmation_url)
        return {
            "local_payment_id": local_id,
            "provider_payment_id": data["id"],
            "confirmation_url": confirmation_url,
            "status": data.get("status", "pending"),
        }

    def local_payment_return_url(self, local_payment_id: int) -> str:
        return f"{self.return_url.rstrip('/')}/pay/return/{local_payment_id}"

    async def fetch_payment(self, provider_payment_id: str) -> Optional[Dict]:
        if not self.enabled:
            return None
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.api_url}/payments/{provider_payment_id}",
                auth=aiohttp.BasicAuth(self.account_id, self.secret_key),
            ) as response:
                data = await response.json(content_type=None)
                if response.status >= 400:
                    raise RuntimeError(f"{self.provider_name} fetch payment failed: {response.status} {data}")
                return data

    async def handle_webhook(self, payload: Dict) -> Dict:
        event = payload.get("event")
        obj = payload.get("object") or {}
        provider_payment_id = obj.get("id")
        if not provider_payment_id:
            return {"ok": False, "reason": "missing payment id"}

        previous = self.db.get_payment_by_provider_id(provider_payment_id)
        verified = await self.fetch_payment(provider_payment_id) if self.enabled else obj
        status = verified.get("status") or obj.get("status") or "pending"

        payment = self.db.mark_payment_status(
            provider_payment_id,
            status,
            json.dumps({"event": event, "object": verified}, ensure_ascii=False),
        )
        if not payment:
            return {"ok": False, "reason": "payment not found", "provider_payment_id": provider_payment_id}

        if status != "succeeded":
            return {"ok": True, "status": status, "payment": payment, "activated": False}

        if previous and previous.get("status") == "succeeded" and previous.get("subscription_id"):
            subscription = self.db.get_subscription_by_id(previous["subscription_id"])
            return {"ok": True, "status": status, "payment": payment, "subscription": subscription, "activated": False}

        subscription = self.db.create_or_extend_subscription(payment)
        payment = self.db.get_payment_by_id(payment["id"])
        referral_bonus = self.db.credit_paid_referral_bonus(payment)
        return {
            "ok": True,
            "status": status,
            "payment": payment,
            "subscription": subscription,
            "activated": True,
            "referral_bonus": referral_bonus,
        }

    async def verify_local_payment(self, local_payment_id: int) -> Dict:
        payment = self.db.get_payment_by_id(local_payment_id)
        if not payment:
            return {"ok": False, "reason": "payment not found"}
        provider_payment_id = payment.get("provider_payment_id") or payment.get("payment_id")
        if not provider_payment_id:
            return {"ok": False, "reason": "provider payment id missing", "payment": payment}
        return await self.handle_webhook({
            "event": "payment.return",
            "object": {"id": provider_payment_id},
        })
