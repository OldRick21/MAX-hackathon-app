"""MAX WebApp initData signing primitives used by the test CLI."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Mapping
from urllib.parse import urlencode, urlsplit, urlunsplit


def generate_init_data(
    bot_token: str,
    max_user_id: int,
    *,
    auth_date: int | None = None,
    query_id: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    username: str | None = None,
) -> str:
    """Return a MAX-compatible, HMAC-signed raw WebApp initData string."""
    if not bot_token:
        raise ValueError("bot token must not be empty")
    if isinstance(max_user_id, bool) or not isinstance(max_user_id, int) or max_user_id <= 0:
        raise ValueError("MAX user ID must be a positive integer")

    user: dict[str, object] = {"id": max_user_id}
    optional_user_fields: Mapping[str, str | None] = {
        "first_name": first_name,
        "last_name": last_name,
        "username": username,
    }
    user.update({key: value for key, value in optional_user_fields.items() if value is not None})

    data = {
        "auth_date": str(int(time.time()) if auth_date is None else auth_date),
        "query_id": query_id or str(uuid.uuid4()),
        "user": json.dumps(user, ensure_ascii=False, separators=(",", ":")),
    }
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    data["hash"] = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return urlencode(data)


def build_launch_url(base_url: str, init_data: str, *, platform: str = "web") -> str:
    """Put raw initData into the WebAppData URL fragment expected by the frontend."""
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("base URL must not contain user information")
    if parsed.fragment:
        raise ValueError("base URL must not already contain a fragment")
    if not init_data:
        raise ValueError("initData must not be empty")
    if not platform:
        raise ValueError("platform must not be empty")

    fragment = urlencode({"WebAppData": init_data, "WebAppPlatform": platform})
    return urlunsplit(parsed._replace(fragment=fragment))
