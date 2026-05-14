import streamlit as st
import pandas as pd
from datetime import date
from dateutil.relativedelta import relativedelta
from google.cloud import bigquery

# --- CONFIG ---
st.set_page_config(
    page_title="BILLZ Onboard Дашборд",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- STYLING ---
st.markdown("""
<style>
    .metric-card {
        background-color: #1e1e1e;
        border-radius: 10px;
        padding: 20px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
        margin-bottom: 20px;
        border: 1px solid #333;
    }
    .metric-value {
        font-size: 28px;
        font-weight: 700;
        color: #60a5fa;
    }
    .metric-label {
        font-size: 14px;
        color: #94a3b8;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
</style>
""", unsafe_allow_html=True)

# --- DATA FETCHING ---
@st.cache_resource
def get_bq_client():
    return bigquery.Client(project="br-clients-02")

@st.cache_data(ttl=60)
def get_onboard_data(selected_month):
    client = get_bq_client()
    
    start_date = selected_month.strftime("%Y-%m-01")
    end_date = (selected_month + relativedelta(months=1)).strftime("%Y-%m-01")

    query = f"""
    WITH amo_deals AS (
        SELECT 
            login, 
            account_executive, 
            gruppa_menedzhera, 
            data_zakrytiya, 
            ssylka_na_sdelku_v_amo,
            sdelka as client_name
        FROM `br-clients-02.ms_ekeppe.sdelki_table`
        WHERE (rezultat_sdelki = 'Успешно реализовано' OR etap_sdelki = 'Сделка успешна')
          AND data_zakrytiya >= TIMESTAMP('{start_date}')
          AND data_zakrytiya < TIMESTAMP('{end_date}')
    ),
    cb_dedup AS (
        SELECT 
            c.cf_loginprefix,
            s.plan_id,
            s.mrr / 100 as mrr,
            DATE(c.created_at) as cb_created_date,
            ROW_NUMBER() OVER (PARTITION BY c.cf_loginprefix ORDER BY s.mrr DESC) as rn
        FROM `br-clients-02.ms_ekeppe.chargebee_customers` c
        JOIN `br-clients-02.ms_ekeppe.chargebee_subscriptions` s
            ON c.customer_id = s.customer_id
        WHERE s.status = 'active'
    )
    SELECT 
        amo.client_name,
        amo.login,
        cb.plan_id,
        cb.mrr,
        cb.cb_created_date,
        amo.account_executive,
        amo.gruppa_menedzhera,
        DATE(amo.data_zakrytiya) as amo_closed_date,
        amo.ssylka_na_sdelku_v_amo as amo_link
    FROM amo_deals amo
    LEFT JOIN cb_dedup cb 
        ON amo.login = cb.cf_loginprefix 
        AND cb.rn = 1
        AND amo.login IS NOT NULL
        AND amo.login NOT IN ('Да', 'Нет', '-', 'Bir hafta ichida', 'Bir oy ichida', 'Hozir emas', 'Keyinroq', 'В течение недели', 'В течение месяца')
    ORDER BY amo.data_zakrytiya DESC
    """
    df = client.query(query).to_dataframe()
    return df


def fmt_money(x):
    """Format number as money string with spaces."""
    if pd.notna(x):
        return f"{int(x):,}".replace(",", " ")
    return "0"


# --- APP LAYOUT ---
def main():
    st.markdown("<h1 style='color: #60a5fa; margin-bottom: 0;'>Onboard: Бонусы Менеджеров</h1>", unsafe_allow_html=True)
    st.markdown("<p style='color: #94a3b8; font-size: 16px; margin-top: 0;'>Дашборд для расчета бонусов за успешные сделки (Chargebee + AmoCRM)</p>", unsafe_allow_html=True)
    st.write("---")

    # Month selector
    today = date.today()
    months_list = [date(today.year, today.month, 1) - relativedelta(months=i) for i in range(12)]
    month_labels = {d: d.strftime("%B %Y") for d in months_list}
    
    col1, col2 = st.columns([1, 3])
    with col1:
        selected_month = st.selectbox(
            "Выберите месяц закрытия сделки в AmoCRM:",
            options=months_list,
            format_func=lambda d: month_labels[d],
            index=0
        )
        
    df = get_onboard_data(selected_month)
    
    if df.empty:
        st.info(f"Нет данных за {month_labels[selected_month]}")
        return

    # Clean up empty values
    df['account_executive'] = df['account_executive'].fillna('Не найден (Без Account Executive)')
    df['gruppa_menedzhera'] = df['gruppa_menedzhera'].fillna('Без группы')

    # --- Sidebar: Filters ---
    with st.sidebar:
        st.markdown("<h2 style='color: #60a5fa;'>Фильтры</h2>", unsafe_allow_html=True)
        st.write("---")
        
        all_groups = sorted(df['gruppa_menedzhera'].unique().tolist())
        selected_group = st.selectbox(
            "Группа менеджера:",
            options=["Все группы"] + all_groups,
            index=0
        )
        
        df['plan_id'] = df['plan_id'].fillna('Без подписки (Нет в Chargebee)')
        all_plans = sorted(df['plan_id'].unique().tolist())
        selected_plan = st.selectbox(
            "Тариф (Chargebee):",
            options=["Все тарифы"] + all_plans,
            index=0
        )
    
    # Apply filters
    if selected_group != "Все группы":
        df = df[df['gruppa_menedzhera'] == selected_group]
        
    if selected_plan != "Все тарифы":
        df = df[df['plan_id'] == selected_plan]
    
    if df.empty:
        st.info(f"Нет данных по выбранным фильтрам за {month_labels[selected_month]}")
        return

    # Top metrics
    total_deals = len(df)
    total_mrr = int(df['mrr'].sum())
    cb_found = len(df[df['plan_id'] != 'Без подписки (Нет в Chargebee)'])
    
    m1, m2, m3 = st.columns(3)
    with m1:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-label">Всего сделок (AmoCRM)</div>
            <div class="metric-value">{total_deals}</div>
        </div>
        """, unsafe_allow_html=True)
    with m2:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-label">С подпиской (Chargebee)</div>
            <div class="metric-value">{cb_found}</div>
        </div>
        """, unsafe_allow_html=True)
    with m3:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-label">Общий MRR (UZS)</div>
            <div class="metric-value">{fmt_money(total_mrr)}</div>
        </div>
        """, unsafe_allow_html=True)

    # --- Summary table ---
    st.subheader("📊 Сводка по Account Executive")
    summary_df = df.groupby(['gruppa_menedzhera', 'account_executive']).agg(
        Deals_Count=('client_name', 'count'),
        Total_MRR=('mrr', 'sum')
    ).reset_index()
    summary_df = summary_df.sort_values(by='Total_MRR', ascending=False)
    
    display_summary = summary_df.copy()
    display_summary.columns = ["Группа", "Account Executive", "Количество сделок", "Общий MRR (UZS)"]
    display_summary["Общий MRR (UZS)"] = display_summary["Общий MRR (UZS)"].apply(fmt_money)
    
    st.dataframe(display_summary, use_container_width=True, hide_index=True)

    # --- Per-manager deal breakdown ---
    st.write("---")
    st.subheader("📋 Сделки по Account Executive")

    managers = summary_df.sort_values(by='Total_MRR', ascending=False)['account_executive'].tolist()
    
    for manager in managers:
        manager_df = df[df['account_executive'] == manager]
        manager_mrr = int(manager_df['mrr'].sum())
        manager_count = len(manager_df)
        
        with st.expander(f"**{manager}** — {manager_count} сделок, MRR: {fmt_money(manager_mrr)} UZS"):
            display_df = manager_df[['client_name', 'login', 'plan_id', 'mrr', 'gruppa_menedzhera', 'cb_created_date', 'amo_closed_date', 'amo_link']].copy()
            display_df.columns = ["Клиент", "Логин", "Тариф", "MRR (UZS)", "Группа", "Дата Chargebee", "Дата AmoCRM", "Ссылка AmoCRM"]
            display_df["MRR (UZS)"] = display_df["MRR (UZS)"].apply(fmt_money)
            
            st.dataframe(
                display_df, 
                use_container_width=True, 
                hide_index=True,
                column_config={
                    "Ссылка AmoCRM": st.column_config.LinkColumn("Ссылка в AmoCRM")
                }
            )

if __name__ == "__main__":
    main()
