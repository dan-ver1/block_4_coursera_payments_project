"""Інтерактивний перегляд платіжних показників."""

from datetime import date, timedelta
from decimal import Decimal

import altair as alt
import pandas as pd
import psycopg
import streamlit as st

from metrics import get_available_dates, get_period
from report_writer import create_report


st.set_page_config(
    page_title="Аналіз платежів",
    page_icon="◈",
    layout="wide",
)

st.html("""
<style>
  :root { color-scheme: light; }
  .stApp { background: #eff1f7; color: #18243a; font-family: 'Segoe UI Variable', 'Segoe UI', sans-serif; }
  .block-container { max-width: 1390px; padding-top: 2.2rem; padding-bottom: 5rem; }
  [data-testid="stSidebar"] { background: #f9faff; border-right: 1px solid #e2e6f0; }
  [data-testid="stSidebar"] .block-container { padding-top: 2rem; }
  [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 { color: #1d2b43; letter-spacing: -.025em; }
  [data-testid="stSidebar"] [data-testid="stCaptionContainer"] { color: #66748b; }
  h1, h2, h3, p { font-family: 'Segoe UI Variable', 'Segoe UI', sans-serif; }
  h2 { letter-spacing: -.035em; }
  .hero { display: flex; justify-content: space-between; align-items: flex-end; gap: 1rem;
          border-bottom: 1px solid #dce2ed; padding: .4rem 0 1.5rem; margin-bottom: 1.6rem; }
  .hero h1 { font-size: clamp(2.2rem, 4vw, 3.7rem); font-weight: 720; line-height: 1.08;
             letter-spacing: -.06em; margin: 0 0 .55rem; color: #172238; }
  .hero p { margin: 0; color: #5d6d82; font-size: 1rem; }
  .hero-period { border-left: 3px solid #4c8cf0; padding-left: 1rem; text-align: right;
                 flex: none; font-variant-numeric: tabular-nums; }
  .hero-period strong { display: block; color: #1e2e48; font-size: 1.05rem; }
  .hero-period span { color: #69778d; font-size: .83rem; }
  .section-heading { margin: 1.9rem 0 .75rem; }
  .section-heading h2 { font-size: 1.45rem; color: #1b2940; margin: 0 0 .25rem; font-weight: 690; }
  .section-heading p { margin: 0; color: #64738a; font-size: .9rem; }
  .kpi-strip { background: #fff; border: 1px solid #e7eaf2; border-radius: 20px;
               box-shadow: 0 8px 26px rgba(30, 48, 84, .045);
               padding: 1.25rem 1.4rem 1.4rem; min-height: 157px; }
  .kpi-label { color: #607089; font-size: .88rem; margin-bottom: .85rem; }
  .kpi-value { color: #182943; font-size: clamp(1.5rem, 2.4vw, 2.3rem); line-height: 1.12;
               letter-spacing: -.045em; font-weight: 710; font-variant-numeric: tabular-nums; }
  .kpi-note { color: #8390a2; font-size: .8rem; margin-top: .8rem; }
  .comparison { background: #fff; border: 1px solid #e5eaf5; border-radius: 18px;
                box-shadow: 0 8px 24px rgba(30, 48, 84, .035);
                padding: 1rem 1.15rem; min-height: 145px; }
  .comparison-label { color: #5b6b82; font-size: .86rem; }
  .comparison-values { margin-top: .55rem; display: flex; align-items: baseline; gap: .5rem;
                       font-variant-numeric: tabular-nums; flex-wrap: wrap; }
  .comparison-values span { color: #687791; font-size: 1.04rem; }
  .comparison-values b { color: #1b2e4a; font-size: 1.32rem; letter-spacing: -.04em; }
  .comparison-values em { color: #9ba8b9; font-style: normal; }
  .delta { margin-top: .55rem; font-size: .88rem; font-weight: 660; }
  .delta.negative { color: #bd5861; }
  .delta.positive { color: #287b73; }
  .delta.neutral { color: #637289; }
  .report-empty { border: 1px dashed #cbd4e4; border-radius: 18px; color: #607087;
                  background: #fff; padding: 1.2rem 1.4rem; margin-top: .8rem; }
  div[data-testid="stVerticalBlockBorderWrapper"] { border-color: #e4e9f2; border-radius: 18px; }
  div.stButton > button, [data-testid="stPopover"] button, [data-testid="stDownloadButton"] button {
    border-radius: 12px; font-weight: 600; border-color: #d9e0ec;
  }
  div.stButton > button:hover, [data-testid="stPopover"] button:hover,
  [data-testid="stDownloadButton"] button:hover { border-color: #4c8cf0; color: #2b6ed1; }
  div.stButton > button[kind="primary"] { background: #347fe8; border: 1px solid #347fe8;
                                           color: white; border-radius: 12px; font-weight: 650; }
  div.stButton > button[kind="primary"]:hover { background: #286ed2; border-color: #286ed2; color: white; }
  [data-testid="stPopoverBody"] { background: #fff; border: 1px solid #e1e7f1;
                                   border-radius: 18px; box-shadow: 0 16px 44px rgba(20, 40, 75, .16); }
  [data-testid="stPopoverBody"] [data-baseweb="select"] > div { background: #f5f7fb; border-color: #e5e9f2; border-radius: 11px; }
  [data-testid="stSidebar"] [data-testid="stPopover"] button { background: #fff; color: #20314b; }
  [data-baseweb="popover"] [role="listbox"] { border-radius: 12px; background: #fff; }
  [data-testid="stRadio"] [role="radiogroup"] label {
    background: #fff; border: 1px solid #e0e6f1; border-radius: 999px;
    padding: .4rem .8rem; margin: .15rem 0;
  }
  [data-testid="stRadio"] [role="radiogroup"] label:has(input:checked) {
    background: #eaf2ff; border-color: #9cc2fa;
  }
  [data-testid="stVegaLiteChart"] { background: #fff; border: 1px solid #e7eaf2;
                                    border-radius: 20px; padding: 1rem .6rem .25rem; overflow: hidden; }
  [data-testid="stExpander"] { background: #fff; border: 1px solid #e4e9f2;
                               border-radius: 18px; box-shadow: 0 6px 22px rgba(30, 48, 84, .035); }
  [data-testid="stExpander"] summary { color: #233451; font-weight: 650; }
  [data-testid="stDataFrame"] { border: 1px solid #e5eaf3; border-radius: 12px; overflow: hidden; }
  button:focus-visible, input:focus-visible { outline: 3px solid #a6c7fb !important; outline-offset: 2px; }
  @media (max-width: 900px) {
    .block-container { padding-top: 1rem; }
    .hero { display: block; }
    .hero-period { border-left: 0; border-top: 2px solid #4c8cf0; text-align: left;
                   padding: .65rem 0 0; margin-top: 1.1rem; }
    [data-testid="stMain"] [data-testid="stHorizontalBlock"] { flex-direction: column; }
    [data-testid="stMain"] [data-testid="stColumn"] { width: 100% !important; min-width: 100% !important; flex: 1 1 100% !important; }
    input { font-size: 16px !important; }
    .stButton button { min-height: 44px; }
  }
  @media (prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior: auto !important; transition: none !important; } }
</style>
""")


