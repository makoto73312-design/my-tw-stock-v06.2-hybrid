import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import requests
import re
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

# --- 1. 網頁核心外觀配置 ---
st.set_page_config(page_title="台股雷達 V06.2", page_icon="🇹🇼", layout="wide")
st.title("🇹🇼 台股量化投資沙盒 V06.2 (法人級多因子雙向感知與四分頁戰術矩陣版)")
st.markdown("已實裝 **V06.2 四維獨立介面與七維戰術矩陣**：**動作分類看板**、**七維戰術共振矩陣**、**歷史驗證線圖** 與 **多線程平行加速引擎** (⚡ 支援 Excel / 換行直貼自選清單)")

# --- 2. 側邊欄控制台 ---
st.sidebar.header("⚙️ 全自動大掃描設定")

GSHEET_URL = "https://docs.google.com/spreadsheets/d/1Gmy2iLCICdI5UtdfLW5o4brX3l-J3-ZfwSUSBrz4bfo/edit?usp=sharing"

GOOGLE_FORM_ID = "1FAIpQLSfFyZVyj0gvInJomErH1shWrIClFF1CEjWKXtQJYkzxSgRcEg"
ENTRY_TICKER_ID = "entry.40654407"
ENTRY_NAME_ID = "entry.1671985547"

@st.cache_data(ttl=60)
def get_tickers_from_sheet(url):
    try:
        if "docs.google.com" not in url:
            return "2330, 2317", {}
        csv_url = url.split("/edit")[0] + "/export?format=csv"
        df = pd.read_csv(csv_url, header=None)
        
        tickers = df.iloc[:, 0].dropna().astype(str).str.strip().str.upper().tolist()
        
        custom_names_dict = {}
        if df.shape[1] > 1:
            raw_names = df.iloc[:, 1].fillna("").astype(str).str.strip().tolist()
            for i, t in enumerate(tickers):
                if i < len(raw_names):
                    name_val = str(raw_names[i]).strip()
                    if name_val and name_val.lower() not in ["nan", "none", ""]:
                        custom_names_dict[t] = name_val
                        
        valid_tickers = [t for t in tickers if len(t) > 0 and t != "股票代號" and not t.startswith("202")]
        ticker_str = ", ".join(valid_tickers) if valid_tickers else "2330, 2317, 2454, 2603, 3231"
        return ticker_str, custom_names_dict
    except Exception as e:
        st.error(f"⚠️ 讀取試算表失敗，原因：{e}")
        return "2330, 2317, 2454, 2603, 3231", {}

default_tickers, cloud_names_dict = get_tickers_from_sheet(GSHEET_URL)
tickers_input = st.sidebar.text_area("📡 當前雲端同步清單 (可貼 Excel / 換行 / 逗號)", default_tickers, height=120)

# 🟢 萬能拆分解析器：支援「換行 (Excel直貼)」、「逗號」、「空格」多重分隔符
temp_raw_list = [t.strip().upper() for t in re.split(r'[\n\r,\s]+', tickers_input) if t.strip()]
raw_list = list(dict.fromkeys(temp_raw_list))

processed_tickers = []
for t in raw_list:
    if t.isdigit():
        processed_tickers.append(f"{t}.TW")
    else:
        processed_tickers.append(t)

backtest_days = st.sidebar.slider("歷史回測天數設定", min_value=100, max_value=500, value=300, step=50)
enable_fcf_filter = st.sidebar.checkbox("🛡️ 啟用「自由現金流 > 0」安全過濾", value=True)
enable_earnings_shield = st.sidebar.checkbox("💣 啟用「3 天內發布財報」強制避險", value=True)

# --- 3. 🌐 V06.2 台股大環境與總經雷達 (VIX & 台灣加權指數) ---
@st.cache_data(ttl=1800)
def fetch_tw_macro_environment():
    try:
        vix_df = yf.download("^VIX", period="5d", progress=False)
        vix_clean = vix_df.dropna(subset=['Close'])
        vix_val = float(vix_clean['Close'].iloc[-1]) if not vix_clean.empty else 18.0
        
        tw_df = yf.download("^TWII", period="1y", progress=False)
        tw_clean = tw_df.dropna(subset=['Close'])
        if not tw_clean.empty:
            tw_close = float(tw_clean['Close'].iloc[-1])
            tw_ma200 = float(tw_clean['Close'].rolling(200).mean().iloc[-1])
            tw_bull = tw_close >= tw_ma200
        else:
            tw_bull = True
            
        if vix_val >= 25 or not tw_bull:
            posture_auto = "🥶 極度謹慎型 (大盤空頭/高恐慌)"
        elif vix_val <= 15 and tw_bull:
            posture_auto = "🚀 大膽進攻型 (晴天多頭行情)"
        else:
            posture_auto = "🛡️ 標準平衡型 (常態橫盤整理)"
            
        return vix_val, tw_bull, posture_auto
    except Exception:
        return 18.0, True, "🛡️ 標準平衡型 (預設)"

vix_score, is_tw_bull, market_posture = fetch_tw_macro_environment()

