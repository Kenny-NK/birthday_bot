from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

import psycopg

from birthday_bot.birthdays import (
    BirthdayEntry,
    is_valid_day_month,
    load_birthdays_from_workbook,
    normalize_text,
)

LOGGER = logging.getLogger(__name__)


class Database:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn)

    def initialize(
        self,
        workbook_path: Optional[Path],
        initial_whitelist_user_ids: list[int],
        legacy_subscribers_path: Optional[Path] = None,
        legacy_whitelist_path: Optional[Path] = None,
    ) -> None:
        self._wait_until_ready()
        self._create_schema()
        self._bootstrap_whitelist(initial_whitelist_user_ids)
        self._migrate_legacy_json(legacy_subscribers_path, legacy_whitelist_path)
        self._seed_birthdays_from_workbook(workbook_path)

    def _wait_until_ready(self) -> None:
        last_error: Optional[Exception] = None
        for _ in range(30):
            try:
                with self._connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1")
                return
            except psycopg.Error as error:
                last_error = error
                time.sleep(1)

        raise RuntimeError("Не удалось подключиться к PostgreSQL.") from last_error

    def _create_schema(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS birthdays (
                        id BIGSERIAL PRIMARY KEY,
                        full_name TEXT NOT NULL,
                        department TEXT NOT NULL DEFAULT '',
                        day SMALLINT NOT NULL,
                        month SMALLINT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS subscribers (
                        chat_id BIGINT PRIMARY KEY,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS whitelist (
                        user_id BIGINT PRIMARY KEY,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            conn.commit()

    def _bootstrap_whitelist(self, initial_whitelist_user_ids: list[int]) -> None:
        if not initial_whitelist_user_ids:
            return

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO whitelist (user_id)
                    VALUES (%s)
                    ON CONFLICT (user_id) DO NOTHING
                    """,
                    [(user_id,) for user_id in initial_whitelist_user_ids],
                )
            conn.commit()

    def _seed_birthdays_from_workbook(self, workbook_path: Optional[Path]) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM birthdays")
                count = cur.fetchone()[0]
            conn.commit()

        if count > 0:
            return

        if workbook_path is None or not workbook_path.exists():
            LOGGER.info("Excel-файл для первичного импорта не найден, таблица birthdays остается пустой.")
            return

        entries = load_birthdays_from_workbook(workbook_path)
        if not entries:
            LOGGER.info("Первичный импорт из Excel не добавил ни одной записи.")
            return

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO birthdays (full_name, department, day, month)
                    VALUES (%s, %s, %s, %s)
                    """,
                    [
                        (entry.full_name, entry.department, entry.day, entry.month)
                        for entry in entries
                    ],
                )
            conn.commit()

        LOGGER.info("В PostgreSQL импортировано %s дней рождений из %s", len(entries), workbook_path)

    def _migrate_legacy_json(
        self,
        legacy_subscribers_path: Optional[Path],
        legacy_whitelist_path: Optional[Path],
    ) -> None:
        subscriber_ids = self._load_int_ids_from_json(legacy_subscribers_path, "chat_ids")
        if subscriber_ids:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.executemany(
                        """
                        INSERT INTO subscribers (chat_id)
                        VALUES (%s)
                        ON CONFLICT (chat_id) DO NOTHING
                        """,
                        [(chat_id,) for chat_id in subscriber_ids],
                    )
                conn.commit()
            LOGGER.info("Из legacy JSON перенесено %s подписчиков в PostgreSQL", len(subscriber_ids))

        whitelist_ids = self._load_int_ids_from_json(legacy_whitelist_path, "user_ids")
        if whitelist_ids:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.executemany(
                        """
                        INSERT INTO whitelist (user_id)
                        VALUES (%s)
                        ON CONFLICT (user_id) DO NOTHING
                        """,
                        [(user_id,) for user_id in whitelist_ids],
                    )
                conn.commit()
            LOGGER.info("Из legacy JSON перенесено %s whitelist-пользователей в PostgreSQL", len(whitelist_ids))

    def _load_int_ids_from_json(self, path: Optional[Path], field_name: str) -> list[int]:
        if path is None or not path.exists():
            return []

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            LOGGER.exception("Не удалось прочитать legacy JSON %s", path)
            return []

        result: list[int] = []
        for item in payload.get(field_name, []):
            try:
                result.append(int(item))
            except (TypeError, ValueError):
                LOGGER.warning("Пропущено некорректное значение %r в %s", item, path)
        return result

    def list_birthdays(self) -> list[BirthdayEntry]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, full_name, department, day, month
                    FROM birthdays
                    ORDER BY month, day, LOWER(full_name), id
                    """
                )
                rows = cur.fetchall()
            conn.commit()

        return [
            BirthdayEntry(
                entry_id=row[0],
                full_name=row[1],
                department=row[2] or "",
                day=row[3],
                month=row[4],
            )
            for row in rows
        ]

    def add_birthday(self, full_name: str, department: str, day: int, month: int) -> BirthdayEntry:
        if not is_valid_day_month(day, month):
            raise ValueError("Дата должна быть корректной.")

        normalized_name = normalize_text(full_name)
        if not normalized_name:
            raise ValueError("ФИО не может быть пустым.")

        normalized_department = normalize_text(department)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO birthdays (full_name, department, day, month)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                    """,
                    (normalized_name, normalized_department, day, month),
                )
                entry_id = cur.fetchone()[0]
            conn.commit()

        return BirthdayEntry(
            entry_id=entry_id,
            full_name=normalized_name,
            department=normalized_department,
            day=day,
            month=month,
        )

    def delete_birthday(self, entry_id: int) -> BirthdayEntry:
        if entry_id < 1:
            raise ValueError("Некорректный ID записи.")

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM birthdays
                    WHERE id = %s
                    RETURNING id, full_name, department, day, month
                    """,
                    (entry_id,),
                )
                row = cur.fetchone()
            conn.commit()

        if row is None:
            raise ValueError("Запись с таким ID не найдена.")

        return BirthdayEntry(
            entry_id=row[0],
            full_name=row[1],
            department=row[2] or "",
            day=row[3],
            month=row[4],
        )

    def add_subscriber(self, chat_id: int) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO subscribers (chat_id)
                    VALUES (%s)
                    ON CONFLICT (chat_id) DO NOTHING
                    RETURNING chat_id
                    """,
                    (chat_id,),
                )
                row = cur.fetchone()
            conn.commit()
        return row is not None

    def remove_subscriber(self, chat_id: int) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM subscribers WHERE chat_id = %s RETURNING chat_id",
                    (chat_id,),
                )
                row = cur.fetchone()
            conn.commit()
        return row is not None

    def list_subscriber_chat_ids(self) -> list[int]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT chat_id FROM subscribers ORDER BY chat_id")
                rows = cur.fetchall()
            conn.commit()
        return [row[0] for row in rows]

    def add_whitelist_user(self, user_id: int) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO whitelist (user_id)
                    VALUES (%s)
                    ON CONFLICT (user_id) DO NOTHING
                    RETURNING user_id
                    """,
                    (user_id,),
                )
                row = cur.fetchone()
            conn.commit()
        return row is not None

    def remove_whitelist_user(self, user_id: int) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM whitelist WHERE user_id = %s RETURNING user_id",
                    (user_id,),
                )
                row = cur.fetchone()
            conn.commit()
        return row is not None

    def list_whitelist_user_ids(self) -> list[int]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id FROM whitelist ORDER BY user_id")
                rows = cur.fetchall()
            conn.commit()
        return [row[0] for row in rows]

    def is_whitelist_user(self, user_id: int) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM whitelist WHERE user_id = %s", (user_id,))
                row = cur.fetchone()
            conn.commit()
        return row is not None