def format_usd(value):
    if value is None:
        return "—"
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " USD"


def format_number(value, decimals=0):
    if value is None:
        return "—"
    return f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",")


def format_date(value):
    return value.strftime("%d.%m.%Y")


def section(title, subtitle=None):
    description = f"<p>{subtitle}</p>" if subtitle else ""
    st.html(f'<div class="section-heading"><h2>{title}</h2>{description}</div>')


def kpi(label, value, note):
    st.html(
        f'<div class="kpi-strip"><div class="kpi-label">{label}</div>'
        f'<div class="kpi-value">{value}</div>'
        f'<div class="kpi-note">{note}</div></div>'
    )


def comparison(label, before, after, *, decimals=0, unit="", neutral=False,
               baseline_label="до періоду порівняння"):
    if before is None or after is None:
        delta_text, tone = "Зміна не визначена", "neutral"
    else:
        difference = after - before
        if before:
            percent = Decimal(difference) / Decimal(before) * 100
            sign = "+" if percent > 0 else ""
            delta_text = f"{sign}{format_number(percent, 2)}% {baseline_label}"
        else:
            delta_text = "Відсоток не визначено: попереднє значення — 0"
        tone = "neutral" if neutral or difference == 0 else (
            "positive" if difference > 0 else "negative"
        )
    st.html(
        f'<div class="comparison"><div class="comparison-label">{label}</div>'
        f'<div class="comparison-values"><span>{format_number(before, decimals)}</span>'
        f'<em>→</em><b>{format_number(after, decimals)}</b><span>{unit}</span></div>'
        f'<div class="delta {tone}">{delta_text}</div></div>'
    )


@st.cache_data(ttl=300, show_spinner=False)
def load_period(start_date, end_date, include_daily=False):
    return get_period(start_date, end_date, include_daily=include_daily)


@st.cache_data(ttl=300, show_spinner=False)
def load_available_dates():
    return get_available_dates()


