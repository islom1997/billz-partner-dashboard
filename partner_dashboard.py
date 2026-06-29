import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import datetime
import math
from google.cloud import bigquery
import numpy as np
import plotly.express as px

# Метка для перехода на вкладку «Мои клиенты» с фильтром по неактивным клиентам
INACTIVE_LABEL = "Неактивные (>4 дн)"

# --- CONFIG ---
st.set_page_config(
    page_title="BILLZ Партнёрский Дашборд",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

USD_RATE = 12800

# --- PARTNER GRADE SYSTEM ---
# Грейд определяется по минимальным требованиям. Партнёр получает самый высокий
# уровень, для которого ОДНОВРЕМЕННО выполнены все условия: Active clients,
# Partner MRR и (начиная с Level 4) Retention за последние 6 месяцев.
GRADES = [
    {
        "level": 1, "title": "Starter", "name": "Starter Partner",
        "min_clients": 1, "min_mrr": 299_000, "min_retention": None,
        "benefits": [
            "Доступ к партнёрскому дашборду с аналитикой по клиентам и MRR",
            "5 SQL-лидов",
            "Онбординг-курс: видеоуроки по продукту и финальный тест",
            "Чат поддержки партнёров",
            "Базовый набор sales-материалов: презентация, FAQ, скрипт продаж",
        ],
    },
    {
        "level": 2, "title": "Certified", "name": "Certified Partner",
        "min_clients": 15, "min_mrr": 7_000_000, "min_retention": None,
        "benefits": [
            "Размещение на сайте BILLZ: карточка с логотипом, описанием и контактами",
            "5 SQL + 2 HQL лида в месяц",
            "Бейдж Certified Partner для сайта, визиток и соцсетей",
            "Индивидуальные вебинары от Sales-команды 2 раза в месяц",
        ],
    },
    {
        "level": 3, "title": "Growth", "name": "Growth Partner",
        "min_clients": 40, "min_mrr": 20_000_000, "min_retention": None,
        "benefits": [
            "Повышенный приоритет в перераспределении лидов",
            "Продажа доп. продуктов: TBC Счёт, Payme Go, Payme QR",
            "4 персональных тренинг-сессии в квартал с Sales Manager BILLZ",
            "Разбор воронки продаж с TeamLead of Sales",
        ],
    },
    {
        "level": 4, "title": "Strategic", "name": "Strategic Partner",
        "min_clients": 80, "min_mrr": 40_000_000, "min_retention": 85,
        "benefits": [
            "Приоритет в распределении лидов: первый в регионе",
            "Размещение в Instagram BILLZ",
            "Квартальные бонусы за выполнение плана",
            "Расширенная поддержка со стороны Sales-команды BILLZ",
        ],
    },
    {
        "level": 5, "title": "Elite", "name": "Elite Partner",
        "min_clients": 200, "min_mrr": 100_000_000, "min_retention": 90,
        "benefits": [
            "Максимальный приоритет в распределении лидов",
            "Приоритет первого партнёра в регионе",
            "Расширенный co-branded marketing с BILLZ",
            "Размещение на сайте и в Instagram BILLZ как топ-партнёр",
            "Номинация на статус «Партнёр года»",
        ],
    },
]


def _grade_met(grade, active_clients, partner_mrr, retention_pct):
    """True, если по грейду выполнены ВСЕ требования одновременно."""
    if active_clients < grade["min_clients"]:
        return False
    if partner_mrr < grade["min_mrr"]:
        return False
    if grade["min_retention"] is not None:
        if retention_pct is None or retention_pct < grade["min_retention"]:
            return False
    return True


def compute_grade(active_clients, partner_mrr, retention_pct):
    """Возвращает (current_grade, next_grade).

    current_grade — самый высокий уровень, где выполнены все условия
    (None, если не достигнут даже Level 1). next_grade — следующий уровень
    (None, если достигнут максимальный).
    """
    current = None
    for g in GRADES:
        if _grade_met(g, active_clients, partner_mrr, retention_pct):
            current = g
    current_level = current["level"] if current else 0
    next_grade = next((g for g in GRADES if g["level"] == current_level + 1), None)
    return current, next_grade


def fmt_uzs(n):
    """1234567 -> '1 234 567'. Безопасно для None/NaN."""
    try:
        if n is None or (isinstance(n, float) and math.isnan(n)):
            return "—"
        return f"{int(round(n)):,}".replace(",", " ")
    except (ValueError, TypeError):
        return "—"


def retention_status(retention_pct, required):
    """(текст, цвет) статуса retention относительно требуемого порога."""
    if required is None:
        return "не требуется для следующего уровня", "#94a3b8"
    if retention_pct is None:
        return "нет данных", "#94a3b8"
    if retention_pct >= required:
        return "выполнено", "#16a34a"
    gap = required - retention_pct
    if gap <= 5:
        return f"не хватает {gap:.0f} п.п.", "#f59e0b"
    return "ниже порога", "#dc2626"


def render_retention_card(retention_pct, required):
    """Карточка статуса Retention (Правка 1 ТЗ).

    Логика порога НЕ меняется — статус берётся из retention_status(). Меняется
    только визуал: при retention ≥ порога — зелёная галочка ✅ (как сейчас);
    при retention < порога — тревожная иконка ⚠️ с красно-оранжевым акцентом и
    фоном (как бейдж «в зоне риска»). Когда порог не требуется / нет данных —
    нейтральный вид.
    """
    status_text, status_color = retention_status(retention_pct, required)
    has_threshold = required is not None and retention_pct is not None
    met = has_threshold and retention_pct >= required
    below = has_threshold and retention_pct < required

    ret_display = f"{retention_pct:.0f}%" if retention_pct is not None else "нет данных"
    icon = "✅" if met else ("⚠️" if below else "")
    icon_html = f"{icon} " if icon else ""
    # Тревожный фон и рамка только когда retention ниже порога; иначе карточка
    # выглядит как соседние метрики (без рамки).
    if below:
        box_style = (
            f"background:{status_color}1f; border:1px solid {status_color}; "
            "border-radius:10px; padding:10px 14px;"
        )
    else:
        box_style = "padding:2px 0;"
    value_color = status_color if (met or below) else "inherit"

    st.markdown(
        f"""
        <div style="{box_style}">
          <div style="font-size:13px; color:#9aa0aa;">Retention (6 мес)</div>
          <div style="font-size:1.9rem; font-weight:600; line-height:1.25; color:{value_color};">{icon_html}{ret_display}</div>
          <div style="font-size:13px; color:{status_color}; font-weight:600;">{status_text}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# --- AUTHENTICATION ---
def check_password():
    """Returns `True` if the user had the correct password."""
    def password_entered():
        # Validate PIN against a simple mapping
        partner_pins = {
            "1234": "Doston Botirov",
            "0000": "Admin",
            "1111": "Parviz Abduhafizov",
            "2222": "Naim Shokirov",
            "3333": "Asilbek Akbaraliev",
            "4444": "Bobur Abdukakhkharov",
            "5555": "Xikmatillo Baxtiyorov",
            "6666": "Saliq Bysenov",
            "8888": "Sardor Ibraximov",
            "1112": "Sarvar Alimov"
        }
        pin = st.session_state["password"]
        if pin in partner_pins:
            st.session_state["password_correct"] = True
            st.session_state["partner_name"] = partner_pins[pin]
            del st.session_state["password"]  # don't store password
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.image("logo.png", width=200)
            st.markdown("#### Партнёрский дашборд")
            st.markdown("Пожалуйста, введите ваш PIN-код")
            st.text_input("PIN", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.image("logo.png", width=200)
            st.markdown("#### Партнёрский дашборд")
            st.text_input("PIN", type="password", on_change=password_entered, key="password")
            st.error("😕 Неверный PIN-код")
        return False
    else:
        return True

# --- DATA FETCHING ---
@st.cache_resource
def get_bq_client():
    try:
        # Streamlit Cloud: use service account from secrets
        from google.oauth2 import service_account
        credentials = service_account.Credentials.from_service_account_info(
            st.secrets["gcp_service_account"]
        )
        return bigquery.Client(credentials=credentials, project="br-clients-02")
    except (KeyError, FileNotFoundError):
        # Local dev: use gcloud ADC
        return bigquery.Client(project="br-clients-02")

@st.cache_data(ttl=600)
def load_clients_activity():
    client = get_bq_client()
    sql = """
    SELECT
      LOWER(prefix) AS login_key,
      last_sale_date,
      last_import_date,
      last_login_date
    FROM `billz-analytics.cs_data.clients_activity`
    WHERE prefix IS NOT NULL AND prefix != ''
    """
    try:
        df = client.query(sql).to_dataframe()
        return df.drop_duplicates(subset='login_key', keep='first')
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300)
def get_partner_data(partner_name):
    if partner_name == "Admin":
        partner_name = "Doston Botirov"
        
    client = get_bq_client()
    # Use first name for matching cf_support_manager (format varies: "Parviz Khafizov Partner", etc.)
    if "saliq" in partner_name.lower():
        first_name = "Texnopos"
    else:
        first_name = partner_name.split()[0]
    
    query_conn_all = f"""
    WITH latest_custs AS (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY loaded_at DESC) as rn
        FROM `br-clients-02.ms_ekeppe.chargebee_customers`
    ),
    deduped_custs AS (
        SELECT * 
        FROM latest_custs
        WHERE rn = 1
    ),
    latest_subs AS (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY subscription_id ORDER BY loaded_at DESC) as rn
        FROM `br-clients-02.ms_ekeppe.chargebee_subscriptions`
    ),
    deduped_subs AS (
        SELECT * 
        FROM latest_subs
        WHERE rn = 1
          AND status = 'active'
    )
    SELECT
        COUNT(DISTINCT sub.customer_id) as connections_total,
        COALESCE(SUM(sub.mrr), 0) / 100 as partner_mrr
    FROM deduped_custs c
    JOIN deduped_subs sub
        ON c.customer_id = sub.customer_id
    WHERE LOWER(c.cf_support_manager) LIKE LOWER('%{first_name}%')
    """
    conn_all_df = client.query(query_conn_all).to_dataframe()
    
    # 2. New Chargebee customers THIS MONTH
    query_new = f"""
    WITH latest_custs AS (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY loaded_at DESC) as rn
        FROM `br-clients-02.ms_ekeppe.chargebee_customers`
    ),
    deduped_custs AS (
        SELECT * 
        FROM latest_custs
        WHERE rn = 1
    ),
    latest_subs AS (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY subscription_id ORDER BY loaded_at DESC) as rn
        FROM `br-clients-02.ms_ekeppe.chargebee_subscriptions`
    ),
    deduped_subs AS (
        SELECT * 
        FROM latest_subs
        WHERE rn = 1
          AND status = 'active'
    )
    SELECT DISTINCT
        c.company as client_name,
        c.cf_loginprefix as login,
        sub.plan_id,
        sub.mrr / 100 as mrr,
        DATE(c.created_at) as created_date
    FROM deduped_custs c
    JOIN deduped_subs sub
        ON c.customer_id = sub.customer_id
    WHERE LOWER(c.cf_support_manager) LIKE LOWER('%{first_name}%')
      AND c.created_at >= TIMESTAMP_TRUNC(CURRENT_TIMESTAMP(), MONTH)
    ORDER BY created_date DESC
    """
    success_df = client.query(query_new).to_dataframe()
    
    # 3. Deals from amoCRM (for lead stats)
    query_deals = f"""
    SELECT 
        COUNT(*) as total_leads,
        SUM(CASE WHEN uspehn_sdelki = 1 THEN 1 ELSE 0 END) as successful_deals
    FROM `br-clients-02.ms_ekeppe.sdelki_table`
    WHERE menedzher = '{partner_name}' 
    """
    deals_df = client.query(query_deals).to_dataframe()

    # 4. Retention за последние 6 месяцев (логотип-ретеншн по активным клиентам):
    #    из клиентов, активных 6 месяцев назад, какая доля активна сейчас.
    query_retention = f"""
    WITH latest_custs AS (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY loaded_at DESC) as rn
        FROM `br-clients-02.ms_ekeppe.chargebee_customers`
    ),
    deduped_custs AS (
        SELECT * FROM latest_custs WHERE rn = 1
    ),
    latest_subs AS (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY subscription_id ORDER BY loaded_at DESC) as rn
        FROM `br-clients-02.ms_ekeppe.chargebee_subscriptions`
    ),
    deduped_subs AS (
        SELECT * FROM latest_subs WHERE rn = 1
    ),
    per_customer AS (
        SELECT
            c.customer_id,
            LOGICAL_OR(
                DATE(sub.created_at) <= DATE_SUB(CURRENT_DATE(), INTERVAL 6 MONTH)
                AND (sub.cancelled_at IS NULL OR DATE(sub.cancelled_at) > DATE_SUB(CURRENT_DATE(), INTERVAL 6 MONTH))
            ) AS active_6mo_ago,
            LOGICAL_OR(sub.status = 'active') AS active_now
        FROM deduped_custs c
        JOIN deduped_subs sub ON c.customer_id = sub.customer_id
        WHERE LOWER(c.cf_support_manager) LIKE LOWER('%{first_name}%')
        GROUP BY c.customer_id
    )
    SELECT
        COUNTIF(active_6mo_ago) AS base_count,
        COUNTIF(active_6mo_ago AND active_now) AS retained_count
    FROM per_customer
    """
    try:
        ret_df = client.query(query_retention).to_dataframe()
        base_count = int(ret_df['base_count'].iloc[0]) if not ret_df.empty else 0
        retained_count = int(ret_df['retained_count'].iloc[0]) if not ret_df.empty else 0
    except Exception:
        base_count, retained_count = 0, 0
    retention_pct = round(retained_count / base_count * 100, 1) if base_count > 0 else None

    # Values
    connections = int(conn_all_df['connections_total'].iloc[0]) if not conn_all_df.empty and pd.notna(conn_all_df['connections_total'].iloc[0]) else 0
    partner_mrr = float(conn_all_df['partner_mrr'].iloc[0]) if not conn_all_df.empty and pd.notna(conn_all_df['partner_mrr'].iloc[0]) else 0.0
    connections_month = len(success_df)

    # Grade: самый высокий уровень, где ОДНОВРЕМЕННО выполнены все требования
    current_grade, next_grade = compute_grade(connections, partner_mrr, retention_pct)
    level = current_grade["level"] if current_grade else 0
    level_title = current_grade["title"] if current_grade else "Без грейда"

    return {
        "name": partner_name,
        "first_name": first_name,
        "level": level,
        "level_title": level_title,
        "connections": connections,
        "partner_mrr": partner_mrr,
        "retention_pct": retention_pct,
        "retention_base": base_count,
        "current_grade": current_grade,
        "next_grade": next_grade,
        "connections_month": connections_month,
        "success_df": success_df
    }

LEVEL_COLORS = {
    0: "#94a3b8",  # без грейда
    1: "#64748b",  # Starter
    2: "#0284c7",  # Certified
    3: "#4f46e5",  # Growth
    4: "#d97706",  # Strategic
    5: "#059669",  # Elite
}


def render_progress_bar(label, current, target, unit=""):
    """Кастомный прогресс-бар с подписью 'current / target'."""
    pct = min(100.0, current / target * 100) if target else 100.0
    done = current >= target
    bar_color = "#16a34a" if done else "#3b82f6"
    unit_txt = f" {unit}" if unit else ""
    check = " ✅" if done else ""
    st.markdown(
        f"""
        <div style="margin-bottom:14px;">
          <div style="display:flex; justify-content:space-between; font-size:13px; margin-bottom:4px;">
            <span style="font-weight:600;">{label}{check}</span>
            <span style="color:#64748b;">{fmt_uzs(current)} / {fmt_uzs(target)}{unit_txt}</span>
          </div>
          <div style="background:#e2e8f0; border-radius:8px; height:10px; overflow:hidden;">
            <div style="width:{pct:.1f}%; background:{bar_color}; height:100%; border-radius:8px;"></div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def draw_grade_section(data):
    """Грейд-роадмап партнёра: текущий уровень, прогресс, бенефиты, next best action."""
    active = data["connections"]
    mrr = data["partner_mrr"]
    ret = data["retention_pct"]
    cur = data["current_grade"]
    nxt = data["next_grade"]

    level = cur["level"] if cur else 0
    color = LEVEL_COLORS.get(level, "#64748b")

    # --- Заголовок: текущий уровень ---
    if cur:
        st.markdown(
            f"<span style='background:{color}1a; color:{color}; padding:4px 12px; "
            f"border-radius:14px; font-size:13px; font-weight:700;'>"
            f"⭐ Level {cur['level']} · {cur['name']}</span>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<span style='background:#f1f5f9; color:#64748b; padding:4px 12px; "
            "border-radius:14px; font-size:13px; font-weight:700;'>Пока без грейда</span>",
            unsafe_allow_html=True,
        )
        st.caption("Для Level 1 — Starter нужно минимум 1 активный клиент и 299 000 сум Partner MRR.")

    # Разрывы до следующего уровня
    clients_gap = max(0, nxt["min_clients"] - active) if nxt else 0
    mrr_gap = max(0, nxt["min_mrr"] - mrr) if nxt else 0

    # --- Текущие метрики (4.1) ---
    c1, c2, c3 = st.columns(3)
    with c1:
        delta = f"до Level {nxt['level']}: +{clients_gap}" if (nxt and clients_gap > 0) else ("✓ выполнено" if nxt else "макс. уровень")
        st.metric("Active clients", active, delta, delta_color="off")
    with c2:
        delta = f"+{fmt_uzs(mrr_gap)} сум" if (nxt and mrr_gap > 0) else ("✓ выполнено" if nxt else "макс. уровень")
        st.metric("Partner MRR", f"{fmt_uzs(mrr)} сум", delta, delta_color="off")
    with c3:
        req_for_status = nxt["min_retention"] if nxt else (cur["min_retention"] if cur else None)
        render_retention_card(ret, req_for_status)

    st.write("")

    # --- Жёлтый баннер: retention ниже порога текущего уровня (Правка 2) ---
    # Текст баннера НЕ меняется. Добавлена только кнопка перехода к неактивным
    # клиентам на вкладке «Мои клиенты» с предустановленным фильтром.
    if cur and cur["min_retention"] is not None and ret is not None and ret < cur["min_retention"]:
        st.warning(
            f"⚠️ Retention {ret:.0f}% ниже порога вашего уровня ({cur['min_retention']}%). "
            "Действует grace period 2 месяца на восстановление — уровень и бенефиты пока сохраняются."
        )
        if st.button("🔎 Посмотреть неактивных клиентов →", key="goto_inactive_from_grade"):
            st.session_state["activity_select"] = INACTIVE_LABEL
            st.session_state["_switch_to_clients"] = True
        st.write("")

    # --- Roadmap уровней (Правка 3: свёрнут под аккордеон по умолчанию) ---
    with st.expander("🗺 Roadmap уровней", expanded=False):
        if nxt:
            # --- Прогресс до следующего уровня (4.2) ---
            st.markdown(f"##### Прогресс до Level {nxt['level']} — {nxt['name']}")
            render_progress_bar("Active clients", active, nxt["min_clients"])
            render_progress_bar("Partner MRR", mrr, nxt["min_mrr"], "сум")
            status_text, status_color = retention_status(ret, nxt["min_retention"])
            ret_display = f"{ret:.0f}%" if ret is not None else "нет данных"
            st.markdown(
                f"<div style='font-size:13px; margin-top:2px;'><b>Retention:</b> {ret_display} — "
                f"<span style='color:{status_color}; font-weight:600;'>{status_text}</span></div>",
                unsafe_allow_html=True,
            )

            # --- Сколько осталось (4.1, текст под таблицей) ---
            remaining_parts = []
            if clients_gap > 0:
                remaining_parts.append(f"подключить {clients_gap} активных клиентов")
            if mrr_gap > 0:
                remaining_parts.append(f"увеличить Partner MRR на {fmt_uzs(mrr_gap)} сум")
            st.write("")
            if remaining_parts:
                st.markdown(f"До **Level {nxt['level']} — {nxt['name']}** осталось: " + " и ".join(remaining_parts) + ".")
            else:
                st.success(f"Все требования для Level {nxt['level']} — {nxt['name']} выполнены. Уровень обновится после ближайшего пересчёта.")
        else:
            st.success(f"🏆 Достигнут максимальный уровень — {cur['name']}. Поздравляем!")

        st.write("---")

        # --- Разблокированные преимущества (4.3) ---
        col_now, col_next = st.columns(2)
        with col_now:
            st.markdown("##### ✅ Доступно сейчас" + (f" — Level {cur['level']}" if cur else ""))
            if cur:
                for b in cur["benefits"]:
                    st.markdown(f"- {b}")
            else:
                st.caption("Бенефиты станут доступны после достижения Level 1 — Starter.")
        with col_next:
            if nxt:
                st.markdown(f"##### 🔓 Откроется на Level {nxt['level']} — {nxt['name']}")
                for b in nxt["benefits"]:
                    st.markdown(f"- {b}")
            else:
                st.markdown("##### 🔓 Следующий уровень")
                st.caption("Вы на максимальном уровне — дальше только удержание Elite-статуса.")

        st.write("---")

        # --- Next Best Action (4.4) ---
        st.markdown("##### 🎯 Что сделать дальше")
        if nxt:
            # Главное действие — наибольший относительный разрыв
            primary_candidates = []
            if clients_gap > 0:
                primary_candidates.append((clients_gap / nxt["min_clients"], f"Подключить ещё {clients_gap} активных клиентов"))
            if mrr_gap > 0:
                primary_candidates.append((mrr_gap / nxt["min_mrr"], f"Увеличить Partner MRR на {fmt_uzs(mrr_gap)} сум"))
            if nxt["min_retention"] is not None and ret is not None and ret < nxt["min_retention"]:
                primary_candidates.append(((nxt["min_retention"] - ret) / nxt["min_retention"], f"Поднять retention до {nxt['min_retention']}%"))
            if primary_candidates:
                primary = max(primary_candidates, key=lambda x: x[0])[1]
                st.info(f"**Главное действие:** {primary} — это ближайший шаг к Level {nxt['level']}.")

            actions = []
            if clients_gap > 0:
                actions.append(f"Подключите ещё {clients_gap} активных клиентов, чтобы выполнить требование по клиентам для Level {nxt['level']}.")
            if mrr_gap > 0:
                actions.append(f"Увеличьте Partner MRR на {fmt_uzs(mrr_gap)} сум, чтобы выполнить MRR-требование для Level {nxt['level']}.")
            if nxt["min_retention"] is not None:
                if ret is None:
                    actions.append(f"Недостаточно данных по retention. Требование для Level {nxt['level']} — {nxt['min_retention']}%.")
                elif ret >= nxt["min_retention"]:
                    actions.append(f"Retention сейчас {ret:.0f}%. Требование для Level {nxt['level']} — {nxt['min_retention']}%, условие уже выполнено.")
                else:
                    actions.append(f"Поднимите retention с {ret:.0f}% до {nxt['min_retention']}% — удерживайте текущих клиентов в ближайшие месяцы.")
            if mrr_gap > 0 and active > 0:
                avg_mrr = mrr / active
                if avg_mrr > 0:
                    need_clients = math.ceil(mrr_gap / avg_mrr)
                    actions.append(
                        f"Средний MRR на клиента сейчас {fmt_uzs(avg_mrr)} сум. Чтобы добрать {fmt_uzs(mrr_gap)} сум MRR, "
                        f"нужно примерно {need_clients} новых клиентов с таким же средним чеком."
                    )
            for a in actions:
                st.markdown(f"- {a}")
        else:
            st.info("**Главное действие:** удерживайте retention и активную базу, чтобы сохранить статус Elite.")
            st.markdown(
                "- Критерии статуса «Партнёр года»: рост Partner MRR, retention, NPS клиентов, "
                "выполнение квартальных планов, качество обработки лидов."
            )


