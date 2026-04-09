import tempfile
import unittest
from datetime import date
from pathlib import Path

from openpyxl import Workbook

from birthday_bot.birthdays import (
    BirthdayEntry,
    birthdays_for_date,
    build_today_birthday_lines,
    build_weekly_reminder_lines,
    load_birthdays_from_workbook,
    observed_birthday,
    parse_day_month_text,
    weekly_birthdays_for_notification,
)


def create_test_workbook(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Внешние коллеги"
    sheet.append(["ФИО", None, "Дата", "Дата", "Месяц", None])
    sheet.append(["Иван Иванов", "Отдел A", "7 апреля", 7, 4, None])
    sheet.append(["Петр Петров", "Отдел B", "9 мая", 9, 5, None])
    workbook.save(path)
    workbook.close()


class BirthdayLogicTests(unittest.TestCase):
    def test_weekly_notification_covers_current_weekend_and_next_week(self) -> None:
        entries = [
            BirthdayEntry(full_name="Суббота", department="", day=11, month=4),
            BirthdayEntry(full_name="Воскресенье", department="", day=12, month=4),
            BirthdayEntry(full_name="Понедельник", department="", day=13, month=4),
            BirthdayEntry(full_name="Следующее воскресенье", department="", day=19, month=4),
            BirthdayEntry(full_name="Пятница", department="", day=10, month=4),
        ]
        dispatch_date = date(2026, 4, 10)

        result = weekly_birthdays_for_notification(entries, dispatch_date)

        self.assertEqual(
            [(item.entry.full_name, item.target_date) for item in result],
            [
                ("Суббота", date(2026, 4, 11)),
                ("Воскресенье", date(2026, 4, 12)),
                ("Понедельник", date(2026, 4, 13)),
                ("Следующее воскресенье", date(2026, 4, 19)),
            ],
        )

    def test_weekend_birthdays_repeat_in_next_friday_digest(self) -> None:
        entries = [
            BirthdayEntry(full_name="Суббота", department="", day=18, month=4),
            BirthdayEntry(full_name="Воскресенье", department="", day=19, month=4),
        ]

        first_result = weekly_birthdays_for_notification(entries, date(2026, 4, 10))
        second_result = weekly_birthdays_for_notification(entries, date(2026, 4, 17))

        self.assertEqual([item.target_date for item in first_result], [date(2026, 4, 18), date(2026, 4, 19)])
        self.assertEqual([item.target_date for item in second_result], [date(2026, 4, 18), date(2026, 4, 19)])

    def test_feb_29_moves_to_feb_28_in_non_leap_year(self) -> None:
        entry = BirthdayEntry(full_name="Leap Person", department="", day=29, month=2)

        self.assertEqual(observed_birthday(entry, 2025), date(2025, 2, 28))
        self.assertEqual(observed_birthday(entry, 2024), date(2024, 2, 29))

    def test_weekly_message_contains_department_and_date_headers(self) -> None:
        scheduled = weekly_birthdays_for_notification(
            [BirthdayEntry(full_name="Мария Петрова", department="SberData", day=11, month=4)],
            date(2026, 4, 10),
        )

        message = "\n".join(build_weekly_reminder_lines(scheduled, date(2026, 4, 10)))

        self.assertIn("Мария Петрова (SberData)", message)
        self.assertIn("Суббота, 11 апреля", message)

    def test_weekly_empty_message_matches_requested_text(self) -> None:
        lines = build_weekly_reminder_lines([], date(2026, 4, 10))

        self.assertEqual(lines, ["Дней рождений в Сбере на следующей неделе нет."])

    def test_today_message_contains_date_and_cluster(self) -> None:
        entries = [
            BirthdayEntry(full_name="Мария Петрова", department="кластер Морозова", day=10, month=4),
        ]

        message = "\n".join(build_today_birthday_lines(entries, date(2026, 4, 10)))

        self.assertIn("День рождения сегодня - 10 апреля у:", message)
        self.assertIn("Мария Петрова (кластер Морозова)", message)

    def test_birthdays_for_date_returns_only_today(self) -> None:
        entries = [
            BirthdayEntry(full_name="Сегодня", department="", day=10, month=4),
            BirthdayEntry(full_name="Не сегодня", department="", day=11, month=4),
        ]

        result = birthdays_for_date(entries, date(2026, 4, 10))

        self.assertEqual([entry.full_name for entry in result], ["Сегодня"])

    def test_parse_numeric_date_text(self) -> None:
        self.assertEqual(parse_day_month_text("07.04"), (7, 4))


class WorkbookImportTests(unittest.TestCase):
    def test_load_birthdays_from_workbook(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workbook_path = Path(temp_dir) / "birthdays.xlsx"
            create_test_workbook(workbook_path)

            entries = load_birthdays_from_workbook(workbook_path)

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].full_name, "Иван Иванов")
        self.assertEqual(entries[0].entry_id, 2)
        self.assertEqual(entries[1].day, 9)
        self.assertEqual(entries[1].month, 5)


if __name__ == "__main__":
    unittest.main()