# --- 4. 🏢 V06.2 台股公司名稱、基本面與新聞雷達 ---
@st.cache_data(ttl=3600)
def fetch_tw_fundamental_and_news(raw_id, processed_id, cloud_dict):
    TW_NAMES_DICT = {
        "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2603": "長榮", "3231": "緯創",
        "2308": "台達電", "2382": "廣達", "2881": "富邦金", "2882": "國泰金", "2303": "聯電",
        "2412": "中華電", "1301": "台塑", "1303": "南亞", "2002": "中鋼", "2891": "中信金",
        "2357": "華碩", "2324": "仁寶", "2344": "華邦電", "2337": "旺宏", "2609": "陽明",
        "2615": "萬海", "2610": "華航", "2618": "長榮航", "3037": "欣興", "3034": "聯詠",
        "2379": "瑞昱", "2353": "宏碁", "2886": "兆豐金", "5880": "合庫金", "2884": "玉山金",
        "3481": "群創", "6695": "芯鼎", "8112": "至上", "4739": "康普"
    }
    
    comp_name = TW_NAMES_DICT.get(raw_id, "台灣個股")
    comp_name = cloud_dict.get(raw_id, comp_name)
    
    f_info = {
        "comp_name": comp_name, "pe": "-", "fcf": "-", "rev_growth": "-", 
        "is_fcf_positive": True, "near_earnings": False, 
        "news_alert": "🟢 無異常", "quality_tag": "一般"
    }
    
    try:
        tk = yf.Ticker(processed_id)
        info = tk.info or {}
        
        if comp_name == "台灣個股":
            short_name = info.get('shortName', raw_id)
            for keyword in ["TAIWAN", "STOCK", "LTD", "INC", "CORPORATION", "COMPANY"]:
                short_name = short_name.upper().split(keyword)[0].strip()
            f_info["comp_name"] = short_name
            
        pe = info.get("trailingPE", None)
        fcf = info.get("freeCashflow", None)
        rev_g = info.get("revenueGrowth", None)
        
        if pe is not None: f_info["pe"] = f"{pe:.1f}倍"
        if fcf is not None:
            f_info["fcf"] = f"NT${fcf / 1e8:.1f}億"
            if fcf < 0: f_info["is_fcf_positive"] = False
        if rev_g is not None: f_info["rev_growth"] = f"{rev_g * 100:+.1f}%"
        
        if (fcf is not None and fcf > 0) and (rev_g is not None and rev_g > 0.10):
            f_info["quality_tag"] = "🔥 財報雙強"
            
        calendar = tk.calendar
        if calendar is not None and "Earnings Date" in calendar:
            e_dates = calendar["Earnings Date"]
            if len(e_dates) > 0:
                next_e = pd.to_datetime(e_dates[0])
                today = pd.to_datetime(datetime.now().date())
                days_diff = (next_e - today).days
                if 0 <= days_diff <= 3:
                    f_info["near_earnings"] = True
                    
        news_list = tk.news or []
        bad_keywords = ["LAWSUIT", "PROBE", "DOWNGRADE", "MISSED", "INVESTIGATION", "FRAUD", "WARN"]
        bad_count = sum(1 for n in news_list[:5] if any(kw in n.get("title", "").upper() for kw in bad_keywords))
        if bad_count >= 1:
            f_info["news_alert"] = f"⚠️ 掃描到 {bad_count} 則利空新聞"
            
    except Exception:
        pass
    return f_info

# --- 5. 技術指標核心大腦 ---
def calculate_indicators(df):
    high_low_diff = (df['High'] - df['Low']).replace(0, 0.001) 
    mf_multiplier = ((df['Close'] - df['Low']) - (df['High'] - df['Close'])) / high_low_diff
    df['主力籌碼'] = (df['Volume'] * mf_multiplier / 1000).round(2)
    
    df['MA5'] = df['Close'].rolling(5).mean()
    df['MA10'] = df['Close'].rolling(10).mean()
    df['MA14'] = df['Close'].rolling(14).mean()
    df['MA20'] = df['Close'].rolling(20).mean()
    df['MA21'] = df['Close'].rolling(21).mean()
    df['MA30'] = df['Close'].rolling(30).mean()
    df['50MA'] = df['Close'].rolling(50).mean()
    df['200MA'] = df['Close'].rolling(200).mean()
    df['ROC14'] = df['Close'].pct_change(14)
    
    delta = df['Close'].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    ema_gain = gain.ewm(com=13, adjust=False).mean()
    ema_loss = loss.ewm(com=13, adjust=False).mean()
    rs = ema_gain / ema_loss.replace(0, 0.001)
    df['RSI_14'] = 100 - (100 / (1 + rs))
    
    df['Vol_MA20'] = df['Volume'].rolling(20).mean()
    df['主力籌碼_Q80'] = df['主力籌碼'].rolling(50).quantile(0.8)
    df['主力籌碼_Q90'] = df['主力籌碼'].rolling(50).quantile(0.9)
    df['主力籌碼_Q95'] = df['主力籌碼'].rolling(50).quantile(0.95)
    
    ema12 = df['Close'].ewm(span=12, adjust=False).mean()
    ema26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema12 - ema26
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACD_Hist'] = df['MACD'] - df['Signal']
    
    macd_shrink = [0] * len(df)
    hist = df['MACD_Hist'].values
    for i in range(1, len(df)):
        if hist[i] < 0 and hist[i] > hist[i-1]:
            macd_shrink[i] = macd_shrink[i-1] + 1
        else:
            macd_shrink[i] = 0
    df['MACD_Shrink'] = macd_shrink
    return df