MONTHS_UK = (
    "", "січень", "лютий", "березень", "квітень", "травень", "червень",
    "липень", "серпень", "вересень", "жовтень", "листопад", "грудень",
)


def choose_date(key, available_dates):
    """Вибрати дату в меню поруч із полем, лише з наявних у джерелі."""
    current = st.session_state[key]
    year_key = f"{key}_year"
    month_key = f"{key}_month"
    day_key = f"{key}_day"

    st.session_state.setdefault(year_key, current.year)
    st.session_state.setdefault(month_key, current.month)
    st.session_state.setdefault(day_key, current.day)

    years = sorted({day.year for day in available_dates})
    year = st.selectbox("Рік", years, key=year_key)

    months = sorted({day.month for day in available_dates if day.year == year})
    if st.session_state[month_key] not in months:
        st.session_state[month_key] = months[0]
    month = st.selectbox(
        "Місяць", months, key=month_key,
        format_func=lambda value: MONTHS_UK[value],
    )

    days = sorted({day.day for day in available_dates
                   if day.year == year and day.month == month})
    if st.session_state[day_key] not in days:
        st.session_state[day_key] = days[0]
    day = st.selectbox("День", days, key=day_key)

    if st.button("Застосувати дату", key=f"{key}_apply", width="stretch"):
        st.session_state[key] = date(year, month, day)
        # Новий ключ створює закрите меню після перезапуску сторінки.
        st.session_state[f"{key}_popover_version"] += 1
        st.rerun()


def date_selector(label, key, available_dates):
    """Відкрити вибір року, місяця й дня біля відповідного поля."""
    st.caption(label)
    st.session_state.setdefault(f"{key}_popover_version", 0)
    version = st.session_state[f"{key}_popover_version"]
    with st.popover(
        format_date(st.session_state[key]),
        key=f"{key}_popover_{version}",
        width="stretch",
    ):
        choose_date(key, available_dates)


try:
    available_dates = load_available_dates()
except ValueError as error:
    st.warning(str(error))
    st.stop()
except (psycopg.Error, RuntimeError):
    st.error("Не вдалося отримати доступні дати. Перевірте підключення до PostgreSQL.")
    st.stop()

first_available_date, last_available_date = available_dates[0], available_dates[-1]

st.session_state.setdefault("start_date", date(2023, 2, 20))
st.session_state.setdefault("end_date", date(2023, 2, 26))
st.session_state.setdefault("comparison_start", date(2023, 2, 13))
st.session_state.setdefault("comparison_end", date(2023, 2, 19))
with st.sidebar:
    st.header("Вибраний період")
    st.caption(
        "Період, який аналізуємо: його показники та денний графік показані "
        "на сторінці."
    )
    date_selector("Від", "start_date", available_dates)
    date_selector("До", "end_date", available_dates)
    st.header("Період порівняння")
    st.caption("Базовий період, з яким порівнюємо вибраний період.")
    date_selector("Від", "comparison_start", available_dates)
    date_selector("До", "comparison_end", available_dates)
    st.caption(
        f"Доступні дати: {format_date(first_available_date)}–"
        f"{format_date(last_available_date)}."
    )
start_date = st.session_state["start_date"]
end_date = st.session_state["end_date"]
comparison_start = st.session_state["comparison_start"]
comparison_end = st.session_state["comparison_end"]

if start_date > end_date or comparison_start > comparison_end:
    st.warning("У кожному періоді початкова дата має бути не пізнішою за кінцеву.")
    st.stop()

days_count = (end_date - start_date).days + 1
comparison_days = (comparison_end - comparison_start).days + 1
st.html(
    '<div class="hero"><div><h1>Аналіз платежів</h1>'
    '<p>Показники, зміни та висновки для вибраного періоду.</p></div>'
    f'<div class="hero-period"><strong>{format_date(start_date)} — '
    f'{format_date(end_date)}</strong><span>{days_count} днів · обидві дати включено'
    '</span></div></div>'
)

try:
    with st.spinner("Завантажуємо показники..."):
        period = load_period(start_date, end_date, include_daily=True)
        comparison_period = load_period(comparison_start, comparison_end)
except ValueError as error:
    st.warning(str(error))
    st.stop()
except (psycopg.Error, RuntimeError):
    st.error("Не вдалося отримати дані. Перевірте підключення до PostgreSQL.")
    st.stop()

section("Показники періоду")
col_sum, col_payers, col_per_payer = st.columns(3, gap="medium")

with col_sum:
    kpi("Сума платежів", format_usd(period["payment_sum_usd"]), "Без прапорця повернення")

