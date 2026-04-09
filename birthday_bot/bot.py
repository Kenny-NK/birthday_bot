from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from functools import wraps
from typing import Awaitable, Callable, TypeVar

import psycopg
from telegram import BotCommand, Update
from telegram.error import Forbidden, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

from birthday_bot.birthdays import (
    birthdays_for_date,
    build_birthday_list_lines,
    build_today_birthday_lines,
    build_weekly_reminder_lines,
    format_day_month,
    parse_day_month_text,
    weekly_birthdays_for_notification,
)
from birthday_bot.config import Settings
from birthday_bot.db import Database

LOGGER = logging.getLogger(__name__)
MAX_MESSAGE_LENGTH = 3500
WEEKLY_NOTIFICATION_WEEKDAY = 4
WEEKLY_NOTIFICATION_HOUR = 12
WEEKLY_NOTIFICATION_MINUTE = 0
DAILY_NOTIFICATION_HOUR = 8
DAILY_NOTIFICATION_MINUTE = 0
HandlerFunc = TypeVar("HandlerFunc", bound=Callable[..., Awaitable[None]])
BOT_COMMANDS = [
    BotCommand("start", "Подписать чат на уведомления"),
    BotCommand("help", "Показать список команд"),
    BotCommand("check", "Показать пятничную рассылку"),
    BotCommand("list", "Показать список дней рождений"),
    BotCommand("add", "Добавить день рождения"),
    BotCommand("delete", "Удалить день рождения по ID"),
    BotCommand("add_whitelist", "Добавить пользователя в whitelist"),
    BotCommand("del_whitelist", "Удалить пользователя из whitelist"),
    BotCommand("add_admin", "Выдать роль админа"),
    BotCommand("del_admin", "Снять роль админа"),
    BotCommand("stop", "Отключить уведомления для чата"),
]


def _today(settings: Settings) -> date:
    return datetime.now(settings.timezone).date()


def _command_payload(update: Update) -> str:
    text = update.message.text if update.message else ""
    return text.partition(" ")[2].strip()


def _next_dispatch_date(today: date) -> date:
    days_until_dispatch = (WEEKLY_NOTIFICATION_WEEKDAY - today.weekday()) % 7
    return today + timedelta(days=days_until_dispatch)


def _parse_user_id(value: str) -> int:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Укажите Telegram user ID.")
    if not normalized.lstrip("-").isdigit():
        raise ValueError("Telegram user ID должен быть числом. Пример: /add_whitelist 123456789")
    return int(normalized)


def _chunk_lines(lines: list[str]) -> list[str]:
    messages: list[str] = []
    current: list[str] = []
    current_length = 0

    for line in lines:
        addition = len(line) + (1 if current else 0)
        if current and current_length + addition > MAX_MESSAGE_LENGTH:
            messages.append("\n".join(current))
            current = [line]
            current_length = len(line)
            continue

        current.append(line)
        current_length += addition

    if current:
        messages.append("\n".join(current))

    return messages


async def _reply_chunked(update: Update, lines: list[str]) -> None:
    if update.message is None:
        return

    for chunk in _chunk_lines(lines):
        await update.message.reply_text(chunk)


async def _send_chunked_message(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    lines: list[str],
) -> None:
    for chunk in _chunk_lines(lines):
        await context.bot.send_message(chat_id=chat_id, text=chunk)


async def _ensure_authorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    database: Database = context.application.bot_data["database"]
    user = update.effective_user
    message = update.message
    if user is None or message is None:
        return False

    if database.is_whitelist_user(user.id):
        return True

    await message.reply_text(
        "Ошибка авторизации. "
        f"Ваш Telegram ID: {user.id}. Передайте его администратору для добавления в белый список."
    )
    return False


async def _ensure_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    database: Database = context.application.bot_data["database"]
    user = update.effective_user
    message = update.message
    if user is None or message is None:
        return False

    if database.is_admin_user(user.id):
        return True

    await message.reply_text("Недостаточно прав. Для этой команды нужна роль администратора.")
    return False