# --- 6. V06.2 台股歷史回測引擎 ---
def run_backtest_engine(df, strategy_name, days, posture, fund_info):
    valid_df = df.dropna(subset=['200MA', 'ROC14', 'MACD_Hist', 'RSI_14', 'Vol_MA20']).tail(days).copy()
    if len(valid_df) < 5:
        return "⚠️ 數據不足", 0, 0, 0, 0, "❌ 不推薦", "🛑 數據不足", "-", "-", "-", [], [], [], valid_df

    if "🚀" in posture: rsi_max, vol_mult, dip_pct, rsi_min, chip_col = 75, 1.05, -0.07, 35, '主力籌碼_Q80'
    elif "🥶" in posture: rsi_max, vol_mult, dip_pct, rsi_min, chip_col = 65, 1.40, -0.12, 25, '主力籌碼_Q95'
    else: rsi_max, vol_mult, dip_pct, rsi_min, chip_col = 70, 1.15, -0.07, 30, '主力籌碼_Q90'

    if "A:" in strategy_name: s_ma, d_ma, stop_loss_pct = valid_df['MA5'], valid_df['MA14'], 0.04
    elif "B:" in strategy_name: s_ma, d_ma, stop_loss_pct = valid_df['MA14'], valid_df['MA21'], 0.05
    elif "C:" in strategy_name: s_ma, d_ma, stop_loss_pct = valid_df['MA10'], valid_df['MA30'], 0.07
    elif "D:" in strategy_name: s_ma, d_ma, stop_loss_pct = valid_df['MA20'], valid_df['200MA'], 0.04
    else: s_ma, d_ma, stop_loss_pct = valid_df['MA5'], valid_df['MA20'], 0.04

    max_ma, min_ma = valid_df[['MA5', 'MA14', '50MA']].max(axis=1), valid_df[['MA5', 'MA14', '50MA']].min(axis=1)
    is_entangled_arr = (((max_ma - min_ma) / valid_df['50MA'].replace(0, 0.001)) < 0.025).values

    closes, highs, lows = valid_df['Close'].values, valid_df['High'].values, valid_df['Low'].values
    s_mas, m200s, r14s, rsis = s_ma.values, valid_df['200MA'].values, valid_df['ROC14'].values, valid_df['RSI_14'].values
    vols, vol_m20s = valid_df['Volume'].values, valid_df['Vol_MA20'].values
    m_shrinks, m_hists, m_flows, chip_threshs = valid_df['MACD_Shrink'].values, valid_df['MACD_Hist'].values, valid_df['主力籌碼'].values, valid_df[chip_col].values

    has_position = False
    entry_price, highest_price_since_entry = 0, 0
    total_trades, win_trades = 0, 0
    total_return, total_gross_profit, total_gross_loss = 0.0, 0.0, 0.0
    trade_logs, plot_buys, plot_sells = [], [], []

    for i in range(len(valid_df)):
        date_str = valid_df.index[i].strftime('%Y-%m-%d')
        close_p, high_p, low_p = closes[i], highs[i], lows[i]
        sma_p, m200_p, r14_p, rsi_p = s_mas[i], m200s[i], r14s[i], rsis[i]
        vol_p, vol_m20_p = vols[i], vol_m20s[i]
        m_shrink_p, m_hist_p, m_flow_p, chip_thresh_p = m_shrinks[i], m_hists[i], m_flows[i], chip_threshs[i]
        m_hist_y = m_hists[i-1] if i > 0 else 0
        is_entangled = is_entangled_arr[i]

        if not has_position:
            is_buy = False
            if "A:" in strategy_name:
                if (m_shrink_p >= 1 or (m_hist_p > m_hist_y and m_hist_p > 0)) and r14_p > 0 and rsi_p < rsi_max: is_buy = True
            elif "B:" in strategy_name or "C:" in strategy_name:
                if (not is_entangled) and close_p > sma_p and vol_p > vol_m20_p * vol_mult: is_buy = True
            elif "D:" in strategy_name:
                if m200_p > 0 and (close_p - m200_p)/m200_p <= dip_pct and m_shrink_p >= 1 and rsi_p < rsi_min: is_buy = True
            elif "E:" in strategy_name:
                if m_flow_p > chip_thresh_p and m_flow_p > 0: is_buy = True
            
            if is_buy:
                has_position, entry_price, highest_price_since_entry = True, close_p, close_p
                total_trades += 1
                trade_logs.append({"交易日期": date_str, "動作狀態": "🟢 買入進場 (BUY)", "執行價格": f"NT${close_p:.2f}", "單筆報酬": "-"})
                plot_buys.append((valid_df.index[i], close_p))
        else:
            highest_price_since_entry = max(highest_price_since_entry, high_p)
            is_exit, exit_price = False, close_p

            if "D:" not in strategy_name:
                if low_p <= highest_price_since_entry * (1 - stop_loss_pct):
                    is_exit, exit_price = True, highest_price_since_entry * (1 - stop_loss_pct)
                elif ("B:" in strategy_name or "C:" in strategy_name) and is_entangled:
                    is_exit, exit_price = True, close_p
            else:
                if high_p >= m200_p: is_exit, exit_price = True, m200_p
                elif low_p <= entry_price * 0.95: is_exit, exit_price = True, entry_price * 0.95

            if is_exit:
                trade_return = (exit_price - entry_price) / entry_price
                total_return += trade_return
                if trade_return > 0: win_trades += 1; total_gross_profit += trade_return
                else: total_gross_loss += abs(trade_return)
                has_position = False
                trade_logs.append({"交易日期": date_str, "動作狀態": "🔴 賣出出場 (SELL)", "執行價格": f"NT${exit_price:.2f}", "單筆報酬": f"{trade_return*100:+.2f}%"})
                plot_sells.append((valid_df.index[i], exit_price))

    final_win_rate = win_trades / total_trades if total_trades > 0 else 0.0
    profit_factor = total_gross_profit / total_gross_loss if total_gross_loss > 0 else (99.9 if total_gross_profit > 0 else 0.0)
    pf_str = "無限" if profit_factor == 99.9 else f"{profit_factor:.2f}"

    stars = "❌ 不推薦"
    if total_return > 0 and total_trades > 0:
        if total_return >= 0.20 and final_win_rate >= 0.52: stars = "⭐⭐⭐⭐⭐"
        elif total_return >= 0.12 or final_win_rate >= 0.48: stars = "⭐⭐⭐⭐"
        else: stars = "⭐⭐"

    last_action = trade_logs[-1] if len(trade_logs) > 0 else None
    today_str = valid_df.index[-1].strftime('%Y-%m-%d')
    current_close = closes[-1]
    
    current_status = "💵 空手觀望 (CASH)"
    entry_price_str = "-"
    sl_price_str = "-"
    pnl_str = "-"

    if has_position:
        if last_action and last_action["交易日期"] == today_str and "BUY" in last_action["動作狀態"]:
            current_status = "🚀 今日大膽建倉 (BUY)"
            entry_price_str = f"NT${current_close:.2f}"
            pnl_str = "0.00%"
        else:
            current_status = "📦 獲利續抱中 (HOLD)"
            unrealized_pnl = (current_close - entry_price) / entry_price
            pnl_str = f"{unrealized_pnl*100:+.2f}%"
            entry_price_str = f"NT${entry_price:.2f}"

        sl_price_str = f"NT${highest_price_since_entry * (1 - stop_loss_pct):.2f}" if "D:" not in strategy_name else f"NT${max(entry_price * 0.95, m200s[-1]):.2f}"
    else:
        if last_action and last_action["交易日期"] == today_str and "SELL" in last_action["動作狀態"]:
            current_status = "🔴 今日觸發防守賣出 (SELL)"
        else:
            current_status = "💵 空手觀望 (CASH)"

    if enable_earnings_shield and fund_info["near_earnings"] and "BUY" in current_status:
        current_status = "💣 財報前夕/強制避險 (CASH)"
        
    if enable_fcf_filter and not fund_info["is_fcf_positive"] and "BUY" in current_status:
        current_status = "⚠️ 現金流不良/阻擋建倉 (CASH)"

    return "📡 運算完畢", total_return, final_win_rate, total_trades, pf_str, stars, current_status, entry_price_str, sl_price_str, pnl_str, trade_logs, plot_buys, plot_sells, valid_df