with col_payers:
    kpi("Унікальні платники", format_number(period["paying_users"]), "Кількість різних платників за період")

with col_per_payer:
    kpi("Сума на платника", format_usd(period["amount_per_payer_usd"]), "Сума платежів ÷ платники")

st.caption(
    f"{format_number(period['payment_count'])} платежів · "
    f"{period['days_with_payments']}/{days_count} днів із платежами. "
    "Сума платежів без прапорця повернення не є підтвердженим чистим доходом."
)

section(
    "Порівняння періодів",
    f"Вибрано {days_count} днів; період порівняння — {comparison_days} днів.",
)
st.caption(
    f"{format_date(comparison_start)}–{format_date(comparison_end)} "
    f"→ {format_date(start_date)}–{format_date(end_date)}"
)
if comparison_days != days_count:
    st.warning(
        "Періоди мають різну тривалість. Порівняння загальних сум і кількості "
        "платників не показує зміну інтенсивності; дивіться суму на день нижче."
    )
if comparison_start <= end_date and start_date <= comparison_end:
    st.info("Періоди перетинаються: частина платежів врахована в обох підсумках.")
left, middle, right = st.columns(3, gap="medium")
with left:
    comparison("Сума платежів", comparison_period["payment_sum_usd"],
               period["payment_sum_usd"], decimals=2, unit="USD")
with middle:
    comparison("Унікальні платники", comparison_period["paying_users"],
               period["paying_users"])
with right:
    comparison("Сума на платника", comparison_period["amount_per_payer_usd"],
               period["amount_per_payer_usd"], decimals=2, unit="USD",
               neutral=True)
if comparison_days != days_count:
    comparison(
        "Сума платежів на календарний день",
        comparison_period["payment_sum_usd"] / comparison_days,
        period["payment_sum_usd"] / days_count,
        decimals=2, unit="USD",
    )
st.caption("Зміни показують різницю між періодами, але не доводять її причину.")

daily_by_date = {row["date"]: row for row in period["daily"]}
daily_rows = []

for offset in range(days_count):
    day = start_date + timedelta(days=offset)
    source_row = daily_by_date.get(day)

    if source_row is None:
        amount = 0.0
        payers = 0
        payments = 0
    else:
        amount = float(source_row["payment_sum_usd"])
        payers = source_row["paying_users"]
        payments = source_row["payment_count"]

    daily_rows.append(
        {
            "Дата": day,
            "Сума платежів, USD": amount,
            "Платники за день": payers,
            "Сума на платника за день, USD": amount / payers if payers else None,
            "Платежі": payments,
        }
    )

daily_df = pd.DataFrame(daily_rows)
daily_df["Дата"] = pd.to_datetime(daily_df["Дата"])

section(
    "Динаміка за днями",
    "Перемикайте показник і згладжування, щоб дослідити коливання всередині періоду.",
)
metric_name = st.radio(
    "Показник на графіку",
    (
        "Сума платежів, USD",
        "Платники за день",
        "Сума на платника за день, USD",
    ),
    horizontal=True,
)
if days_count > 1:
    smoothing = st.slider(
        "Згладжування, днів", 1, min(14, days_count), 1,
        help="1 день — лише фактичні значення. Більше днів — додаткова лінія "
             "середнього за останні N днів, щоб легше бачити загальний напрямок.",
    )
    st.caption(
        "1 день — без згладжування; більше днів — середнє за останні N днів. "
        "Це допомагає побачити напрямок руху, але не є прогнозом."
    )
else:
    smoothing = 1