def authorized_only(handler: HandlerFunc) -> HandlerFunc:
    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await _ensure_authorized(update, context):
            return
        await handler(update, context)

    return wrapper  # type: ignore[return-value]


def admin_only(handler: HandlerFunc) -> HandlerFunc:
    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await _ensure_authorized(update, context):
            return
        if not await _ensure_admin(update, context):
            return
        await handler(update, context)

    return wrapper  # type: ignore[return-value]


async def _configure_bot_commands(application: Application) -> None:
    await application.bot.set_my_commands(BOT_COMMANDS)


@authorized_only
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    database: Database = context.application.bot_data["database"]
    chat = update.effective_chat
    if chat is None or update.message is None:
        return

    added = database.add_subscriber(chat.id)
    if added:
        await update.message.reply_text(
            "Чат подписан на напоминания. "
            "Бот будет присылать сообщения по пятницам в 12:00 по Москве и в 08:00 в дни рождения."
        )
        return

    await update.message.reply_text("Этот чат уже подписан на напоминания.")


@authorized_only
async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    database: Database = context.application.bot_data["database"]
    chat = update.effective_chat
    if chat is None or update.message is None:
        return

    removed = database.remove_subscriber(chat.id)
    if removed:
        await update.message.reply_text("Подписка на напоминания отключена.")
        return

    await update.message.reply_text("Этот чат не был подписан.")


@authorized_only
async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    database: Database = context.application.bot_data["database"]
    if update.message is None:
        return

    today = _today(settings)
    dispatch_date = _next_dispatch_date(today)
    scheduled_birthdays = weekly_birthdays_for_notification(database.list_birthdays(), dispatch_date)
    await _reply_chunked(update, build_weekly_reminder_lines(scheduled_birthdays, dispatch_date))


@authorized_only
async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    database: Database = context.application.bot_data["database"]
    if update.message is None:
        return

    entries = database.list_birthdays()
    if not entries:
        await update.message.reply_text("Список дней рождений пуст.")
        return

    await _reply_chunked(update, build_birthday_list_lines(entries))


@admin_only
async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    database: Database = context.application.bot_data["database"]
    if update.message is None:
        return

    payload = _command_payload(update)
    parts = [part.strip() for part in payload.split("|")]
    if len(parts) == 2:
        full_name, date_text = parts
        department = ""
    elif len(parts) == 3:
        full_name, department, date_text = parts
    else:
        await update.message.reply_text(
            "Используйте формат: /add ФИО | Подразделение | ДД.ММ\n"
            "Или без подразделения: /add ФИО | ДД.ММ"
        )
        return

    try:
        day, month = parse_day_month_text(date_text)
        entry = database.add_birthday(full_name=full_name, department=department, day=day, month=month)
    except ValueError as error:
        await update.message.reply_text(str(error))
        return
    except psycopg.Error as error:
        LOGGER.exception("Не удалось добавить запись в PostgreSQL: %s", error)
        await update.message.reply_text("Не удалось записать изменения в PostgreSQL.")
        return

    suffix = f" ({entry.department})" if entry.department else ""
    await update.message.reply_text(
        f"Запись добавлена: #{entry.entry_id} {entry.full_name}{suffix}, {format_day_month(entry.day, entry.month)}."
    )


@admin_only
async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    database: Database = context.application.bot_data["database"]
    if update.message is None:
        return

    payload = _command_payload(update)
    if not payload.isdigit():
        await update.message.reply_text("Используйте формат: /delete ID\nПример: /delete 42")
        return

    try:
        entry = database.delete_birthday(int(payload))
    except ValueError as error:
        await update.message.reply_text(str(error))
        return
    except psycopg.Error as error:
        LOGGER.exception("Не удалось удалить запись из PostgreSQL: %s", error)
        await update.message.reply_text("Не удалось записать изменения в PostgreSQL.")
        return

    suffix = f" ({entry.department})" if entry.department else ""
    await update.message.reply_text(
        f"Запись удалена: #{entry.entry_id} {entry.full_name}{suffix}, {format_day_month(entry.day, entry.month)}."
    )


