"""Платіжні показники за два тижні або довільний проміжок дат."""

import argparse
import os
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
from dotenv import load_dotenv


DEFAULT_WEEK_START = date(2023, 2, 13)

TOTAL_QUERY = """
    SELECT
        COALESCE(SUM(amount_usd), 0),
        COUNT(DISTINCT user_id),
        COUNT(*),
        COUNT(DISTINCT paid_at)
    FROM public.payments
    WHERE paid_at >= %(start)s
      AND paid_at < %(end_exclusive)s
      AND is_refunded = 0
"""

DAILY_QUERY = """
    SELECT
        paid_at,
        COALESCE(SUM(amount_usd), 0),
        COUNT(DISTINCT user_id),
        COUNT(*)
    FROM public.payments
    WHERE paid_at >= %(start)s
      AND paid_at < %(end_exclusive)s
      AND is_refunded = 0
    GROUP BY paid_at
    ORDER BY paid_at
"""


def check_environment():
    load_dotenv(Path(__file__).with_name(".env"))
    required = ("PGHOST", "PGDATABASE", "PGUSER", "PGPASSWORD")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"У .env бракує змінних: {', '.join(missing)}")


def get_available_dates():
    """Отримати лише дати платежів, які враховує дашборд."""
    check_environment()

    with psycopg.connect(connect_timeout=10) as connection:
        connection.read_only = True
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = 15000")
            cursor.execute(
                "SELECT DISTINCT paid_at FROM public.payments "
                "WHERE paid_at IS NOT NULL AND is_refunded = 0 ORDER BY paid_at"
            )
            available_dates = tuple(row[0] for row in cursor.fetchall())

    if not available_dates:
        raise ValueError("У таблиці payments немає дат врахованих платежів")
    return available_dates


def calculate_period(cursor, start_date, end_date, include_daily=False):
    """Розрахувати показники за дати включно."""
    end_exclusive = end_date + timedelta(days=1)
    params = {"start": start_date, "end_exclusive": end_exclusive}

    cursor.execute(TOTAL_QUERY, params)
    payment_sum, paying_users, payment_count, days_with_payments = cursor.fetchone()

    payment_sum = Decimal(payment_sum)
    amount_per_payer = payment_sum / paying_users if paying_users else None

    result = {
        "start": start_date,
        "end": end_date,
        "payment_sum_usd": payment_sum,
        "paying_users": paying_users,
        "amount_per_payer_usd": amount_per_payer,
        "payment_count": payment_count,
        "days_with_payments": days_with_payments,
    }

    if include_daily:
        cursor.execute(DAILY_QUERY, params)
        result["daily"] = [
            {
                "date": day,
                "payment_sum_usd": Decimal(day_sum),
                "paying_users": day_users,
                "payment_count": day_payments,
            }
            for day, day_sum, day_users, day_payments in cursor.fetchall()
        ]

    return result


def get_period(start_date, end_date, include_daily=False):
    """Прочитати дані за довільний проміжок без зміни бази."""
    if start_date > end_date:
        raise ValueError("Початкова дата має бути не пізнішою за кінцеву")

    check_environment()

    with psycopg.connect(connect_timeout=10) as connection:
        connection.read_only = True
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = 15000")
            cursor.execute("SELECT MIN(paid_at), MAX(paid_at) FROM public.payments")
            first_date, last_date = cursor.fetchone()

            if first_date is None or last_date is None:
                raise ValueError("У таблиці payments немає дат платежів")
            if start_date < first_date or end_date > last_date:
                raise ValueError(
                    f"Виберіть дати в межах {first_date}—{last_date}"
                )

            return calculate_period(
                cursor, start_date, end_date, include_daily=include_daily
            )


def get_two_weeks(week_start):
    """Порівняти два послідовні семиденні періоди."""
    if week_start.weekday() != 0:
        raise ValueError("Для двотижневого звіту дата початку має бути понеділком")

    first = get_period(week_start, week_start + timedelta(days=6))
    second_start = week_start + timedelta(days=7)
    second = get_period(second_start, second_start + timedelta(days=6))
    return first, second


def show_change(label, before, after):
    if before is None or after is None:
        print(f"  {label}: порівняння неможливе — немає платників")
        return

    difference = after - before
    difference_text = (
        f"{difference:+.2f}" if isinstance(difference, Decimal) else f"{difference:+}"
    )

    if before == 0:
        print(f"  {label}: зміна {difference_text}; відсоток не визначено")
        return

    percent = (Decimal(difference) / Decimal(before) * 100).quantize(
        Decimal("0.01")
    )
    print(f"  {label}: зміна {difference_text} ({percent:+}%)")


def print_totals(period):
    print(f"\n{period['start']}—{period['end']}")
    print(f"  Сума платежів без прапорця повернення: {period['payment_sum_usd']} USD")
    print(f"  Унікальних платників: {period['paying_users']}")

    per_payer = period["amount_per_payer_usd"]
    per_payer_text = f"{per_payer:.2f} USD" if per_payer is not None else "не визначено"
    print(f"  Сума на платника: {per_payer_text}")

    print(f"  Платежів: {period['payment_count']}")
    total_days = (period["end"] - period["start"]).days + 1
    print(f"  Днів із такими платежами: {period['days_with_payments']}/{total_days}")


def print_two_week_report(first, second):
    print_totals(first)
    print_totals(second)

    print("\nЗміна другого тижня відносно першого:")
    show_change(
        "Сума платежів, USD",
        first["payment_sum_usd"],
        second["payment_sum_usd"],
    )
    show_change("Платники", first["paying_users"], second["paying_users"])
    show_change(
        "Сума на платника, USD",
        first["amount_per_payer_usd"],
        second["amount_per_payer_usd"],
    )


def print_custom_period(period):
    print_totals(period)
    print("\nДинаміка за днями:")
    print("Дата         Сума, USD    Платники    Платежі")

    daily_by_date = {row["date"]: row for row in period["daily"]}
    total_days = (period["end"] - period["start"]).days + 1

    for offset in range(total_days):
        day = period["start"] + timedelta(days=offset)
        row = daily_by_date.get(day)

        if row is None:
            print(f"{day}         0.00           0          0")
        else:
            print(
                f"{day}  "
                f"{row['payment_sum_usd']:>12.2f}  "
                f"{row['paying_users']:>10}  "
                f"{row['payment_count']:>9}"
            )


def main():
    parser = argparse.ArgumentParser(
        description="Показники за два тижні або довільний проміжок дат"
    )
    parser.add_argument(
        "dates",
        nargs="*",
        type=date.fromisoformat,
        help="Дати у форматі РРРР-ММ-ДД",
    )
    args = parser.parse_args()

    if len(args.dates) == 0:
        first, second = get_two_weeks(DEFAULT_WEEK_START)
        print_two_week_report(first, second)
    elif len(args.dates) == 2:
        start_date, end_date = args.dates
        if start_date > end_date:
            parser.error("початкова дата має бути не пізнішою за кінцеву")
        period = get_period(start_date, end_date, include_daily=True)
        print_custom_period(period)
    else:
        parser.error("вкажіть або жодної дати, або дві дати: початкову та кінцеву")


if __name__ == "__main__":
    main()
