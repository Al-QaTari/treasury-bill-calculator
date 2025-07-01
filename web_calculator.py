# --- 1. Import Libraries ---
import streamlit as st
import pandas as pd
from io import StringIO
from datetime import datetime
import os
import time
import requests
from bs4 import BeautifulSoup
import re
import arabic_reshaper
from bidi.algorithm import get_display
import traceback
import pytz
# --- Import Selenium for advanced web scraping ---
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# --- 2. Define Constants and Helper Functions ---
YIELD_COLUMN_NAME = "العائد (%)"
TENOR_COLUMN_NAME = "المدة (الأيام)"
CSV_FILENAME = "cbe_tbill_rates_processed.csv"
CBE_DATA_URL = "https://www.cbe.org.eg/ar/auctions/egp-t-bills"

# بيانات أولية في حالة عدم توفر ملف
INITIAL_DATA = {
    TENOR_COLUMN_NAME: [91, 182, 273, 364],
    YIELD_COLUMN_NAME: [30.108, 30.274, 30.184, 29.230]
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

# --- FINAL AUTOMATED FUNCTION v13: REMOVED HARDCODED PATH ---
def fetch_data_from_cbe():
    """
    Fetches T-Bill auction results using Selenium, allowing Streamlit to manage the webdriver.
    """
    print("INFO: Initializing Selenium WebDriver...")
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    
    driver = None
    try:
        # Let Streamlit handle the driver setup automatically
        # No need for hardcoded paths or Service object
        driver = webdriver.Firefox(options=options)
        
        print(f"INFO: Navigating to {CBE_DATA_URL}")
        driver.get(CBE_DATA_URL)

        # --- Define XPaths provided by the user ---
        header_table_xpath = "/html/body/div[1]/section[3]/div[2]/div[2]/table"
        results_row_xpath = "/html/body/div[1]/section[3]/div[4]/div[2]/table/tbody/tr[5]"
        
        print("INFO: Waiting for page elements to load...")
        wait = WebDriverWait(driver, 30)
        wait.until(EC.presence_of_element_located((By.XPATH, header_table_xpath)))
        wait.until(EC.presence_of_element_located((By.XPATH, results_row_xpath)))
        
        # --- 1. Extract Headers ---
        print("INFO: Extracting headers from header table...")
        header_table_element = driver.find_element(By.XPATH, header_table_xpath)
        header_html = header_table_element.get_attribute('outerHTML')
        header_df = pd.read_html(StringIO(header_html), header=0)[0]
        tenors_raw = header_df.columns[1:].tolist() # Get all column names except the first one

        # --- 2. Extract Data ---
        print("INFO: Extracting data from results table...")
        results_table_element = driver.find_element(By.XPATH, results_row_xpath + "/ancestor::table")
        results_html = results_table_element.get_attribute('outerHTML')
        results_df = pd.read_html(StringIO(results_html))[0]

        search_phrase = "متوسط العائد المرجح"
        yield_row_df = results_df[results_df.iloc[:, 0].astype(str).str.contains(search_phrase, na=False)]
        
        if yield_row_df.empty:
            msg = f"تم العثور على الجداول، ولكن لم يتم العثور على صف '{search_phrase}'."
            return None, 'NO_DATA_FOUND', msg, None

        yields = yield_row_df.iloc[0, 1:].values

        # --- 3. Combine and Process ---
        if len(tenors_raw) != len(yields):
            msg = "عدد الآجال لا يتطابق مع عدد العوائد. تغير هيكل الموقع."
            return None, 'STRUCTURE_CHANGED', msg, None

        final_df = pd.DataFrame({
            'RawTenor': tenors_raw,
            YIELD_COLUMN_NAME: yields
        })
        
        final_df[TENOR_COLUMN_NAME] = final_df['RawTenor'].astype(str).str.extract(r'(\d+)', expand=False)
        final_df = final_df.drop(columns=['RawTenor'])
        
        final_df = final_df.dropna().copy()
        final_df[YIELD_COLUMN_NAME] = pd.to_numeric(final_df[YIELD_COLUMN_NAME], errors='coerce')
        final_df[TENOR_COLUMN_NAME] = pd.to_numeric(final_df[TENOR_COLUMN_NAME], errors='coerce')
        final_df.dropna(inplace=True)
        
        if not final_df.empty:
            final_df[TENOR_COLUMN_NAME] = final_df[TENOR_COLUMN_NAME].astype(int)
            final_df.sort_values(TENOR_COLUMN_NAME, inplace=True)
            final_df.rename(columns={YIELD_COLUMN_NAME: "متوسط العائد المرجح المقبول (%)"}, inplace=True)
            
            # --- 4. Save new CSV file ---
            final_df.to_csv(CSV_FILENAME, index=False, encoding='utf-8-sig')
            print(f"INFO: Successfully created new data file: {CSV_FILENAME}")
            return final_df, 'SUCCESS', "تم تحديث البيانات بنجاح!", datetime.now().strftime("%Y-%m-%d")
            
        return None, 'NO_DATA_FOUND', "لم يتم العثور على بيانات صالحة بعد المعالجة.", None

    except Exception as e:
        traceback.print_exc()
        msg = f"خطأ غير متوقع أثناء التشغيل الآلي: {e}"
        return None, 'SELENIUM_ERROR', msg, None
    finally:
        if driver:
            print("INFO: Closing Selenium WebDriver.")
            driver.quit()

# --- 3. Streamlit App Layout (Aesthetic UI) ---
st.set_page_config(layout="wide", page_title="حاسبة أذون الخزانة", page_icon="🏦")

# --- Global Style for RTL, Fonts, and Shadows ---
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Cairo:wght@400;700&display=swap');
    
    html, body, [class*="st-"], button, input, textarea, select {
        direction: rtl !important;
        text-align: right !important;
        font-family: 'Cairo', sans-serif !important;
        box-sizing: border-box;
    }
    h1, h2, h3, h4, h5, h6 { font-weight: 700 !important; }
    
    .main > div {
        background-color: #f0f2f6;
    }

    .st-emotion-cache-1r6slb0 {
        box-shadow: 0 4px 12px 0 rgba(0,0,0,0.1) !important;
        border-radius: 15px !important;
        border: 1px solid #495057 !important;
        padding: 25px !important;
        height: 100%;
        background-color: #343a40 !important;
        color: #f8f9fa !important;
    }

    div[data-testid="stMetric"] {
        text-align: center;
    }
    div[data-testid="stMetricValue"] { 
        color: #f8f9fa !important; 
        font-size: 1.15rem !important;
        padding: 0 !important;
        margin: 0 !important;
    }
    div[data-testid="stMetricLabel"] { 
        color: #adb5bd !important;
        font-size: 0.75rem !important;
        white-space: normal; 
        word-wrap: break-word;
        padding: 0 !important;
        margin-top: 5px !important;
    }
    
    .app-title { 
        text-align: center !important; 
        padding: 1.5rem 1rem;
        background-color: #343a40;
        border-radius: 15px;
        margin-bottom: 1rem;
        box-shadow: 0 4px 12px 0 rgba(0,0,0,0.1) !important;
    }
    .app-title h1 { color: #ffffff !important; }
    .app-title p { color: #dee2e6 !important; }
    </style>
""", unsafe_allow_html=True)


# --- Header ---
st.markdown(f"""
<div class="app-title">
    <h1>{prepare_arabic_text("🏦 حاسبة أذون الخزانة")}</h1>
    <p>{prepare_arabic_text("تطبيق تفاعلي لحساب وتحليل عوائد أذون الخزانة")}</p>
</div>
""", unsafe_allow_html=True)

# Re-define the original column name for display purposes
YIELD_COLUMN_NAME = "متوسط العائد المرجح المقبول (%)"

# Initialize session state
if 'df_data' not in st.session_state:
    if os.path.exists(CSV_FILENAME):
        try:
            st.session_state.df_data = pd.read_csv(CSV_FILENAME, encoding='utf-8-sig')
            file_mod_time = os.path.getmtime(CSV_FILENAME)
            st.session_state.last_update = datetime.fromtimestamp(file_mod_time).strftime("%d-%m-%Y %H:%M")
        except Exception:
            st.session_state.df_data = pd.DataFrame(INITIAL_DATA)
            st.session_state.last_update = "البيانات الأولية"
    else:
        st.session_state.df_data = pd.DataFrame(INITIAL_DATA)
        st.session_state.last_update = "البيانات الأولية"
data_df = st.session_state.df_data

# --- Top Row: Key Metrics & Update Section ---
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

# --- Restored Automated UI ---
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
        st.markdown("<br>", unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        with c1:
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
        with c2:
            st.link_button(prepare_arabic_text("🔗 فتح موقع البنك"), CBE_DATA_URL, use_container_width=True)


st.divider()

# --- Main Calculator Section ---
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
            st.warning("لم يتم تحميل الآجال، يتم استخدام القيم الافتراضية.")

        st.subheader(prepare_arabic_text("2. قم بحساب العائد"), anchor=False)
        calculate_button_main = st.button(prepare_arabic_text("احسب العائد الآن"), use_container_width=True, type="primary", key="main_calc")

results_placeholder_main = col_results_main.empty()

if calculate_button_main:
    if not data_df.empty and TENOR_COLUMN_NAME in data_df.columns and YIELD_COLUMN_NAME in data_df.columns:
        yield_rate_row = data_df[data_df[TENOR_COLUMN_NAME] == selected_tenor_main]
        if not yield_rate_row.empty:
            yield_rate = yield_rate_row[YIELD_COLUMN_NAME].iloc[0]
            annual_yield_decimal = yield_rate / 100.0
            gross_return = investment_amount_main * (annual_yield_decimal / 365.0) * selected_tenor_main
            tax_amount = gross_return * 0.20
            net_return = gross_return - tax_amount
            total_payout = investment_amount_main + net_return
            
            with results_placeholder_main.container(border=True):
                st.subheader(prepare_arabic_text(f"✨ تفاصيل أجل {selected_tenor_main} يوم"), anchor=False)
                st.markdown(f'<p style="font-size: 1.0rem; color: #adb5bd;">{prepare_arabic_text("العائد الصافي بعد الضريبة")}</p><p style="font-size: 2.0rem; color: #49c57a; font-weight: 700;">{net_return:,.2f} {prepare_arabic_text("جنيه")}</p>', unsafe_allow_html=True)
                st.markdown('<hr style="border-color: #495057;">', unsafe_allow_html=True)
                st.markdown(f'<table style="width:100%; font-size: 1.0rem;"><tr><td style="padding-bottom: 8px;">{prepare_arabic_text("💰 المبلغ المستثمر")}</td><td style="text-align:left;">{investment_amount_main:,.2f} {prepare_arabic_text("جنيه")}</td></tr><tr><td style="padding-bottom: 8px; color: #8ab4f8;">{prepare_arabic_text("📈 العائد الإجمالي")}</td><td style="text-align:left; color: #8ab4f8;">{gross_return:,.2f} {prepare_arabic_text("جنيه")}</td></tr><tr><td style="padding-bottom: 15px; color: #f28b82;">{prepare_arabic_text("💸 ضريبة الأرباح (20%)")}</td><td style="text-align:left; color: #f28b82;">- {tax_amount:,.2f} {prepare_arabic_text("جنيه")}</td></tr></table>', unsafe_allow_html=True)
                st.markdown(f'<div style="background-color: #495057; padding: 10px; border-radius: 8px; display: flex; justify-content: space-between; align-items: center;"><span style="font-size: 1.1rem;">{prepare_arabic_text("🏦 إجمالي المستلم")}</span><span style="font-size: 1.2rem;">{total_payout:,.2f} {prepare_arabic_text("جنيه")}</span></div>', unsafe_allow_html=True)
                
                st.markdown('<hr style="border-color: #495057;">', unsafe_allow_html=True)
                st.markdown(f"<h6 style='text-align:center; color:#dee2e6;'>{prepare_arabic_text('مقارنة سريعة مع الآجال الأخرى')}</h6>", unsafe_allow_html=True)
                
                other_tenors = [t for t in sorted(data_df[TENOR_COLUMN_NAME].unique()) if t != selected_tenor_main]
                if other_tenors:
                    cols = st.columns(len(other_tenors))
                    for i, tenor in enumerate(other_tenors):
                        with cols[i]:
                            comp_yield_rate_row = data_df[data_df[TENOR_COLUMN_NAME] == tenor]
                            comp_yield_rate = comp_yield_rate_row[YIELD_COLUMN_NAME].iloc[0]
                            comp_annual_yield_decimal = comp_yield_rate / 100.0
                            comp_gross_return = investment_amount_main * (comp_annual_yield_decimal / 365.0) * tenor
                            comp_net_return = comp_gross_return * 0.80
                            st.metric(label=prepare_arabic_text(f"صافي ربح {tenor} يوم"), value=f"{comp_net_return:,.2f}")
else:
    with results_placeholder_main.container(border=True):
        st.info(prepare_arabic_text("✨ نتائج العائد الأساسي ستظهر هنا بعد ملء النموذج والضغط على زر الحساب."))


st.divider()

# --- Secondary Market Sale Calculator ---
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

        st.subheader(prepare_arabic_text("2. أدخل تفاصيل البيع"), anchor=False)
        early_sale_days_secondary = st.number_input(
            prepare_arabic_text("أيام الاحتفاظ الفعلية (قبل البيع)"),
            min_value=1, value=min(60, original_tenor_secondary -1 if original_tenor_secondary > 1 else 1),
            max_value=original_tenor_secondary - 1 if original_tenor_secondary > 1 else 1,
            step=1
        )
        secondary_market_yield = st.number_input(
            prepare_arabic_text("العائد السائد في السوق للمشتري (%)"),
            min_value=1.0, value=30.0, step=0.1,
            format="%.3f",
            help=prepare_arabic_text("هذا هو العائد الذي يتوقعه مشترٍ جديد في السوق اليوم لشراء إذن له مدة متبقية مماثلة. يتأثر بأسعار الفائدة الحالية.")
        )
        st.subheader(prepare_arabic_text("3. قم بتحليل قرار البيع"), anchor=False)
        calc_secondary_sale_button = st.button(prepare_arabic_text("حلل سعر البيع الثانوي"), use_container_width=True, type="primary", key="secondary_calc")

secondary_results_placeholder = col_secondary_results.empty()

if calc_secondary_sale_button:
    original_purchase_price = face_value_secondary / (1 + (original_yield_secondary / 100 * original_tenor_secondary / 365))
    remaining_days = original_tenor_secondary - early_sale_days_secondary
    
    if remaining_days <= 0:
        secondary_results_placeholder.error(prepare_arabic_text("أيام الاحتفاظ يجب أن تكون أقل من أجل الإذن الأصلي."))
    else:
        sale_price = face_value_secondary / (1 + (secondary_market_yield / 100 * remaining_days / 365))
        gross_profit = sale_price - original_purchase_price
        tax_amount_secondary = max(0, gross_profit * 0.20)
        net_profit = gross_profit - tax_amount_secondary
        annualized_yield_secondary = (net_profit / original_purchase_price) * (365 / early_sale_days_secondary) * 100 if early_sale_days_secondary > 0 else 0
        
        gross_return_full = face_value_secondary - original_purchase_price
        net_return_full = gross_return_full * 0.80
        cost_of_liquidity = net_return_full - net_profit
        percentage_lost = (cost_of_liquidity / net_return_full) * 100 if net_return_full > 0 else 0

        with secondary_results_placeholder.container(border=True):
            st.subheader(prepare_arabic_text("✨ تحليل سعر البيع الثانوي"), anchor=False)
            c1, c2 = st.columns(2)
            c1.metric(label=prepare_arabic_text("🏷️ سعر البيع الفعلي للإذن"), value=f"{sale_price:,.2f} جنيه")
            c2.metric(label=prepare_arabic_text("💰 صافي الربح / الخسارة"), value=f"{net_profit:,.2f} جنيه", delta=f"{annualized_yield_secondary:.2f}% سنوياً")
            st.markdown('<hr style="border-color: #495057;">', unsafe_allow_html=True)
            st.markdown(f"<h6 style='text-align:center; color:#dee2e6;'>{prepare_arabic_text('تفاصيل حساب الضريبة')}</h6>", unsafe_allow_html=True)
            if gross_profit > 0:
                 st.markdown(f"""
                <table style="width:100%; font-size: 0.9rem;  text-align:center;">
                    <tr>
                        <td style="color: #8ab4f8;">{prepare_arabic_text('إجمالي الربح الخاضع للضريبة')}</td>
                        <td style="color: #f28b82;">{prepare_arabic_text('قيمة الضريبة (20%)')}</td>
                        <td style="color: #49c57a;">{prepare_arabic_text('صافي الربح بعد الضريبة')}</td>
                    </tr>
                    <tr>
                        <td style="font-size: 1.1rem; color: #8ab4f8;">{gross_profit:,.2f}</td>
                        <td style="font-size: 1.1rem; color: #f28b82;">- {tax_amount_secondary:,.2f}</td>
                        <td style="font-size: 1.1rem; color: #49c57a;">{net_profit:,.2f}</td>
                    </tr>
                </table>
                """, unsafe_allow_html=True)
            else:
                 st.info(prepare_arabic_text("لا توجد ضريبة على الخسائر الرأسمالية."), icon="ℹ️")
            st.markdown('<hr style="border-color: #495057;">', unsafe_allow_html=True)
            st.markdown(f"""
            <div style="background-color: #593b00; color: #ffebb9; border: 1px solid #856404; text-align:center; padding: 10px; border-radius: 8px; margin-top: 15px;">
                <h6 style="margin-bottom: 5px; color: #ffebb9;">{prepare_arabic_text("💡 ملخص القرار")}</h6>
                <p style="font-size: 0.9rem; line-height: 1.6;">
                {prepare_arabic_text("مقابل الحصول على سيولة فورية، ستتنازل عن مبلغ")} <b style="color: #ffffff; font-size: 1rem;">{cost_of_liquidity:,.2f}</b> {prepare_arabic_text("جنيه")},
                <br>
                {prepare_arabic_text("وهو ما يمثل حوالي")} <b style="color: #ffffff; font-size: 1rem;">{percentage_lost:.1f}%</b> {prepare_arabic_text("من أرباحك الصافية المتوقعة.")}
                </p>
            </div>
            """, unsafe_allow_html=True)
else:
    with secondary_results_placeholder.container(border=True):
        st.info(prepare_arabic_text("✨ أدخل بيانات البيع في النموذج لتحليل قرارك."))

# --- Help Section ---
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
    2.  **حساب الضريبة:** `إجمالي الربح × 0.20`
    3.  **حساب صافي الربح:** `إجمالي الربح - قيمة الضريبة`
    4.  **إجمالي المستلم:** `المبلغ المستثمر + صافي الربح`
    ---
    #### **كيف تعمل حاسبة البيع في السوق الثانوي؟**
    هذه الحاسبة تجيب على سؤال: "كم سيكون ربحي أو خسارتي إذا بعت الإذن اليوم قبل تاريخ استحقاقه؟". سعر البيع هنا لا يعتمد على سعر شرائك، بل على سعر الفائدة **الحالي** في السوق.
    1.  **حساب سعر شرائك الأصلي:** `سعر الشراء = القيمة الإسمية ÷ (1 + (عائد الشراء ÷ 100) × (الأجل الأصلي ÷ 365))`
    2.  **حساب سعر البيع اليوم:** `الأيام المتبقية = الأجل الأصلي - أيام الاحتفاظ`، `سعر البيع = القيمة الإسمية ÷ (1 + (العائد السائد ÷ 100) × (الأيام المتبقية ÷ 365))`
    3.  **النتيجة النهائية:** `الربح أو الخسارة = سعر البيع - سعر الشراء الأصلي`. يتم حساب الضريبة (20%) على هذا الربح إذا كان موجباً.
    ---
    ***إخلاء مسؤولية:*** *هذا التطبيق هو أداة استرشادية فقط، والأرقام الناتجة هي تقديرات. للحصول على أرقام نهائية ودقيقة، يرجى الرجوع إلى البنك أو المؤسسة المالية التي تتعامل معها.*
    """))