@admin_only
async def add_whitelist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    database: Database = context.application.bot_data["database"]
    if update.message is None:
        return

    payload = _command_payload(update)
    try:
        user_id = _parse_user_id(payload)
    except ValueError as error:
        await update.message.reply_text(str(error))
        return

    added = database.add_whitelist_user(user_id)
    if added:
        await update.message.reply_text(f"Пользователь {user_id} добавлен в белый список.")
        return

    await update.message.reply_text(f"Пользователь {user_id} уже есть в белом списке.")


@admin_only
async def del_whitelist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    database: Database = context.application.bot_data["database"]
    if update.message is None:
        return

    payload = _command_payload(update)
    try:
        user_id = _parse_user_id(payload)
    except ValueError as error:
        await update.message.reply_text(str(error))
        return

    user_ids = database.list_whitelist_user_ids()
    if user_id in user_ids and len(user_ids) == 1:
        await update.message.reply_text("Нельзя удалить последнего пользователя из белого списка.")
        return
    if database.is_admin_user(user_id):
        await update.message.reply_text("Нельзя удалить пользователя из белого списка, пока у него есть роль администратора.")
        return

    removed = database.remove_whitelist_user(user_id)
    if removed:
        database.remove_subscriber(user_id)
        await update.message.reply_text(f"Пользователь {user_id} удален из белого списка.")
        return

    await update.message.reply_text(f"Пользователя {user_id} нет в белом списке.")


@admin_only
async def add_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    database: Database = context.application.bot_data["database"]
    if update.message is None:
        return

    payload = _command_payload(update)
    try:
        user_id = _parse_user_id(payload)
    except ValueError as error:
        await update.message.reply_text(str(error))
        return

    added = database.add_admin_user(user_id)
    if added:
        await update.message.reply_text(f"Пользователю {user_id} выдана роль администратора.")
        return

    await update.message.reply_text(f"Пользователь {user_id} уже является администратором.")


@admin_only
async def del_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    database: Database = context.application.bot_data["database"]
    if update.message is None:
        return

    payload = _command_payload(update)
    try:
        user_id = _parse_user_id(payload)
    except ValueError as error:
        await update.message.reply_text(str(error))
        return

    admin_ids = database.list_admin_user_ids()
    if user_id in admin_ids and len(admin_ids) == 1:
        await update.message.reply_text("Нельзя удалить последнего администратора.")
        return

    removed = database.remove_admin_user(user_id)
    if removed:
        await update.message.reply_text(f"У пользователя {user_id} снята роль администратора.")
        return

    await update.message.reply_text(f"Пользователь {user_id} не является администратором.")


@authorized_only
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    database: Database = context.application.bot_data["database"]
    user = update.effective_user
    if update.message is None:
        return

    lines = [
        "/start - подписать текущий чат",
        "/stop - отключить напоминания",
        "/check - показать, что уйдет в ближайшую пятницу",
        "/list - показать весь список дней рождений",
        "/help - показать команды",
    ]
    if user is not None and database.is_admin_user(user.id):
        lines.extend(
            [
                "",
                "Команды администратора:",
                "/add ФИО | Подразделение | ДД.ММ - добавить запись",
                "/delete ID - удалить запись из списка",
                "/add_whitelist USER_ID - добавить пользователя в белый список",
                "/del_whitelist USER_ID - удалить пользователя из белого списка",
                "/add_admin USER_ID - выдать роль администратора",
                "/del_admin USER_ID - снять роль администратора",
            ]
        )

    await update.message.reply_text("\n".join(lines))


