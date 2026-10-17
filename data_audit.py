"""Read-only перевірка структури, якості та покриття платіжних даних."""

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv


load_dotenv(Path(__file__).with_name(".env"))

required = ("PGHOST", "PGDATABASE", "PGUSER", "PGPASSWORD")
missing = [name for name in required if not os.getenv(name)]
if missing:
    raise RuntimeError(f"У .env бракує змінних: {', '.join(missing)}")

query = """
    SELECT
        c.table_schema,
        c.table_name,
        t.table_type,
        c.column_name,
        c.data_type,
        c.is_nullable
    FROM information_schema.columns AS c
    JOIN information_schema.tables AS t
      ON t.table_schema = c.table_schema
     AND t.table_name = c.table_name
    WHERE c.table_schema NOT IN ('pg_catalog', 'information_schema')
    ORDER BY c.table_schema, c.table_name, c.ordinal_position
"""

with psycopg.connect(connect_timeout=10) as connection:
    connection.read_only = True

    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL statement_timeout = 15000")
        cursor.execute(query)
        columns = cursor.fetchall()

current_table = None
for schema, table, table_type, column, data_type, nullable in columns:
    if (schema, table) != current_table:
        current_table = (schema, table)
        print(f"\n{schema}.{table} [{table_type}]")

    print(f"  {column}: {data_type}; NULL: {nullable}")

if not columns:
    print("Доступних таблиць або представлень не знайдено.")
quality_query = """
    SELECT
        COUNT(*) AS total_rows,
        COUNT(DISTINCT payment_id) AS distinct_payment_ids,
        COUNT(*) - COUNT(DISTINCT payment_id) AS repeated_payment_ids,
        COUNT(*) FILTER (WHERE btrim(payment_id) = '') AS empty_payment_ids,
        COUNT(*) FILTER (WHERE user_id IS NULL OR btrim(user_id) = '') AS missing_user_ids,
        COUNT(*) FILTER (WHERE paid_at IS NULL) AS missing_dates,
        MIN(paid_at) AS first_payment_date,
        MAX(paid_at) AS last_payment_date,
        COUNT(*) FILTER (WHERE amount_usd IS NULL) AS missing_amounts,
        COUNT(*) FILTER (WHERE amount_usd < 0) AS negative_amounts,
        COUNT(*) FILTER (WHERE amount_usd = 0) AS zero_amounts,
        COUNT(*) FILTER (WHERE plan IS NULL OR btrim(plan) = '') AS missing_plans,
        COUNT(*) FILTER (WHERE is_refunded IS NULL) AS missing_refund_flags,
        COUNT(*) FILTER (
            WHERE is_refunded IS NOT NULL AND is_refunded NOT IN (0, 1)
        ) AS unexpected_refund_flags
    FROM public.payments
"""

with psycopg.connect(connect_timeout=10) as connection:
    connection.read_only = True

    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL statement_timeout = 15000")
        cursor.execute(quality_query)
        names = [column.name for column in cursor.description]
        values = cursor.fetchone()

print("\nЯкість таблиці payments:")
for name, value in zip(names, values):
    print(f"  {name}: {value}")
distribution_query = """
    SELECT
        plan,
        is_refunded,
        COUNT(*) AS payment_count,
        MIN(amount_usd) AS min_amount_usd,
        MAX(amount_usd) AS max_amount_usd,
        ROUND(AVG(amount_usd), 2) AS avg_amount_usd
    FROM public.payments
    GROUP BY plan, is_refunded
    ORDER BY plan, is_refunded
"""

with psycopg.connect(connect_timeout=10) as connection:
    connection.read_only = True

    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL statement_timeout = 15000")
        cursor.execute(distribution_query)
        distribution = cursor.fetchall()

print("\nПлани та ознаки повернення:")
for row in distribution:
    print(row)
duplicate_query = """
    WITH groups AS (
        SELECT
            user_id, paid_at, plan, amount_usd, is_refunded,
            COUNT(*) AS rows_in_group
        FROM public.payments
        GROUP BY user_id, paid_at, plan, amount_usd, is_refunded
        HAVING COUNT(*) > 1
    )
    SELECT
        COUNT(*) AS repeated_groups,
        COALESCE(SUM(rows_in_group - 1), 0) AS possible_extra_rows,
        COALESCE(MAX(rows_in_group), 0) AS largest_group
    FROM groups
"""

with psycopg.connect(connect_timeout=10) as connection:
    connection.read_only = True

    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL statement_timeout = 15000")
        cursor.execute(duplicate_query)
        result = cursor.fetchone()

print("\nМожливі повтори операцій:")
print(f"  груп із повторами: {result[0]}")
print(f"  можливих зайвих рядків: {result[1]}")
print(f"  найбільша група: {result[2]}")
duplicate_details_query = """
    SELECT
        paid_at,
        plan,
        amount_usd,
        is_refunded,
        ARRAY_AGG(payment_id ORDER BY payment_id) AS payment_ids
    FROM public.payments
    GROUP BY user_id, paid_at, plan, amount_usd, is_refunded
    HAVING COUNT(*) > 1
"""

