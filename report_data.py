"""Підготовка розрахованих даних вибраного періоду для LLM."""

import argparse
import json
from datetime import date, timedelta
from decimal import Decimal

from metrics import get_period


DEFAULT_START = date(2023, 2, 20)
DEFAULT_END = date(2023, 2, 26)
DEFAULT_COMPARISON_START = date(2023, 2, 13)
DEFAULT_COMPARISON_END = date(2023, 2, 19)


def money_text(value):
    """Грошові значення передаємо як рядки, щоб не втратити точність."""
    if value is None:
        return None
    return str(Decimal(value).quantize(Decimal("0.01")))


def period_summary(period):
    return {
        "start": period["start"].isoformat(),
        "end": period["end"].isoformat(),
        "payment_sum_usd": money_text(period["payment_sum_usd"]),
        "paying_users": period["paying_users"],
        "amount_per_payer_usd": money_text(period["amount_per_payer_usd"]),
        "payment_count": period["payment_count"],
        "days_with_payments": period["days_with_payments"],
    }


def metric_change(before, after):
    if before is None or after is None:
        return {"absolute": None, "percent": None}

    difference = after - before
    percent = (
        Decimal(difference) / Decimal(before) * 100
        if before != 0
        else None
    )

    absolute = (
        money_text(difference)
        if isinstance(difference, Decimal)
        else difference
    )

    return {
        "absolute": absolute,
        "percent": money_text(percent),
    }


def daily_summary(period):
    """Повернути один запис на кожну вибрану дату."""
    source_by_date = {row["date"]: row for row in period["daily"]}
    day_count = (period["end"] - period["start"]).days + 1
    result = []

    for offset in range(day_count):
        day = period["start"] + timedelta(days=offset)
        row = source_by_date.get(day)

        if row is None:
            amount = Decimal("0")
            payers = 0
            payments = 0
        else:
            amount = row["payment_sum_usd"]
            payers = row["paying_users"]
            payments = row["payment_count"]

        amount_per_payer = amount / payers if payers else None

        result.append(
            {
                "date": day.isoformat(),
                "payment_sum_usd": money_text(amount),
                "paying_users_that_day": payers,
                "amount_per_payer_that_day_usd": money_text(amount_per_payer),
                "payment_count": payments,
            }
        )

    return result


def build_report_data(start_date, end_date, comparison_start=None, comparison_end=None):
    """Підготувати факти для двох незалежно вибраних проміжків."""
    if (comparison_start is None) != (comparison_end is None):
        raise ValueError("Вкажіть обидві дати періоду порівняння")
    if comparison_start is None:
        day_count = (end_date - start_date).days + 1
        comparison_end = start_date - timedelta(days=1)
        comparison_start = start_date - timedelta(days=day_count)

    period = get_period(start_date, end_date, include_daily=True)
    baseline = get_period(comparison_start, comparison_end)
    day_count = (end_date - start_date).days + 1
    comparison_days = (comparison_end - comparison_start).days + 1
    overlaps = comparison_start <= end_date and start_date <= comparison_end
    metric_names = ("payment_sum_usd", "paying_users", "amount_per_payer_usd")

    result = {
        "selected_period": period_summary(period),
        "number_of_days": day_count,
        "daily_metrics": daily_summary(period),
        "comparison": {
            "comparison_period": period_summary(baseline),
            "selected_days": day_count,
            "comparison_days": comparison_days,
            "overlaps": overlaps,
            "changes_selected_vs_comparison": {
                name: metric_change(baseline[name], period[name])
                for name in metric_names
            },
            "payment_sum_per_day_usd": {
                "comparison": money_text(baseline["payment_sum_usd"] / comparison_days),
                "selected": money_text(period["payment_sum_usd"] / day_count),
                "change": metric_change(
                    baseline["payment_sum_usd"] / comparison_days,
                    period["payment_sum_usd"] / day_count,
                ),
            },
        },
        "metric_definitions": {
            "payment_sum_usd": (
                "Сума amount_usd для платежів із is_refunded = 0; "
                "це не підтверджений чистий дохід."
            ),
            "paying_users": "Кількість різних user_id за весь вибраний період.",
            "amount_per_payer_usd": (
                "Сума платежів за період / унікальні платники за період."
            ),
            "daily_paying_users": (
                "Унікальні платники в окремий день; ці денні значення "
                "не можна складати для отримання унікальних платників за період."
            ),
        },
        "possible_follow_up_checks": [
            "У public.payments є plan і amount_usd: можна окремо перевірити, "
            "чи змінилася структура тарифних планів та сум платежів. "
            "Результату цієї перевірки в поточному JSON немає.",
            "У public.payments є user_id: можна окремо зіставити платників "
            "двох проміжків, але сам перетин ще не доведе відтік клієнтів "
            "або причину зміни платежів. Перетин зараз не розраховано.",
            "Можна зіставити денні показники з аналогічними днями інших "
            "тижнів, перш ніж робити висновок про повторюваність спаду.",
        ],
        "limitations": [
            "У таблиці немає дати повернення платежу.",
            "Зміни показників самі по собі не доводять їхніх причин.",
        ],
    }
    if comparison_days != day_count:
        result["limitations"].append(
            "Проміжки мають різну тривалість: різниця загальних сум і "
            "кількості платників не є порівнянням інтенсивності."
        )
    if overlaps:
        result["limitations"].append(
            "Проміжки перетинаються: частина платежів входить в обидва підсумки."
        )

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Підготувати показники довільного проміжку для LLM"
    )
    parser.add_argument(
        "dates",
        nargs="*",
        type=date.fromisoformat,
        help="Початкова й кінцева дати у форматі РРРР-ММ-ДД",
    )
    args = parser.parse_args()

    if len(args.dates) == 0:
        start_date, end_date = DEFAULT_START, DEFAULT_END
        comparison_start, comparison_end = DEFAULT_COMPARISON_START, DEFAULT_COMPARISON_END
    elif len(args.dates) == 2:
        start_date, end_date = args.dates
        comparison_start = comparison_end = None
    elif len(args.dates) == 4:
        start_date, end_date, comparison_start, comparison_end = args.dates
    else:
        parser.error("вкажіть 0, 2 або 4 дати: вибраний період, потім період порівняння")

    if start_date > end_date:
        parser.error("початкова дата має бути не пізнішою за кінцеву")

    result = build_report_data(start_date, end_date, comparison_start, comparison_end)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