def draw_main_dashboard(data):
    st.markdown(f"### Добро пожаловать, {data['name']}!")

    draw_grade_section(data)

    st.write("---")
    
    # --- Monthly connections line chart ---
    st.markdown("#### 📈 Подключения по месяцам")
    first_name = data['first_name']
    client = get_bq_client()
    safe_name = first_name.replace("'", "\\'")
    query_monthly = f"""
    WITH latest_custs AS (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY loaded_at DESC) as rn
        FROM `br-clients-02.ms_ekeppe.chargebee_customers`
    ),
    deduped_custs AS (
        SELECT * 
        FROM latest_custs
        WHERE rn = 1
    ),
    latest_subs AS (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY subscription_id ORDER BY loaded_at DESC) as rn
        FROM `br-clients-02.ms_ekeppe.chargebee_subscriptions`
    ),
    deduped_subs AS (
        SELECT * 
        FROM latest_subs
        WHERE rn = 1
    ),
    months AS (
        SELECT DISTINCT FORMAT_TIMESTAMP('%Y-%m', created_at) as month
        FROM `br-clients-02.ms_ekeppe.chargebee_customers`
    ),
    created_by_month AS (
        SELECT 
            FORMAT_TIMESTAMP('%Y-%m', c.created_at) as month,
            COUNT(DISTINCT c.customer_id) as created_count
        FROM deduped_custs c
        JOIN deduped_subs sub ON c.customer_id = sub.customer_id
        WHERE LOWER(c.cf_support_manager) LIKE LOWER('%{safe_name}%')
        GROUP BY month
    ),
    cancelled_by_month AS (
        SELECT 
            FORMAT_TIMESTAMP('%Y-%m', sub.cancelled_at) as month,
            COUNT(DISTINCT c.customer_id) as churned_count
        FROM deduped_custs c
        JOIN deduped_subs sub ON c.customer_id = sub.customer_id
        WHERE LOWER(c.cf_support_manager) LIKE LOWER('%{safe_name}%')
          AND sub.status = 'cancelled'
          AND sub.cancelled_at IS NOT NULL
        GROUP BY month
    ),
    active_by_month AS (
        SELECT 
            m.month,
            COUNT(DISTINCT c.customer_id) as active_base
        FROM months m
        JOIN deduped_custs c ON LOWER(c.cf_support_manager) LIKE LOWER('%{safe_name}%')
        JOIN deduped_subs sub ON c.customer_id = sub.customer_id
        WHERE DATE(sub.created_at) <= LAST_DAY(PARSE_DATE('%Y-%m', m.month))
          AND (sub.status != 'cancelled' OR DATE(sub.cancelled_at) > LAST_DAY(PARSE_DATE('%Y-%m', m.month)))
        GROUP BY month
    )
    SELECT 
        m.month,
        COALESCE(cr.created_count, 0) as created_count,
        COALESCE(ca.churned_count, 0) as churned_count,
        COALESCE(act.active_base, 0) as active_base
    FROM months m
    LEFT JOIN created_by_month cr ON m.month = cr.month
    LEFT JOIN cancelled_by_month ca ON m.month = ca.month
    LEFT JOIN active_by_month act ON m.month = act.month
    WHERE PARSE_DATE('%Y-%m', m.month) >= DATE_SUB(DATE_TRUNC(CURRENT_DATE(), MONTH), INTERVAL 12 MONTH)
    ORDER BY month
    """
    try:
        monthly_df = client.query(query_monthly).to_dataframe()
        if not monthly_df.empty:
            month_names = {
                '01': 'Янв', '02': 'Фев', '03': 'Мар', '04': 'Апр',
                '05': 'Май', '06': 'Июн', '07': 'Июл', '08': 'Авг',
                '09': 'Сен', '10': 'Окт', '11': 'Ноя', '12': 'Дек'
            }
            monthly_df['sort_key'] = monthly_df['month']
            monthly_df['month'] = monthly_df['month'].apply(
                lambda x: f"{month_names[x[5:]]} {x[:4]}"
            )
            
            # Sort chronologically
            monthly_df = monthly_df.sort_values('sort_key').reset_index(drop=True)
            month_order = monthly_df['month'].tolist()

            # Melt for the grouped bar chart
            bars_df = monthly_df.melt(
                id_vars=['month', 'sort_key'],
                value_vars=['created_count', 'churned_count'],
                var_name='metric',
                value_name='value'
            )
            bars_df['metric'] = bars_df['metric'].map({
                'created_count': 'Новые подключения',
                'churned_count': 'Отток'
            })
            
            import altair as alt
            
            # Grouped bars
            bars = alt.Chart(bars_df).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
                x=alt.X('month:N', sort=month_order, title='Месяц'),
                xOffset='metric:N',
                y=alt.Y('value:Q', title='Количество новых / оттока'),
                color=alt.Color('metric:N', title='Метрика', scale=alt.Scale(
                    domain=['Новые подключения', 'Отток'],
                    range=['#10B981', '#EF4444']
                )),
                tooltip=[
                    alt.Tooltip('month:N', title='Месяц'),
                    alt.Tooltip('metric:N', title='Метрика'),
                    alt.Tooltip('value:Q', title='Количество')
                ]
            )

            # Text labels for bars
            bar_text = alt.Chart(bars_df).mark_text(
                align='center',
                baseline='bottom',
                dy=-3,
                fontSize=10,
                fontWeight='bold'
            ).encode(
                x=alt.X('month:N', sort=month_order),
                xOffset='metric:N',
                y=alt.Y('value:Q'),
                text=alt.Text('value:Q')
            )

            # Cumulative active base line
            line = alt.Chart(monthly_df).mark_line(color='#2563EB', strokeWidth=3, point=True).encode(
                x=alt.X('month:N', sort=month_order),
                y=alt.Y('active_base:Q', title='Активная база'),
                tooltip=[
                    alt.Tooltip('month:N', title='Месяц'),
                    alt.Tooltip('active_base:Q', title='Активная база')
                ]
            )

            # Text labels for active base line
            line_text = alt.Chart(monthly_df).mark_text(
                align='center',
                baseline='bottom',
                dy=-10,
                color='#2563EB',
                fontSize=11,
                fontWeight='bold'
            ).encode(
                x=alt.X('month:N', sort=month_order),
                y=alt.Y('active_base:Q'),
                text=alt.Text('active_base:Q')
            )

            # Layer them together
            bars_layer = alt.layer(bars, bar_text)
            line_layer = alt.layer(line, line_text)
            
            chart = alt.layer(bars_layer, line_layer).resolve_scale(
                y='independent'
            ).properties(height=350)
            
            st.altair_chart(chart, use_container_width=True)
        else:
            st.info("Нет данных для графика.")
    except Exception as e:
        st.warning(f"Ошибка загрузки графика: {e}")
    
    st.write("---")
    st.markdown(f"#### 🆕 Новые клиенты в этом месяце ({data['connections_month']})")
    sdf = data["success_df"]
    if not sdf.empty:
        display_sdf = sdf.copy()
        display_sdf.columns = ["Клиент", "Логин", "Тариф", "MRR (UZS)", "Дата создания"]
        display_sdf["MRR (UZS)"] = display_sdf["MRR (UZS)"].apply(lambda x: f"{int(x):,}".replace(",", " ") if pd.notna(x) else "—")
        st.dataframe(display_sdf, use_container_width=True, hide_index=True)
    else:
        st.info("В этом месяце пока нет новых клиентов.")


