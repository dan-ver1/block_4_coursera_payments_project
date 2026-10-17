"""Локальні перевірки без PostgreSQL і виклику Gemini."""

import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from llm_report import SYSTEM_INSTRUCTION, compact_for_model, make_prompt, validate_report_dates
from metrics import DAILY_QUERY, TOTAL_QUERY, calculate_period, get_available_dates, get_period
from report_data import build_report_data, metric_change
from report_writer import create_report, make_markdown


def period(start, end, amount, payers, payments, daily=None):
    result = {
        "start": start,
        "end": end,
        "payment_sum_usd": Decimal(amount),
        "paying_users": payers,
        "amount_per_payer_usd": Decimal(amount) / payers if payers else None,
        "payment_count": payments,
        "days_with_payments": (end - start).days + 1 if payments else 0,
    }
    if daily is not None:
        result["daily"] = daily
    return result


class MetricsTests(unittest.TestCase):
    def test_sql_uses_same_refund_filter(self):
        self.assertIn("is_refunded = 0", TOTAL_QUERY)
        self.assertIn("is_refunded = 0", DAILY_QUERY)

    def test_period_calculation_keeps_unrounded_ratio(self):
        cursor = Mock()
        cursor.fetchone.return_value = (Decimal("10.00"), 3, 3, 2)
        cursor.fetchall.return_value = [
            (date(2023, 2, 13), Decimal("4.00"), 1, 1),
            (date(2023, 2, 14), Decimal("6.00"), 2, 2),
        ]

        result = calculate_period(
            cursor, date(2023, 2, 13), date(2023, 2, 14), include_daily=True
        )

        self.assertEqual(result["amount_per_payer_usd"], Decimal("10") / 3)
        self.assertEqual(result["daily"][1]["payment_sum_usd"], Decimal("6.00"))
        self.assertEqual(cursor.execute.call_args_list[0].args[1]["end_exclusive"], date(2023, 2, 15))

    def test_reversed_dates_fail_before_database_access(self):
        with self.assertRaises(ValueError):
            get_period(date(2023, 2, 15), date(2023, 2, 14))

    def test_available_dates_come_from_included_payments(self):
        cursor = Mock()
        cursor.fetchall.return_value = [
            (date(2023, 2, 13),), (date(2023, 2, 14),)
        ]
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        connection.cursor.return_value.__enter__ = Mock(return_value=cursor)
        connection.cursor.return_value.__exit__ = Mock(return_value=False)
        with (
            patch("metrics.check_environment"),
            patch("metrics.psycopg.connect", return_value=connection),
        ):
            dates = get_available_dates()
        self.assertEqual(dates, (date(2023, 2, 13), date(2023, 2, 14)))
        self.assertIn("is_refunded = 0", cursor.execute.call_args.args[0])


class ReportDataTests(unittest.TestCase):
    def test_two_consecutive_weeks_are_compared(self):
        start = date(2023, 2, 20)
        end = date(2023, 2, 26)
        first = period(date(2023, 2, 13), date(2023, 2, 19), "60956", 794, 794)
        second = period(start, end, "56581", 719, 719, daily=[])

        with patch("report_data.get_period", side_effect=[second, first]) as mocked:
            result = build_report_data(start, end)

        change = result["comparison"]["changes_selected_vs_comparison"]
        self.assertEqual(change["payment_sum_usd"]["absolute"], "-4375.00")
        self.assertEqual(change["payment_sum_usd"]["percent"], "-7.18")
        self.assertEqual(change["paying_users"]["absolute"], -75)
        self.assertEqual(result["comparison"]["comparison_days"], 7)
        self.assertEqual(mocked.call_count, 2)

    def test_arbitrary_lengths_use_independent_dates_and_daily_rate(self):
        start = date(2023, 2, 6)
        end = date(2023, 2, 15)
        comparison_start = date(2023, 1, 20)
        comparison_end = date(2023, 1, 24)
        entire = period(start, end, "100", 2, 2, daily=[])
        baseline = period(comparison_start, comparison_end, "100", 2, 2)

        with patch("report_data.get_period", side_effect=[entire, baseline]) as mocked:
            result = build_report_data(start, end, comparison_start, comparison_end)

        self.assertEqual(result["number_of_days"], 10)
        self.assertEqual(len(result["daily_metrics"]), 10)
        self.assertEqual(result["comparison"]["comparison_days"], 5)
        self.assertFalse(result["comparison"]["overlaps"])
        self.assertEqual(result["comparison"]["payment_sum_per_day_usd"]["comparison"], "20.00")
        self.assertEqual(result["comparison"]["payment_sum_per_day_usd"]["selected"], "10.00")
        self.assertEqual(result["daily_metrics"][0]["payment_sum_usd"], "0.00")
        self.assertEqual(mocked.call_count, 2)

    def test_single_days_can_be_compared(self):
        selected_day = date(2023, 2, 15)
        baseline_day = date(2023, 2, 3)
        selected = period(selected_day, selected_day, "20", 1, 1, daily=[])
        baseline = period(baseline_day, baseline_day, "10", 1, 1)
        with patch("report_data.get_period", side_effect=[selected, baseline]):
            result = build_report_data(selected_day, selected_day, baseline_day, baseline_day)
        self.assertEqual(result["number_of_days"], 1)
        self.assertEqual(result["comparison"]["comparison_days"], 1)
        self.assertEqual(result["comparison"]["changes_selected_vs_comparison"]["payment_sum_usd"]["percent"], "100.00")

    def test_default_comparison_matches_selected_length(self):
        start = date(2023, 2, 6)
        end = date(2023, 2, 18)
        selected = period(start, end, "0", 0, 0, daily=[])
        baseline = period(date(2023, 1, 24), date(2023, 2, 5), "0", 0, 0)
        with patch("report_data.get_period", side_effect=[selected, baseline]) as mocked:
            build_report_data(start, end)
        self.assertEqual(mocked.call_args_list[1].args, (date(2023, 1, 24), date(2023, 2, 5)))

    def test_zero_baseline_has_no_percent_change(self):
        self.assertEqual(
            metric_change(Decimal("0"), Decimal("5")),
            {"absolute": "5.00", "percent": None},
        )


