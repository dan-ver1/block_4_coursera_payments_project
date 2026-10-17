"""Пояснення розрахованих платіжних показників за вибраний проміжок."""

import argparse
import json
import os
import re
from datetime import date
from decimal import Decimal
from math import ceil
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

from report_data import (
    DEFAULT_COMPARISON_END, DEFAULT_COMPARISON_START,
    DEFAULT_END, DEFAULT_START, build_report_data,
)


SYSTEM_INSTRUCTION = """\
Ти бізнес-аналітик навчальної платформи. Пиши українською для керівника,
який хоче зрозуміти зміну показників і вирішити, що перевірити далі.

Точність:
- Бери числа й дати лише з переданого JSON. Не вигадуй подій, причин,
  властивостей користувачів або результатів майбутніх дій.
- Називай суму платежів саме сумою платежів, а не чистим доходом. Не додавай
  стандартної примітки про повернення, якщо питання не стосується повернень.
- Менша кількість платників не доводить відтік або падіння конверсії. Для таких
  висновків потрібні окремі дані про користувачів та їхні дії.
- Сума на платника не завжди дорівнює середньому чеку однієї транзакції.
  Не називай її «чеком», «середнім чеком» чи «ефективністю» у звіті.
  Не складай денні кількості унікальних платників у підсумок за період.
- Пояснюй арифметичний внесок показника, але не видавай його за доведену
  причину. Припущення називай припущеннями й пропонуй спосіб їх перевірити.
- Не вважай один піковий або слабкий день доказом ефекту вихідного дня.
  Не пропонуй акції, знижки, розсилки чи зміну тарифів як готове рішення
  без окремої перевірки. Не стверджуй, що кампанія або збій справді були.
- Не називай період порівняння «попереднім», якщо він не передує вибраному
  безпосередньо. Для різних років називай обидва роки, але не стверджуй,
  що одна пара проміжків доводить сезонність.
- Відсутність платежу користувача в одному з двох проміжків не означає,
  що він новий або пішов із платформи.

Стиль:
- Пиши як колега: просто, прямо й без канцеляризмів. Вживай активні дієслова.
- Без метафор на кшталт «тижнева пастка» та без оцінок, яких немає в даних.
- Не починай з «Ось звіт», «На основі наданих даних» або «Варто зазначити».
- Не повторюй ту саму зміну в кожному розділі й не переписуй усю денну таблицю.
- Замість «рекомендується провести додатковий аналіз» назви конкретну
  перевірку та поясни, яке рішення вона допоможе ухвалити.
- Не повторюй у кожній рекомендації шаблон «це зніме питання» та
  «стане можливим вирішити». Пиши природно: дія, навіщо вона, що робити
  залежно від результату перевірки.
- Не додавай окремий список загальних обмежень. Зазнач лише застереження,
  без якого читач неправильно зрозуміє конкретне порівняння.
- Не повідомляй окремо, що проміжки однакової довжини або не перетинаються:
  зауважуй лише різну тривалість чи фактичний перетин, коли вони є.
- Не згадуй назви полів JSON або технічні деталі програми в основному тексті.
- Перед відповіддю звір усі згадані дати з обома проміжками. Покажи лише
  остаточний текст без чернеткових виправлень на кшталт «правильно: ...».
"""


ISO_DATE_PATTERN = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")


def validate_report_dates(text, data):
    """Не зберігати звіт із датою поза двома обраними проміжками."""
    selected = data["selected_period"]
    baseline = data["comparison"]["comparison_period"]
    ranges = (
        (date.fromisoformat(selected["start"]), date.fromisoformat(selected["end"])),
        (date.fromisoformat(baseline["start"]), date.fromisoformat(baseline["end"])),
    )
    for token in ISO_DATE_PATTERN.findall(text):
        try:
            mentioned = date.fromisoformat(token)
        except ValueError as error:
            raise RuntimeError("Gemini повернув некоректну дату у звіті") from error
        if not any(start <= mentioned <= end for start, end in ranges):
            raise RuntimeError(
                "Gemini згадав дату поза вибраними періодами. "
                "Звіт не збережено; спробуйте сформувати його ще раз."
            )


def compact_for_model(data):
    """Для довгих проміжків передати всі дні як коротші групи."""
    daily = data["daily_metrics"]

    if len(daily) <= 31:
        return data

    group_size = ceil(len(daily) / 24)
    groups = []

    for offset in range(0, len(daily), group_size):
        rows = daily[offset : offset + group_size]
        amount = sum(
            (Decimal(row["payment_sum_usd"]) for row in rows),
            Decimal("0"),
        )

        groups.append(
            {
                "start": rows[0]["date"],
                "end": rows[-1]["date"],
                "days": len(rows),
                "payment_sum_usd": str(amount.quantize(Decimal("0.01"))),
                "payment_count": sum(row["payment_count"] for row in rows),
            }
        )

    result = {
        key: value
        for key, value in data.items()
        if key != "daily_metrics"
    }
    result["grouped_daily_metrics"] = groups
    result["lowest_day_by_payment_sum"] = min(
        daily, key=lambda row: Decimal(row["payment_sum_usd"])
    )
    result["highest_day_by_payment_sum"] = max(
        daily, key=lambda row: Decimal(row["payment_sum_usd"])
    )
    result["grouping_note"] = (
        "Групи охоплюють усі дні вибраного проміжку без пропусків. "
        "Сума та кількість платежів у групі складені з денних значень. "
        "Кількість унікальних платників для кожної групи не розрахована."
    )
    return result


