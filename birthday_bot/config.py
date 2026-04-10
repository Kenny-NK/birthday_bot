from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


def _resolve_workbook_path(base_dir: Path) -> Optional[Path]:
    explicit_path = os.getenv("BIRTHDAYS_FILE")
    if explicit_path:
        candidate = Path(explicit_path).expanduser()
        return candidate if candidate.is_absolute() else (base_dir / candidate).resolve()

    candidates = sorted(base_dir.glob("*.xlsx"))
    if len(candidates) == 1:
        return candidates[0].resolve()

    return None


@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_url: str
    workbook_path: Optional[Path]
    legacy_subscribers_path: Path
    legacy_whitelist_path: Path
    timezone_name: str
    telegram_local_address: Optional[str]
    telegram_proxy_url: Optional[str]
    initial_whitelist_user_ids: list[int]
    initial_admin_user_ids: list[int]

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @classmethod
    def from_env(cls, base_dir: Path) -> "Settings":
        load_dotenv(base_dir / ".env")

        bot_token = os.getenv("BOT_TOKEN")
        if not bot_token:
            raise RuntimeError("Переменная BOT_TOKEN не задана.")

        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise RuntimeError("Переменная DATABASE_URL не задана.")

        initial_whitelist_user_ids: list[int] = []
        raw_whitelist = os.getenv("INITIAL_WHITELIST_USER_IDS", "")
        for item in raw_whitelist.split(","):
            value = item.strip()
            if value:
                initial_whitelist_user_ids.append(int(value))

        initial_admin_user_ids: list[int] = []
        raw_admins = os.getenv("INITIAL_ADMIN_USER_IDS", raw_whitelist)
        for item in raw_admins.split(","):
            value = item.strip()
            if value:
                initial_admin_user_ids.append(int(value))

        return cls(
            bot_token=bot_token,
            database_url=database_url,
            workbook_path=_resolve_workbook_path(base_dir),
            legacy_subscribers_path=(base_dir / "data/subscribers.json").resolve(),
            legacy_whitelist_path=(base_dir / "data/whitelist.json").resolve(),
            timezone_name=os.getenv("TIMEZONE", "Europe/Moscow"),
            telegram_local_address=os.getenv("TELEGRAM_LOCAL_ADDRESS") or None,
            telegram_proxy_url=os.getenv("TELEGRAM_PROXY_URL") or None,
            initial_whitelist_user_ids=initial_whitelist_user_ids,
            initial_admin_user_ids=initial_admin_user_ids,
        )
