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

# --- 2. Define Constants and Helper Functions ---
YIELD_COLUMN_NAME = "متوسط العائد المرجح المقبول (%)"
TENOR_COLUMN_NAME = "المدة (الأيام)"
CSV_FILENAME = "cbe_tbill_rates_processed.csv"

CBE_DATA_URL = "https://www.cbe.org.eg/ar/auctions/egp-t-bills"

# بيانات أولية في حالة عدم توفر ملف أو فشل الاتصال
INITIAL_DATA = {
    TENOR_COLUMN_NAME: [91, 182, 273, 364],
    YIELD_COLUMN_NAME: [29.108, 28.274, 27.184, 25.230]
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

@st.cache_data(ttl=3600)
def fetch_data_from_cbe():
    """
    Fetches T-Bill auction results from the CBE website.
    This function is cached for 1 hour to avoid excessive requests.
    Returns a status code along with the data and message.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    try:
        print("INFO: Attempting to fetch data directly from CBE source...")
        response = requests.get(CBE_DATA_URL, headers=headers, timeout=20)
        response.raise_for_status()
        
        page_source = response.text
        soup = BeautifulSoup(page_source, 'html.parser')
        
        results_headers = soup.find_all('h2', string=lambda text: text and 'النتائج' in text)
        if not results_headers:
            msg = "لم يتم العثور على قسم 'النتائج' في صفحة البنك المركزي. قد يكون التصميم قد تغير."
            return None, 'STRUCTURE_CHANGED', msg, None

        next_tables = results_headers[-1].find_all_next('table')
        if not next_tables:
            msg = "تم العثور على قسم النتائج ولكن لم يتم العثور على أي جدول بعده."
            return None, 'STRUCTURE_CHANGED', msg, None

        results_table_df = pd.read_html(StringIO(str(next_tables[0])))[0]
        
        accepted_yield_rows = results_table_df[results_table_df.iloc[:, 0].astype(str).str.contains("متوسط العائد المرجح المقبول", na=False)]

        if not accepted_yield_rows.empty:
            yield_row = accepted_yield_rows.iloc[-1]
        else:
            all_yield_rows = results_table_df[results_table_df.iloc[:, 0].astype(str).str.contains("متوسط العائد المرجح", na=False)]
            if not all_yield_rows.empty:
                yield_row = all_yield_rows.iloc[-1]
            else:
                msg = "لا توجد نتائج عطاءات جديدة متاحة حالياً."
                return None, 'NO_DATA_FOUND', msg, None
        
        yield_data_df = pd.DataFrame(yield_row).T
        yield_data_df.columns = results_table_df.columns
        yield_data_df.rename(columns={yield_data_df.columns[0]: 'البيان'}, inplace=True)
        
        melt_vars = [col for col in yield_data_df.columns if col != 'البيان']
        df_unpivoted = pd.melt(yield_data_df, id_vars=['البيان'], value_vars=melt_vars, var_name=TENOR_COLUMN_NAME, value_name='القيمة')
        
        df_unpivoted.rename(columns={'القيمة': YIELD_COLUMN_NAME}, inplace=True)
        final_df = df_unpivoted[[TENOR_COLUMN_NAME, YIELD_COLUMN_NAME]]
        
        final_df[TENOR_COLUMN_NAME] = final_df[TENOR_COLUMN_NAME].astype(str).str.extract(r'(\d+)', expand=False)
        final_df = final_df.dropna().copy()
        final_df[YIELD_COLUMN_NAME] = pd.to_numeric(final_df[YIELD_COLUMN_NAME], errors='coerce')
        final_df[TENOR_COLUMN_NAME] = pd.to_numeric(final_df[TENOR_COLUMN_NAME], errors='coerce')
        final_df.dropna(inplace=True)
        
        if not final_df.empty:
            final_df[TENOR_COLUMN_NAME] = final_df[TENOR_COLUMN_NAME].astype(int)
            final_df.sort_values(TENOR_COLUMN_NAME, inplace=True)
            final_df.to_csv(CSV_FILENAME, index=False, encoding='utf-8-sig')
            return final_df, 'SUCCESS', "تم تحديث البيانات بنجاح!", datetime.now().strftime("%Y-%m-%d")
            
        return None, 'NO_DATA_FOUND', "لم يتم العثور على بيانات صالحة بعد المعالجة.", None

    except requests.exceptions.RequestException as e:
        msg = f"فشل الاتصال بموقع البنك المركزي. ({e})"
        return None, 'REQUEST_ERROR', msg, None
    except Exception as e:
        traceback.print_exc()
        msg = f"خطأ غير متوقع أثناء تحليل البيانات: {e}"
        return None, 'UNEXPECTED_ERROR', msg, None

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
        box-sizing: border-box; /* Prevent padding from breaking layout */
    }
    h1, h2, h3, h4, h5, h6 { font-weight: 700 !important; }
    
    /* Main app background */
    .main > div {
        background-color: #f0f2f6; /* Light gray background */
    }

    /* --- DARK THEME FOR ALL CARDS --- */
    .st-emotion-cache-1r6slb0 {
        box-shadow: 0 4px 12px 0 rgba(0,0,0,0.1) !important;
        border-radius: 15px !important;
        border: 1px solid #495057 !important;
        padding: 25px !important;
        height: 100%;
        background-color: #343a40 !important; /* Dark background */
        color: #f8f9fa !important; /* Light text */
    }
    .st-emotion-cache-1r6slb0:hover {
        box-shadow: 0 8px 24px 0 rgba(0,0,0,0.2) !important;
    }

    /* Text and header colors within dark cards */
    .st-emotion-cache-1r6slb0 h1, .st-emotion-cache-1r6slb0 h2, .st-emotion-cache-1r6slb0 h3, .st-emotion-cache-1r6slb0 h4, .st-emotion-cache-1r6slb0 p, .st-emotion-cache-1r6slb0 span, .st-emotion-cache-1r6slb0 li, .st-emotion-cache-1r6slb0 div, .st-emotion-cache-1r6slb0 label {
        color: #f8f9fa !important;
    }
    
    /* Input field label color */
    .st-emotion-cache-1r6slb0 .st-emotion-cache-ue6h4q {
        color: #dee2e6 !important;
    }

    /* Metric styling for dark cards */
    .st-emotion-cache-1r6slb0 div[data-testid="stMetricValue"] { color: #f8f9fa !important; font-size: 1.75rem !important; }
    .st-emotion-cache-1r6slb0 div[data-testid="stMetricLabel"] { color: #adb5bd !important; }
    
    /* Button styling */
    .st-emotion-cache-19rxjzo {
        border-radius: 10px !important; font-weight: 700 !important;
        box-shadow: 0 2px 4px 0 rgba(0,0,0,0.1) !important;
        padding: 0.75rem 1rem !important; font-size: 1.05rem !important;
    }
    
    /* Title styling - Dark card for header */
    .app-title { 
        text-align: center !important; 
        padding: 1.5rem 1rem;
        background-color: #343a40;
        border-radius: 15px;
        margin-bottom: 1rem;
        box-shadow: 0 4px 12px 0 rgba(0,0,0,0.1) !important;
    }
    .app-title h1 {
        color: #ffffff !important;
    }
    .app-title p {
        color: #dee2e6 !important; /* Lighter color for subtitle */
    }
    
    </style>
""", unsafe_allow_html=True)