async def weekly_reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    app = context.application
    settings: Settings = app.bot_data["settings"]
    database: Database = app.bot_data["database"]

    chat_ids = database.list_subscriber_chat_ids()
    if not chat_ids:
        LOGGER.info("Нет подписанных чатов, рассылка пропущена.")
        return

    dispatch_date = _today(settings)
    scheduled_birthdays = weekly_birthdays_for_notification(database.list_birthdays(), dispatch_date)
    lines = build_weekly_reminder_lines(scheduled_birthdays, dispatch_date)
    stale_chat_ids: list[int] = []

    for chat_id in chat_ids:
        try:
            await _send_chunked_message(context, chat_id, lines)
        except Forbidden:
            LOGGER.warning("Чат %s больше недоступен, удаляю из подписки.", chat_id)
            stale_chat_ids.append(chat_id)
        except TelegramError:
            LOGGER.exception("Не удалось отправить сообщение в чат %s", chat_id)

    for chat_id in stale_chat_ids:
        database.remove_subscriber(chat_id)


async def today_birthday_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    app = context.application
    settings: Settings = app.bot_data["settings"]
    database: Database = app.bot_data["database"]

    chat_ids = database.list_subscriber_chat_ids()
    if not chat_ids:
        LOGGER.info("Нет подписанных чатов, ежедневная рассылка пропущена.")
        return

    target_date = _today(settings)
    entries = birthdays_for_date(database.list_birthdays(), target_date)
    if not entries:
        LOGGER.info("На %s именинников нет.", target_date.isoformat())
        return

    lines = build_today_birthday_lines(entries, target_date)
    stale_chat_ids: list[int] = []

    for chat_id in chat_ids:
        try:
            await _send_chunked_message(context, chat_id, lines)
        except Forbidden:
            LOGGER.warning("Чат %s больше недоступен, удаляю из подписки.", chat_id)
            stale_chat_ids.append(chat_id)
        except TelegramError:
            LOGGER.exception("Не удалось отправить ежедневное сообщение в чат %s", chat_id)

    for chat_id in stale_chat_ids:
        database.remove_subscriber(chat_id)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOGGER.exception("Ошибка при обработке обновления", exc_info=context.error)


def build_application(settings: Settings, database: Database) -> Application:
    application = ApplicationBuilder().token(settings.bot_token).post_init(_configure_bot_commands).build()
    if application.job_queue is None:
        raise RuntimeError(
            "JobQueue недоступен. Установите зависимости из requirements.txt, "
            "включая python-telegram-bot[job-queue]."
        )

    application.bot_data["settings"] = settings
    application.bot_data["database"] = database

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CommandHandler("check", check_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("add", add_command))
    application.add_handler(CommandHandler("delete", delete_command))
    application.add_handler(CommandHandler("add_whitelist", add_whitelist_command))
    application.add_handler(CommandHandler("del_whitelist", del_whitelist_command))
    application.add_handler(CommandHandler("add_admin", add_admin_command))
    application.add_handler(CommandHandler("del_admin", del_admin_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_error_handler(error_handler)

    application.job_queue.run_custom(
        weekly_reminder_job,
        job_kwargs={
            "trigger": "cron",
            "day_of_week": "fri",
            "hour": WEEKLY_NOTIFICATION_HOUR,
            "minute": WEEKLY_NOTIFICATION_MINUTE,
            "timezone": settings.timezone,
        },
        name="birthday-weekly-reminders",
    )
    application.job_queue.run_custom(
        today_birthday_job,
        job_kwargs={
            "trigger": "cron",
            "hour": DAILY_NOTIFICATION_HOUR,
            "minute": DAILY_NOTIFICATION_MINUTE,
            "timezone": settings.timezone,
        },
        name="birthday-daily-today-reminders",
    )
    return application


def run(settings: Settings) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    database = Database(settings.database_url)
    database.initialize(
        workbook_path=settings.workbook_path,
        initial_whitelist_user_ids=settings.initial_whitelist_user_ids,
        initial_admin_user_ids=settings.initial_admin_user_ids,
        legacy_subscribers_path=settings.legacy_subscribers_path,
        legacy_whitelist_path=settings.legacy_whitelist_path,
    )
    application = build_application(settings, database)
    LOGGER.info("Бот запущен. БД: %s", settings.database_url)
    application.run_polling()