def make_prompt(data, question):
    facts = json.dumps(
        compact_for_model(data),
        ensure_ascii=False,
        separators=(",", ":"),
    )

    if question:
        task = (
            f"Відповідай на запитання користувача: {question}\n"
            "Якщо переданих показників недостатньо для відповіді, "
            "прямо скажи, яких даних бракує. Не замінюй відсутні "
            "дані припущеннями."
        )
    else:
        task = (
            "Напиши короткий платіжний звіт для керівника, орієнтир 180–260 слів. "
            "Заголовок із датами додасть програма, тому не повторюй його. "
            "Почни з 2–3 речень: назви обидва проміжки й головну зміну "
            "з ключовими числами. Не перелічуй усі поля JSON. "
            "Далі дай розділ «### Три висновки» з трьома різними темами: "
            "(1) зміна суми платежів і кількості платників між проміжками; "
            "(2) зміна суми на платника й те, як вона співвідноситься "
            "із загальною сумою; (3) конкретна особливість денної динаміки "
            "вибраного проміжку. Для кожної теми наведи число й коротко "
            "поясни, чому на неї варто звернути увагу. Якщо справді бракує "
            "даних для однієї з тем, не вигадуй висновку. Не виводь перелік "
            "усіх днів. "
            "Заверши розділом «### Що зробити далі»: 2–3 практичні дії. "
            "Для кожної дії назви, які дані перевірити і як використати "
            "результат для рішення. Не використовуй для всіх дій однакову "
            "формулу. Прив'язуй дію "
            "до спостереження зі звіту; не обіцяй результату. Використовуй "
            "можливі наступні перевірки з JSON лише як план, не як виконаний "
            "аналіз. Не роби готових продуктових або маркетингових висновків "
            "з одного дня спостереження. Для одного незвичного дня спершу "
            "запропонуй перевірити, чи повторюється така картина в інші дні "
            "або тижні, і тільки потім вирішувати, чи потрібна дія. "
            "Якщо проміжки різної довжини, порівнюй також суму на календарний "
            "день і коротко поясни, чому загальні суми не зіставні напряму. "
            "Якщо тривалість однакова, не пояснюй цього окремим реченням. "
            "Якщо проміжки перетинаються, зазнач, що частина платежів "
            "потрапляє в обидва підсумки. Не додавай окремого розділу "
            "«Обмеження» й не повторюй технічні визначення показників."
        )

    return f"{task}\n\nРозраховані показники у JSON:\n{facts}"


def generate_text(data, question):
    load_dotenv(Path(__file__).with_name(".env"))
    api_key = os.getenv("GEMINI_API_KEY")
    model = os.getenv("GEMINI_MODEL")

    if not api_key:
        raise RuntimeError("У локальному .env немає GEMINI_API_KEY")
    if not model:
        raise RuntimeError("У локальному .env немає GEMINI_MODEL")

    with genai.Client(api_key=api_key) as client:
        response = client.models.generate_content(
            model=model,
            contents=make_prompt(data, question),
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                temperature=0.4,
                thinking_config=types.ThinkingConfig(thinking_level="low"),
                max_output_tokens=8192,
            ),
        )

    if not response.candidates:
        raise RuntimeError("Gemini не повернув варіант відповіді")

    finish_reason = response.candidates[0].finish_reason

    if finish_reason == types.FinishReason.MAX_TOKENS:
        raise RuntimeError(
            "Gemini досяг ліміту токенів: відповідь неповна, "
            "тому звіт не показано"
        )

    if finish_reason != types.FinishReason.STOP:
        raise RuntimeError(
            "Gemini не завершив відповідь звичайним способом: "
            f"{finish_reason}"
        )

    if not response.text:
        raise RuntimeError("Gemini не повернув текст відповіді")

    if question is None:
        validate_report_dates(response.text, data)

    return response.text


def main():
    parser = argparse.ArgumentParser(
        description="Звіт Gemini за будь-який вибраний проміжок дат"
    )
    parser.add_argument(
        "dates",
        nargs="*",
        type=date.fromisoformat,
        help="Початкова й кінцева дати у форматі РРРР-ММ-ДД",
    )
    parser.add_argument(
        "--question",
        help="Не звіт, а відповідь на конкретне запитання за цими даними",
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

    data = build_report_data(start_date, end_date, comparison_start, comparison_end)
    print(generate_text(data, args.question))


if __name__ == "__main__":
    main()
