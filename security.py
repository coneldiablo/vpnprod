#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import base64
import hashlib
import hmac
import os


def _master_key() -> bytes:
    raw = os.getenv("ENCRYPTION_KEY") or os.getenv("SECRET_KEY") or "change-me-local-dev-key"
    return hashlib.sha256(raw.encode("utf-8")).digest()


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    chunks = []
    counter = 0
    while sum(len(chunk) for chunk in chunks) < length:
        chunks.append(hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest())
        counter += 1
    return b"".join(chunks)[:length]


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    key = _master_key()
    nonce = os.urandom(16)
    plain = value.encode("utf-8")
    stream = _keystream(key, nonce, len(plain))
    cipher = bytes(a ^ b for a, b in zip(plain, stream))
    mac = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    payload = base64.urlsafe_b64encode(nonce + cipher + mac).decode("ascii")
    return f"v1:{payload}"


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    if not value.startswith("v1:"):
        return value
    key = _master_key()
    raw = base64.urlsafe_b64decode(value[3:].encode("ascii"))
    nonce, rest = raw[:16], raw[16:]
    cipher, mac = rest[:-32], rest[-32:]
    expected = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        raise ValueError("Encrypted secret checksum mismatch")
    stream = _keystream(key, nonce, len(cipher))
    plain = bytes(a ^ b for a, b in zip(cipher, stream))
    return plain.decode("utf-8")
