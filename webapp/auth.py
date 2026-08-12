"""initData валидация по официальному алгоритму Telegram.

Алгоритм (https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app):
1. Достаём hash из параметров
2. Все остальные параметры сортируем по ключу, склеиваем `key=value\n...`
3. Считаем HMAC-SHA256 от bot_token (с константой "WebAppData"), получаем secret_key
4. Считаем HMAC-SHA256 от data_check_string с secret_key
5. Сравниваем с hash (constant-time)
6. Проверяем auth_date (±60s skew, < 3600s old)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
from dataclasses import dataclass
from typing import Optional


@dataclass
class WebAppUser:
    id: int
    first_name: str
    last_name: str = ""
    username: str = ""
    photo_url: str = ""
    auth_date: int = 0


def _parse_qs(init_data: str) -> dict[str, str]:
    """Парсит initData в dict (URL-decoded)."""
    pairs = init_data.split("&")
    out = {}
    for p in pairs:
        if "=" not in p:
            continue
        k, v = p.split("=", 1)
        try:
            v = urllib.parse.unquote(v)
        except Exception:
            pass  # malformed — оставляем как есть
        out[k] = v
    return out


def validate_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int = 3600,
    clock_skew_seconds: int = 60,
) -> Optional[WebAppUser]:
    """Возвращает WebAppUser если initData валидна, иначе None."""
    if not init_data or not bot_token:
        return None
    try:
        params = _parse_qs(init_data)
    except Exception:
        return None
    recv_hash = params.pop("hash", None)
    if not recv_hash:
        return None

    # data_check_string
    data_check = "\n".join(f"{k}={params[k]}" for k in sorted(params.keys()))

    # secret_key = HMAC-SHA256(bot_token, "WebAppData")
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    # calc_hash = HMAC-SHA256(data_check, secret_key)
    calc_hash = hmac.new(secret_key, data_check.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calc_hash, recv_hash):
        return None

    # auth_date freshness
    auth_date_str = params.get("auth_date", "0")
    try:
        auth_date = int(auth_date_str)
    except (ValueError, TypeError):
        return None
    now = int(time.time())
    if auth_date > now + clock_skew_seconds:
        return None
    if now - auth_date > max_age_seconds + clock_skew_seconds:
        return None

    # Парсим user
    user_str = params.get("user", "")
    if not user_str:
        return None
    try:
        u = json.loads(user_str)
        user_id = int(u["id"])
        first_name = str(u.get("first_name", ""))
        last_name = str(u.get("last_name", ""))
        username = str(u.get("username", ""))
        photo_url = str(u.get("photo_url", ""))
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None

    return WebAppUser(
        id=user_id,
        first_name=first_name,
        last_name=last_name,
        username=username,
        photo_url=photo_url,
        auth_date=auth_date,
    )