# --- Header ---
st.markdown(f"""
<div class="app-title">
    <h1>{prepare_arabic_text("🏦 حاسبة أذون الخزانة")}</h1>
    <p>{prepare_arabic_text("تطبيق تفاعلي لحساب عوائد أذون الخزانة بناءً على أحدث نتائج العطاءات")}</p>
</div>
""", unsafe_allow_html=True)

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
        if not data_df.empty:
            sorted_tenors = sorted(data_df[TENOR_COLUMN_NAME].unique())
            cols = st.columns(len(sorted_tenors))
            tenor_icons = {91: "⏳", 182: "🗓️", 273: "📆", 364: "🗓️✨"}
            for i, tenor in enumerate(sorted_tenors):
                with cols[i]:
                    icon = tenor_icons.get(tenor, "🪙")
                    rate = data_df[data_df[TENOR_COLUMN_NAME] == tenor][YIELD_COLUMN_NAME].iloc[0]
                    st.metric(label=prepare_arabic_text(f"{icon} أجل {tenor} يوم"), value=f"{rate:.3f}%")
        else:
            st.warning(prepare_arabic_text("لم يتم تحميل البيانات."))

with top_col2:
    with st.container(border=True):
        st.subheader(prepare_arabic_text("📡 حالة الاتصال بالبنك المركزي"), anchor=False)
        days_ar = {'Monday':'الإثنين','Tuesday':'الثلاثاء','Wednesday':'الأربعاء','Thursday':'الخميس','Friday':'الجمعة','Saturday':'السبت','Sunday':'الأحد'}
        now = datetime.now()
        day_name_en = now.strftime('%A')
        day_name_ar = days_ar.get(day_name_en, day_name_en)
        current_time_str = now.strftime(f"%Y/%m/%d | %H:%M")
        
        st.write(f"{prepare_arabic_text('**الوقت الحالي:**')} {prepare_arabic_text(day_name_ar)}، {current_time_str}")
        st.write(f"{prepare_arabic_text('**آخر تحديث مسجل:**')} {st.session_state.last_update}")
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button(prepare_arabic_text("🔄 جلب أحدث البيانات الآن"), use_container_width=True, type="primary"):
            with st.spinner(prepare_arabic_text("جاري الاتصال بالبنك المركزي...")):
                new_df, status, message, update_time = fetch_data_from_cbe()
                if status == 'SUCCESS':
                    st.session_state.df_data = new_df; st.session_state.last_update = datetime.now().strftime("%d-%m-%Y %H:%M")
                    st.success(prepare_arabic_text("✅ تم التحديث بنجاح!"), icon="✅"); time.sleep(2); st.rerun()
                elif status == 'NO_DATA_FOUND':
                    st.info(prepare_arabic_text("ℹ️ لا توجد نتائج جديدة."), icon="ℹ️"); time.sleep(3)
                else:
                    st.error(prepare_arabic_text(f"⚠️ {message}"), icon="⚠️"); time.sleep(4)
