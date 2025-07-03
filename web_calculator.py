# --- 1. Import Libraries ---
import streamlit as st
import pandas as pd
from io import StringIO
from datetime import datetime
import os
import traceback
import pytz
import sqlite3 # Import for SQLite

# --- Import Selenium for advanced web scraping ---
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import arabic_reshaper
from bidi.algorithm import get_display


# --- 2. Define Constants and Helper Functions ---
TENOR_COLUMN_NAME = "المدة (الأيام)"
YIELD_COLUMN_NAME = "متوسط العائد المرجح المقبول (%)"
DATE_COLUMN_NAME = "تاريخ العطاء"
DB_FILENAME = "cbe_historical_data.db" # SQLite database file
TABLE_NAME = "cbe_bids"
CBE_DATA_URL = "https://www.cbe.org.eg/ar/auctions/egp-t-bills"

# --- NEW: Centralized Constants ---
DAYS_IN_YEAR = 365
DEFAULT_TAX_RATE_PERCENT = 20.0

# بيانات أولية في حالة عدم توفر ملف
INITIAL_DATA = {
    TENOR_COLUMN_NAME: [91, 182, 273, 364],
    YIELD_COLUMN_NAME: [26.914, 27.151, 26.534, 24.994]
}

def prepare_arabic_text(text):
    """
    Handles Arabic text shaping for correct display in Streamlit widgets.
    """
    if text is None: return ""
    try:
        configuration = {'delete_harakat': True, 'support_ligatures': True}
        reshaped_text = arabic_reshaper.reshape(str(text), configuration)
        return get_display(reshaped_text)
    except Exception:
        return str(text)