with psycopg.connect(connect_timeout=10) as connection:
    connection.read_only = True

    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL statement_timeout = 15000")
        cursor.execute(duplicate_details_query)
        suspected_groups = cursor.fetchall()

print("\nДеталі можливого повтору:")
for paid_at, plan, amount, refunded, ids in suspected_groups:
    print(f"  дата: {paid_at}; план: {plan}; сума: {amount}; is_refunded: {refunded}")
    print(f"  кількість записів: {len(ids)}; довжини ID: {[len(item) for item in ids]}")

    if len(ids) == 2:
        first, second = ids
        if first.startswith(second):
            print(f"  відмінність ID: до коротшого додано {first[len(second):]!r}")
        elif second.startswith(first):
            print(f"  відмінність ID: до коротшого додано {second[len(first):]!r}")
        else:
            print("  один ID не є продовженням іншого")
user_integrity_query = """
    WITH user_keys AS (
        SELECT user_id, COUNT(*) AS records_per_user
        FROM public.users
        GROUP BY user_id
    )
    SELECT
        (SELECT COUNT(*) FROM public.users) AS user_rows,
        (SELECT COUNT(*) FROM user_keys) AS distinct_user_ids,
        (SELECT COUNT(*) FROM user_keys WHERE records_per_user > 1)
            AS repeated_user_ids,
        (
            SELECT COUNT(*)
            FROM public.payments AS p
            LEFT JOIN user_keys AS u ON u.user_id = p.user_id
            WHERE u.user_id IS NULL
        ) AS payments_without_user
"""

with psycopg.connect(connect_timeout=10) as connection:
    connection.read_only = True

    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL statement_timeout = 15000")
        cursor.execute(user_integrity_query)
        names = [column.name for column in cursor.description]
        values = cursor.fetchone()

print("\nЗв'язок payments → users:")
for name, value in zip(names, values):
    print(f"  {name}: {value}")
date_integrity_query = """
    SELECT
        COALESCE(
            SUM(p.amount_usd) FILTER (WHERE u.user_id IS NULL), 0
        ) AS amount_without_user_usd,
        MIN(p.paid_at) FILTER (WHERE u.user_id IS NULL)
            AS first_payment_without_user,
        MAX(p.paid_at) FILTER (WHERE u.user_id IS NULL)
            AS last_payment_without_user,
        COUNT(*) FILTER (
            WHERE u.user_id IS NOT NULL AND u.signup_date IS NULL
        ) AS payments_with_missing_signup_date,
        COUNT(*) FILTER (
            WHERE u.signup_date IS NOT NULL
              AND p.paid_at < u.signup_date
        ) AS payments_before_signup
    FROM public.payments AS p
    LEFT JOIN public.users AS u ON u.user_id = p.user_id
"""

with psycopg.connect(connect_timeout=10) as connection:
    connection.read_only = True

    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL statement_timeout = 15000")
        cursor.execute(date_integrity_query)
        names = [column.name for column in cursor.description]
        values = cursor.fetchone()

print("\nВплив відсутніх користувачів і перевірка дат:")
for name, value in zip(names, values):
    print(f"  {name}: {value}")
calendar_query = """
    WITH bounds AS (
        SELECT MIN(paid_at) AS first_day, MAX(paid_at) AS last_day
        FROM public.payments
    ),
    calendar AS (
        SELECT generate_series(
            first_day::timestamp,
            last_day::timestamp,
            INTERVAL '1 day'
        )::date AS day
        FROM bounds
    ),
    daily AS (
        SELECT paid_at, COUNT(*) AS payment_count
        FROM public.payments
        GROUP BY paid_at
    )
    SELECT COUNT(*) FILTER (WHERE daily.payment_count IS NULL)
    FROM calendar
    LEFT JOIN daily ON daily.paid_at = calendar.day
"""

weeks_query = """
    WITH weeks AS (
        SELECT
            date_trunc('week', paid_at)::date AS week_start,
            COUNT(*) AS payment_count,
            COUNT(DISTINCT paid_at) AS days_with_payments
        FROM public.payments
        GROUP BY 1
    )
    SELECT
        week_start,
        week_start + 6 AS week_end,
        payment_count,
        days_with_payments
    FROM weeks
    ORDER BY week_start DESC
    LIMIT 6
"""

with psycopg.connect(connect_timeout=10) as connection:
    connection.read_only = True

    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL statement_timeout = 15000")

        cursor.execute(calendar_query)
        missing_days = cursor.fetchone()[0]

        cursor.execute(weeks_query)
        recent_weeks = cursor.fetchall()

print(f"\nДнів без платежів за весь період: {missing_days}")
print("Останні шість тижнів:")
for start, end, count, days in recent_weeks:
    print(f"  {start}—{end}: {count} платежів, {days}/7 днів із платежами")
