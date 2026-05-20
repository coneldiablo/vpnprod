#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple


def utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat()


class Database:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or os.getenv("DB_PATH", "vpn_bot.db")
        self.init_db()

    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_db(self):
        conn = self.get_connection()
        try:
            self._create_base_tables(conn)
            self._migrate_existing_tables(conn)
            self._create_indexes(conn)
            self.seed_default_plans(conn)
            conn.commit()
        finally:
            conn.close()

    def _create_base_tables(self, conn):
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                referral_code TEXT,
                referred_by_user_id INTEGER,
                referral_bonus_pending_days INTEGER DEFAULT 0,
                referral_bonus_applied_days INTEGER DEFAULT 0,
                referral_bonus_disabled INTEGER DEFAULT 0,
                is_blocked INTEGER DEFAULT 0,
                trial_used INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (referred_by_user_id) REFERENCES users(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS plans (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                months INTEGER NOT NULL,
                price REAL NOT NULL,
                traffic_limit_gb INTEGER NOT NULL,
                is_active INTEGER DEFAULT 1,
                sort_order INTEGER DEFAULT 0
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                plan_id TEXT,
                x_ui_client_id TEXT,
                x_ui_inbound_id INTEGER,
                config_url TEXT,
                subscription_key TEXT,
                start_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                end_date TIMESTAMP,
                status TEXT DEFAULT 'active',
                traffic_used INTEGER DEFAULT 0,
                traffic_limit INTEGER,
                revoked_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (plan_id) REFERENCES plans(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                plan_id TEXT,
                subscription_id INTEGER,
                amount REAL NOT NULL,
                duration_months INTEGER NOT NULL,
                payment_id TEXT,
                provider TEXT DEFAULT 'payment_provider',
                provider_payment_id TEXT,
                confirmation_url TEXT,
                idempotence_key TEXT,
                status TEXT DEFAULT 'pending',
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (plan_id) REFERENCES plans(id),
                FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                country_code TEXT DEFAULT 'UN',
                country_name TEXT DEFAULT 'Unknown',
                flag TEXT DEFAULT '🌍',
                host TEXT,
                port INTEGER,
                protocol TEXT,
                config_url TEXT NOT NULL,
                xui_url TEXT NOT NULL DEFAULT '',
                xui_username TEXT NOT NULL DEFAULT '',
                xui_password_encrypted TEXT NOT NULL DEFAULT '',
                inbound_id INTEGER NOT NULL DEFAULT 0,
                status TEXT DEFAULT 'active',
                sort_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS server_clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                subscription_id INTEGER NOT NULL,
                server_id INTEGER NOT NULL,
                xui_client_id TEXT NOT NULL,
                xui_email TEXT NOT NULL,
                config_url TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                traffic_up INTEGER DEFAULT 0,
                traffic_down INTEGER DEFAULT 0,
                traffic_limit INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(subscription_id, server_id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (subscription_id) REFERENCES subscriptions(id),
                FOREIGN KEY (server_id) REFERENCES servers(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS support_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                admin_id INTEGER,
                direction TEXT NOT NULL,
                message_text TEXT NOT NULL,
                status TEXT DEFAULT 'open',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (admin_id) REFERENCES users(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_user_id INTEGER NOT NULL,
                referred_user_id INTEGER UNIQUE NOT NULL,
                bonus_days INTEGER NOT NULL DEFAULT 3,
                status TEXT DEFAULT 'pending',
                credited_payment_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                credited_at TIMESTAMP,
                FOREIGN KEY (referrer_user_id) REFERENCES users(id),
                FOREIGN KEY (referred_user_id) REFERENCES users(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS subscription_devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subscription_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                device_key TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                is_primary INTEGER DEFAULT 0,
                last_seen_at TIMESTAMP,
                last_ip TEXT,
                last_user_agent TEXT,
                request_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (subscription_id) REFERENCES subscriptions(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS referral_adjustments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_user_id INTEGER,
                target_user_id INTEGER NOT NULL,
                days_delta INTEGER NOT NULL,
                reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (admin_user_id) REFERENCES users(id),
                FOREIGN KEY (target_user_id) REFERENCES users(id)
            )
        """)

    def _migrate_existing_tables(self, conn):
        migrations = {
            "users": {
                "referral_code": "TEXT",
                "referred_by_user_id": "INTEGER",
                "referral_bonus_pending_days": "INTEGER DEFAULT 0",
                "referral_bonus_applied_days": "INTEGER DEFAULT 0",
                "referral_bonus_disabled": "INTEGER DEFAULT 0",
                "is_blocked": "INTEGER DEFAULT 0",
                "trial_used": "INTEGER DEFAULT 0",
                "updated_at": "TIMESTAMP",
            },
            "subscriptions": {
                "plan_id": "TEXT",
                "subscription_key": "TEXT",
                "revoked_reason": "TEXT",
                "created_at": "TIMESTAMP",
                "updated_at": "TIMESTAMP",
            },
            "payments": {
                "plan_id": "TEXT",
                "subscription_id": "INTEGER",
            "provider": "TEXT DEFAULT 'payment_provider'",
                "provider_payment_id": "TEXT",
                "confirmation_url": "TEXT",
                "idempotence_key": "TEXT",
                "metadata": "TEXT",
                "updated_at": "TIMESTAMP",
            },
            "referrals": {
                "credited_payment_id": "INTEGER",
            },
        }

        cursor = conn.cursor()
        for table, columns in migrations.items():
            existing = {row["name"] for row in cursor.execute(f"PRAGMA table_info({table})")}
            for column, definition in columns.items():
                if column not in existing:
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

        cursor.execute("""
            UPDATE subscriptions
            SET subscription_key = lower(hex(randomblob(12)))
            WHERE subscription_key IS NULL OR subscription_key = ''
        """)
        now = utc_now_iso()
        cursor.execute("UPDATE users SET updated_at = COALESCE(updated_at, created_at, ?)", (now,))
        cursor.execute("UPDATE users SET referral_bonus_pending_days = COALESCE(referral_bonus_pending_days, 0)")
        cursor.execute("UPDATE users SET referral_bonus_applied_days = COALESCE(referral_bonus_applied_days, 0)")
        cursor.execute("UPDATE users SET referral_bonus_disabled = COALESCE(referral_bonus_disabled, 0)")
        for row in cursor.execute("SELECT id FROM users WHERE referral_code IS NULL OR referral_code = ''").fetchall():
            cursor.execute("UPDATE users SET referral_code = ? WHERE id = ?", (self._generate_referral_code(cursor), row["id"]))
        cursor.execute("UPDATE subscriptions SET created_at = COALESCE(created_at, start_date, ?), updated_at = COALESCE(updated_at, start_date, ?)", (now, now))
        cursor.execute("UPDATE payments SET updated_at = COALESCE(updated_at, created_at, ?)", (now,))

    def _create_indexes(self, conn):
        cursor = conn.cursor()
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code) WHERE referral_code IS NOT NULL")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_key ON subscriptions(subscription_key)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_user_status ON subscriptions(user_id, status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_payments_provider_id ON payments(provider_payment_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_server_clients_sub ON server_clients(subscription_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_support_user_status ON support_messages(user_id, status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_referrals_referred ON referrals(referred_user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_referrals_status ON referrals(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_devices_subscription ON subscription_devices(subscription_id, status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_devices_user ON subscription_devices(user_id, status)")

    def seed_default_plans(self, conn=None):
        should_close = conn is None
        conn = conn or self.get_connection()
        try:
            traffic = int(os.getenv("TRAFFIC_LIMIT_GB", "100"))
            trial_days = int(os.getenv("TRIAL_DAYS", "3"))
            trial_traffic = int(os.getenv("TRIAL_TRAFFIC_GB") or traffic)
            trial_name = "1 день бесплатно" if trial_days == 1 else f"{trial_days} дня бесплатно"
            plans = [
                ("trial", trial_name, 0, 0.0, trial_traffic, 0, 0),
                ("1", "1 месяц", 1, float(os.getenv("PRICE_1_MONTH", "99")), traffic, 1, 1),
                ("3", "3 месяца", 3, float(os.getenv("PRICE_3_MONTHS", "249")), traffic * 3, 1, 2),
                ("6", "6 месяцев", 6, float(os.getenv("PRICE_6_MONTHS", "499")), traffic * 6, 1, 3),
            ]
            conn.executemany("""
                INSERT INTO plans (id, name, months, price, traffic_limit_gb, is_active, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    months = excluded.months,
                    price = excluded.price,
                    traffic_limit_gb = excluded.traffic_limit_gb,
                    is_active = excluded.is_active,
                    sort_order = excluded.sort_order
            """, plans)
            conn.execute("UPDATE plans SET is_active = 0 WHERE id NOT IN ('trial', '1', '3', '6')")
            conn.commit()
        finally:
            if should_close:
                conn.close()

    def add_user(self, telegram_id: int, username: str = None, first_name: str = None) -> int:
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO users (telegram_id, username, first_name, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    updated_at = excluded.updated_at
            """, (telegram_id, username, first_name, utc_now_iso()))
            row = cursor.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
            self._ensure_referral_code(cursor, row["id"])
            conn.commit()
            return row["id"]
        finally:
            conn.close()

    def get_user(self, telegram_id: int) -> Optional[Dict]:
        return self._fetch_one("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))

    def get_user_by_id(self, user_id: int) -> Optional[Dict]:
        return self._fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))

    def get_user_by_referral_code(self, code: str) -> Optional[Dict]:
        return self._fetch_one("SELECT * FROM users WHERE referral_code = ?", ((code or "").strip(),))

    def search_users(self, query: str, limit: int = 10) -> List[Dict]:
        q = f"%{query.strip().lstrip('@')}%"
        return self._fetch_all("""
            SELECT * FROM users
            WHERE CAST(telegram_id AS TEXT) LIKE ? OR username LIKE ? OR first_name LIKE ?
            ORDER BY updated_at DESC
            LIMIT ?
        """, (q, q, q, limit))

    def list_recent_users(self, limit: int = 10) -> List[Dict]:
        return self._fetch_all("""
            SELECT * FROM users
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,))

    def list_broadcast_users(self) -> List[Dict]:
        return self._fetch_all("""
            SELECT * FROM users
            ORDER BY created_at ASC
        """)

    def count_broadcast_users(self) -> int:
        row = self._fetch_one("SELECT COUNT(*) AS total FROM users")
        return int(row["total"] if row else 0)

    def set_user_blocked(self, user_id: int, blocked: bool):
        self._execute(
            "UPDATE users SET is_blocked = ?, updated_at = ? WHERE id = ?",
            (1 if blocked else 0, utc_now_iso(), user_id),
        )

    def list_plans(self) -> List[Dict]:
        return self._fetch_all("SELECT * FROM plans WHERE is_active = 1 ORDER BY sort_order, months")

    def get_plan(self, plan_id: str) -> Optional[Dict]:
        return self._fetch_one("SELECT * FROM plans WHERE id = ? AND is_active = 1", (plan_id,))

    def create_payment(self, user_id: int, plan: Dict, provider: str = "payment_provider") -> int:
        idempotence_key = secrets.token_hex(16)
        return self._insert("""
            INSERT INTO payments
            (user_id, plan_id, amount, duration_months, payment_id, provider, idempotence_key, status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """, (
            user_id,
            plan["id"],
            plan["price"],
            plan["months"],
            f"local_{secrets.token_hex(8)}",
            provider,
            idempotence_key,
            utc_now_iso(),
        ))

    def create_manual_payment(self, user_id: int, plan_id: str) -> Dict:
        plan = self.get_plan(plan_id)
        if not plan:
            raise ValueError("Plan not found")
        payment_id = f"manual_{secrets.token_hex(8)}"
        local_id = self._insert("""
            INSERT INTO payments
            (user_id, plan_id, amount, duration_months, payment_id, provider,
             provider_payment_id, idempotence_key, status, updated_at)
            VALUES (?, ?, ?, ?, ?, 'manual', ?, ?, 'succeeded', ?)
        """, (
            user_id,
            plan["id"],
            plan["price"],
            plan["months"],
            payment_id,
            payment_id,
            secrets.token_hex(16),
            utc_now_iso(),
        ))
        return self.get_payment_by_id(local_id)

    def attach_provider_payment(self, local_payment_id: int, provider_payment_id: str, confirmation_url: str):
        self._execute("""
            UPDATE payments
            SET provider_payment_id = ?, payment_id = ?, confirmation_url = ?, updated_at = ?
            WHERE id = ?
        """, (provider_payment_id, provider_payment_id, confirmation_url, utc_now_iso(), local_payment_id))

    def mark_payment_status(self, provider_payment_id: str, status: str, metadata: str = None) -> Optional[Dict]:
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            row = cursor.execute("""
                SELECT * FROM payments
                WHERE provider_payment_id = ? OR payment_id = ?
            """, (provider_payment_id, provider_payment_id)).fetchone()
            if not row:
                return None
            cursor.execute("""
                UPDATE payments
                SET status = ?, metadata = COALESCE(?, metadata), updated_at = ?
                WHERE id = ?
            """, (status, metadata, utc_now_iso(), row["id"]))
            conn.commit()
            return self._row_to_dict(cursor.execute("SELECT * FROM payments WHERE id = ?", (row["id"],)).fetchone())
        finally:
            conn.close()

    def get_payment_by_provider_id(self, provider_payment_id: str) -> Optional[Dict]:
        return self._fetch_one("""
            SELECT * FROM payments
            WHERE provider_payment_id = ? OR payment_id = ?
        """, (provider_payment_id, provider_payment_id))

    def get_payment_by_id(self, payment_id: int) -> Optional[Dict]:
        return self._fetch_one("SELECT * FROM payments WHERE id = ?", (payment_id,))

    def get_payment(self, payment_id: str) -> Optional[Dict]:
        return self.get_payment_by_provider_id(payment_id)

    def list_recent_payments(self, limit: int = 10) -> List[Dict]:
        return self._fetch_all("""
            SELECT p.*, u.telegram_id, u.username, u.first_name, pl.name AS plan_name
            FROM payments p
            JOIN users u ON u.id = p.user_id
            LEFT JOIN plans pl ON pl.id = p.plan_id
            ORDER BY p.created_at DESC
            LIMIT ?
        """, (limit,))

    def list_pending_provider_payments(self, provider: str = "payment_provider", limit: int = 30) -> List[Dict]:
        return self._fetch_all("""
            SELECT p.*, u.telegram_id, u.username, u.first_name
            FROM payments p
            JOIN users u ON u.id = p.user_id
            WHERE p.provider = ?
              AND p.status = 'pending'
              AND p.provider_payment_id IS NOT NULL
            ORDER BY p.created_at DESC
            LIMIT ?
        """, (provider, limit))

    def create_or_extend_subscription(self, payment: Dict) -> Dict:
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            payment_row = cursor.execute("SELECT * FROM payments WHERE id = ?", (payment["id"],)).fetchone()
            if not payment_row:
                raise ValueError("Payment not found")
            if payment_row["subscription_id"]:
                subscription = cursor.execute(
                    "SELECT * FROM subscriptions WHERE id = ?",
                    (payment_row["subscription_id"],),
                ).fetchone()
                conn.commit()
                return self._row_to_dict(subscription)
            payment = self._row_to_dict(payment_row)

            user = cursor.execute("SELECT * FROM users WHERE id = ?", (payment["user_id"],)).fetchone()
            plan = cursor.execute("SELECT * FROM plans WHERE id = ?", (payment["plan_id"],)).fetchone()
            if not user or not plan:
                raise ValueError("User or plan not found for payment")

            active = cursor.execute("""
                SELECT * FROM subscriptions
                WHERE user_id = ? AND status = 'active'
                ORDER BY end_date DESC
                LIMIT 1
            """, (payment["user_id"],)).fetchone()

            now = datetime.utcnow()
            if active and active["end_date"]:
                current_end = datetime.fromisoformat(active["end_date"])
                start = current_end if current_end > now else now
                end_date = start + timedelta(days=int(plan["months"]) * 30)
                cursor.execute("""
                    UPDATE subscriptions
                    SET plan_id = ?, end_date = ?, traffic_limit = COALESCE(traffic_limit, 0) + ?,
                        status = 'active', updated_at = ?
                    WHERE id = ?
                """, (
                    plan["id"],
                    end_date.isoformat(),
                    int(plan["traffic_limit_gb"]) * 1024 ** 3,
                    utc_now_iso(),
                    active["id"],
                ))
                subscription_id = active["id"]
            else:
                end_date = now + timedelta(days=int(plan["months"]) * 30)
                subscription_key = secrets.token_urlsafe(18)
                traffic_limit = int(plan["traffic_limit_gb"]) * 1024 ** 3
                cursor.execute("""
                    INSERT INTO subscriptions
                    (user_id, plan_id, subscription_key, start_date, end_date, status, traffic_limit, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
                """, (
                    payment["user_id"],
                    plan["id"],
                    subscription_key,
                    utc_now_iso(),
                    end_date.isoformat(),
                    traffic_limit,
                    utc_now_iso(),
                ))
                subscription_id = cursor.lastrowid

            cursor.execute(
                "UPDATE payments SET subscription_id = ?, updated_at = ? WHERE id = ?",
                (subscription_id, utc_now_iso(), payment["id"]),
            )
            self._apply_pending_referral_bonus_locked(cursor, payment["user_id"], subscription_id)
            conn.commit()
            return self._row_to_dict(cursor.execute("SELECT * FROM subscriptions WHERE id = ?", (subscription_id,)).fetchone())
        finally:
            conn.close()

    def create_trial_subscription(self, user_id: int, days: int = 3, traffic_gb: int = None) -> Dict:
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            user = cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not user:
                raise ValueError("User not found")
            if int(user["trial_used"] or 0):
                raise ValueError("Trial already used")

            active = cursor.execute("""
                SELECT * FROM subscriptions
                WHERE user_id = ? AND status = 'active'
                ORDER BY end_date DESC
                LIMIT 1
            """, (user_id,)).fetchone()
            if active:
                raise ValueError("Active subscription already exists")

            plan = cursor.execute("SELECT * FROM plans WHERE id = 'trial'").fetchone()
            if not plan:
                self.seed_default_plans(conn)

            now = datetime.utcnow()
            end_date = now + timedelta(days=int(days))
            subscription_key = secrets.token_urlsafe(18)
            traffic_limit = int(traffic_gb if traffic_gb is not None else os.getenv("TRIAL_TRAFFIC_GB") or os.getenv("TRAFFIC_LIMIT_GB", "100")) * 1024 ** 3
            cursor.execute("""
                INSERT INTO subscriptions
                (user_id, plan_id, subscription_key, start_date, end_date, status, traffic_limit, updated_at)
                VALUES (?, 'trial', ?, ?, ?, 'active', ?, ?)
            """, (
                user_id,
                subscription_key,
                utc_now_iso(),
                end_date.isoformat(),
                traffic_limit,
                utc_now_iso(),
            ))
            subscription_id = cursor.lastrowid
            cursor.execute("UPDATE users SET trial_used = 1, updated_at = ? WHERE id = ?", (utc_now_iso(), user_id))
            self._apply_pending_referral_bonus_locked(cursor, user_id, subscription_id)
            conn.commit()
            return self._row_to_dict(cursor.execute("SELECT * FROM subscriptions WHERE id = ?", (subscription_id,)).fetchone())
        finally:
            conn.close()

    def register_referral(self, referred_user_id: int, referral_code: str, bonus_days: int = 3) -> Dict:
        code = (referral_code or "").strip()
        if not code:
            return {"ok": False, "reason": "empty_code"}

        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            referrer = cursor.execute("SELECT * FROM users WHERE referral_code = ?", (code,)).fetchone()
            referred = cursor.execute("SELECT * FROM users WHERE id = ?", (referred_user_id,)).fetchone()
            if not referrer:
                return {"ok": False, "reason": "referrer_not_found"}
            if not referred:
                return {"ok": False, "reason": "referred_not_found"}
            if int(referrer["id"]) == int(referred_user_id):
                return {"ok": False, "reason": "self_referral"}
            if referred["referred_by_user_id"]:
                return {"ok": False, "reason": "already_referred"}
            existing = cursor.execute(
                "SELECT id FROM referrals WHERE referred_user_id = ?",
                (referred_user_id,),
            ).fetchone()
            if existing:
                return {"ok": False, "reason": "already_registered"}

            now = utc_now_iso()
            referrer_id = int(referrer["id"])
            referrer_disabled = bool(int(referrer["referral_bonus_disabled"] or 0))
            credited_count = self._count_credited_referrals_locked(cursor, referrer_id)
            status = "bonus_disabled" if referrer_disabled else (
                "credited_on_signup" if credited_count < 5 else "awaiting_paid_payment"
            )
            cursor.execute("""
                INSERT INTO referrals (referrer_user_id, referred_user_id, bonus_days, status)
                VALUES (?, ?, ?, ?)
            """, (referrer_id, referred_user_id, int(bonus_days), status))
            referral_id = cursor.lastrowid
            cursor.execute("""
                UPDATE users
                SET referred_by_user_id = ?, updated_at = ?
                WHERE id = ?
            """, (referrer_id, now, referred_user_id))

            credited = False
            if status == "credited_on_signup":
                credited = self._credit_referral_bonus_locked(cursor, referrer_id, int(bonus_days), referral_id, status)
            conn.commit()
            return {
                "ok": True,
                "status": status,
                "credited": credited,
                "bonus_days": int(bonus_days),
                "referrer_user_id": referrer_id,
                "referrer_telegram_id": int(referrer["telegram_id"]),
            }
        finally:
            conn.close()

    def get_referral_summary(self, user_id: int) -> Dict:
        row = self._fetch_one("""
            SELECT
                u.referral_code,
                u.referred_by_user_id,
                u.referral_bonus_pending_days,
                u.referral_bonus_applied_days,
                u.referral_bonus_disabled,
                ref.username AS referrer_username,
                ref.first_name AS referrer_first_name,
                COUNT(r.id) AS invited_count,
                COALESCE(SUM(CASE WHEN r.status IN ('applied', 'credited_on_signup', 'credited_on_payment') THEN 1 ELSE 0 END), 0) AS credited_count,
                COALESCE(SUM(CASE WHEN r.status = 'awaiting_paid_payment' THEN 1 ELSE 0 END), 0) AS awaiting_payment_count,
                COALESCE(SUM(CASE WHEN r.status IN ('applied', 'credited_on_signup', 'credited_on_payment') THEN r.bonus_days ELSE 0 END), 0) AS applied_days_from_rows,
                COALESCE(SUM(CASE WHEN r.status IN ('pending', 'awaiting_paid_payment') THEN r.bonus_days ELSE 0 END), 0) AS pending_days_from_rows
            FROM users u
            LEFT JOIN users ref ON ref.id = u.referred_by_user_id
            LEFT JOIN referrals r ON r.referrer_user_id = u.id
            WHERE u.id = ?
            GROUP BY u.id
        """, (user_id,))
        return row or {
            "referral_code": "",
            "referred_by_user_id": None,
            "referral_bonus_pending_days": 0,
            "referral_bonus_applied_days": 0,
            "referral_bonus_disabled": 0,
            "referrer_username": None,
            "referrer_first_name": None,
            "invited_count": 0,
            "credited_count": 0,
            "awaiting_payment_count": 0,
            "applied_days_from_rows": 0,
            "pending_days_from_rows": 0,
        }

    def _count_credited_referrals_locked(self, cursor, user_id: int) -> int:
        row = cursor.execute("""
            SELECT COUNT(*) AS total
            FROM referrals
            WHERE referrer_user_id = ?
              AND status IN ('applied', 'credited_on_signup', 'credited_on_payment')
        """, (user_id,)).fetchone()
        return int(row["total"] if row else 0)

    def _credit_referral_bonus_locked(self, cursor, user_id: int, bonus_days: int,
                                      referral_id: int = None, credited_status: str = "applied",
                                      credited_payment_id: int = None) -> bool:
        active = cursor.execute("""
            SELECT * FROM subscriptions
            WHERE user_id = ? AND status = 'active'
            ORDER BY end_date DESC
            LIMIT 1
        """, (user_id,)).fetchone()
        now = utc_now_iso()
        if active and active["end_date"]:
            base = datetime.fromisoformat(active["end_date"])
            start = base if base > datetime.utcnow() else datetime.utcnow()
            new_end = start + timedelta(days=int(bonus_days))
            cursor.execute("""
                UPDATE subscriptions
                SET end_date = ?, updated_at = ?
                WHERE id = ?
            """, (new_end.isoformat(), now, active["id"]))
            cursor.execute("""
                UPDATE users
                SET referral_bonus_applied_days = COALESCE(referral_bonus_applied_days, 0) + ?,
                    updated_at = ?
                WHERE id = ?
            """, (int(bonus_days), now, user_id))
            if referral_id:
                cursor.execute("""
                    UPDATE referrals
                    SET status = ?, credited_at = ?, credited_payment_id = COALESCE(?, credited_payment_id)
                    WHERE id = ?
                """, (credited_status, now, credited_payment_id, referral_id))
            return True

        cursor.execute("""
            UPDATE users
            SET referral_bonus_pending_days = COALESCE(referral_bonus_pending_days, 0) + ?,
                updated_at = ?
            WHERE id = ?
        """, (int(bonus_days), now, user_id))
        if referral_id:
            cursor.execute("""
                UPDATE referrals
                SET status = ?, credited_at = ?, credited_payment_id = COALESCE(?, credited_payment_id)
                WHERE id = ?
            """, (credited_status, now, credited_payment_id, referral_id))
        return False

    def _apply_pending_referral_bonus_locked(self, cursor, user_id: int, subscription_id: int = None) -> int:
        user = cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        pending_days = int(user["referral_bonus_pending_days"] or 0) if user else 0
        if pending_days <= 0:
            return 0

        active = cursor.execute("""
            SELECT * FROM subscriptions
            WHERE id = COALESCE(?, id) AND user_id = ? AND status = 'active'
            ORDER BY end_date DESC
            LIMIT 1
        """, (subscription_id, user_id)).fetchone()
        if not active or not active["end_date"]:
            return 0

        base = datetime.fromisoformat(active["end_date"])
        start = base if base > datetime.utcnow() else datetime.utcnow()
        new_end = start + timedelta(days=pending_days)
        now = utc_now_iso()
        cursor.execute("UPDATE subscriptions SET end_date = ?, updated_at = ? WHERE id = ?", (new_end.isoformat(), now, active["id"]))
        cursor.execute("""
            UPDATE users
            SET referral_bonus_pending_days = 0,
                referral_bonus_applied_days = COALESCE(referral_bonus_applied_days, 0) + ?,
                updated_at = ?
            WHERE id = ?
        """, (pending_days, now, user_id))
        cursor.execute("""
            UPDATE referrals
            SET status = 'applied', credited_at = ?
            WHERE referrer_user_id = ? AND status = 'pending'
        """, (now, user_id))
        return pending_days

    def credit_paid_referral_bonus(self, payment: Dict, bonus_days: int = 3) -> Dict:
        if not payment:
            return {"ok": False, "reason": "missing_payment"}
        if payment.get("provider") in ("manual", "trial", "", None):
            return {"ok": False, "reason": "not_external_paid_provider"}
        if payment.get("status") != "succeeded":
            return {"ok": False, "reason": "payment_not_succeeded"}
        if float(payment.get("amount") or 0) <= 0 or payment.get("plan_id") == "trial":
            return {"ok": False, "reason": "not_paid_plan"}

        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            referral = cursor.execute("""
                SELECT r.*, u.telegram_id AS referrer_telegram_id, u.referral_bonus_disabled
                FROM referrals r
                JOIN users u ON u.id = r.referrer_user_id
                WHERE r.referred_user_id = ? AND r.status = 'awaiting_paid_payment'
                LIMIT 1
            """, (payment["user_id"],)).fetchone()
            if not referral:
                conn.commit()
                return {"ok": False, "reason": "no_waiting_referral"}
            if int(referral["referral_bonus_disabled"] or 0):
                cursor.execute("UPDATE referrals SET status = 'bonus_disabled' WHERE id = ?", (referral["id"],))
                conn.commit()
                return {"ok": False, "reason": "referrer_disabled"}

            applied = self._credit_referral_bonus_locked(
                cursor,
                int(referral["referrer_user_id"]),
                int(referral["bonus_days"] or bonus_days),
                int(referral["id"]),
                "credited_on_payment",
                int(payment["id"]),
            )
            conn.commit()
            return {
                "ok": True,
                "status": "credited_on_payment",
                "credited": applied,
                "bonus_days": int(referral["bonus_days"] or bonus_days),
                "referrer_user_id": int(referral["referrer_user_id"]),
                "referrer_telegram_id": int(referral["referrer_telegram_id"]),
            }
        finally:
            conn.close()

    def list_referral_leaders(self, limit: int = 10) -> List[Dict]:
        return self._fetch_all("""
            SELECT u.id, u.telegram_id, u.username, u.first_name,
                   u.referral_bonus_pending_days, u.referral_bonus_applied_days, u.referral_bonus_disabled,
                   COUNT(r.id) AS invited_count,
                   COALESCE(SUM(CASE WHEN r.status IN ('applied', 'credited_on_signup', 'credited_on_payment') THEN 1 ELSE 0 END), 0) AS credited_count,
                   COALESCE(SUM(CASE WHEN r.status = 'awaiting_paid_payment' THEN 1 ELSE 0 END), 0) AS awaiting_payment_count,
                   COALESCE(SUM(CASE WHEN r.status IN ('applied', 'credited_on_signup', 'credited_on_payment') THEN r.bonus_days ELSE 0 END), 0) AS credited_days
            FROM users u
            LEFT JOIN referrals r ON r.referrer_user_id = u.id
            GROUP BY u.id
            HAVING invited_count > 0
            ORDER BY credited_count DESC, invited_count DESC, credited_days DESC
            LIMIT ?
        """, (limit,))

    def list_referrals_for_user(self, user_id: int, limit: int = 12) -> List[Dict]:
        return self._fetch_all("""
            SELECT r.*, u.telegram_id, u.username, u.first_name
            FROM referrals r
            JOIN users u ON u.id = r.referred_user_id
            WHERE r.referrer_user_id = ?
            ORDER BY r.created_at DESC
            LIMIT ?
        """, (user_id, limit))

    def set_referral_bonus_disabled(self, user_id: int, disabled: bool):
        self._execute(
            "UPDATE users SET referral_bonus_disabled = ?, updated_at = ? WHERE id = ?",
            (1 if disabled else 0, utc_now_iso(), user_id),
        )

    def cancel_pending_referral_bonuses(self, user_id: int, admin_user_id: int = None, reason: str = "admin_cancel_pending") -> int:
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            now = utc_now_iso()
            pending_days = cursor.execute(
                "SELECT COALESCE(referral_bonus_pending_days, 0) AS days FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
            days = int(pending_days["days"] if pending_days else 0)
            cursor.execute("""
                UPDATE referrals
                SET status = 'canceled', credited_at = COALESCE(credited_at, ?)
                WHERE referrer_user_id = ?
                  AND status IN ('pending', 'awaiting_paid_payment')
            """, (now, user_id))
            cursor.execute("""
                UPDATE users
                SET referral_bonus_pending_days = 0, updated_at = ?
                WHERE id = ?
            """, (now, user_id))
            cursor.execute("""
                INSERT INTO referral_adjustments (admin_user_id, target_user_id, days_delta, reason)
                VALUES (?, ?, ?, ?)
            """, (admin_user_id, user_id, -days, reason))
            conn.commit()
            return days
        finally:
            conn.close()

    def adjust_subscription_days(self, user_id: int, days_delta: int, admin_user_id: int = None,
                                 reason: str = "admin_adjust") -> Optional[Dict]:
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            active = cursor.execute("""
                SELECT * FROM subscriptions
                WHERE user_id = ? AND status = 'active'
                ORDER BY end_date DESC
                LIMIT 1
            """, (user_id,)).fetchone()
            now_dt = datetime.utcnow()
            now = utc_now_iso()
            if not active:
                if int(days_delta) <= 0:
                    cursor.execute("""
                        INSERT INTO referral_adjustments (admin_user_id, target_user_id, days_delta, reason)
                        VALUES (?, ?, ?, ?)
                    """, (admin_user_id, user_id, int(days_delta), reason))
                    conn.commit()
                    return None
                traffic_limit = int(os.getenv("TRAFFIC_LIMIT_GB", "100")) * 1024 ** 3
                subscription_key = secrets.token_urlsafe(18)
                end_date = now_dt + timedelta(days=int(days_delta))
                cursor.execute("""
                    INSERT INTO subscriptions
                    (user_id, plan_id, subscription_key, start_date, end_date, status, traffic_limit, updated_at)
                    VALUES (?, NULL, ?, ?, ?, 'active', ?, ?)
                """, (user_id, subscription_key, now, end_date.isoformat(), traffic_limit, now))
                subscription_id = cursor.lastrowid
            else:
                base = datetime.fromisoformat(active["end_date"]) if active["end_date"] else now_dt
                new_end = base + timedelta(days=int(days_delta))
                status = "expired" if new_end <= now_dt else "active"
                cursor.execute("""
                    UPDATE subscriptions
                    SET end_date = ?, status = ?, updated_at = ?
                    WHERE id = ?
                """, (new_end.isoformat(), status, now, active["id"]))
                subscription_id = active["id"]

            cursor.execute("""
                INSERT INTO referral_adjustments (admin_user_id, target_user_id, days_delta, reason)
                VALUES (?, ?, ?, ?)
            """, (admin_user_id, user_id, int(days_delta), reason))
            conn.commit()
            return self.get_subscription_by_id(subscription_id)
        finally:
            conn.close()

    def create_subscription(self, user_id: int, client_id: str, inbound_id: int,
                            config_url: str, end_date: str, traffic_limit: int,
                            subscription_key: str = None) -> int:
        subscription_key = subscription_key or secrets.token_urlsafe(18)
        return self._insert("""
            INSERT INTO subscriptions
            (user_id, x_ui_client_id, x_ui_inbound_id, config_url, subscription_key,
             end_date, status, traffic_limit, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
        """, (user_id, client_id, inbound_id, config_url, subscription_key, end_date, traffic_limit, utc_now_iso()))

    def get_active_subscription(self, user_id: int) -> Optional[Dict]:
        return self._fetch_one("""
            SELECT * FROM subscriptions
            WHERE user_id = ? AND status = 'active'
              AND datetime(replace(end_date, 'T', ' ')) > datetime('now')
            ORDER BY end_date DESC LIMIT 1
        """, (user_id,))

    def get_subscription_by_key(self, subscription_key: str) -> Optional[Dict]:
        return self._fetch_one("""
            SELECT s.*, u.telegram_id, u.username, u.first_name, u.is_blocked
            FROM subscriptions s
            JOIN users u ON u.id = s.user_id
            WHERE s.subscription_key = ?
        """, (subscription_key,))

    def get_subscription_by_id(self, subscription_id: int) -> Optional[Dict]:
        return self._fetch_one("SELECT * FROM subscriptions WHERE id = ?", (subscription_id,))

    def get_user_subscriptions(self, user_id: int) -> List[Dict]:
        return self._fetch_all("""
            SELECT * FROM subscriptions WHERE user_id = ? ORDER BY start_date DESC
        """, (user_id,))

    def list_subscription_devices(self, subscription_id: int, active_only: bool = False) -> List[Dict]:
        status_filter = "AND status = 'active'" if active_only else ""
        return self._fetch_all(f"""
            SELECT * FROM subscription_devices
            WHERE subscription_id = ? {status_filter}
            ORDER BY is_primary DESC, id ASC
        """, (subscription_id,))

    def get_subscription_device_by_id(self, device_id: int) -> Optional[Dict]:
        return self._fetch_one("SELECT * FROM subscription_devices WHERE id = ?", (device_id,))

    def get_subscription_device_by_key(self, device_key: str) -> Optional[Dict]:
        return self._fetch_one("""
            SELECT d.*, s.subscription_key, s.end_date, s.status AS subscription_status,
                   s.traffic_limit, u.telegram_id, u.username, u.first_name, u.is_blocked
            FROM subscription_devices d
            JOIN subscriptions s ON s.id = d.subscription_id
            JOIN users u ON u.id = d.user_id
            WHERE d.device_key = ?
        """, (device_key,))

    def ensure_primary_device(self, subscription: Dict) -> Optional[Dict]:
        existing = self._fetch_one("""
            SELECT * FROM subscription_devices
            WHERE subscription_id = ? AND is_primary = 1
            ORDER BY id ASC LIMIT 1
        """, (subscription["id"],))
        if existing:
            return existing
        return self.create_subscription_device(
            subscription["id"],
            subscription["user_id"],
            "Устройство 1",
            is_primary=True,
            limit=int(os.getenv("DEVICE_LIMIT", "3")),
        )

    def create_subscription_device(self, subscription_id: int, user_id: int, name: str,
                                   is_primary: bool = False, limit: int = 3) -> Optional[Dict]:
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("BEGIN IMMEDIATE")
            active_count = cursor.execute("""
                SELECT COUNT(*) AS total
                FROM subscription_devices
                WHERE subscription_id = ? AND status = 'active'
            """, (subscription_id,)).fetchone()["total"]
            if int(active_count) >= int(limit):
                conn.commit()
                return None
            device_key = self._generate_device_key(cursor)
            cursor.execute("""
                INSERT INTO subscription_devices
                (subscription_id, user_id, device_key, name, status, is_primary, updated_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?)
            """, (subscription_id, user_id, device_key, name, 1 if is_primary else 0, utc_now_iso()))
            device_id = cursor.lastrowid
            conn.commit()
            return self.get_subscription_device_by_id(device_id)
        finally:
            conn.close()

    def mark_device_seen(self, device_id: int, ip: str = "", user_agent: str = ""):
        self._execute("""
            UPDATE subscription_devices
            SET last_seen_at = ?, last_ip = ?, last_user_agent = ?,
                request_count = COALESCE(request_count, 0) + 1,
                updated_at = ?
            WHERE id = ?
        """, (utc_now_iso(), ip or "", (user_agent or "")[:300], utc_now_iso(), device_id))

    def delete_subscription_device(self, device_id: int):
        self._execute("""
            UPDATE subscription_devices
            SET status = 'deleted', updated_at = ?
            WHERE id = ?
        """, (utc_now_iso(), device_id))

    def reset_subscription_device(self, device_id: int) -> Optional[Dict]:
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            device_key = self._generate_device_key(cursor)
            cursor.execute("""
                UPDATE subscription_devices
                SET device_key = ?, status = 'active', last_seen_at = NULL, last_ip = NULL,
                    last_user_agent = NULL, request_count = 0, updated_at = ?
                WHERE id = ?
            """, (device_key, utc_now_iso(), device_id))
            conn.commit()
            return self.get_subscription_device_by_id(device_id)
        finally:
            conn.close()

    def update_subscription_status(self, subscription_id: int, status: str, reason: str = None):
        self._execute("""
            UPDATE subscriptions
            SET status = ?, revoked_reason = COALESCE(?, revoked_reason), updated_at = ?
            WHERE id = ?
        """, (status, reason, utc_now_iso(), subscription_id))

    def revoke_active_subscription(self, user_id: int, reason: str = "manual"):
        self._execute("""
            UPDATE subscriptions
            SET status = 'revoked', revoked_reason = ?, updated_at = ?
            WHERE user_id = ? AND status = 'active'
        """, (reason, utc_now_iso(), user_id))
        self._execute("""
            UPDATE server_clients
            SET status = 'revoked', updated_at = ?
            WHERE user_id = ? AND status = 'active'
        """, (utc_now_iso(), user_id))

    def get_expired_subscriptions(self) -> List[Dict]:
        return self._fetch_all("""
            SELECT * FROM subscriptions
            WHERE status = 'active' AND datetime(replace(end_date, 'T', ' ')) < datetime('now')
        """)

    def add_server(self, name: str, country_code: str, country_name: str, flag: str,
                   host: str, port: int, protocol: str, config_url: str,
                   xui_url: str = "", xui_username: str = "", xui_password_encrypted: str = "",
                   inbound_id: int = 0) -> int:
        sort_order = self._fetch_one("SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_order FROM servers")["next_order"]
        return self._insert("""
            INSERT INTO servers
            (name, country_code, country_name, flag, host, port, protocol, config_url,
             xui_url, xui_username, xui_password_encrypted, inbound_id, status, sort_order, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
        """, (
            name,
            country_code,
            country_name,
            flag,
            host,
            port,
            protocol,
            config_url,
            (xui_url or "").rstrip("/"),
            xui_username or "",
            xui_password_encrypted or "",
            int(inbound_id or 0),
            sort_order,
            utc_now_iso(),
        ))

    def list_servers(self, active_only: bool = False, include_deleted: bool = False) -> List[Dict]:
        if active_only:
            return self._fetch_all("SELECT * FROM servers WHERE status = 'active' ORDER BY sort_order, id")
        if include_deleted:
            return self._fetch_all("SELECT * FROM servers ORDER BY sort_order, id")
        return self._fetch_all("SELECT * FROM servers WHERE status != 'deleted' ORDER BY sort_order, id")

    def get_server(self, server_id: int) -> Optional[Dict]:
        return self._fetch_one("SELECT * FROM servers WHERE id = ?", (server_id,))

    def set_server_status(self, server_id: int, status: str):
        self._execute("UPDATE servers SET status = ?, updated_at = ? WHERE id = ?", (status, utc_now_iso(), server_id))

    def update_server_name(self, server_id: int, name: str):
        self._execute("UPDATE servers SET name = ?, updated_at = ? WHERE id = ?", (name, utc_now_iso(), server_id))

    def soft_delete_server(self, server_id: int):
        conn = self.get_connection()
        try:
            now = utc_now_iso()
            conn.execute("UPDATE servers SET status = 'deleted', updated_at = ? WHERE id = ?", (now, server_id))
            conn.execute("""
                UPDATE server_clients
                SET status = 'revoked', updated_at = ?
                WHERE server_id = ? AND status = 'active'
            """, (now, server_id))
            conn.commit()
        finally:
            conn.close()

    def mark_client_status_by_server(self, server_id: int, status: str):
        self._execute("""
            UPDATE server_clients SET status = ?, updated_at = ?
            WHERE server_id = ? AND status = 'active'
        """, (status, utc_now_iso(), server_id))

    def count_clients_for_server(self, server_id: int) -> Dict:
        row = self._fetch_one("""
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END), 0) AS active
            FROM server_clients
            WHERE server_id = ?
        """, (server_id,))
        return row or {"total": 0, "active": 0}

    def upsert_server_client(self, user_id: int, subscription_id: int, server_id: int,
                             xui_client_id: str, xui_email: str, config_url: str,
                             traffic_limit: int):
        self._execute("""
            INSERT INTO server_clients
            (user_id, subscription_id, server_id, xui_client_id, xui_email, config_url,
             status, traffic_limit, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(subscription_id, server_id) DO UPDATE SET
                xui_client_id = excluded.xui_client_id,
                xui_email = excluded.xui_email,
                config_url = excluded.config_url,
                status = 'active',
                traffic_limit = excluded.traffic_limit,
                updated_at = excluded.updated_at
        """, (user_id, subscription_id, server_id, xui_client_id, xui_email, config_url, traffic_limit, utc_now_iso()))

    def update_server_client_config(self, client_id: int, config_url: str):
        self._execute("""
            UPDATE server_clients
            SET config_url = ?, updated_at = ?
            WHERE id = ?
        """, (config_url, utc_now_iso(), client_id))

    def list_clients_for_subscription(self, subscription_id: int, active_only: bool = True) -> List[Dict]:
        status_filter = "AND sc.status = 'active' AND s.status = 'active'" if active_only else ""
        return self._fetch_all(f"""
            SELECT sc.*, s.name AS server_name, s.country_name, s.country_code, s.flag, s.host, s.protocol
            FROM server_clients sc
            JOIN servers s ON s.id = sc.server_id
            WHERE sc.subscription_id = ? {status_filter}
            ORDER BY s.sort_order, s.id
        """, (subscription_id,))

    def list_clients_for_server(self, server_id: int, active_only: bool = True) -> List[Dict]:
        status_filter = "AND sc.status = 'active'" if active_only else ""
        return self._fetch_all(f"""
            SELECT sc.*, sub.end_date, sub.status AS subscription_status
            FROM server_clients sc
            JOIN subscriptions sub ON sub.id = sc.subscription_id
            WHERE sc.server_id = ? {status_filter}
        """, (server_id,))

    def list_active_subscriptions(self) -> List[Dict]:
        return self._fetch_all("""
            SELECT s.*, u.telegram_id, u.username, u.first_name, u.is_blocked
            FROM subscriptions s
            JOIN users u ON u.id = s.user_id
            WHERE s.status = 'active' AND u.is_blocked = 0
        """)

    def mark_client_status_by_user(self, user_id: int, status: str):
        self._execute("""
            UPDATE server_clients SET status = ?, updated_at = ?
            WHERE user_id = ? AND status = 'active'
        """, (status, utc_now_iso(), user_id))

    def update_client_traffic(self, client_id: int, up: int, down: int, enabled: bool = True):
        self._execute("""
            UPDATE server_clients
            SET traffic_up = ?, traffic_down = ?, status = CASE WHEN ? THEN status ELSE 'disabled' END,
                updated_at = ?
            WHERE id = ?
        """, (up, down, 1 if enabled else 0, utc_now_iso(), client_id))

    def update_server_client_limit(self, client_id: int, traffic_limit: int, status: str = "active"):
        self._execute("""
            UPDATE server_clients
            SET traffic_limit = ?, status = ?, updated_at = ?
            WHERE id = ?
        """, (traffic_limit, status, utc_now_iso(), client_id))

    def add_support_message(self, user_id: int, direction: str, message_text: str, admin_id: int = None) -> int:
        return self._insert("""
            INSERT INTO support_messages (user_id, admin_id, direction, message_text, status)
            VALUES (?, ?, ?, ?, 'open')
        """, (user_id, admin_id, direction, message_text))

    def list_open_support_threads(self, limit: int = 10) -> List[Dict]:
        return self._fetch_all("""
            SELECT sm.user_id, MAX(sm.created_at) AS last_at, COUNT(*) AS messages,
                   u.telegram_id, u.username, u.first_name
            FROM support_messages sm
            JOIN users u ON u.id = sm.user_id
            WHERE sm.status = 'open'
            GROUP BY sm.user_id
            ORDER BY last_at DESC
            LIMIT ?
        """, (limit,))

    def list_support_messages(self, user_id: int, limit: int = 12) -> List[Dict]:
        return self._fetch_all("""
            SELECT * FROM support_messages
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (user_id, limit))

    def close_support_thread(self, user_id: int):
        self._execute("UPDATE support_messages SET status = 'closed' WHERE user_id = ? AND status = 'open'", (user_id,))

    def get_user_summary(self, user_id: int) -> Optional[Dict]:
        return self._fetch_one("""
            SELECT u.*,
                   s.id AS subscription_id,
                   s.status AS subscription_status,
                   s.end_date,
                   s.subscription_key,
                   COALESCE(SUM(sc.traffic_up + sc.traffic_down), 0) AS traffic_used,
                   COALESCE(SUM(sc.traffic_limit), s.traffic_limit, 0) AS traffic_limit
            FROM users u
            LEFT JOIN subscriptions s ON s.user_id = u.id AND s.status = 'active'
            LEFT JOIN server_clients sc ON sc.subscription_id = s.id AND sc.status = 'active'
            WHERE u.id = ?
            GROUP BY u.id, s.id
        """, (user_id,))

    def get_stats(self) -> Dict:
        row = self._fetch_one("""
            SELECT
                (SELECT COUNT(*) FROM users) AS total_users,
                (SELECT COUNT(*) FROM users WHERE is_blocked = 1) AS blocked_users,
                (SELECT COUNT(*) FROM subscriptions WHERE status = 'active') AS active_subs,
                (SELECT COUNT(*) FROM servers WHERE status = 'active') AS active_servers,
                (SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'succeeded') AS total_revenue,
                (SELECT COUNT(*) FROM support_messages WHERE status = 'open' AND direction = 'user') AS open_messages,
                (SELECT COUNT(*) FROM referrals) AS total_referrals,
                (SELECT COALESCE(SUM(bonus_days), 0) FROM referrals WHERE status IN ('applied', 'credited_on_signup', 'credited_on_payment')) AS referral_bonus_days
        """)
        return row or {
            "total_users": 0,
            "blocked_users": 0,
            "active_subs": 0,
            "active_servers": 0,
            "total_revenue": 0,
            "open_messages": 0,
            "total_referrals": 0,
            "referral_bonus_days": 0,
        }

    def _fetch_one(self, sql: str, params: Tuple = ()) -> Optional[Dict]:
        conn = self.get_connection()
        try:
            row = conn.execute(sql, params).fetchone()
            return self._row_to_dict(row)
        finally:
            conn.close()

    def _fetch_all(self, sql: str, params: Tuple = ()) -> List[Dict]:
        conn = self.get_connection()
        try:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_dict(row) for row in rows]
        finally:
            conn.close()

    def _insert(self, sql: str, params: Tuple = ()) -> int:
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def _execute(self, sql: str, params: Tuple = ()):
        conn = self.get_connection()
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def _generate_referral_code(self, cursor) -> str:
        while True:
            code = secrets.token_urlsafe(7).replace("-", "").replace("_", "")[:8]
            if not cursor.execute("SELECT 1 FROM users WHERE referral_code = ?", (code,)).fetchone():
                return code

    def _generate_device_key(self, cursor) -> str:
        while True:
            key = secrets.token_urlsafe(18)
            if not cursor.execute("SELECT 1 FROM subscription_devices WHERE device_key = ?", (key,)).fetchone():
                return key

    def _ensure_referral_code(self, cursor, user_id: int) -> str:
        row = cursor.execute("SELECT referral_code FROM users WHERE id = ?", (user_id,)).fetchone()
        if row and row["referral_code"]:
            return row["referral_code"]
        code = self._generate_referral_code(cursor)
        cursor.execute(
            "UPDATE users SET referral_code = ?, updated_at = ? WHERE id = ?",
            (code, utc_now_iso(), user_id),
        )
        return code

    @staticmethod
    def _row_to_dict(row) -> Optional[Dict]:
        return dict(row) if row else None