st.divider()

# --- Main Calculator Section ---
st.header(prepare_arabic_text("🧮 حاسبة العائد الأساسية"))
col_form_main, col_results_main = st.columns(2, gap="large")

with col_form_main:
    with st.container(border=True):
        st.subheader(prepare_arabic_text("1. أدخل بيانات الاستثمار"), anchor=False)
        investment_amount_main = st.number_input(prepare_arabic_text("المبلغ المستثمر (بالجنيه)"), min_value=1000.0, value=25000.0, step=1000.0, key="main_investment")
        selected_tenor_main = st.selectbox(prepare_arabic_text("اختر مدة الاستحقاق (بالأيام)"), options=data_df[TENOR_COLUMN_NAME].unique(), key="main_tenor")
        st.subheader(prepare_arabic_text("2. قم بحساب العائد"), anchor=False)
        calculate_button_main = st.button(prepare_arabic_text("احسب العائد الآن"), use_container_width=True, type="primary", key="main_calc")

results_placeholder_main = col_results_main.empty()

if calculate_button_main:
    if not data_df.empty:
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
                
                # --- Comparison Section Restored ---
                st.markdown('<hr style="border-color: #495057;">', unsafe_allow_html=True)
                st.subheader(prepare_arabic_text("📈 مقارنة سريعة"), anchor=False)
                
                other_tenors = [t for t in sorted(data_df[TENOR_COLUMN_NAME].unique()) if t != selected_tenor_main]
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
        original_tenor_secondary = st.selectbox(prepare_arabic_text("أجل الإذن الأصلي (بالأيام)"), options=data_df[TENOR_COLUMN_NAME].unique(), key="secondary_tenor", index=0)
        
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
    # Calculations
    original_purchase_price = face_value_secondary / (1 + (original_yield_secondary / 100 * original_tenor_secondary / 365))
    remaining_days = original_tenor_secondary - early_sale_days_secondary
    
    if remaining_days <= 0:
        secondary_results_placeholder.error(prepare_arabic_text("أيام الاحتفاظ يجب أن تكون أقل من أجل الإذن الأصلي."))
    else:
        sale_price = face_value_secondary / (1 + (secondary_market_yield / 100 * remaining_days / 365))
        gross_profit = sale_price - original_purchase_price
        tax_amount_secondary = max(0, gross_profit * 0.20) # Tax is only on profit
        net_profit = gross_profit - tax_amount_secondary
        annualized_yield_secondary = (net_profit / original_purchase_price) * (365 / early_sale_days_secondary) * 100 if early_sale_days_secondary > 0 else 0
        
        gross_return_full = face_value_secondary - original_purchase_price
        net_return_full = gross_return_full * 0.80 # 20% tax on full profit
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
        st.info(prepare_arabic_text("✨ أدخل بيانات البيع في النموذج على اليمين لتحليل قرارك."))