# ⚡ 多線程 Worker 函數 (單個台股處理單元)
def process_single_stock_tw(raw_name, ticker, cloud_dict, backtest_days, posture, strategies):
    try:
        df_stock = yf.download(ticker, period="2y", progress=False)
        if df_stock.empty:
            return [], {}
        df_stock.columns = [col[0] if isinstance(col, tuple) else col for col in df_stock.columns]
        df_stock = calculate_indicators(df_stock)
        
        df_temp_clean = df_stock.dropna(subset=['Close'])
        current_close = float(df_temp_clean['Close'].iloc[-1]) if not df_temp_clean.empty else 0.0
        
        fund_info = fetch_tw_fundamental_and_news(raw_name, ticker, cloud_dict)
        
        stock_reports = []
        stock_details = {}
        
        for strat in strategies:
            radar, ret, win, trades, pf, stars, cur_status, entry_price_val, sl_price, pnl, t_logs, p_buys, p_sells, v_df = run_backtest_engine(df_stock, strat, backtest_days, posture, fund_info)
            stock_details[(raw_name, strat)] = {"logs": pd.DataFrame(t_logs), "buys": p_buys, "sells": p_sells, "v_df": v_df}
            stock_reports.append({
                "台股代號": raw_name, "公司名稱": fund_info["comp_name"], "當前股價": f"NT${current_close:.2f}", 
                "策略手法": strat, "倉位狀態": cur_status,
                "基本面評價": fund_info["quality_tag"],
                "本益比 (PE)": fund_info["pe"],
                "自由現金流": fund_info["fcf"],
                "新聞警告": fund_info["news_alert"],
                "建議進場價(持股成本)": entry_price_val, "未實現損益": pnl,
                "嚴格防守價": sl_price, "總報酬率": f"{ret * 100:+.2f}%", 
                "歷史勝率": f"{win * 100:.1f}%", "交易次數": trades, "獲利因子": pf, "推薦指數": stars
            })
        return stock_reports, stock_details
    except Exception:
        return [], {}

# --- 7. Session State 記憶庫 ---
if "calculated" not in st.session_state:
    st.session_state.calculated = False
    st.session_state.final_df = None
    st.session_state.detail_db = {}