def draw_leaderboard(data):
    """Draws partner leaderboard in a separate tab."""
    st.header("🏆 Рейтинг партнёров")
    
    client = get_bq_client()
    query_leaderboard = """
    WITH latest_custs AS (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY loaded_at DESC) as rn
        FROM `br-clients-02.ms_ekeppe.chargebee_customers`
    ),
    deduped_custs AS (
        SELECT * 
        FROM latest_custs
        WHERE rn = 1
    ),
    latest_subs AS (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY subscription_id ORDER BY loaded_at DESC) as rn
        FROM `br-clients-02.ms_ekeppe.chargebee_subscriptions`
    ),
    deduped_subs AS (
        SELECT * 
        FROM latest_subs
        WHERE rn = 1
          AND status IN ('active', 'in_trial')
    )
    SELECT 
        c.cf_support_manager as partner,
        COUNT(DISTINCT c.customer_id) as total_clients,
        SUM(sub.mrr) / 100 as total_mrr,
        COUNT(DISTINCT CASE WHEN c.created_at >= TIMESTAMP_TRUNC(CURRENT_TIMESTAMP(), MONTH) 
              THEN c.customer_id END) as new_this_month
    FROM deduped_custs c
    JOIN deduped_subs sub
        ON c.customer_id = sub.customer_id
    WHERE c.cf_support_manager IS NOT NULL
      AND c.cf_support_manager != ''
      AND LOWER(c.cf_support_manager) LIKE '%partner%'
    GROUP BY c.cf_support_manager
    """
    lb_df = client.query(query_leaderboard).to_dataframe()
    
    if lb_df.empty:
        st.info("Нет данных для рейтинга.")
        return
    
    partner_first = data['first_name'].lower()
    
    # --- Ranking by NEW clients this month ---
    st.markdown("#### 🆕 По новым клиентам (этот месяц)")
    new_df = lb_df[lb_df['new_this_month'] > 0].sort_values('new_this_month', ascending=False).reset_index(drop=True)
    if not new_df.empty:
        new_df.insert(0, 'rank', range(1, len(new_df) + 1))
        
        my_rank_new = new_df[new_df['partner'].apply(lambda x: partner_first in x.lower())]['rank'].values
        if len(my_rank_new) > 0:
            st.markdown(f"Ваше место: **#{my_rank_new[0]}** из {len(new_df)}")
        else:
            st.markdown("У вас пока нет новых клиентов в этом месяце")
        
        display_new = new_df[['rank', 'partner', 'new_this_month']].copy()
        display_new.columns = ["#", "Партнёр", "Новые клиенты"]
        
        def highlight_new(row):
            if partner_first in str(new_df.iloc[row.name]['partner']).lower():
                return ['background-color: #1e3a5f'] * len(row)
            return [''] * len(row)
        
        st.dataframe(display_new.style.apply(highlight_new, axis=1), use_container_width=True, hide_index=True)
    else:
        st.info("В этом месяце пока нет продаж.")


