"""Створення та збереження платіжного звіту у Markdown."""

import argparse
from datetime import date, datetime, timezone
from pathlib import Path

from llm_report import generate_text
from report_data import (
    DEFAULT_COMPARISON_END, DEFAULT_COMPARISON_START,
    DEFAULT_END, DEFAULT_START, build_report_data,
)


def make_markdown(data, generated_text):
    period = data["selected_period"]
    lines = generated_text.strip().splitlines()
    if lines and (
        lines[0].startswith("**Звіт за період")
        or lines[0].startswith("# Звіт за період")
    ):
        lines = lines[1:]
    body = "\n".join(lines).strip()

    return (
        f"# Платіжний звіт: {period['start']}—{period['end']}\n\n"
        f"{body}\n\n"
        "Джерело: `public.payments`, платежі з `is_refunded = 0`.\n"
    )


def save_markdown(markdown, start_date, end_date):
    report_dir = Path(__file__).with_name("reports")
    report_dir.mkdir(exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    filename = (
        f"payment_report_{start_date.isoformat()}_"
        f"{end_date.isoformat()}_{timestamp}.md"
    )
    path = report_dir / filename

    # Режим "x" не дозволить випадково перезаписати наявний звіт.
    with path.open("x", encoding="utf-8") as file:
        file.write(markdown)

    return path


def create_report(start_date, end_date, comparison_start=None, comparison_end=None):
    """Зібрати факти, отримати текст моделі та зберегти готовий звіт."""
    if start_date > end_date:
        raise ValueError("початкова дата має бути не пізнішою за кінцеву")

    data = build_report_data(start_date, end_date, comparison_start, comparison_end)
    generated_text = generate_text(data, question=None)
    markdown = make_markdown(data, generated_text)
    path = save_markdown(markdown, start_date, end_date)
    return markdown, path


def main():
    parser = argparse.ArgumentParser(
        description="Створити Markdown-звіт за вибраний проміжок"
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

    _, path = create_report(start_date, end_date, comparison_start, comparison_end)

    print(f"Звіт збережено: {path}")


if __name__ == "__main__":
    main()