# --- 8. 頂部總經抬頭控制卡 ---
col_v1, col_v2, col_v3 = st.columns(3)
col_v1.metric("VIX 恐慌指數", f"{vix_score:.2f}", delta="避險戒備" if vix_score >= 25 else "市場平穩", delta_color="inverse")
col_v2.metric("台灣加權指數 (大盤)", "年線之上 (多頭)" if is_tw_bull else "跌破年線 (空頭)")
col_v3.metric("系統自動環境姿態", market_posture)
st.divider()

if st.button("🚀 啟動 V06.2 台股全自動多因子掃描引擎 (⚡ 多線程平行加速)", use_container_width=True):
    with st.spinner("正在啟動 ThreadPoolExecutor 多線程引擎進行全維度平行運算..."):
        master_report, strategies = [], ["A: 激進動能型", "B: 穩健波段型", "C: 槓桿防守型", "D: 均值回歸抄底型", "E: 籌碼主力跟隨型"]
        
        futures = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            for idx, ticker in enumerate(processed_tickers):
                raw_name = raw_list[idx]
                f = executor.submit(process_single_stock_tw, raw_name, ticker, cloud_names_dict, backtest_days, market_posture, strategies)
                futures.append(f)
                
        for f in futures:
            s_reports, s_details = f.result()
            if s_reports:
                master_report.extend(s_reports)
                st.session_state.detail_db.update(s_details)
                
        st.session_state.final_df = pd.DataFrame(master_report)
        st.session_state.calculated = True
        st.success("📊 V06.2 台股多因子矩陣計算完成！請至下方各分頁切換檢視。")

# --- 9. 網頁四獨立分頁系統 ---
tab_v061, tab_matrix, tab_debug, tab_manage = st.tabs([
    "📊 倉位動作與多因子總表", 
    "🎯 七維量化戰術矩陣", 
    "🔍 歷史回測與線圖驗證", 
    "➕ 雲端自選清單管理"
])