# --- ADMIN DASHBOARD ---
def draw_admin_dashboard():
    st.header("Панель администратора")
    
    client = get_bq_client()
    
    # 1. Total partners (from amoCRM group Partners)
    query_total_partners = """
    SELECT COUNT(DISTINCT menedzher) as total_partners
    FROM `br-clients-02.ms_ekeppe.sdelki_table`
    WHERE gruppa_menedzhera IN ('Partners', 'Partners Manager UZ')
    """
    total_partners_df = client.query(query_total_partners).to_dataframe()
    total_partners = int(total_partners_df['total_partners'].iloc[0])
    
    # 2. Partners with sales THIS month (new Chargebee clients)
    query_active_partners = """
    WITH latest_custs AS (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY loaded_at DESC) as rn
        FROM `br-clients-02.ms_ekeppe.chargebee_customers`
    ),
    deduped_custs AS (
        SELECT * 
        FROM latest_custs
        WHERE rn = 1
    ),
    latest_subs AS (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY subscription_id ORDER BY loaded_at DESC) as rn
        FROM `br-clients-02.ms_ekeppe.chargebee_subscriptions`
    ),
    deduped_subs AS (
        SELECT * 
        FROM latest_subs
        WHERE rn = 1
          AND status = 'active'
    )
    SELECT c.cf_support_manager as partner, COUNT(DISTINCT c.customer_id) as sales_count
    FROM deduped_custs c
    JOIN deduped_subs sub
        ON c.customer_id = sub.customer_id
    WHERE c.created_at >= TIMESTAMP_TRUNC(CURRENT_TIMESTAMP(), MONTH)
      AND c.cf_support_manager IS NOT NULL
      AND c.cf_support_manager != ''
      AND LOWER(c.cf_support_manager) LIKE '%partner%'
    GROUP BY c.cf_support_manager
    ORDER BY sales_count DESC
    """
    active_partners_df = client.query(query_active_partners).to_dataframe()
    active_partners_count = len(active_partners_df)
    
    # 3. Planned payments from partner pipeline
    query_planned = """
    SELECT 
        etap_sdelki,
        COUNT(*) as deal_count,
        COALESCE(SUM(summa_sdelok), 0) as total_budget
    FROM `br-clients-02.ms_ekeppe.sdelki_table`
    WHERE voronka = "BILLZ Partners' deals"
      AND etap_sdelki IN ('Тестовый период', 'В процессе подключения', 'Получение оплаты')
    GROUP BY etap_sdelki
    """
    planned_df = client.query(query_planned).to_dataframe()
    total_planned_sum = int(planned_df['total_budget'].sum()) if not planned_df.empty else 0
    total_planned_count = int(planned_df['deal_count'].sum()) if not planned_df.empty else 0
    
    # 4. Average connections per partner
    query_avg_connections = """
    WITH latest_custs AS (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY loaded_at DESC) as rn
        FROM `br-clients-02.ms_ekeppe.chargebee_customers`
    ),
    deduped_custs AS (
        SELECT * 
        FROM latest_custs
        WHERE rn = 1
    ),
    latest_subs AS (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY subscription_id ORDER BY loaded_at DESC) as rn
        FROM `br-clients-02.ms_ekeppe.chargebee_subscriptions`
    ),
    deduped_subs AS (
        SELECT * 
        FROM latest_subs
        WHERE rn = 1
          AND status = 'active'
    )
    SELECT 
        c.cf_support_manager as partner,
        COUNT(DISTINCT sub.customer_id) as connections
    FROM deduped_custs c
    JOIN deduped_subs sub
        ON c.customer_id = sub.customer_id
    WHERE c.cf_support_manager IS NOT NULL
      AND c.cf_support_manager != ''
      AND LOWER(c.cf_support_manager) LIKE '%partner%'
    GROUP BY c.cf_support_manager
    """
    avg_conn_df = client.query(query_avg_connections).to_dataframe()
    avg_connections = round(avg_conn_df['connections'].mean(), 1) if not avg_conn_df.empty else 0
    
    # --- Display Metrics ---
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Всего партнёров", total_partners)
    with c2:
        st.metric("С продажами в этом мес.", active_partners_count)
    with c3:
        st.metric("План. оплата (UZS)", f"{total_planned_sum:,}".replace(",", " "), f"{total_planned_count} сделок")
    with c4:
        st.metric("Ср. подключений/партнёр", avg_connections)
    
    st.write("---")
    
    # Pipeline breakdown
    st.markdown("#### 📋 Планируемые оплаты (Партнёрская воронка)")
    if not planned_df.empty:
        display_planned = planned_df.copy()
        display_planned.columns = ["Этап", "Кол-во сделок", "Сумма (UZS)"]
        display_planned["Сумма (UZS)"] = display_planned["Сумма (UZS)"].apply(lambda x: f"{int(x):,}".replace(",", " "))
        st.dataframe(display_planned, use_container_width=True, hide_index=True)
    else:
        st.info("Нет сделок в планируемых этапах.")
    
    st.write("---")
    
    # Partners with sales this month
    st.markdown(f"#### 🟢 Партнёры с продажами в этом месяце ({active_partners_count})")
    if not active_partners_df.empty:
        st.dataframe(active_partners_df.rename(columns={"partner": "Партнёр", "sales_count": "Продажи"}), use_container_width=True, hide_index=True)
    else:
        st.info("В этом месяце нет продаж.")
    
    st.write("---")
    
    # Top partners by connections
    st.markdown("#### 🏆 Топ партнёров по подключениям")
    if not avg_conn_df.empty:
        top_partners = avg_conn_df.sort_values("connections", ascending=False).head(15)
        top_partners.columns = ["Партнёр", "Подключения"]
        st.dataframe(top_partners, use_container_width=True, hide_index=True)