chart_data = daily_df.copy()
chart_data["Згладжений показник"] = (
    chart_data[metric_name].rolling(window=smoothing, min_periods=smoothing).mean()
)
tick_step = max(1, (days_count + 7) // 8)
tick_dates = [
    start_date + timedelta(days=offset)
    for offset in range(0, days_count, tick_step)
]
if tick_dates[-1] != end_date:
    tick_dates.append(end_date)
date_axis = alt.X(
    "Дата:T",
    title=None,
    scale=alt.Scale(domain=[
        pd.Timestamp(start_date) - pd.Timedelta(hours=12),
        pd.Timestamp(end_date) + pd.Timedelta(hours=12),
    ]),
    axis=alt.Axis(format="%d.%m", values=tick_dates, labelAngle=0, grid=False),
)
value_axis = alt.Y(
    f"{metric_name}:Q",
    title=None,
    scale=alt.Scale(zero=True),
    axis=alt.Axis(grid=True, gridColor="#e6eaf2"),
)
tooltips = [
    alt.Tooltip("Дата:T", title="Дата", format="%d.%m.%Y"),
    alt.Tooltip(f"{metric_name}:Q", title=metric_name, format=",.2f"),
]
base = alt.Chart(chart_data).encode(x=date_axis)
if metric_name == "Сума на платника за день, USD":
    primary = base.mark_line(
        color="#347fe8", strokeWidth=2.5, point=True
    ).encode(y=value_axis, tooltip=tooltips)
else:
    primary = base.mark_bar(
        color="#4c8cf0", opacity=0.94,
        cornerRadiusTopLeft=12, cornerRadiusTopRight=12,
        size=alt.ExprRef(expr=f"min(64, max(2, width / {days_count} * 0.7))"),
    ).encode(y=value_axis, tooltip=tooltips)

if smoothing > 1:
    overlay = base.mark_line(color="#df805f", strokeWidth=2.6).encode(
        y=alt.Y("Згладжений показник:Q", title=None),
        tooltip=[
            alt.Tooltip("Дата:T", title="Дата", format="%d.%m.%Y"),
            alt.Tooltip(
                "Згладжений показник:Q",
                title=f"Ковзне середнє ({smoothing} дн.)",
                format=",.2f",
            ),
        ],
    )
    chart = alt.layer(primary, overlay)
else:
    chart = primary
st.altair_chart(
    chart.properties(height=350).configure(
        background="#ffffff",
        view={"stroke": None},
        axis={"labelColor": "#607089", "titleColor": "#607089"},
    ),
    width="stretch",
    theme=None,
)
if metric_name == "Сума на платника за день, USD":
    st.caption(
        "Синя лінія — сума платежів на одного платника за кожен день, USD."
    )
elif metric_name == "Платники за день":
    st.caption("Сині стовпці — кількість унікальних платників за кожен день.")
else:
    st.caption("Сині стовпці — сума платежів за кожен день, USD.")
if smoothing > 1:
    st.caption(
        f"Помаранчева лінія — {smoothing}-денне ковзне середнє вибраного "
        "показника; на початку періоду для нього ще недостатньо днів. "
        "Це не окрема трендова лінія."
    )

with st.expander("Точні значення за кожен день"):
    st.dataframe(
        daily_df,
        hide_index=True,
        width="stretch",
        height=320,
        column_config={
            "Дата": st.column_config.DatetimeColumn("Дата", format="DD.MM.YYYY"),
            "Сума платежів, USD": st.column_config.NumberColumn(
                "Сума платежів, USD", format="%.2f"
            ),
            "Сума на платника за день, USD": st.column_config.NumberColumn(
                "Сума на платника за день, USD", format="%.2f"
            ),
        },
    )
    st.caption(
        "Денних платників не можна складати, щоб отримати кількість унікальних "
        "платників за весь період: одна людина може траплятися в кількох днях."
    )

section(
    "Аналітичний звіт за вибраний період",
    "AI-помічник сформує висновки й рекомендації. Для цього натисніть «Сформувати AI-звіт».",
)
st.caption(
    "Звіт аналізує два вибрані періоди за розрахованими показниками."
)

if st.button("Сформувати AI-звіт", type="primary"):
    st.session_state.pop("payment_report", None)
    try:
        with st.spinner("Формуємо та зберігаємо звіт..."):
            markdown, report_path = create_report(
                start_date, end_date, comparison_start, comparison_end
            )
    except ValueError as error:
        st.warning(str(error))
    except Exception:
        st.error(
            "Не вдалося сформувати звіт. Перевірте підключення до бази, "
            "налаштування Gemini та журнал запуску Streamlit."
        )
    else:
        st.session_state["payment_report"] = {
            "start": start_date,
            "end": end_date,
            "comparison_start": comparison_start,
            "comparison_end": comparison_end,
            "markdown": markdown,
            "filename": report_path.name,
        }

saved_report = st.session_state.get("payment_report")
if saved_report and (saved_report["start"], saved_report["end"],
                     saved_report["comparison_start"], saved_report["comparison_end"]) == (
    start_date,
    end_date,
    comparison_start,
    comparison_end,
):
    with st.container(border=True):
        st.markdown(saved_report["markdown"])
    st.download_button(
        "Завантажити Markdown",
        data=saved_report["markdown"].encode("utf-8"),
        file_name=saved_report["filename"],
        mime="text/markdown",
    )
else:
    st.html(
        '<div class="report-empty">Звіт для цього періоду ще не сформовано. '
        'Натисніть кнопку вище, щоб отримати висновки та рекомендації.</div>'
    )