# --- 分頁 1: 倉位動作快選 + 完整總表 ---
with tab_v061:
    if st.session_state.calculated:
        df_res = st.session_state.final_df
        
        st.subheader("🎯 倉位狀態分類看板 (動作快選)")
        
        buy_df = df_res[df_res['倉位狀態'].str.contains("BUY", na=False)]
        sell_df = df_res[df_res['倉位狀態'].str.contains("SELL", na=False)]
        hold_df = df_res[df_res['倉位狀態'].str.contains("HOLD", na=False)]
        risk_df = df_res[df_res['倉位狀態'].str.contains("現金流不良|財報前夕", na=False)]
        cash_df = df_res[df_res['倉位狀態'].str.contains("空手觀望", na=False)]
        
        col_c1, col_c2, col_c3, col_c4, col_c5 = st.columns(5)
        col_c1.metric("🚀 今日建議建倉", f"{len(buy_df)} 筆")
        col_c2.metric("🔴 觸發防守賣出", f"{len(sell_df)} 筆")
        col_c3.metric("📦 獲利續抱中", f"{len(hold_df)} 筆")
        col_c4.metric("🛡️ 風控安全閥攔截", f"{len(risk_df)} 筆")
        col_c5.metric("💵 空手觀望", f"{len(cash_df)} 筆")
        
        display_cols = ['台股代號', '公司名稱', '當前股價', '策略手法', '倉位狀態', '建議進場價(持股成本)', '未實現損益', '嚴格防守價', '推薦指數']
        
        with st.expander(f"🚀 今日建議大膽建倉 ({len(buy_df)} 筆)", expanded=len(buy_df)>0):
            if not buy_df.empty: st.dataframe(buy_df[display_cols], use_container_width=True, hide_index=True)
            else: st.info("今日無觸發建倉訊號之個股。")
                
        with st.expander(f"🔴 今日觸發防守賣出 ({len(sell_df)} 筆)", expanded=len(sell_df)>0):
            if not sell_df.empty: st.dataframe(sell_df[display_cols], use_container_width=True, hide_index=True)
            else: st.info("今日無觸發賣出/停損訊號之個股。")
                
        with st.expander(f"📦 獲利續抱中 ({len(hold_df)} 筆)", expanded=False):
            if not hold_df.empty: st.dataframe(hold_df[display_cols], use_container_width=True, hide_index=True)
            else: st.info("目前無持有中之個股。")
                
        with st.expander(f"🛡️ 風控安全閥攔截 ({len(risk_df)} 筆)", expanded=len(risk_df)>0):
            if not risk_df.empty:
                st.caption("提示：技術面雖達買點，但因自由現金流不良或財報將至，已被系統強制轉為 CASH 避險。")
                st.dataframe(risk_df[display_cols], use_container_width=True, hide_index=True)
            else: st.info("今日無被風控閥攔截之個股。")

        with st.expander(f"💵 空手觀望 ({len(cash_df)} 筆)", expanded=False):
            if not cash_df.empty: st.dataframe(cash_df[display_cols], use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("📋 完整多因子對照總表")

        def apply_block_shading(df):
            unique_tickers = df["台股代號"].unique()
            styles = pd.DataFrame('', index=df.index, columns=df.columns)
            for i, ticker in enumerate(unique_tickers):
                bg_color = 'background-color: rgba(128, 128, 128, 0.16)' if i % 2 == 0 else 'background-color: rgba(0, 0, 0, 0)'
                mask = df["台股代號"] == ticker
                styles.loc[mask, :] = bg_color
            return styles

        styled_df = df_res.style.apply(apply_block_shading, axis=None)
        st.dataframe(styled_df, use_container_width=True, hide_index=True)
    else:
        st.info("💡 請按下上方「🚀 啟動 V06.2 台股全自動多因子掃描引擎」按鈕開始運算。")

# --- 分頁 2: 新增【🎯 七維量化戰術矩陣】獨立專頁 (含說明手冊按鍵) ---
with tab_matrix:
    st.header("🎯 七維量化戰術矩陣看板 (跨策略共振與型態快選)")
    st.markdown("透過跨策略訊號共振，1 秒辨識「飆股發動、高勝率突破、大戶吸籌、頭部背離與假突破陷阱」")

    # 🟢 點擊按鍵展開說明手冊 (交互式按鍵手冊)
    with st.expander("📖 點擊展開：【七維戰術矩陣】七大代表涵義與實戰注意事項說明書", expanded=False):
        st.markdown("""
        ### 📖 七維量化戰術矩陣 — 實戰對照與策略手冊

        | 戰術型態 | 代表市場涵義 | 技術與籌碼定義 | 接下來實戰注意事項 |
        | :--- | :--- | :--- | :--- |
        | **🔥 1. 全面共振多頭** | **主升段發動 / 飆股首選** | 動能(A) + 波段/槓桿(B/C) + 主力籌碼(E) **三大維度 100% 同步看多**，且具高勝率評級。 | **勝率與延續性最高！** 可優先作為資金集中建倉的第一首選，並以距離現價最近的策略停損價作為第一防守線。 |
        | **🌊 2. 高勝率波段突破** | **波段起漲 / 單點強突破** | 波段(B) 或 槓桿(C) 策略獨立爆發 `🚀 大膽建倉`，且歷史評級為 **`⭐⭐⭐⭐` 或 `⭐⭐⭐⭐⭐`**。 | **補捉單點強勢突破點！** 雖然動能或籌碼尚未全面共振，但歷史勝率極高，建議建立標準波段部位並嚴守防守線。 |
        | **🕵️ 3. 籌碼大戶潛伏** | **大戶暗中吸籌 / 蹲點打底** | 主力籌碼(E) 已率先買進或獲利續抱，但技術波段(B/C) 仍在打底橫盤觀望 (`CASH`)。 | **最佳預備觀察名單 (Watchlist)！** 代表大戶在默默吃貨，可先分批建立底倉或等技術面突破時再加碼。 |
        | **🛒 4. 價值超跌窪地** | **價值超跌 / 左側抄底** | 股價遠低於 200MA (年線) 並觸發抄底策略(D)，且完全通過自由現金流與財報風控安全過濾。 | **左側交易需控管部位！** 超跌反彈可能面臨上方均線反壓，建議小部位分批建立，並以歷史前低嚴格控管風險。 |
        | **⚠️ 5. 動能/籌碼頭部背離**| **拉高倒貨 / 趨勢末端** | 波段策略(B/C) 仍處於 `📦 獲利續抱`，但短線動能(A) 或 主力籌碼(E) 今日已率先發出 `🔴 防守賣出`。 | **警惕頭部反轉與利潤回吐！** 嚴禁在此時加碼追高，應將移動防守停損價拉緊，準備隨時獲利落袋出場。 |
        | **🔴 6. 多頭集體撤退** | **三方潰敗 / 系統性避險** | 當天有 2 個 (含) 以上的策略同時爆發 `🔴 防守賣出 (SELL)` 訊號。 | **最高風險警報！** 代表動能、均線與籌碼多重指標同步崩壞。手上有持股者應無條件果斷退場，嚴禁攤平。 |
        | **❌ 7. 高波動洗盤怪獸**| **假突破誘多 / 洗盤陷阱** | 單日有多個策略跳出 `🚀 大膽建倉`，但所有買訊策略的歷史評級全為 `❌ 不推薦` (歷史回測皆虧損)。 | **堅決觀望，嚴禁追高！** 代表該標的歷史充滿「今天暴漲誘多，明天立刻倒貨」的習性，切勿被當天 K 線誘惑。 |
        """)

    st.divider()

    if st.session_state.calculated:
        df_res = st.session_state.final_df
        matrix_data = []
        unique_tickers = df_res['台股代號'].unique()

        for t in unique_tickers:
            sub = df_res[df_res['台股代號'] == t].set_index('策略手法')
            
            get_s = lambda name: sub.loc[name, '倉位狀態'] if name in sub.index else ''
            get_r = lambda name: sub.loc[name, '推薦指數'] if name in sub.index else ''
            
            st_A, st_B, st_C, st_D, st_E = get_s('A: 激進動能型'), get_s('B: 穩健波段型'), get_s('C: 槓桿防守型'), get_s('D: 均值回歸抄底型'), get_s('E: 籌碼主力跟隨型')
            rec_A, rec_B, rec_C, rec_D, rec_E = get_r('A: 激進動能型'), get_r('B: 穩健波段型'), get_r('C: 槓桿防守型'), get_r('D: 均值回歸抄底型'), get_r('E: 籌碼主力跟隨型')
            
            comp_name = sub['公司名稱'].iloc[0] if '公司名稱' in sub.columns else '台灣個股'
            price = sub['當前股價'].iloc[0] if '當前股價' in sub.columns else '-'
            
            sells = [s for s in [st_A, st_B, st_C, st_D, st_E] if 'SELL' in s]
            
            is_m1 = ('BUY' in st_A) and ('BUY' in st_B or 'BUY' in st_C) and ('BUY' in st_E) and any(r != '❌ 不推薦' for r in [rec_A, rec_B, rec_C, rec_E])
            is_m2 = ('BUY' in st_B and rec_B in ['⭐⭐⭐⭐', '⭐⭐⭐⭐⭐']) or ('BUY' in st_C and rec_C in ['⭐⭐⭐⭐', '⭐⭐⭐⭐⭐'])
            is_m3 = ('BUY' in st_E or 'HOLD' in st_E) and ('CASH' in st_B and 'CASH' in st_C) and (rec_E != '❌ 不推薦')
            is_m4 = ('BUY' in st_D)
            is_m5 = any(('現金流' in s or '財報' in s) for s in [st_A, st_B, st_C, st_D, st_E])
            is_m6 = ('HOLD' in st_B or 'HOLD' in st_C) and ('SELL' in st_A or 'SELL' in st_E)
            is_m7 = len(sells) >= 2
            
            buy_rows = [sub.loc[s] for s in sub.index if 'BUY' in sub.loc[s, '倉位狀態']]
            is_m8 = len(buy_rows) >= 2 and all(b['推薦指數'] == '❌ 不推薦' for b in buy_rows)

            matrix_data.append({
                "台股代號": t, "公司名稱": comp_name, "當前股價": price,
                "🔥全面共振": is_m1, "🌊波段突破": is_m2, "🕵️籌碼吸籌": is_m3,
                "🛒價值窪地": is_m4, "🛑風控攔截": is_m5, "⚠️頭部背離": is_m6,
                "🔴集體撤退": is_m7, "❌洗盤怪獸": is_m8
            })

        m_df = pd.DataFrame(matrix_data)

        df_m1 = m_df[m_df['🔥全面共振']][['台股代號', '公司名稱', '當前股價']]
        df_m2 = m_df[m_df['🌊波段突破']][['台股代號', '公司名稱', '當前股價']]
        df_m3 = m_df[m_df['🕵️籌碼吸籌']][['台股代號', '公司名稱', '當前股價']]
        df_m4 = m_df[m_df['🛒價值窪地']][['台股代號', '公司名稱', '當前股價']]
        df_m5 = m_df[m_df['🛑風控攔截']][['台股代號', '公司名稱', '當前股價']]
        df_m6 = m_df[m_df['⚠️頭部背離']][['台股代號', '公司名稱', '當前股價']]
        df_m7 = m_df[m_df['🔴集體撤退']][['台股代號', '公司名稱', '當前股價']]
        df_m8 = m_df[m_df['❌洗盤怪獸']][['台股代號', '公司名稱', '當前股價']]

        col_m1, col_m2, col_m3, col_m4, col_m5, col_m6, col_m7 = st.columns(7)
        col_m1.metric("🔥全面共振", f"{len(df_m1)} 檔")
        col_m2.metric("🌊波段突破", f"{len(df_m2)} 檔")
        col_m3.metric("🕵️籌碼吸籌", f"{len(df_m3)} 檔")
        col_m4.metric("🛒價值窪地", f"{len(df_m4)} 檔")
        col_m5.metric("⚠️頭部背離", f"{len(df_m6)} 檔")
        col_m6.metric("🔴集體撤退", f"{len(df_m7)} 檔")
        col_m7.metric("❌洗盤怪獸", f"{len(df_m8)} 檔")

        st.divider()

        with st.expander(f"🔥 1. 全面共振多頭 (動能+突破+籌碼三大維度 100% 同步看多) — {len(df_m1)} 檔", expanded=len(df_m1)>0):
            if not df_m1.empty: st.dataframe(df_m1, use_container_width=True, hide_index=True)
            else: st.info("今日無出現多方全面共振訊號之標的。")

        with st.expander(f"🌊 2. 高勝率波段突破 (波段/槓桿策略強勢突破 + 高歷史勝率評級) — {len(df_m2)} 檔", expanded=len(df_m2)>0):
            if not df_m2.empty: st.dataframe(df_m2, use_container_width=True, hide_index=True)
            else: st.info("今日無出現波段獨立突破買訊之標的。")

        with st.expander(f"🕵️ 3. 籌碼大戶潛伏 (主力籌碼卡位/續抱，技術線型打底未突破) — {len(df_m3)} 檔", expanded=len(df_m3)>0):
            if not df_m3.empty: st.dataframe(df_m3, use_container_width=True, hide_index=True)
            else: st.info("今日無主力暗中吸籌之預備觀察標的。")

        with st.expander(f"🛒 4. 價值超跌窪地 (遠低於 200MA 年線，且基本面健全之抄底標的) — {len(df_m4)} 檔", expanded=len(df_m4)>0):
            if not df_m4.empty: st.dataframe(df_m4, use_container_width=True, hide_index=True)
            else: st.info("今日無符合超跌價值抄底條件之標的。")

        with st.expander(f"⚠️ 5. 動能/籌碼頭部背離 (波段續抱，但短線動能/主力已率先賣出) — {len(df_m6)} 檔", expanded=len(df_m6)>0):
            if not df_m6.empty: st.dataframe(df_m6, use_container_width=True, hide_index=True)
            else: st.info("今日無出現頭部背離警訊之標的。")

        with st.expander(f"🔴 6. 多頭集體撤退 (2 個或以上策略同時跳出防守賣出，警惕潰敗) — {len(df_m7)} 檔", expanded=len(df_m7)>0):
            if not df_m7.empty: st.dataframe(df_m7, use_container_width=True, hide_index=True)
            else: st.info("今日無出現多重賣訊共振之標的。")

        with st.expander(f"❌ 7. 高波動洗盤怪獸 (單日多策略買訊，但歷史評級全為不推薦，嚴禁追高) — {len(df_m8)} 檔", expanded=len(df_m8)>0):
            if not df_m8.empty: st.dataframe(df_m8, use_container_width=True, hide_index=True)
            else: st.info("今日無出現高波動洗盤怪獸標的。")
    else:
        st.info("💡 請按下上方「🚀 啟動 V06.2 台股全自動多因子掃描引擎」按鈕開始運算。")

# --- 分頁 3: 歷史回測與線圖驗證 ---
with tab_debug:
    st.header("🛠️ 台股歷史 K 線與訊號檢查器")
    if st.session_state.calculated:
        col_tk, col_st = st.columns(2)
        with col_tk: debug_ticker = st.selectbox("🎯 選擇想檢查的台股代號", raw_list)
        with col_st: debug_strat = st.selectbox("🔮 選擇策略", ["A: 激進動能型", "B: 穩健波段型", "C: 槓桿防守型", "D: 均值回歸抄底型", "E: 籌碼主力跟隨型"])
        db_key = (debug_ticker, debug_strat)
        if db_key in st.session_state.detail_db:
            data_pack = st.session_state.detail_db[db_key]
            logs_df, buys, sells, v_df = data_pack["logs"], data_pack["buys"], data_pack["sells"], data_pack["v_df"]
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=v_df.index, y=v_df['Close'], mode='lines', name='收盤價', line=dict(color='lightgrey', width=1.5)))
            if len(buys) > 0:
                fig.add_trace(go.Scatter(x=[b[0] for b in buys], y=[b[1] for b in buys], mode='markers', name='🟢 BUY (進場)', marker=dict(symbol='triangle-up', size=12, color='#00FF00'), hovertemplate='<b>進場買點</b><br>日期: %{x|%Y-%m-%d}<br>價格: NT$%{y:.2f}<extra></extra>'))
            if len(sells) > 0:
                fig.add_trace(go.Scatter(x=[s[0] for s in sells], y=[s[1] for s in sells], mode='markers', name='🔴 SELL (出場)', marker=dict(symbol='triangle-down', size=12, color='#FF0000'), hovertemplate='<b>風控出場點</b><br>日期: %{x|%Y-%m-%d}<br>價格: NT$%{y:.2f}<extra></extra>'))
            fig.update_layout(title=f"<b>台股 {debug_ticker} - {debug_strat} V06.2 軌跡圖</b>", xaxis_title="交易日期", yaxis_title="股價 (NT$)", template="plotly_dark")
            st.plotly_chart(fig, use_container_width=True)
            if not logs_df.empty: st.dataframe(logs_df, use_container_width=True, hide_index=True)
    else:
        st.info("💡 請按下上方「🚀 啟動 V06.2 台股全自動多因子掃描引擎」按鈕開始運算。")

