# Telegram VPN Subscription Bot

Telegram bot and FastAPI subscription backend for a small VPN subscription service.

The project is intentionally provider-neutral: Telegram token, domain, payment gateway, 3X-UI access, support contacts and images are configured only through `.env`.

## Features

- Telegram inline-button user flow.
- Admin panel inside Telegram.
- Plans, trial period, subscription renewal and manual admin actions.
- FastAPI subscription endpoint for HAPP/Hub/Hiddify clients.
- Up to 3 device subscription links per user.
- Referral system with anti-abuse rules.
- Support tickets and optional direct support chat link.
- 3X-UI managed servers and static external config links.
- Generic redirect payment API integration via `PAYMENT_*` variables.

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
nano .env

python migrations.py
python subscription_server.py
```

In another terminal:

```bash
source .venv/bin/activate
python bot.py
```

For local testing set:

```env
DB_PATH=./vpn_bot_local.db
PUBLIC_BASE_URL=http://127.0.0.1:8000
API_HOST=127.0.0.1
API_PORT=8000
```

## Required Configuration

Create a bot with [@BotFather](https://t.me/BotFather), then fill:

```env
TELEGRAM_BOT_TOKEN=
ADMIN_TELEGRAM_IDS=
SERVER_NAME=VPN Service
BOT_USERNAME=your_bot_username
PUBLIC_BASE_URL=https://your-domain.example
DB_PATH=./vpn_bot.db
ENCRYPTION_KEY=replace_with_a_long_random_secret
```

For support buttons:

```env
SUPPORT_URL=https://t.me/your_support_username
SUPPORT_ADMIN_URL=https://t.me/your_admin_username
```

For 3X-UI provisioning:

```env
X_UI_URL=
X_UI_USERNAME=
X_UI_PASSWORD=
DEFAULT_INBOUND_ID=1
DEFAULT_SERVER_CONFIG_URL=
```

You can also add servers later from the Telegram admin panel.

## Payments

Payments are implemented as a generic redirect-payment flow:

- bot creates a local pending payment;
- backend sends `POST {PAYMENT_API_URL}/payments` with Basic Auth;
- provider returns a `confirmation_url`;
- user opens the payment page;
- backend verifies payment on `/pay/return/{local_payment_id}` or `/webhook/payment`.

Configure your own provider:

```env
PAYMENT_PROVIDER=payment_provider
PAYMENT_PROVIDER_NAME=Payment provider
PAYMENT_ACCOUNT_ID=
PAYMENT_SECRET_KEY=
PAYMENT_API_URL=
PAYMENT_RETURN_URL=https://your-domain.example
PAYMENT_WEBHOOK_SECRET=
PAYMENT_CURRENCY=RUB
```

If your provider uses another API format, adapt `payment_service.py`.

## Subscription URLs

Main subscription endpoint:

```text
https://your-domain.example/sub/{subscription_key}
```

Device endpoint:

```text
https://your-domain.example/sub/device/{device_key}
```

When a subscription is expired, revoked or blocked, the backend still returns `200 text/plain`, but with dummy configs. This makes subscription refresh replace real servers with placeholders in compatible clients.

## Admin Panel

Open `/admin` in the bot from an account listed in `ADMIN_TELEGRAM_IDS`.

Available sections:

- users and subscriptions;
- support messages;
- servers;
- payments;
- referrals;
- broadcast;
- statistics;
- settings.

## Production Notes

- Use HTTPS for `PUBLIC_BASE_URL`.
- Keep `.env`, SQLite databases, logs and backups out of git.
- Change default 3X-UI credentials.
- Use a long random `ENCRYPTION_KEY` before adding 3X-UI passwords.
- Put the bot and API behind systemd or another process manager.
- If you use static external configs, full revocation is only possible through subscription refresh, not through already-copied raw configs.

## Repository Hygiene

Before publishing or pushing:

```bash
git status --short
rg --hidden -n "TOKEN|SECRET|PASSWORD|PRIVATE|PAYMENT_SECRET|TELEGRAM_BOT_TOKEN" . \
  --glob '!.git/**' --glob '!.venv/**' --glob '!__pycache__/**'
```

Do not commit `.env`, databases, logs, private keys or real provider credentials.