# --- NEW: SQLite Database Functions ---
def init_sqlite_db():
    """Initializes the SQLite database and creates the table if it doesn't exist."""
    conn = sqlite3.connect(DB_FILENAME)
    cursor = conn.cursor()
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
        "{DATE_COLUMN_NAME}" TEXT NOT NULL,
        "{TENOR_COLUMN_NAME}" INTEGER NOT NULL,
        "{YIELD_COLUMN_NAME}" REAL NOT NULL,
        PRIMARY KEY ("{DATE_COLUMN_NAME}", "{TENOR_COLUMN_NAME}")
    )
    """)
    conn.commit()
    conn.close()
    print(f"INFO: Database '{DB_FILENAME}' initialized and table '{TABLE_NAME}' is ready.")

# --- MODIFIED: fetch_data_from_cbe to use SQLite ---
@st.cache_data(ttl=43200)
def fetch_data_from_cbe():
    """
    Fetches the latest T-bill data from the CBE website, processes it,
    and stores it in the SQLite database.
    This version now uses SQLite instead of a CSV file.
    """
    print("🚀 INFO: Initializing fetching process...")
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = None
    try:
        driver = webdriver.Firefox(options=options)
        driver.set_page_load_timeout(60)
        driver.get(CBE_DATA_URL)

        data_anchor_text = "متوسط العائد المرجح (%)"
        wait_xpath = f"//*[contains(text(), '{data_anchor_text}')]"
        
        wait = WebDriverWait(driver, 45)
        wait.until(EC.presence_of_element_located((By.XPATH, wait_xpath)))

        page_source = driver.page_source
        all_dfs = pd.read_html(StringIO(page_source))

        tenors_table = next((df for df in all_dfs if not df.empty), None)
        if tenors_table is None or tenors_table.empty:
            raise ValueError("Could not find the first table for tenors.")
        tenors_list = tenors_table.iloc[:, 0].tolist()
        
        target_df = None
        for df in all_dfs:
            if not df.empty and df.to_string().find(data_anchor_text) != -1:
                if df.iloc[:, 0].str.contains(data_anchor_text, regex=False, na=False).any():
                    target_df = df.copy()

        if target_df is None:
            raise ValueError("Could not find any table containing the required yield data.")
        
        yield_row_df = target_df[target_df.iloc[:, 0].str.contains(data_anchor_text, regex=False, na=False)]
        if yield_row_df.empty:
            raise ValueError("Could not find the yield row in the target table.")

        yields_list = yield_row_df.iloc[0, 1:].tolist()
        if len(tenors_list) != len(yields_list):
            raise ValueError(f"Data mismatch: Found {len(tenors_list)} tenors and {len(yields_list)} yields.")

        initial_df = pd.DataFrame({
            'المدة (الأيام)': tenors_list,
            'العائد (%)': yields_list
        })

        initial_df['المدة (الأيام)'] = pd.to_numeric(initial_df['المدة (الأيام)'], errors='coerce')
        initial_df['العائد (%)'] = pd.to_numeric(initial_df['العائد (%)'], errors='coerce')
        initial_df.dropna(subset=['المدة (الأيام)', 'العائد (%)'], inplace=True)
        initial_df['المدة (الأيام)'] = initial_df['المدة (الأيام)'].astype(int)

        if 182 in initial_df['المدة (الأيام)'].values and 364 in initial_df['المدة (الأيام)'].values:
            print("⚙️ INFO: Applying the observed mapping correction for 182 and 364 day tenors...")
            yield_for_182_incorrect = initial_df.loc[initial_df['المدة (الأيام)'] == 182, 'العائد (%)'].iloc[0]
            yield_for_364_incorrect = initial_df.loc[initial_df['المدة (الأيام)'] == 364, 'العائد (%)'].iloc[0]
            
            initial_df.loc[initial_df['المدة (الأيام)'] == 182, 'العائد (%)'] = yield_for_364_incorrect
            initial_df.loc[initial_df['المدة (الأيام)'] == 364, 'العائد (%)'] = yield_for_182_incorrect
            print("✅ INFO: Correction applied successfully.")
        
        final_df = initial_df.sort_values('المدة (الأيام)').reset_index(drop=True)
        final_df.rename(columns={'العائد (%)': YIELD_COLUMN_NAME}, inplace=True)
        final_df[DATE_COLUMN_NAME] = datetime.now().strftime("%Y-%m-%d")

        # --- SQLite Database Insertion ---
        conn = sqlite3.connect(DB_FILENAME)
        cursor = conn.cursor()
        
        # Use INSERT OR REPLACE to add new data or update existing data for the same date/tenor
        for index, row in final_df.iterrows():
            cursor.execute(f"""
                INSERT OR REPLACE INTO {TABLE_NAME} (
                    "{DATE_COLUMN_NAME}", "{TENOR_COLUMN_NAME}", "{YIELD_COLUMN_NAME}"
                ) VALUES (?, ?, ?)
            """, (row[DATE_COLUMN_NAME], row[TENOR_COLUMN_NAME], row[YIELD_COLUMN_NAME]))
        
        conn.commit()
        conn.close()
        print(f"✅ INFO: Data for {final_df[DATE_COLUMN_NAME].iloc[0]} successfully saved to SQLite.")

        return final_df, 'SUCCESS', "تم تحديث البيانات بنجاح من الجدول الصحيح وحفظها!", datetime.now().strftime("%Y-%m-%d")

    except Exception as e:
        traceback.print_exc()
        return None, 'ERROR', f"خطأ أثناء جلب البيانات: {e}", None
    finally:
        if driver:
            print("🚪 INFO: Closing Selenium WebDriver.")
            driver.quit()

# --- MODIFIED: load_data to use SQLite ---
def load_data():
    """Loads the latest data from the SQLite database."""
    if not os.path.exists(DB_FILENAME):
        return pd.DataFrame(INITIAL_DATA), "البيانات الأولية (قاعدة بيانات غير موجودة)"

    try:
        conn = sqlite3.connect(DB_FILENAME)
        # Find the most recent date in the database
        latest_date_query = f'SELECT MAX("{DATE_COLUMN_NAME}") FROM {TABLE_NAME}'
        latest_date = pd.read_sql_query(latest_date_query, conn).iloc[0, 0]

        if latest_date is None:
            conn.close()
            return pd.DataFrame(INITIAL_DATA), "البيانات الأولية (قاعدة بيانات فارغة)"
        
        # Fetch all records for the most recent date
        query = f'SELECT * FROM {TABLE_NAME} WHERE "{DATE_COLUMN_NAME}" = ?'
        latest_df = pd.read_sql_query(query, conn, params=(latest_date,))
        conn.close()

        file_mod_time = os.path.getmtime(DB_FILENAME)
        last_update = datetime.fromtimestamp(file_mod_time).strftime("%d-%m-%Y %H:%M")
        
        return latest_df, last_update
    except Exception as e:
        traceback.print_exc()
        return pd.DataFrame(INITIAL_DATA), f"خطأ في تحميل البيانات: {e}"

# --- 3. Calculation Logic Functions (Unchanged) ---

def calculate_primary_yield(investment_amount, tenor, yield_rate, tax_rate):
    """
    Calculates the net return for a primary T-bill investment.
    Separates calculation logic from the UI.
    """
    annual_yield_decimal = yield_rate / 100.0
    gross_return = investment_amount * (annual_yield_decimal / DAYS_IN_YEAR) * tenor
    tax_amount = gross_return * (tax_rate / 100.0)
    net_return = gross_return - tax_amount
    total_payout = investment_amount + net_return
    return {
        "gross_return": gross_return,
        "tax_amount": tax_amount,
        "net_return": net_return,
        "total_payout": total_payout
    }

def analyze_secondary_sale(face_value, original_yield, original_tenor, holding_days, secondary_yield, tax_rate):
    """
    Analyzes the outcome of selling a T-bill on the secondary market.
    Separates calculation logic from the UI.
    """
    original_purchase_price = face_value / (1 + (original_yield / 100 * original_tenor / DAYS_IN_YEAR))
    remaining_days = original_tenor - holding_days
    
    if remaining_days <= 0:
        return {"error": "أيام الاحتفاظ يجب أن تكون أقل من أجل الإذن الأصلي."}

    sale_price = face_value / (1 + (secondary_yield / 100 * remaining_days / DAYS_IN_YEAR))
    gross_profit = sale_price - original_purchase_price
    tax_amount = max(0, gross_profit * (tax_rate / 100.0))
    net_profit = gross_profit - tax_amount
    annualized_yield = (net_profit / original_purchase_price) * (DAYS_IN_YEAR / holding_days) * 100 if holding_days > 0 else 0
    
    return {
        "error": None,
        "sale_price": sale_price,
        "gross_profit": gross_profit,
        "tax_amount": tax_amount,
        "net_profit": net_profit,
        "annualized_yield": annualized_yield,
        "original_purchase_price": original_purchase_price
    }

# --- 4. Streamlit App Layout ---
st.set_page_config(layout="wide", page_title="حاسبة أذون الخزانة", page_icon="🏦")

# --- Initialize Database on App Start ---
init_sqlite_db()

# --- Global Style (Unchanged) ---
st.markdown("""<style> @import url('https://fonts.googleapis.com/css2?family=Cairo:wght@400;700&display=swap'); html, body, [class*="st-"], button, input, textarea, select { direction: rtl !important; text-align: right !important; font-family: 'Cairo', sans-serif !important; box-sizing: border-box; } h1, h2, h3, h4, h5, h6 { font-weight: 700 !important; } .main > div { background-color: #f0f2f6; } .st-emotion-cache-1r6slb0 { box-shadow: 0 4px 12px 0 rgba(0,0,0,0.1) !important; border-radius: 15px !important; border: 1px solid #495057 !important; padding: 25px !important; height: 100%; background-color: #343a40 !important; color: #f8f9fa !importante; } div[data-testid="stMetric"] { text-align: center; } div[data-testid="stMetricValue"] { color: #f8f9fa !important; font-size: 1.15rem !important; padding: 0 !important; margin: 0 !important; } div[data-testid="stMetricLabel"] { color: #adb5bd !important; font-size: 0.75rem !important; white-space: normal; word-wrap: break-word; padding: 0 !important; margin-top: 5px !important; } .app-title { text-align: center !important; padding: 1.5rem 1rem; background-color: #343a40; border-radius: 15px; margin-bottom: 1rem; box-shadow: 0 4px 12px 0 rgba(0,0,0,0.1) !important; } .app-title h1 { color: #ffffff !important; } .app-title p { color: #dee2e6 !important; } </style>""", unsafe_allow_html=True)

# --- Header (Unchanged) ---
st.markdown(f""" <div class="app-title"> <h1>{prepare_arabic_text("🏦 حاسبة أذون الخزانة")}</h1> <p>{prepare_arabic_text("تطبيق تفاعلي لحساب وتحليل عوائد أذون الخزانة")}</p> </div> """, unsafe_allow_html=True)

# --- Data Loading ---
if 'df_data' not in st.session_state:
    st.session_state.df_data, st.session_state.last_update = load_data()
data_df = st.session_state.df_data

# --- Top Row: Key Metrics & Update Section (Unchanged) ---
top_col1, top_col2 = st.columns(2, gap="large")

with top_col1:
    with st.container(border=True):
        st.subheader(prepare_arabic_text("📊 أحدث العوائد المعتمدة"), anchor=False)
        if not data_df.empty and TENOR_COLUMN_NAME in data_df.columns and YIELD_COLUMN_NAME in data_df.columns:
            sorted_tenors = sorted(data_df[TENOR_COLUMN_NAME].unique())
            cols = st.columns(len(sorted_tenors) if sorted_tenors else 1)
            tenor_icons = {91: "⏳", 182: "🗓️", 273: "📆", 364: "🗓️✨"}
            for i, tenor in enumerate(sorted_tenors):
                with cols[i]:
                    icon = tenor_icons.get(tenor, "🪙")
                    rate = data_df[data_df[TENOR_COLUMN_NAME] == tenor][YIELD_COLUMN_NAME].iloc[0]
                    st.metric(label=prepare_arabic_text(f"{icon} أجل {tenor} يوم"), value=f"{rate:.3f}%")
        else:
            st.warning(prepare_arabic_text("لم يتم تحميل البيانات أو أن البيانات غير مكتملة."))

with top_col2:
    with st.container(border=True):
        st.subheader(prepare_arabic_text("📡 حالة الاتصال بالبنك المركزي"), anchor=False)
        cairo_tz = pytz.timezone('Africa/Cairo')
        now_cairo = datetime.now(cairo_tz)
        days_ar = {'Monday':'الإثنين','Tuesday':'الثلاثاء','Wednesday':'الأربعاء','Thursday':'الخميس','Friday':'الجمعة','Saturday':'السبت','Sunday':'الأحد'}
        day_name_en = now_cairo.strftime('%A')
        day_name_ar = days_ar.get(day_name_en, day_name_en)
        current_time_str = now_cairo.strftime(f"%Y/%m/%d | %H:%M")
        
        st.write(f"{prepare_arabic_text('**التوقيت المحلي (القاهرة):**')} {prepare_arabic_text(day_name_ar)}، {current_time_str}")
        st.write(f"{prepare_arabic_text('**آخر تحديث مسجل:**')} {st.session_state.last_update}")
        
        if st.button(prepare_arabic_text("🔄 جلب أحدث البيانات"), use_container_width=True, type="primary"):
            with st.spinner(prepare_arabic_text("جاري تشغيل المتصفح لجلب البيانات...")):
                new_df, status, message, update_time = fetch_data_from_cbe()
                if status == 'SUCCESS':
                    st.session_state.df_data = new_df
                    st.session_state.last_update = datetime.now(cairo_tz).strftime("%d-%m-%Y %H:%M")
                    st.toast(prepare_arabic_text("✅ تم التحديث بنجاح!"), icon="✅")
                    st.rerun() 
                else:
                    st.error(prepare_arabic_text(f"⚠️ {message}"), icon="⚠️")
        
        st.link_button(prepare_arabic_text("🔗 فتح موقع البنك"), CBE_DATA_URL, use_container_width=True)

st.divider()

# --- Main Calculator Section (Unchanged) ---
st.header(prepare_arabic_text("🧮 حاسبة العائد الأساسية"))
col_form_main, col_results_main = st.columns(2, gap="large")

with col_form_main:
    with st.container(border=True):
        st.subheader(prepare_arabic_text("1. أدخل بيانات الاستثمار"), anchor=False)
        investment_amount_main = st.number_input(prepare_arabic_text("المبلغ المستثمر (بالجنيه)"), min_value=1000.0, value=100000.0, step=1000.0, key="main_investment")
        
        if TENOR_COLUMN_NAME in data_df.columns and not data_df[TENOR_COLUMN_NAME].empty:
            options = sorted(data_df[TENOR_COLUMN_NAME].unique())
            selected_tenor_main = st.selectbox(prepare_arabic_text("اختر مدة الاستحقاق (بالأيام)"), options=options, key="main_tenor")
        else:
            selected_tenor_main = st.selectbox(prepare_arabic_text("اختر مدة الاستحقاق (بالأيام)"), options=[91, 182, 273, 364], key="main_tenor")

        tax_rate_main = st.number_input(prepare_arabic_text("نسبة الضريبة على الأرباح (%)"), min_value=0.0, max_value=100.0, value=DEFAULT_TAX_RATE_PERCENT, step=0.5, format="%.1f", key="main_tax")

        st.subheader(prepare_arabic_text("2. قم بحساب العائد"), anchor=False)
        calculate_button_main = st.button(prepare_arabic_text("احسب العائد الآن"), use_container_width=True, type="primary", key="main_calc")

results_placeholder_main = col_results_main.empty()

if calculate_button_main:
    if not data_df.empty and TENOR_COLUMN_NAME in data_df.columns and YIELD_COLUMN_NAME in data_df.columns:
        yield_rate_row = data_df[data_df[TENOR_COLUMN_NAME] == selected_tenor_main]
        if not yield_rate_row.empty:
            yield_rate = yield_rate_row[YIELD_COLUMN_NAME].iloc[0]
            
            results = calculate_primary_yield(investment_amount_main, selected_tenor_main, yield_rate, tax_rate_main)
            
            with results_placeholder_main.container(border=True):
                st.subheader(prepare_arabic_text(f"✨ تفاصيل أجل {selected_tenor_main} يوم"), anchor=False)
                st.markdown(f'<p style="font-size: 1.0rem; color: #adb5bd;">{prepare_arabic_text("العائد الصافي بعد الضريبة")}</p><p style="font-size: 2.0rem; color: #49c57a; font-weight: 700;">{results["net_return"]:,.2f} {prepare_arabic_text("جنيه")}</p>', unsafe_allow_html=True)
                st.markdown('<hr style="border-color: #495057;">', unsafe_allow_html=True)
                st.markdown(f'<table style="width:100%; font-size: 1.0rem;"><tr><td style="padding-bottom: 8px;">{prepare_arabic_text("💰 المبلغ المستثمر")}</td><td style="text-align:left;">{investment_amount_main:,.2f} {prepare_arabic_text("جنيه")}</td></tr><tr><td style="padding-bottom: 8px; color: #8ab4f8;">{prepare_arabic_text("📈 العائد الإجمالي")}</td><td style="text-align:left; color: #8ab4f8;">{results["gross_return"]:,.2f} {prepare_arabic_text("جنيه")}</td></tr><tr><td style="padding-bottom: 15px; color: #f28b82;">{prepare_arabic_text(f"💸 ضريبة الأرباح ({tax_rate_main}%)")}</td><td style="text-align:left; color: #f28b82;">- {results["tax_amount"]:,.2f} {prepare_arabic_text("جنيه")}</td></tr></table>', unsafe_allow_html=True)
                st.markdown(f'<div style="background-color: #495057; padding: 10px; border-radius: 8px; display: flex; justify-content: space-between; align-items: center;"><span style="font-size: 1.1rem;">{prepare_arabic_text("🏦 إجمالي المستلم")}</span><span style="font-size: 1.2rem;">{results["total_payout"]:,.2f} {prepare_arabic_text("جنيه")}</span></div>', unsafe_allow_html=True)
        else:
             with results_placeholder_main.container(border=True):
                st.error(prepare_arabic_text("لم يتم العثور على عائد للأجل المحدد."))
else:
    with results_placeholder_main.container(border=True):
        st.info(prepare_arabic_text("✨ نتائج العائد الأساسي ستظهر هنا بعد ملء النموذج والضغط على زر الحساب."))


# --- Secondary Market Sale Calculator (NOW FULLY UPGRADED) ---
st.divider()
st.header(prepare_arabic_text("⚖️ حاسبة البيع في السوق الثانوي"))
col_secondary_form, col_secondary_results = st.columns(2, gap="large")

with col_secondary_form:
    with st.container(border=True):
        st.subheader(prepare_arabic_text("1. أدخل بيانات الإذن الأصلي"), anchor=False)
        face_value_secondary = st.number_input(prepare_arabic_text("القيمة الإسمية للإذن"), min_value=1000.0, value=100000.0, step=1000.0, key="secondary_face_value")
        original_yield_secondary = st.number_input(prepare_arabic_text("عائد الشراء الأصلي (%)"), min_value=1.0, value=29.0, step=0.1, key="secondary_original_yield", format="%.3f")
        
        if TENOR_COLUMN_NAME in data_df.columns and not data_df[TENOR_COLUMN_NAME].empty:
            options = sorted(data_df[TENOR_COLUMN_NAME].unique())
            original_tenor_secondary = st.selectbox(prepare_arabic_text("أجل الإذن الأصلي (بالأيام)"), options=options, key="secondary_tenor", index=0)
        else:
            original_tenor_secondary = st.selectbox(prepare_arabic_text("أجل الإذن الأصلي (بالأيام)"), options=[91, 182, 273, 364], key="secondary_tenor", index=0)

        tax_rate_secondary = st.number_input(prepare_arabic_text("نسبة الضريبة على الأرباح (%)"), min_value=0.0, max_value=100.0, value=DEFAULT_TAX_RATE_PERCENT, step=0.5, format="%.1f", key="secondary_tax")

        st.subheader(prepare_arabic_text("2. أدخل تفاصيل البيع"), anchor=False)
        early_sale_days_secondary = st.number_input(prepare_arabic_text("أيام الاحتفاظ الفعلية (قبل البيع)"), min_value=1, value=min(60, original_tenor_secondary -1 if original_tenor_secondary > 1 else 1), max_value=original_tenor_secondary - 1 if original_tenor_secondary > 1 else 1, step=1)
        secondary_market_yield = st.number_input(prepare_arabic_text("العائد السائد في السوق للمشتري (%)"), min_value=1.0, value=30.0, step=0.1, format="%.3f")
        
        st.subheader(prepare_arabic_text("3. قم بتحليل قرار البيع"), anchor=False)
        calc_secondary_sale_button = st.button(prepare_arabic_text("حلل سعر البيع الثانوي"), use_container_width=True, type="primary", key="secondary_calc")

secondary_results_placeholder = col_secondary_results.empty()

if calc_secondary_sale_button:
    results = analyze_secondary_sale(face_value_secondary, original_yield_secondary, original_tenor_secondary, early_sale_days_secondary, secondary_market_yield, tax_rate_secondary)

    if results["error"]:
        secondary_results_placeholder.error(prepare_arabic_text(results["error"]))
    else:
        with secondary_results_placeholder.container(border=True):
            st.subheader(prepare_arabic_text("✨ تحليل سعر البيع الثانوي"), anchor=False)
            c1, c2 = st.columns(2)
            c1.metric(label=prepare_arabic_text("🏷️ سعر البيع الفعلي للإذن"), value=f"{results['sale_price']:,.2f} جنيه")
            c2.metric(label=prepare_arabic_text("💰 صافي الربح / الخسارة"), value=f"{results['net_profit']:,.2f} جنيه", delta=f"{results['annualized_yield']:.2f}% سنوياً")
            
            st.markdown('<hr style="border-color: #495057;">', unsafe_allow_html=True)
            st.markdown(f"<h6 style='text-align:center; color:#dee2e6;'>{prepare_arabic_text('تفاصيل حساب الضريبة')}</h6>", unsafe_allow_html=True)
            if results['gross_profit'] > 0:
                 st.markdown(f""" <table style="width:100%; font-size: 0.9rem;  text-align:center;"> <tr> <td style="color: #8ab4f8;">{prepare_arabic_text('إجمالي الربح الخاضع للضريبة')}</td> <td style="color: #f28b82;">{prepare_arabic_text(f'قيمة الضريبة ({tax_rate_secondary}%)')}</td> <td style="color: #49c57a;">{prepare_arabic_text('صافي الربح بعد الضريبة')}</td> </tr> <tr> <td style="font-size: 1.1rem; color: #8ab4f8;">{results['gross_profit']:,.2f}</td> <td style="font-size: 1.1rem; color: #f28b82;">- {results['tax_amount']:,.2f}</td> <td style="font-size: 1.1rem; color: #49c57a;">{results['net_profit']:,.2f}</td> </tr> </table> """, unsafe_allow_html=True)
            else:
                 st.info(prepare_arabic_text("لا توجد ضريبة على الخسائر الرأسمالية."), icon="ℹ️")

            # --- UPGRADED: Decision Card ---
            st.markdown('<hr style="border-color: #495057;">', unsafe_allow_html=True)
            net_profit = results['net_profit']
            
            if net_profit > 0:
                decision_html = f"""
                <div style="background-color: #1e4620; padding: 15px; border-radius: 8px; border: 1px solid #49c57a; text-align: right;">
                    <h5 style="color: #ffffff; margin-bottom: 10px;">{prepare_arabic_text("✅ قرار البيع: مربح")}</h5>
                    <p style="color: #e0e0e0; font-size: 0.95rem; margin-bottom: 10px;">
                        {prepare_arabic_text(f"البيع الآن سيحقق لك <b>ربحاً صافياً</b> قدره <b>{net_profit:,.2f} جنيه</b>.")}
                        <br>
                        <small>{prepare_arabic_text("حدث هذا الربح لأن العائد السائد في السوق حالياً أقل من عائد شرائك الأصلي.")}</small>
                    </p>
                    <p style="color: #ffffff; font-size: 1rem; margin-bottom: 0;">
                        <b>{prepare_arabic_text("النصيحة:")}</b> {prepare_arabic_text("قد يكون البيع خياراً جيداً إذا كنت بحاجة للسيولة، أو وجدت فرصة استثمارية أخرى بعائد أعلى.")}
                    </p>
                </div>
                """
                st.markdown(decision_html, unsafe_allow_html=True)
            elif net_profit < 0:
                loss_value = abs(net_profit)
                decision_html = f"""
                <div style="background-color: #4a2a2a; padding: 15px; border-radius: 8px; border: 1px solid #f28b82; text-align: right;">
                    <h5 style="color: #ffffff; margin-bottom: 10px;">{prepare_arabic_text("⚠️ قرار البيع: غير مربح")}</h5>
                    <p style="color: #e0e0e0; font-size: 0.95rem; margin-bottom: 10px;">
                        {prepare_arabic_text(f"البيع الآن سيتسبب في <b>خسارة صافية</b> قدرها <b>{loss_value:,.2f} جنيه</b>.")}
                        <br>
                        <small>{prepare_arabic_text("حدثت هذه الخسارة لأن العائد السائد في السوق حالياً أعلى من عائد شرائك الأصلي.")}</small>
                    </p>
                    <p style="color: #ffffff; font-size: 1rem; margin-bottom: 0;">
                        <b>{prepare_arabic_text("النصيحة:")}</b> {prepare_arabic_text("يُنصح بالانتظار حتى تاريخ الاستحقاق لتجنب هذه الخسارة وتحقيق عائدك الأصلي.")}
                    </p>
                </div>
                """
                st.markdown(decision_html, unsafe_allow_html=True)
            else: # net_profit is zero
                decision_html = f"""
                <div style="background-color: #2a394a; padding: 15px; border-radius: 8px; border: 1px solid #8ab4f8; text-align: right;">
                    <h5 style="color: #ffffff; margin-bottom: 10px;">{prepare_arabic_text("⚖️ قرار البيع: متعادل")}</h5>
                    <p style="color: #e0e0e0; font-size: 0.95rem; margin-bottom: 10px;">
                        {prepare_arabic_text("البيع الآن لن ينتج عنه أي ربح أو خسارة.")}
                    </p>
                    <p style="color: #ffffff; font-size: 1rem; margin-bottom: 0;">
                        <b>{prepare_arabic_text("النصيحة:")}</b> {prepare_arabic_text("يمكنك البيع إذا كنت بحاجة لاسترداد قيمة الشراء مبكراً دون أي تغيير في قيمتها.")}
                    </p>
                </div>
                """
                st.markdown(decision_html, unsafe_allow_html=True)

else:
    with secondary_results_placeholder.container(border=True):
        st.info(prepare_arabic_text("✨ أدخل بيانات البيع في النموذج لتحليل قرارك."))


# --- Help Section (Unchanged) ---
st.divider()
with st.expander(prepare_arabic_text("💡 شرح ومساعدة (أسئلة شائعة)")):
    st.markdown(prepare_arabic_text("""
    #### **ما الفرق بين "العائد" و "الفائدة"؟**
    - **الفائدة (Interest):** تُحسب على أصل المبلغ وتُضاف إليه دورياً (مثل شهادات الادخار).
    - **العائد (Yield):** في أذون الخزانة، أنت تشتري الإذن بسعر **أقل** من قيمته الإسمية (مثلاً تشتريه بـ 975 وهو يساوي 1000)، وربحك هو الفارق الذي ستحصل عليه في نهاية المدة. الحاسبة تحول هذا الفارق إلى نسبة مئوية سنوية لتسهيل المقارنة.
    ---
    #### **كيف تعمل حاسبة العائد الأساسية؟**
    هذه الحاسبة تجيب على سؤال: "كم سأربح إذا احتفظت بالإذن حتى نهاية مدته؟".
    1.  **حساب إجمالي الربح:** `المبلغ المستثمر × (العائد ÷ 100) × (مدة الإذن ÷ 365)`
    2.  **حساب الضريبة:** `إجمالي الربح × (نسبة الضريبة ÷ 100)`
    3.  **حساب صافي الربح:** `إجمالي الربح - قيمة الضريبة`
    4.  **إجمالي المستلم:** `المبلغ المستثمر + صافي الربح`
    ---
    #### **كيف تعمل حاسبة البيع في السوق الثانوي؟**
    هذه الحاسبة تجيب على سؤال: "كم سيكون ربحي أو خسارتي إذا بعت الإذن اليوم قبل تاريخ استحقاقه؟". سعر البيع هنا لا يعتمد على سعر شرائك، بل على سعر الفائدة **الحالي** في السوق.
    1.  **حساب سعر شرائك الأصلي:** `سعر الشراء = القيمة الإسمية ÷ (1 + (عائد الشراء ÷ 100) × (الأجل الأصلي ÷ 365))`
    2.  **حساب سعر البيع اليوم:** `الأيام المتبقية = الأجل الأصلي - أيام الاحتفاظ`، `سعر البيع = القيمة الإسمية ÷ (1 + (العائد السائد ÷ 100) × (الأيام المتبقية ÷ 365))`
    3.  **النتيجة النهائية:** `الربح أو الخسارة = سعر البيع - سعر الشراء الأصلي`. يتم حساب الضريبة على هذا الربح إذا كان موجباً.
    ---
    ***إخلاء مسؤولية:*** *هذا التطبيق هو أداة استرشادية فقط، والأرقام الناتجة هي تقديرات. للحصول على أرقام نهائية ودقيقة، يرجى الرجوع إلى البنك أو المؤسسة المالية التي تتعامل معها.*
    """))