# --- 分頁 4: 雲端自選清單線上管理 ---
with tab_manage:
    st.header("➕ 線上新增台股至雲端清單")
    st.markdown("填寫下方欄位按下送出，系統將會**自動寫入你的 Google 試算表**，60 秒內全自動同步！")
    
    with st.form("add_tw_stock_form"):
        new_ticker = st.text_input("🎯 股票代號 (例如: 2330 或 2454)", placeholder="2330").strip().upper()
        new_name = st.text_input("🏷️ 公司中文名稱 (例如: 台積電)", placeholder="台積電").strip()
        submit_btn = st.form_submit_button("🚀 一鍵同步新增至雲端試算表")
        
        if submit_btn:
            if not new_ticker:
                st.warning("⚠️ 請務必輸入股票代號！")
            else:
                form_url = f"https://docs.google.com/forms/d/e/{GOOGLE_FORM_ID}/formResponse"
                form_data = {
                    ENTRY_TICKER_ID: new_ticker,
                    ENTRY_NAME_ID: new_name
                }
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                }
                try:
                    res = requests.post(form_url, data=form_data, headers=headers)
                    if res.status_code == 200:
                        st.success(f"🎉 成功寫入！已將 【{new_ticker} {new_name}】 自動新增至雲端試算表！")
                        st.info("💡 請等待 60 秒快取更新，或至左側選單重新載入，即可在回測矩陣中看到新股票！")
                    else:
                        st.error(f"⚠️ 寫入失敗！Google 伺服器回應代碼：[{res.status_code}]")
                        st.caption("提示：若代碼為 400 代表欄位格式不合；若為 403 代表表單權限尚未開放。")
                except Exception as e:
                    st.error(f"❌ 連線發生錯誤: {e}")