# --- APP LAYOUT ---
def main():
    if not check_password():
        return
    
    partner_name = st.session_state["partner_name"]

    with st.sidebar:
        st.image("logo.png", width=150)
        st.markdown("---")
        st.markdown(f"**{partner_name}**")
        st.caption("Админ" if partner_name == "Admin" else "Магазин / Партнер")
        if st.button("Выйти", use_container_width=True):
            del st.session_state["password_correct"]
            st.rerun()

    # Admin gets a different view
    if partner_name == "Admin":
        tab1, tab2 = st.tabs(["Обзор", "Партнёр (демо)"])
        
        with tab1:
            draw_admin_dashboard()
        
        with tab2:
            # Admin can preview a partner's view
            data = get_partner_data("Admin")
            draw_main_dashboard(data)
    else:
        data = get_partner_data(partner_name)
        
        tab1, tab2, tab_payouts, tab3 = st.tabs(["Главная", "Мои клиенты", "Выплаты", "Рейтинг"])
        
        with tab1:
            draw_main_dashboard(data)
            
        with tab3:
            draw_leaderboard(data)
        
        with tab2:
            st.header("Мои клиенты")
            
            from datetime import date
            from dateutil.relativedelta import relativedelta
            
            # Month & Status selectors
            today = date.today()
            selected_month = date(today.year, today.month, 1)
            
            col_s, col_a = st.columns(2)
            with col_s:
                selected_status = st.selectbox(
                    "Статус подписки",
                    options=["Все", "Активен", "В триале", "Отменен", "Не продлевается"],
                    index=0
                )
            with col_a:
                # key="activity_select" — чтобы кнопка из баннера (Правка 2) могла
                # заранее установить фильтр «Неактивные (>4 дн)» через session_state.
                selected_activity = st.selectbox(
                    "Активность",
                    options=["Все", "Активные (≤1 дн)", "В зоне риска (2-4 дн)", INACTIVE_LABEL, "Нет данных"],
                    key="activity_select"
                )
            
            # Build a query for all active clients directly from Chargebee
            first_name = data['first_name']
            client_bq = get_bq_client()
            query_my_clients = f"""
            WITH latest_custs AS (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY loaded_at DESC) as rn
                FROM `br-clients-02.ms_ekeppe.chargebee_customers`
            ),
            deduped_custs AS (
                SELECT * 
                FROM latest_custs
                WHERE rn = 1
            ),
            latest_subs AS (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY subscription_id ORDER BY loaded_at DESC) as rn
                FROM `br-clients-02.ms_ekeppe.chargebee_subscriptions`
            ),
            deduped_subs AS (
                SELECT * 
                FROM latest_subs
                WHERE rn = 1
            ),
            first_activations AS (
                SELECT 
                    customer_id, 
                    MIN(activated_at) as first_activated_at
                FROM `br-clients-02.ms_ekeppe.chargebee_subscriptions`
                WHERE activated_at IS NOT NULL
                GROUP BY customer_id
            ),
            invoices_deduped AS (
                SELECT * FROM (
                    SELECT i.*, ROW_NUMBER() OVER(PARTITION BY i.invoice_id ORDER BY i.loaded_at DESC) AS rn
                    FROM `br-clients-02.ms_ekeppe.chargebee_invoices` i
                )
                WHERE rn = 1
            ),
            debt_summary AS (
                SELECT
                    c.customer_id,
                    SUM(
                        CASE i.currency_code
                            WHEN 'USD' THEN i.amount_due * {USD_RATE}
                            WHEN 'KZT' THEN i.amount_due * 27
                            WHEN 'KGS' THEN i.amount_due * 145
                            WHEN 'TJS' THEN i.amount_due * 1100
                            ELSE i.amount_due
                        END
                    ) / 100 AS total_debt
                FROM invoices_deduped i
                JOIN deduped_custs c ON i.customer_id = c.customer_id
                WHERE i.status IN ('payment_due', 'not_paid')
                GROUP BY c.customer_id
            )
            SELECT DISTINCT
                c.company as client_name,
                c.cf_loginprefix as login,
                sub.plan_id,
                sub.mrr / 100 as mrr,
                COALESCE(d.total_debt, 0) as debt,
                sub.status as status,
                c.cf_sales_manager as sales_manager,
                c.cf_support_manager as support_manager,
                COALESCE(DATE(fa.first_activated_at, 'Asia/Tashkent'), DATE(c.created_at, 'Asia/Tashkent')) as created_date
            FROM deduped_custs c
            JOIN deduped_subs sub ON c.customer_id = sub.customer_id
            LEFT JOIN first_activations fa ON c.customer_id = fa.customer_id
            LEFT JOIN debt_summary d ON c.customer_id = d.customer_id
            WHERE LOWER(c.cf_support_manager) LIKE LOWER('%{first_name}%')
            ORDER BY created_date DESC
            """
            my_clients_df = client_bq.query(query_my_clients).to_dataframe()
            
            if not my_clients_df.empty:
                # Only clients that existed during the selected month
                end_of_month = selected_month + relativedelta(months=1)
                my_clients_df = my_clients_df[my_clients_df['created_date'] < end_of_month]
                
                # Merge with activity data from billz-analytics
                activity_df = load_clients_activity()
                if not activity_df.empty:
                    my_clients_df['_login_key'] = my_clients_df['login'].str.lower()
                    my_clients_df = my_clients_df.merge(activity_df, left_on='_login_key', right_on='login_key', how='left')
                    my_clients_df.drop(columns=['_login_key', 'login_key'], inplace=True, errors='ignore')

                    # Calculate Activity
                    now = pd.Timestamp.now().normalize()
                    last_sale = pd.to_datetime(my_clients_df['last_sale_date'], errors='coerce')
                    last_import = pd.to_datetime(my_clients_df['last_import_date'], errors='coerce')
                    last_login = pd.to_datetime(my_clients_df['last_login_date'], errors='coerce')

                    # Find max active date
                    max_active_dt = pd.concat([last_sale, last_import, last_login], axis=1).max(axis=1)
                    days_since_active = (now - max_active_dt).dt.days

                    my_clients_df['Активность'] = np.where(
                        days_since_active.isna(), 'Нет данных',
                        np.where(days_since_active > 4, 'Неактивные (>4 дн)',
                        np.where(days_since_active <= 1, 'Активные (≤1 дн)', 'В зоне риска (2-4 дн)'))
                    )
                else:
                    my_clients_df['Активность'] = 'Нет данных'

                # Override MRR to 0 for inactive/cancelled clients
                my_clients_df.loc[~my_clients_df['status'].isin(['active', 'in_trial']), 'mrr'] = 0

                # Sort: active first, then by created date descending
                my_clients_df['is_active_sort'] = my_clients_df['status'].isin(['active', 'in_trial'])
                my_clients_df = my_clients_df.sort_values(by=['is_active_sort', 'created_date'], ascending=[False, False]).drop(columns=['is_active_sort']).reset_index(drop=True)

                # Cutoff: 12 months before the selected month
                cutoff_date = selected_month - relativedelta(months=12)
                
                def calc_bonus(row):
                    created = row['created_date']
                    if pd.notna(created) and created < cutoff_date:
                        return 0
                    
                    sales = str(row['sales_manager']).lower() if pd.notna(row['sales_manager']) else ''
                    support = str(row['support_manager']).lower() if pd.notna(row['support_manager']) else ''
                    sales_match = first_name.lower() in sales
                    support_match = first_name.lower() in support
                    if sales_match and support_match:
                        return 50
                    elif support_match:
                        return 20
                    else:
                        return 0
                
                my_clients_df['bonus_pct'] = my_clients_df.apply(calc_bonus, axis=1)
                my_clients_df['bonus_amount'] = (my_clients_df['mrr'] * my_clients_df['bonus_pct'] / 100).astype(int)
                
                # Portfolio Income
                active_clients_count = len(my_clients_df[my_clients_df['status'].isin(['active', 'in_trial'])])
                portfolio_pct = 0
                if active_clients_count >= 150:
                    portfolio_pct = 20
                elif active_clients_count >= 100:
                    portfolio_pct = 15
                elif active_clients_count >= 50:
                    portfolio_pct = 10
                
                my_clients_df['portfolio_pct'] = portfolio_pct
                my_clients_df['portfolio_amount'] = (my_clients_df['mrr'] * portfolio_pct / 100).astype(int)
                my_clients_df['total_bonus'] = my_clients_df['bonus_amount'] + my_clients_df['portfolio_amount']
                
                # Summary metrics
                total_mrr = int(my_clients_df['mrr'].sum())
                total_sales_bonus = int(my_clients_df['bonus_amount'].sum())
                total_portfolio_bonus = int(my_clients_df['portfolio_amount'].sum())
                total_all_bonus = int(my_clients_df['total_bonus'].sum())
                
                # Filter by status if selected
                status_filter_map = {
                    "Активен": "active",
                    "В триале": "in_trial",
                    "Отменен": "cancelled",
                    "Не продлевается": "non_renewing"
                }
                filtered_df = my_clients_df.copy()
                if selected_status != "Все":
                    target_status = status_filter_map[selected_status]
                    filtered_df = filtered_df[filtered_df['status'] == target_status]
                
                # Filter by activity if selected
                if selected_activity != "Все":
                    filtered_df = filtered_df[filtered_df['Активность'] == selected_activity]

                col_left, col_right = st.columns([2, 1])
                with col_left:
                    c1, c2 = st.columns(2)
                    with c1:
                        st.metric("Клиенты (активные)", active_clients_count, f"Портфель: {portfolio_pct}%")
                    with c2:
                        st.metric("Общий MRR (UZS)", f"{total_mrr:,}".replace(",", " "))
                    
                    c3, c4 = st.columns(2)
                    with c3:
                        st.metric("Бонус с продаж (UZS)", f"{total_sales_bonus:,}".replace(",", " "))
                    with c4:
                        st.metric("Портфельн. доход (UZS)", f"{total_portfolio_bonus:,}".replace(",", " "))
                    
                    st.write("---")
                    st.markdown(f"#### Итого бонус за месяц: <span style='color:#16a34a;'>{total_all_bonus:,} UZS</span>".replace(",", " "), unsafe_allow_html=True)
                
                with col_right:
                    st.markdown("<h4 style='font-size:16px; margin-bottom: 5px; text-align: center;'>Активность клиентов</h4>", unsafe_allow_html=True)
                    activity_counts = filtered_df[filtered_df['Активность'] != 'Нет данных']['Активность'].value_counts().reset_index()
                    activity_counts.columns = ['Активность', 'Количество']
                    if not activity_counts.empty:
                        # Правка 4: в легенде рядом с процентом показываем число клиентов.
                        # Цвета и порядок сегментов не меняются — данные те же.
                        seg_colors = {
                            'Активные (≤1 дн)': '#16A34A',
                            'В зоне риска (2-4 дн)': '#F59E0B',
                            INACTIVE_LABEL: '#DC2626',
                        }
                        total_clients = int(activity_counts['Количество'].sum())
                        activity_counts['Легенда'] = activity_counts.apply(
                            lambda r: f"{r['Активность']}: {int(r['Количество'])} клиентов · "
                                      f"{r['Количество'] / total_clients * 100:.1f}%",
                            axis=1,
                        )
                        legend_colors = {
                            row['Легенда']: seg_colors.get(row['Активность'], '#94A3B8')
                            for _, row in activity_counts.iterrows()
                        }
                        fig = px.pie(activity_counts, names='Легенда', values='Количество',
                                     hole=0.5, color='Легенда',
                                     color_discrete_map=legend_colors)
                        # На секторах оставляем только проценты — подробности в легенде
                        fig.update_traces(textinfo='percent', textposition='inside')
                        fig.update_layout(
                            margin=dict(t=0, b=0, l=0, r=0),
                            height=200,
                            showlegend=True,
                            legend=dict(font=dict(size=11)),
                            paper_bgcolor='rgba(0,0,0,0)',
                            plot_bgcolor='rgba(0,0,0,0)'
                        )
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.info("Нет данных об активности")
                
                st.write("---")

                if not filtered_df.empty:
                    display_clients = filtered_df[['client_name', 'login', 'plan_id', 'status', 'Активность', 'mrr', 'debt', 'bonus_pct', 'bonus_amount', 'portfolio_pct', 'portfolio_amount', 'total_bonus', 'created_date']].copy()
                    status_map = {
                        "active": "Активен",
                        "in_trial": "В триале",
                        "cancelled": "Отменен",
                        "non_renewing": "Не продлевается",
                        "future": "Будущий"
                    }
                    display_clients["status"] = display_clients["status"].map(status_map).fillna(display_clients["status"])
                    display_clients.columns = ["Клиент", "Логин", "Тариф", "Статус в CB", "Активность", "MRR (UZS)", "Долг клиента (UZS)", "Бонус продаж %", "Бонус продаж (UZS)", "Портфель %", "Портфель (UZS)", "Итого бонус (UZS)", "Дата создания"]
                    
                    for col in ["MRR (UZS)", "Долг клиента (UZS)", "Бонус продаж (UZS)", "Портфель (UZS)", "Итого бонус (UZS)"]:
                        display_clients[col] = display_clients[col].apply(lambda x: f"{int(x):,}".replace(",", " ") if pd.notna(x) else "—")
                    
                    display_clients["Бонус продаж %"] = display_clients["Бонус продаж %"].apply(lambda x: f"{int(x)}%" if pd.notna(x) else "—")
                    display_clients["Портфель %"] = display_clients["Портфель %"].apply(lambda x: f"{int(x)}%" if pd.notna(x) else "—")
                    
                    st.dataframe(display_clients, use_container_width=True, hide_index=True)
                else:
                    st.info("Нет клиентов с выбранными фильтрами в этом месяце.")
            else:
                st.info("Нет клиентов в Chargebee.")

        with tab_payouts:
            st.header("История выплат")

            # Month selector for payouts
            today = date.today()
            payout_months = []
            for i in range(12):
                d = today - relativedelta(months=i)
                payout_months.append(date(d.year, d.month, 1))

            payout_month_labels = {d: d.strftime("%B %Y") for d in payout_months}
            selected_payout_month = st.selectbox(
                "Выберите месяц для просмотра выплат",
                options=payout_months,
                format_func=lambda d: payout_month_labels[d],
                index=0,
                key="payout_month_selector"
            )

            # Calculate selected month boundaries
            payout_end_month = selected_payout_month + relativedelta(months=1)

            # Fetch paid invoices for this partner
            first_name = data['first_name']
            client_bq = get_bq_client()

            query_payouts = f"""
            WITH latest_custs AS (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY loaded_at DESC) as rn
                FROM `br-clients-02.ms_ekeppe.chargebee_customers`
            ),
            deduped_custs AS (
                SELECT * 
                FROM latest_custs
                WHERE rn = 1
            ),
            first_activations AS (
                SELECT 
                    customer_id, 
                    MIN(activated_at) as first_activated_at
                FROM `br-clients-02.ms_ekeppe.chargebee_subscriptions`
                WHERE activated_at IS NOT NULL
                GROUP BY customer_id
            ),
            invoices_deduped AS (
                SELECT * FROM (
                    SELECT i.*, ROW_NUMBER() OVER(PARTITION BY i.invoice_id ORDER BY i.loaded_at DESC) AS rn
                    FROM `br-clients-02.ms_ekeppe.chargebee_invoices` i
                )
                WHERE rn = 1
            )
            SELECT DISTINCT
                i.invoice_id,
                c.company as client_name,
                c.cf_loginprefix as login,
                i.subscription_id,
                (CASE i.currency_code
                    WHEN 'USD' THEN i.amount_paid * {USD_RATE}
                    WHEN 'KZT' THEN i.amount_paid * 27
                    WHEN 'KGS' THEN i.amount_paid * 145
                    WHEN 'TJS' THEN i.amount_paid * 1100
                    ELSE i.amount_paid
                END) / 100 as amount_paid,
                DATE(i.date, 'Asia/Tashkent') as invoice_date,
                c.cf_sales_manager as sales_manager,
                c.cf_support_manager as support_manager,
                COALESCE(DATE(fa.first_activated_at, 'Asia/Tashkent'), DATE(c.created_at, 'Asia/Tashkent')) as created_date
            FROM invoices_deduped i
            JOIN deduped_custs c ON i.customer_id = c.customer_id
            LEFT JOIN first_activations fa ON c.customer_id = fa.customer_id
            WHERE i.status = 'paid'
              AND i.recurring = true
              AND DATE(i.date, 'Asia/Tashkent') >= '{selected_payout_month}'
              AND DATE(i.date, 'Asia/Tashkent') < '{payout_end_month}'
              AND LOWER(c.cf_support_manager) LIKE LOWER('%{first_name}%')
            ORDER BY invoice_date DESC
            """

            payouts_df = client_bq.query(query_payouts).to_dataframe()

            if not payouts_df.empty:
                # Group by unique login/customer to calculate active count for portfolio tier
                unique_active_clients = len(payouts_df['login'].unique())

                # Determine Portfolio Bonus Pct
                portfolio_pct_tier = 0
                if unique_active_clients >= 150:
                    portfolio_pct_tier = 20
                elif unique_active_clients >= 100:
                    portfolio_pct_tier = 15
                elif unique_active_clients >= 50:
                    portfolio_pct_tier = 10

                # Calculate historical metrics
                def calc_historical_metrics(row):
                    created = row['created_date']
                    invoice_date = row['invoice_date']
                    amount_paid = row['amount_paid']

                    is_revshare_period = pd.notna(created) and pd.notna(invoice_date) and (invoice_date - created).days <= 365

                    # A. Sales Revshare Bonus (only in first 12 months)
                    if is_revshare_period:
                        sales = str(row['sales_manager']).lower() if pd.notna(row['sales_manager']) else ''
                        support = str(row['support_manager']).lower() if pd.notna(row['support_manager']) else ''
                        sales_match = first_name.lower() in sales
                        support_match = first_name.lower() in support
                        if sales_match and support_match:
                            bonus_pct = 50
                        elif support_match:
                            bonus_pct = 20
                        else:
                            bonus_pct = 0
                    else:
                        bonus_pct = 0

                    bonus_amount = int(amount_paid * bonus_pct / 100)

                    # B. Portfolio Bonus (only starting from 13th month)
                    if not is_revshare_period:
                        portfolio_pct = portfolio_pct_tier
                    else:
                        portfolio_pct = 0

                    portfolio_amount = int(amount_paid * portfolio_pct / 100)
                    total_bonus = bonus_amount + portfolio_amount

                    return pd.Series([bonus_pct, bonus_amount, portfolio_pct, portfolio_amount, total_bonus])

                payouts_df[['bonus_pct', 'bonus_amount', 'portfolio_pct', 'portfolio_amount', 'total_bonus']] = payouts_df.apply(calc_historical_metrics, axis=1)

                # Metrics
                total_paid_mrr = int(payouts_df['amount_paid'].sum())
                total_historical_sales_bonus = int(payouts_df['bonus_amount'].sum())
                total_historical_portfolio_bonus = int(payouts_df['portfolio_amount'].sum())
                total_historical_all_bonus = int(payouts_df['total_bonus'].sum())
                clients_50pct = payouts_df[payouts_df['bonus_pct'] == 50]['login'].nunique()

                c1, c2, c3, c4, c5 = st.columns(5)
                with c1:
                    st.metric("Оплаченные клиенты", unique_active_clients, f"Ставка портфеля: {portfolio_pct_tier}%")
                with c2:
                    st.metric("Клиенты (≤12 мес)", clients_50pct)
                with c3:
                    st.metric("Оплаченный MRR (UZS)", f"{total_paid_mrr:,}".replace(",", " "))
                with c4:
                    st.metric("Бонус с продаж (UZS)", f"{total_historical_sales_bonus:,}".replace(",", " "))
                with c5:
                    st.metric("Портфельн. доход (UZS)", f"{total_historical_portfolio_bonus:,}".replace(",", " "))

                st.write("---")
                st.markdown(f"#### Выплаченный бонус за месяц: <span style='color:#16a34a;'>{total_historical_all_bonus:,} UZS</span>".replace(",", " "), unsafe_allow_html=True)
                st.write("---")

                # Table display
                display_payouts = payouts_df[['client_name', 'login', 'amount_paid', 'bonus_pct', 'bonus_amount', 'portfolio_pct', 'portfolio_amount', 'total_bonus', 'invoice_date', 'created_date']].copy()
                display_payouts.columns = ["Клиент", "Логин", "Оплачено (UZS)", "Бонус продаж %", "Бонус продаж (UZS)", "Портфель %", "Портфель (UZS)", "Итого бонус (UZS)", "Дата счета", "Дата активации"]

                for col in ["Оплачено (UZS)", "Бонус продаж (UZS)", "Портфель (UZS)", "Итого бонус (UZS)"]:
                    display_payouts[col] = display_payouts[col].apply(lambda x: f"{int(x):,}".replace(",", " ") if pd.notna(x) else "—")

                display_payouts["Бонус продаж %"] = display_payouts["Бонус продаж %"].apply(lambda x: f"{int(x)}%" if pd.notna(x) else "—")
                display_payouts["Портфель %"] = display_payouts["Портфель %"].apply(lambda x: f"{int(x)}%" if pd.notna(x) else "—")

                st.dataframe(display_payouts, use_container_width=True, hide_index=True)
            else:
                st.info("Нет оплаченных счетов за выбранный месяц.")

        # --- Правка 2: переход на вкладку «Мои клиенты» по кнопке из баннера ---
        # st.tabs нельзя переключить из Python, поэтому кликаем по нужной вкладке
        # через JS. Фильтр «Неактивные (>4 дн)» уже выставлен в
        # session_state["activity_select"] обработчиком кнопки выше.
        if st.session_state.pop("_switch_to_clients", False):
            components.html(
                """
                <script>
                  const go = () => {
                    const tabs = window.parent.document.querySelectorAll('button[data-baseweb="tab"]');
                    if (tabs && tabs.length > 1) { tabs[1].click(); }
                  };
                  setTimeout(go, 150);
                </script>
                """,
                height=0,
            )

if __name__ == "__main__":
    main()
