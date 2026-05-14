import streamlit as st
import pandas as pd
import datetime
from google.cloud import bigquery

# --- CONFIG ---
st.set_page_config(
    page_title="BILLZ Партнёрский Дашборд",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
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
            "5555": "Xikmatillo Baxtiyorov"
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

@st.cache_data(ttl=300)
def get_partner_data(partner_name):
    if partner_name == "Admin":
        partner_name = "Doston Botirov"
        
    client = get_bq_client()
    # Use first name for matching cf_support_manager (format varies: "Parviz Khafizov Partner", etc.)
    first_name = partner_name.split()[0]
    
    # 1. All-time active connections (directly from Chargebee)
    query_conn_all = f"""
    SELECT 
        COUNT(DISTINCT sub.customer_id) as connections_total
    FROM `br-clients-02.ms_ekeppe.chargebee_customers` c
    JOIN `br-clients-02.ms_ekeppe.chargebee_subscriptions` sub
        ON c.customer_id = sub.customer_id
    WHERE LOWER(c.cf_support_manager) LIKE LOWER('%{first_name}%')
      AND sub.status = 'active'
    """
    conn_all_df = client.query(query_conn_all).to_dataframe()
    
    # 2. New Chargebee customers THIS MONTH
    query_new = f"""
    SELECT DISTINCT
        c.company as client_name,
        c.cf_loginprefix as login,
        sub.plan_id,
        sub.mrr / 100 as mrr,
        DATE(c.created_at) as created_date
    FROM `br-clients-02.ms_ekeppe.chargebee_customers` c
    JOIN `br-clients-02.ms_ekeppe.chargebee_subscriptions` sub
        ON c.customer_id = sub.customer_id
    WHERE LOWER(c.cf_support_manager) LIKE LOWER('%{first_name}%')
      AND sub.status = 'active'
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
    
    # Values
    connections = int(conn_all_df['connections_total'].iloc[0]) if not conn_all_df.empty and pd.notna(conn_all_df['connections_total'].iloc[0]) else 0
    connections_month = len(success_df)
    
    # Scoring (from index.html logic)
    points_connections = connections * 50
    total_points = points_connections
    
    level = 1
    level_title = "Новичок"
    if total_points >= 1200:
        level, level_title = 5, "Легенда"
    elif total_points >= 700:
        level, level_title = 4, "Мастер"
    elif total_points >= 350:
        level, level_title = 3, "Профи"
    elif total_points >= 120:
        level, level_title = 2, "Боец"

    return {
        "name": partner_name,
        "first_name": first_name,
        "level": level,
        "level_title": level_title,
        "total_points": total_points,
        "connections": connections,
        "connections_month": connections_month,
        "success_df": success_df
    }

def draw_main_dashboard(data):
    st.markdown(f"### Добро пожаловать, {data['name']}!")
    st.markdown(f"<span style='background:#e0e7ff; color:#4338ca; padding:4px 8px; border-radius:12px; font-size:12px; font-weight:bold;'>⭐ Уровень {data['level']} · {data['level_title']}</span>", unsafe_allow_html=True)
    
    st.write("---")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Баллы", f"{data['total_points']}")
    with col2:
        st.metric("Подключения (всего)", data['connections'])
    with col3:
        st.metric("Новые в этом месяце", data['connections_month'])

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
    SELECT 
        c.cf_support_manager as partner,
        COUNT(DISTINCT c.customer_id) as total_clients,
        SUM(sub.mrr) / 100 as total_mrr,
        COUNT(DISTINCT CASE WHEN c.created_at >= TIMESTAMP_TRUNC(CURRENT_TIMESTAMP(), MONTH) 
              THEN c.customer_id END) as new_this_month
    FROM `br-clients-02.ms_ekeppe.chargebee_customers` c
    JOIN `br-clients-02.ms_ekeppe.chargebee_subscriptions` sub
        ON c.customer_id = sub.customer_id
    WHERE sub.status IN ('active', 'in_trial')
      AND c.cf_support_manager IS NOT NULL
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
    
    st.write("---")
    
    # --- Ranking by TOTAL clients ---
    st.markdown("#### 📊 По общему числу клиентов")
    total_df = lb_df.sort_values('total_clients', ascending=False).reset_index(drop=True)
    total_df.insert(0, 'rank', range(1, len(total_df) + 1))
    
    my_rank_total = total_df[total_df['partner'].apply(lambda x: partner_first in x.lower())]['rank'].values
    if len(my_rank_total) > 0:
        st.markdown(f"Ваше место: **#{my_rank_total[0]}** из {len(total_df)}")
    
    display_total = total_df[['rank', 'partner', 'total_clients']].copy()
    display_total.columns = ["#", "Партнёр", "Всего клиентов"]
    
    def highlight_total(row):
        if partner_first in str(total_df.iloc[row.name]['partner']).lower():
            return ['background-color: #1e3a5f'] * len(row)
        return [''] * len(row)
    
    st.dataframe(display_total.style.apply(highlight_total, axis=1), use_container_width=True, hide_index=True)


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
    SELECT c.cf_support_manager as partner, COUNT(DISTINCT c.customer_id) as sales_count
    FROM `br-clients-02.ms_ekeppe.chargebee_customers` c
    JOIN `br-clients-02.ms_ekeppe.chargebee_subscriptions` sub
        ON c.customer_id = sub.customer_id
    WHERE sub.status = 'active'
      AND c.created_at >= TIMESTAMP_TRUNC(CURRENT_TIMESTAMP(), MONTH)
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
    SELECT 
        c.cf_support_manager as partner,
        COUNT(DISTINCT sub.customer_id) as connections
    FROM `br-clients-02.ms_ekeppe.chargebee_customers` c
    JOIN `br-clients-02.ms_ekeppe.chargebee_subscriptions` sub
        ON c.customer_id = sub.customer_id
    WHERE sub.status = 'active'
      AND c.cf_support_manager IS NOT NULL
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
        
        tab1, tab2, tab3 = st.tabs(["Главная", "Мои клиенты", "Рейтинг"])
        
        with tab1:
            draw_main_dashboard(data)
            
        with tab3:
            draw_leaderboard(data)
        
        with tab2:
            st.header("Мои клиенты")
            
            from datetime import date
            from dateutil.relativedelta import relativedelta
            
            # Month selector
            today = date.today()
            months_list = []
            for i in range(12):
                d = today - relativedelta(months=i)
                months_list.append(date(d.year, d.month, 1))
            
            month_labels = {d: d.strftime("%B %Y") for d in months_list}
            selected_month = st.selectbox(
                "Выберите месяц",
                options=months_list,
                format_func=lambda d: month_labels[d],
                index=0
            )
            
            # Build a query for all active clients directly from Chargebee
            first_name = data['first_name']
            client_bq = get_bq_client()
            query_my_clients = f"""
            SELECT DISTINCT
                c.company as client_name,
                c.cf_loginprefix as login,
                sub.plan_id,
                sub.mrr / 100 as mrr,
                c.cf_sales_manager as sales_manager,
                c.cf_support_manager as support_manager,
                DATE(c.created_at) as created_date
            FROM `br-clients-02.ms_ekeppe.chargebee_customers` c
            JOIN `br-clients-02.ms_ekeppe.chargebee_subscriptions` sub
                ON c.customer_id = sub.customer_id
            WHERE LOWER(c.cf_support_manager) LIKE LOWER('%{first_name}%')
              AND sub.status IN ('active', 'in_trial')
            ORDER BY created_date DESC
            """
            my_clients_df = client_bq.query(query_my_clients).to_dataframe()
            
            if not my_clients_df.empty:
                # Only clients that existed during the selected month
                end_of_month = selected_month + relativedelta(months=1)
                my_clients_df = my_clients_df[my_clients_df['created_date'] < end_of_month]
                
                # Cutoff: 12 months before the selected month
                cutoff_date = selected_month - relativedelta(months=12)
                
                def calc_bonus(row):
                    created = row['created_date']
                    if pd.notna(created) and created < cutoff_date:
                        return 0
                    
                    sales = str(row['sales_manager']).lower() if pd.notna(row['sales_manager']) else ''
                    support = str(row['support_manager']).lower() if pd.notna(row['support_manager']) else ''
                    partner_lower = partner_name.lower()
                    sales_match = partner_lower.split()[0].lower() in sales
                    support_match = partner_lower.split()[0].lower() in support
                    if sales_match and support_match:
                        return 50
                    elif support_match:
                        return 20
                    else:
                        return 0
                
                my_clients_df['bonus_pct'] = my_clients_df.apply(calc_bonus, axis=1)
                my_clients_df['bonus_amount'] = (my_clients_df['mrr'] * my_clients_df['bonus_pct'] / 100).astype(int)
                
                # Portfolio Income
                total_clients = len(my_clients_df)
                portfolio_pct = 0
                if total_clients >= 150:
                    portfolio_pct = 20
                elif total_clients >= 100:
                    portfolio_pct = 15
                elif total_clients >= 50:
                    portfolio_pct = 10
                
                my_clients_df['portfolio_pct'] = portfolio_pct
                my_clients_df['portfolio_amount'] = (my_clients_df['mrr'] * portfolio_pct / 100).astype(int)
                my_clients_df['total_bonus'] = my_clients_df['bonus_amount'] + my_clients_df['portfolio_amount']
                
                # Summary metrics
                total_mrr = int(my_clients_df['mrr'].sum())
                total_sales_bonus = int(my_clients_df['bonus_amount'].sum())
                total_portfolio_bonus = int(my_clients_df['portfolio_amount'].sum())
                total_all_bonus = int(my_clients_df['total_bonus'].sum())
                
                c1, c2, c3, c4 = st.columns(4)
                with c1:
                    st.metric("Клиенты", total_clients, f"Портфель: {portfolio_pct}%")
                with c2:
                    st.metric("Общий MRR (UZS)", f"{total_mrr:,}".replace(",", " "))
                with c3:
                    st.metric("Бонус с продаж (UZS)", f"{total_sales_bonus:,}".replace(",", " "))
                with c4:
                    st.metric("Портфельн. доход (UZS)", f"{total_portfolio_bonus:,}".replace(",", " "))
                
                st.write("---")
                st.markdown(f"#### Итого бонус за месяц: <span style='color:#16a34a;'>{total_all_bonus:,} UZS</span>".replace(",", " "), unsafe_allow_html=True)
                st.write("---")
                
                display_clients = my_clients_df[['client_name', 'login', 'plan_id', 'mrr', 'bonus_pct', 'bonus_amount', 'portfolio_pct', 'portfolio_amount', 'total_bonus', 'created_date']].copy()
                display_clients.columns = ["Клиент", "Логин", "Тариф", "MRR (UZS)", "Бонус продаж %", "Бонус продаж (UZS)", "Портфель %", "Портфель (UZS)", "Итого бонус (UZS)", "Дата создания"]
                
                for col in ["MRR (UZS)", "Бонус продаж (UZS)", "Портфель (UZS)", "Итого бонус (UZS)"]:
                    display_clients[col] = display_clients[col].apply(lambda x: f"{int(x):,}".replace(",", " ") if pd.notna(x) else "—")
                
                display_clients["Бонус продаж %"] = display_clients["Бонус продаж %"].apply(lambda x: f"{x}%")
                display_clients["Портфель %"] = display_clients["Портфель %"].apply(lambda x: f"{x}%")
                
                st.dataframe(display_clients, use_container_width=True, hide_index=True)
            else:
                st.info("Нет клиентов в Chargebee.")

if __name__ == "__main__":
    main()