class ReportWriterTests(unittest.TestCase):
    def test_report_rejects_date_outside_selected_periods(self):
        data = {
            "selected_period": {"start": "2023-01-01", "end": "2023-01-14"},
            "comparison": {
                "comparison_period": {"start": "2022-12-18", "end": "2022-12-31"}
            },
        }
        validate_report_dates("Порівняння з 2022-12-18 — 2022-12-31.", data)
        with self.assertRaises(RuntimeError):
            validate_report_dates("2022-12-14 (правильно: 2022-12-31)", data)

    def test_long_period_compaction_preserves_amount(self):
        daily = [
            {
                "date": (date(2023, 1, 1) + timedelta(days=i)).isoformat(),
                "payment_sum_usd": "1.00",
                "payment_count": 1,
            }
            for i in range(32)
        ]
        compact = compact_for_model({"daily_metrics": daily, "selected_period": {}})

        self.assertNotIn("daily_metrics", compact)
        self.assertLessEqual(len(compact["grouped_daily_metrics"]), 24)
        self.assertEqual(
            sum(Decimal(row["payment_sum_usd"]) for row in compact["grouped_daily_metrics"]),
            Decimal("32.00"),
        )

    def test_markdown_has_one_title_and_concise_source_footer(self):
        data = {
            "selected_period": {"start": "2023-02-13", "end": "2023-02-26"},
            "comparison": {
                "comparison_period": {"start": "2023-01-30", "end": "2023-02-12"}
            },
        }
        text = (
            "**Звіт за період 13–26 лютого**\n\n"
            "Порівнюємо з 2023-01-30—2023-02-12.\n\n### Три висновки\nТест"
        )
        markdown = make_markdown(data, text)

        self.assertEqual(markdown.count("Звіт за період"), 0)
        self.assertEqual(markdown.count("# Платіжний звіт"), 1)
        self.assertIn("is_refunded = 0", markdown)
        self.assertIn("2023-01-30—2023-02-12", markdown)
        self.assertIn("### Три висновки", markdown)
        self.assertNotIn("підтвердженим чистим доходом", markdown)
        self.assertNotIn("Дати фактичних повернень", markdown)
        self.assertTrue(markdown.rstrip().endswith(
            "Джерело: `public.payments`, платежі з `is_refunded = 0`."
        ))

    def test_report_prompt_asks_for_readable_actions_without_raw_daily_list(self):
        prompt = make_prompt({"daily_metrics": [], "selected_period": {}}, question=None)
        self.assertIn("трьома різними темами", prompt)
        self.assertIn("що робити", SYSTEM_INSTRUCTION)
        self.assertIn("якщо", prompt.lower())
        self.assertIn("не виводь перелік усіх днів", prompt.lower())
        self.assertIn("повторюється така картина", prompt)
        self.assertIn("можливі наступні перевірки", prompt)
        self.assertIn("не стверджуй,\n  що одна пара проміжків доводить сезонність", SYSTEM_INSTRUCTION)
        self.assertIn("не додавай", SYSTEM_INSTRUCTION.lower())

    def test_create_report_uses_shared_pipeline_once(self):
        start = date(2023, 2, 13)
        end = date(2023, 2, 26)
        data = {"selected_period": {"start": start.isoformat(), "end": end.isoformat()}}

        with (
            patch("report_writer.build_report_data", return_value=data) as facts,
            patch("report_writer.generate_text", return_value="### Показники\nТест") as model,
            patch("report_writer.save_markdown", return_value=Path("report.md")) as save,
        ):
            markdown, path = create_report(start, end)

        facts.assert_called_once_with(start, end, None, None)
        model.assert_called_once_with(data, question=None)
        save.assert_called_once_with(markdown, start, end)
        self.assertEqual(path, Path("report.md"))

    def test_model_error_does_not_save_report(self):
        start = date(2023, 2, 13)
        end = date(2023, 2, 26)
        data = {"selected_period": {"start": start.isoformat(), "end": end.isoformat()}}

        with (
            patch("report_writer.build_report_data", return_value=data),
            patch("report_writer.generate_text", side_effect=RuntimeError("API error")),
            patch("report_writer.save_markdown") as save,
        ):
            with self.assertRaises(RuntimeError):
                create_report(start, end)

        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
