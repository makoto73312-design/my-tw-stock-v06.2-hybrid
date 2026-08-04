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
st.title("🇹🇼 台股量化投資沙盒 V06.2 (新增前向實盤驗證引擎)")
st.markdown("已實裝 **V06.2 五維獨立介面**：**動作分類看板**、**七維戰術矩陣**、**歷史驗證線圖**、**實盤前向驗證 (昨日訊號vs今日實況)** 與 **多線程平行加速引擎**")

# --- 2. 側邊欄控制台 ---
st.sidebar.header("⚙️ 全自動大掃描設定")

GSHEET_URL = "https://docs.google.com/spreadsheets/d/1Gmy2iLCICdI5UtdfLW5o4brX3l-J3-ZfwSUSBrz4bfo/edit?usp=sharing"
GOOGLE_FORM_ID = "1FAIpQLSfFyZVyj0gvInJomErH1shWrIClFF1CEjWKXtQJYkzxSgRcEg"
ENTRY_TICKER_ID = "entry.40654407"
ENTRY_NAME_ID = "entry.1671985547"

@st.cache_data(ttl=60)
def get_tickers_from_sheet(url):
    try:
        if "docs.google.com" not in url: return "2330, 2317", {}
        csv_url = url.split("/edit")[0] + "/export?format=csv"
        df = pd.read_csv(csv_url, header=None)
        tickers = df.iloc[:, 0].dropna().astype(str).str.strip().str.upper().tolist()
        custom_names_dict = {}
        if df.shape[1] > 1:
            raw_names = df.iloc[:, 1].fillna("").astype(str).str.strip().tolist()
            for i, t in enumerate(tickers):
                if i < len(raw_names) and str(raw_names[i]).strip() and str(raw_names[i]).strip().lower() not in ["nan", "none", ""]:
                    custom_names_dict[t] = str(raw_names[i]).strip()
        valid_tickers = [t for t in tickers if len(t) > 0 and t != "股票代號" and not t.startswith("202")]
        return ", ".join(valid_tickers) if valid_tickers else "2330, 2317, 2454, 2603, 3231", custom_names_dict
    except Exception: return "2330, 2317, 2454, 2603, 3231", {}

default_tickers, cloud_names_dict = get_tickers_from_sheet(GSHEET_URL)
tickers_input = st.sidebar.text_area("📡 當前雲端同步清單 (可貼 Excel)", default_tickers, height=120)

temp_raw_list = [t.strip().upper() for t in re.split(r'[\n\r,\s]+', tickers_input) if t.strip()]
raw_list = list(dict.fromkeys(temp_raw_list))
processed_tickers = [f"{t}.TW" if t.isdigit() else t for t in raw_list]

backtest_days = st.sidebar.slider("歷史回測天數設定", min_value=100, max_value=500, value=300, step=50)
enable_fcf_filter = st.sidebar.checkbox("🛡️ 啟用「自由現金流 > 0」安全過濾", value=True)
enable_earnings_shield = st.sidebar.checkbox("💣 啟用「3 天內發布財報」強制避險", value=True)

# --- 3. 大環境雷達 ---
@st.cache_data(ttl=1800)
def fetch_tw_macro_environment():
    try:
        vix_df, tw_df = yf.download("^VIX", period="5d", progress=False), yf.download("^TWII", period="1y", progress=False)
        vix_clean, tw_clean = vix_df.dropna(subset=['Close']), tw_df.dropna(subset=['Close'])
        vix_val = float(vix_clean['Close'].iloc[-1]) if not vix_clean.empty else 18.0
        tw_bull = float(tw_clean['Close'].iloc[-1]) >= float(tw_clean['Close'].rolling(200).mean().iloc[-1]) if not tw_clean.empty else True
        if vix_val >= 25 or not tw_bull: return vix_val, tw_bull, "🥶 極度謹慎型 (大盤空頭/高恐慌)"
        elif vix_val <= 15 and tw_bull: return vix_val, tw_bull, "🚀 大膽進攻型 (晴天多頭行情)"
        return vix_val, tw_bull, "🛡️ 標準平衡型 (常態橫盤整理)"
    except Exception: return 18.0, True, "🛡️ 標準平衡型 (預設)"

vix_score, is_tw_bull, market_posture = fetch_tw_macro_environment()

# --- 4. 基本面與公司名稱雷達 (含 3 層對照機制) ---
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
    
    # 🔍 3 層名稱尋找順序：1. Google Sheet 雲端自訂表 -> 2. 系統內建字典 -> 3. yfinance 英文清洗
    comp_name = cloud_dict.get(raw_id, "")
    if not comp_name or comp_name.lower() in ["nan", "none", ""]:
        comp_name = TW_NAMES_DICT.get(raw_id, "")

    f_info = {
        "comp_name": comp_name if comp_name else "台灣個股", 
        "pe": "-", "fcf": "-", "rev_growth": "-", 
        "is_fcf_positive": True, "near_earnings": False, 
        "news_alert": "🟢 無異常", "quality_tag": "一般"
    }
    try:
        tk = yf.Ticker(processed_id)
        info = tk.info or {}
        
        # 若雲端與內建庫均無中文名稱，嘗試從 yfinance 抓取 shortName 進行切割清洗
        if not comp_name:
            short_name = info.get('shortName', raw_id)
            if short_name and short_name != raw_id:
                for kw in ["TAIWAN", "STOCK", "LTD", "INC", "CORPORATION", "COMPANY", "CO."]:
                    short_name = short_name.upper().split(kw)[0].strip()
                f_info["comp_name"] = short_name if short_name else raw_id
            else:
                f_info["comp_name"] = raw_id
                
        pe, fcf, rev_g = info.get("trailingPE"), info.get("freeCashflow"), info.get("revenueGrowth")
        if pe is not None: f_info["pe"] = f"{pe:.1f}倍"
        if fcf is not None: f_info["fcf"], f_info["is_fcf_positive"] = f"NT${fcf / 1e8:.1f}億", (fcf >= 0)
        if rev_g is not None: f_info["rev_growth"] = f"{rev_g * 100:+.1f}%"
        if fcf and fcf > 0 and rev_g and rev_g > 0.10: f_info["quality_tag"] = "🔥 財報雙強"
        calendar = tk.calendar
        if calendar is not None and "Earnings Date" in calendar and len(calendar["Earnings Date"]) > 0:
            if 0 <= (pd.to_datetime(calendar["Earnings Date"][0]) - pd.to_datetime(datetime.now().date())).days <= 3: f_info["near_earnings"] = True
        bad_count = sum(1 for n in (tk.news or [])[:5] if any(kw in n.get("title", "").upper() for kw in ["LAWSUIT", "PROBE", "DOWNGRADE", "MISSED"]))
        if bad_count >= 1: f_info["news_alert"] = f"⚠️ 掃描到 {bad_count} 則利空新聞"
    except Exception: pass
    return f_info

# --- 5. 技術指標 ---
def calculate_indicators(df):
    high_low_diff = (df['High'] - df['Low']).replace(0, 0.001) 
    mf_multiplier = ((df['Close'] - df['Low']) - (df['High'] - df['Close'])) / high_low_diff
    df['主力籌碼'] = (df['Volume'] * mf_multiplier / 1000).round(2)
    for p in [5,10,14,20,21,30,50,200]: df[f'MA{p}' if p<50 else f'{p}MA'] = df['Close'].rolling(p).mean()
    df['ROC14'] = df['Close'].pct_change(14)
    delta = df['Close'].diff()
    df['RSI_14'] = 100 - (100 / (1 + delta.clip(lower=0).ewm(com=13, adjust=False).mean() / -delta.clip(upper=0).ewm(com=13, adjust=False).mean().replace(0, 0.001)))
    df['Vol_MA20'] = df['Volume'].rolling(20).mean()
    for q in [80,90,95]: df[f'主力籌碼_Q{q}'] = df['主力籌碼'].rolling(50).quantile(q/100)
    df['MACD'] = df['Close'].ewm(span=12, adjust=False).mean() - df['Close'].ewm(span=26, adjust=False).mean()
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACD_Hist'] = df['MACD'] - df['Signal']
    macd_shrink = [0] * len(df); hist = df['MACD_Hist'].values
    for i in range(1, len(df)): macd_shrink[i] = macd_shrink[i-1] + 1 if hist[i] < 0 and hist[i] > hist[i-1] else 0
    df['MACD_Shrink'] = macd_shrink
    return df

# --- 6. 歷史回測引擎 ---
def run_backtest_engine(df, strategy_name, days, posture, fund_info):
    valid_df = df.dropna(subset=['200MA', 'ROC14', 'MACD_Hist', 'RSI_14', 'Vol_MA20']).tail(days).copy()
    if len(valid_df) < 5: return "⚠️ 數據不足", 0, 0, 0, 0, "❌ 不推薦", "🛑 數據不足", "-", "-", "-", [], [], [], valid_df, 0.0, 0.0

    if "🚀" in posture: rsi_max, vol_mult, dip_pct, rsi_min, chip_col = 75, 1.05, -0.07, 35, '主力籌碼_Q80'
    elif "🥶" in posture: rsi_max, vol_mult, dip_pct, rsi_min, chip_col = 65, 1.40, -0.12, 25, '主力籌碼_Q95'
    else: rsi_max, vol_mult, dip_pct, rsi_min, chip_col = 70, 1.15, -0.07, 30, '主力籌碼_Q90'

    if "A:" in strategy_name: s_ma, d_ma, stop_loss_pct = valid_df['MA5'], valid_df['MA14'], 0.04
    elif "B:" in strategy_name: s_ma, d_ma, stop_loss_pct = valid_df['MA14'], valid_df['MA21'], 0.05
    elif "C:" in strategy_name: s_ma, d_ma, stop_loss_pct = valid_df['MA10'], valid_df['MA30'], 0.07
    elif "D:" in strategy_name: s_ma, d_ma, stop_loss_pct = valid_df['MA20'], valid_df['200MA'], 0.04
    else: s_ma, d_ma, stop_loss_pct = valid_df['MA5'], valid_df['MA20'], 0.04

    is_entangled_arr = (((valid_df[['MA5', 'MA14', '50MA']].max(axis=1) - valid_df[['MA5', 'MA14', '50MA']].min(axis=1)) / valid_df['50MA'].replace(0, 0.001)) < 0.025).values
    closes, highs, lows = valid_df['Close'].values, valid_df['High'].values, valid_df['Low'].values
    s_mas, m200s, r14s, rsis = s_ma.values, valid_df['200MA'].values, valid_df['ROC14'].values, valid_df['RSI_14'].values
    vols, vol_m20s = valid_df['Volume'].values, valid_df['Vol_MA20'].values
    m_shrinks, m_hists, m_flows, chip_threshs = valid_df['MACD_Shrink'].values, valid_df['MACD_Hist'].values, valid_df['主力籌碼'].values, valid_df[chip_col].values

    has_position, entry_price, highest_price_since_entry = False, 0, 0
    total_trades, win_trades, total_return, total_gross_profit, total_gross_loss = 0, 0, 0.0, 0.0, 0.0
    trade_logs, plot_buys, plot_sells = [], [], []

    for i in range(len(valid_df)):
        date_str, close_p, high_p, low_p = valid_df.index[i].strftime('%Y-%m-%d'), closes[i], highs[i], lows[i]
        sma_p, m200_p, r14_p, rsi_p, vol_p, vol_m20_p = s_mas[i], m200s[i], r14s[i], rsis[i], vols[i], vol_m20s[i]
        m_shrink_p, m_hist_p, m_flow_p, chip_thresh_p = m_shrinks[i], m_hists[i], m_flows[i], chip_threshs[i]
        m_hist_y, is_entangled = m_hists[i-1] if i > 0 else 0, is_entangled_arr[i]

        if not has_position:
            is_buy = False
            if "A:" in strategy_name and (m_shrink_p >= 1 or (m_hist_p > m_hist_y and m_hist_p > 0)) and r14_p > 0 and rsi_p < rsi_max: is_buy = True
            elif ("B:" in strategy_name or "C:" in strategy_name) and (not is_entangled) and close_p > sma_p and vol_p > vol_m20_p * vol_mult: is_buy = True
            elif "D:" in strategy_name and m200_p > 0 and (close_p - m200_p)/m200_p <= dip_pct and m_shrink_p >= 1 and rsi_p < rsi_min: is_buy = True
            elif "E:" in strategy_name and m_flow_p > chip_thresh_p and m_flow_p > 0: is_buy = True
            
            if is_buy:
                has_position, entry_price, highest_price_since_entry = True, close_p, close_p
                total_trades += 1
                trade_logs.append({"交易日期": date_str, "動作狀態": "🟢 買入進場 (BUY)", "執行價格": f"NT${close_p:.2f}", "單筆報酬": "-"})
                plot_buys.append((valid_df.index[i], close_p))
        else:
            highest_price_since_entry = max(highest_price_since_entry, high_p)
            is_exit, exit_price = False, close_p
            if "D:" not in strategy_name:
                if low_p <= highest_price_since_entry * (1 - stop_loss_pct): is_exit, exit_price = True, highest_price_since_entry * (1 - stop_loss_pct)
                elif ("B:" in strategy_name or "C:" in strategy_name) and is_entangled: is_exit, exit_price = True, close_p
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
    today_str, current_close = valid_df.index[-1].strftime('%Y-%m-%d'), closes[-1]
    
    current_status, entry_price_str, sl_price_str, pnl_str = "💵 空手觀望 (CASH)", "-", "-", "-"

    if has_position:
        if last_action and last_action["交易日期"] == today_str and "BUY" in last_action["動作狀態"]:
            current_status, entry_price_str, pnl_str = "🚀 今日大膽建倉 (BUY)", f"NT${current_close:.2f}", "0.00%"
        else:
            current_status, entry_price_str, pnl_str = "📦 獲利續抱中 (HOLD)", f"NT${entry_price:.2f}", f"{(current_close - entry_price) / entry_price * 100:+.2f}%"
        sl_price_str = f"NT${highest_price_since_entry * (1 - stop_loss_pct):.2f}" if "D:" not in strategy_name else f"NT${max(entry_price * 0.95, m200s[-1]):.2f}"
    else:
        current_status = "🔴 今日觸發防守賣出 (SELL)" if last_action and last_action["交易日期"] == today_str and "SELL" in last_action["動作狀態"] else "💵 空手觀望 (CASH)"

    if enable_earnings_shield and fund_info["near_earnings"] and "BUY" in current_status: current_status = "💣 財報前夕/強制避險 (CASH)"
    if enable_fcf_filter and not fund_info["is_fcf_positive"] and "BUY" in current_status: current_status = "⚠️ 現金流不良/阻擋建倉 (CASH)"

    raw_entry_price = entry_price if has_position else 0.0
    if has_position: raw_sl_price = highest_price_since_entry * (1 - stop_loss_pct) if "D:" not in strategy_name else max(entry_price * 0.95, m200s[-1])
    else: raw_sl_price = 0.0

    return "📡 運算完畢", total_return, final_win_rate, total_trades, pf_str, stars, current_status, entry_price_str, sl_price_str, pnl_str, trade_logs, plot_buys, plot_sells, valid_df, raw_entry_price, raw_sl_price

# ⚡ 多線程 Worker 函數
def process_single_stock_tw(raw_name, ticker, cloud_dict, backtest_days, posture, strategies):
    try:
        df_stock = yf.download(ticker, period="2y", progress=False)
        if df_stock.empty: return [], {}, []
        df_stock.columns = [col[0] if isinstance(col, tuple) else col for col in df_stock.columns]
        df_stock = calculate_indicators(df_stock)
        
        df_temp_clean = df_stock.dropna(subset=['Close'])
        current_close = float(df_temp_clean['Close'].iloc[-1]) if not df_temp_clean.empty else 0.0
        fund_info = fetch_tw_fundamental_and_news(raw_name, ticker, cloud_dict)
        
        has_t_minus_1 = False
        df_yesterday = None
        if len(df_temp_clean) >= 2:
            has_t_minus_1 = True
            df_yesterday = df_stock.iloc[:-1].copy()
            today_k = df_temp_clean.iloc[-1]
            t_open, t_high, t_low, t_close = float(today_k['Open']), float(today_k['High']), float(today_k['Low']), float(today_k['Close'])
        
        stock_reports, stock_details, forward_reports = [], {}, []
        
        for strat in strategies:
            radar, ret, win, trades, pf, stars, cur_status, entry_price_val, sl_price, pnl, t_logs, p_buys, p_sells, v_df, raw_entry, raw_sl = run_backtest_engine(df_stock, strat, backtest_days, posture, fund_info)
            stock_details[(raw_name, strat)] = {"logs": pd.DataFrame(t_logs), "buys": p_buys, "sells": p_sells, "v_df": v_df}
            stock_reports.append({
                "台股代號": raw_name, "公司名稱": fund_info["comp_name"], "當前股價": f"NT${current_close:.2f}", 
                "策略手法": strat, "倉位狀態": cur_status, "基本面評價": fund_info["quality_tag"],
                "本益比 (PE)": fund_info["pe"], "自由現金流": fund_info["fcf"], "新聞警告": fund_info["news_alert"],
                "建議進場價(持股成本)": entry_price_val, "未實現損益": pnl, "嚴格防守價": sl_price, 
                "總報酬率": f"{ret * 100:+.2f}%", "歷史勝率": f"{win * 100:.1f}%", "交易次數": trades, "獲利因子": pf, "推薦指數": stars
            })
            
            # 2. 昨日訊號實盤比對 (包含【公司名稱】欄位)
            if has_t_minus_1:
                y_res = run_backtest_engine(df_yesterday, strat, backtest_days, posture, fund_info)
                y_cur_status = y_res[6]
                if "BUY" in y_cur_status and "大膽建倉" in y_cur_status:
                    y_stars, y_entry, y_sl = y_res[5], y_res[14], y_res[15]
                    if y_entry > 0:
                        forward_reports.append({
                            "股票代號": raw_name,
                            "公司名稱": fund_info["comp_name"],
                            "觸發策略": strat,
                            "歷史評級": y_stars,
                            "昨日建議進場價": y_entry,
                            "今日開盤跳空%": (t_open - y_entry) / y_entry * 100,
                            "盤中最高獲利空間%": (t_high - y_entry) / y_entry * 100,
                            "今日即時最新損益%": (t_close - y_entry) / y_entry * 100,
                            "盤中最大回撤%": (t_low - y_entry) / y_entry * 100,
                            "嚴格防守價": y_sl,
                            "防守線狀態": "🔴 已踩停損出局" if t_low <= y_sl else "🟢 安全未破"
                        })
        return stock_reports, stock_details, forward_reports
    except Exception: return [], {}, []

# --- 7. Session State 記憶庫 ---
if "calculated" not in st.session_state:
    st.session_state.calculated = False
    st.session_state.final_df = None
    st.session_state.forward_df = None
    st.session_state.detail_db = {}

# --- 8. 頂部總經抬頭控制卡 ---
col_v1, col_v2, col_v3 = st.columns(3)
col_v1.metric("VIX 恐慌指數", f"{vix_score:.2f}", delta="避險戒備" if vix_score >= 25 else "市場平穩", delta_color="inverse")
col_v2.metric("台灣加權指數 (大盤)", "年線之上 (多頭)" if is_tw_bull else "跌破年線 (空頭)")
col_v3.metric("系統自動環境姿態", market_posture)
st.divider()

if st.button("🚀 啟動 V06.2 台股全自動多因子掃描引擎 (⚡ 多線程平行加速)", use_container_width=True):
    with st.spinner("正在啟動 ThreadPoolExecutor 多線程引擎進行全維度平行運算..."):
        master_report, forward_report, strategies = [], [], ["A: 激進動能型", "B: 穩健波段型", "C: 槓桿防守型", "D: 均值回歸抄底型", "E: 籌碼主力跟隨型"]
        futures = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            for idx, ticker in enumerate(processed_tickers):
                futures.append(executor.submit(process_single_stock_tw, raw_list[idx], ticker, cloud_names_dict, backtest_days, market_posture, strategies))
        for f in futures:
            s_reports, s_details, f_reports = f.result()
            if s_reports: master_report.extend(s_reports)
            if s_details: st.session_state.detail_db.update(s_details)
            if f_reports: forward_report.extend(f_reports)
                
        st.session_state.final_df = pd.DataFrame(master_report)
        st.session_state.forward_df = pd.DataFrame(forward_report)
        st.session_state.calculated = True
        st.success("📊 V06.2 台股多因子矩陣與前向驗證計算完成！請至下方各分頁切換檢視。")

# --- 9. 網頁五獨立分頁系統 ---
tab_v061, tab_matrix, tab_debug, tab_forward, tab_manage = st.tabs([
    "📊 倉位動作與多因子總表", "🎯 七維量化戰術矩陣", "🔍 歷史回測與線圖驗證", "📈 昨日訊號 vs 今日成效", "➕ 雲端自選清單管理"
])

# (Tab 1) 倉位動作總表
with tab_v061:
    if st.session_state.calculated:
        df_res = st.session_state.final_df
        st.subheader("🎯 倉位狀態分類看板 (動作快選)")
        buy_df, sell_df, hold_df = df_res[df_res['倉位狀態'].str.contains("BUY", na=False)], df_res[df_res['倉位狀態'].str.contains("SELL", na=False)], df_res[df_res['倉位狀態'].str.contains("HOLD", na=False)]
        risk_df, cash_df = df_res[df_res['倉位狀態'].str.contains("現金流不良|財報前夕", na=False)], df_res[df_res['倉位狀態'].str.contains("空手觀望", na=False)]
        col_c1, col_c2, col_c3, col_c4, col_c5 = st.columns(5)
        col_c1.metric("🚀 今日建議建倉", f"{len(buy_df)} 筆"); col_c2.metric("🔴 觸發防守賣出", f"{len(sell_df)} 筆"); col_c3.metric("📦 獲利續抱中", f"{len(hold_df)} 筆")
        col_c4.metric("🛡️ 風控安全閥攔截", f"{len(risk_df)} 筆"); col_c5.metric("💵 空手觀望", f"{len(cash_df)} 筆")
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
            if not risk_df.empty: st.dataframe(risk_df[display_cols], use_container_width=True, hide_index=True)
            else: st.info("今日無被風控閥攔截之個股。")
        with st.expander(f"💵 空手觀望 ({len(cash_df)} 筆)", expanded=False):
            if not cash_df.empty: st.dataframe(cash_df[display_cols], use_container_width=True, hide_index=True)

        st.divider(); st.subheader("📋 完整多因子對照總表")
        def apply_block_shading(df):
            styles = pd.DataFrame('', index=df.index, columns=df.columns)
            for i, ticker in enumerate(df["台股代號"].unique()):
                styles.loc[df["台股代號"] == ticker, :] = 'background-color: rgba(128, 128, 128, 0.16)' if i % 2 == 0 else 'background-color: rgba(0, 0, 0, 0)'
            return styles
        st.dataframe(df_res.style.apply(apply_block_shading, axis=None), use_container_width=True, hide_index=True)
    else:
        st.info("💡 請按下上方「🚀 啟動 V06.2 台股全自動多因子掃描引擎」按鈕開始運算。")

# (Tab 2) 七維戰術矩陣
with tab_matrix:
    st.header("🎯 七維量化戰術矩陣看板 (跨策略共振與型態快選)")
    with st.expander("📖 點擊展開：【七維戰術矩陣】說明手冊", expanded=False): st.markdown("（手冊說明與原本定義完全相同）")
    st.divider()
    
    if st.session_state.calculated:
        df_res = st.session_state.final_df
        matrix_data = []
        for t in df_res['台股代號'].unique():
            sub = df_res[df_res['台股代號'] == t].set_index('策略手法')
            get_s = lambda name: sub.loc[name, '倉位狀態'] if name in sub.index else ''
            get_r = lambda name: sub.loc[name, '推薦指數'] if name in sub.index else ''
            
            st_A, st_B, st_C, st_D, st_E = get_s('A: 激進動能型'), get_s('B: 穩健波段型'), get_s('C: 槓桿防守型'), get_s('D: 均值回歸抄底型'), get_s('E: 籌碼主力跟隨型')
            rec_A, rec_B, rec_C, rec_D, rec_E = get_r('A: 激進動能型'), get_r('B: 穩健波段型'), get_r('C: 槓桿防守型'), get_r('D: 均值回歸抄底型'), get_r('E: 籌碼主力跟隨型')
            
            sells = [s for s in [st_A, st_B, st_C, st_D, st_E] if 'SELL' in s]
            buy_rows = [sub.loc[s] for s in sub.index if 'BUY' in sub.loc[s, '倉位狀態']]
            
            matrix_data.append({
                "台股代號": t, "公司名稱": sub['公司名稱'].iloc[0] if '公司名稱' in sub.columns else '台灣個股', "當前股價": sub['當前股價'].iloc[0] if '當前股價' in sub.columns else '-',
                "🔥全面共振": ('BUY' in st_A) and ('BUY' in st_B or 'BUY' in st_C) and ('BUY' in st_E) and any(r != '❌ 不推薦' for r in [rec_A, rec_B, rec_C, rec_E]),
                "🌊波段突破": ('BUY' in st_B and rec_B in ['⭐⭐⭐⭐', '⭐⭐⭐⭐⭐']) or ('BUY' in st_C and rec_C in ['⭐⭐⭐⭐', '⭐⭐⭐⭐⭐']),
                "🕵️籌碼吸籌": ('BUY' in st_E or 'HOLD' in st_E) and ('CASH' in st_B and 'CASH' in st_C) and (rec_E != '❌ 不推薦'),
                "🛒價值窪地": ('BUY' in st_D), "🛑風控攔截": any(('現金流' in s or '財報' in s) for s in [st_A, st_B, st_C, st_D, st_E]),
                "⚠️頭部背離": ('HOLD' in st_B or 'HOLD' in st_C) and ('SELL' in st_A or 'SELL' in st_E), "🔴集體撤退": len(sells) >= 2,
                "❌洗盤怪獸": len(buy_rows) >= 2 and all(b['推薦指數'] == '❌ 不推薦' for b in buy_rows)
            })
        m_df = pd.DataFrame(matrix_data)
        
        col_m1, col_m2, col_m3, col_m4, col_m5, col_m6, col_m7 = st.columns(7)
        keys = ['🔥全面共振','🌊波段突破','🕵️籌碼吸籌','🛒價值窪地','⚠️頭部背離','🔴集體撤退','❌洗盤怪獸']
        cols = [col_m1, col_m2, col_m3, col_m4, col_m5, col_m6, col_m7]
        for k, col in zip(keys, cols): col.metric(k, f"{len(m_df[m_df[k]])} 檔")
        
        st.divider()
        
        titles = ["🔥 1. 全面共振多頭", "🌊 2. 高勝率波段突破", "🕵️ 3. 籌碼大戶潛伏", "🛒 4. 價值超跌窪地", "⚠️ 5. 動能/籌碼頭部背離", "🔴 6. 多頭集體撤退", "❌ 7. 高波動洗盤怪獸"]
        for k, title in zip(keys, titles):
            sub_df = m_df[m_df[k]][['台股代號', '公司名稱', '當前股價']]
            with st.expander(f"{title} — {len(sub_df)} 檔", expanded=len(sub_df)>0):
                if not sub_df.empty: st.dataframe(sub_df, use_container_width=True, hide_index=True)
                else: st.info("今日無訊號。")
    else:
        st.info("💡 請按下上方「🚀 啟動 V06.2 台股全自動多因子掃描引擎」按鈕開始運算。")

# (Tab 3) 回測線圖
with tab_debug:
    st.header("🛠️ 台股歷史 K 線與訊號檢查器")
    if st.session_state.calculated:
        c1, c2 = st.columns(2)
        with c1: dt = st.selectbox("🎯 選擇代號", raw_list)
        with c2: ds = st.selectbox("🔮 選擇策略", ["A: 激進動能型", "B: 穩健波段型", "C: 槓桿防守型", "D: 均值回歸抄底型", "E: 籌碼主力跟隨型"])
        if (dt, ds) in st.session_state.detail_db:
            dp = st.session_state.detail_db[(dt, ds)]
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=dp["v_df"].index, y=dp["v_df"]['Close'], mode='lines', name='收盤價', line=dict(color='lightgrey', width=1.5)))
            if dp["buys"]: fig.add_trace(go.Scatter(x=[b[0] for b in dp["buys"]], y=[b[1] for b in dp["buys"]], mode='markers', name='🟢 BUY', marker=dict(symbol='triangle-up', size=12, color='#00FF00')))
            if dp["sells"]: fig.add_trace(go.Scatter(x=[s[0] for s in dp["sells"]], y=[s[1] for s in dp["sells"]], mode='markers', name='🔴 SELL', marker=dict(symbol='triangle-down', size=12, color='#FF0000')))
            fig.update_layout(title=f"<b>{dt} - {ds} 軌跡圖</b>", xaxis_title="日期", yaxis_title="股價", template="plotly_dark")
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(dp["logs"], use_container_width=True, hide_index=True)
    else:
        st.info("💡 請按下上方「🚀 啟動 V06.2 台股全自動多因子掃描引擎」按鈕開始運算。")

# (Tab 4) 前向實盤驗證引擎 (包含【股票代號】與【公司名稱】)
with tab_forward:
    st.header("📈 前向實盤驗證 (昨日訊號 vs 今日成效)")
    st.markdown("由系統自動回到昨天收盤抓出買訊，並與今日最新盤中跳動實況進行殘酷比對，檢驗策略抗洗盤與開盤跳空能力。")
    if st.session_state.calculated:
        f_df = st.session_state.forward_df
        if f_df is not None and not f_df.empty:
            safe_count = len(f_df[f_df['防守線狀態'].str.contains("🟢", na=False)])
            stop_count = len(f_df[f_df['防守線狀態'].str.contains("🔴", na=False)])
            
            col_f1, col_f2, col_f3 = st.columns(3)
            col_f1.metric("昨日觸發買進訊號總數", f"{len(f_df)} 筆")
            col_f2.metric("今日盤中安全存活數 (🟢 強勢延續)", f"{safe_count} 筆")
            col_f3.metric("今日踩停損/洗盤出局數 (🔴 防守觸發)", f"{stop_count} 筆")
            
            st.divider()
            st.subheader("📋 實盤驗證明細表")
            
            disp_df = f_df.copy()
            disp_df['昨日建議進場價'] = disp_df['昨日建議進場價'].apply(lambda x: f"NT${x:.2f}")
            disp_df['嚴格防守價'] = disp_df['嚴格防守價'].apply(lambda x: f"NT${x:.2f}")
            
            pct_cols = ['今日開盤跳空%', '盤中最高獲利空間%', '今日即時最新損益%', '盤中最大回撤%']
            for col in pct_cols: disp_df[col] = disp_df[col].apply(lambda x: f"{x:+.2f}%")
                
            def style_forward_df(val):
                if isinstance(val, str):
                    if val.startswith('+') and '%' in val: return 'color: #00FF00;'
                    elif val.startswith('-') and '%' in val: return 'color: #FF4B4B;'
                    elif "🟢" in val: return 'color: #00FF00;'
                    elif "🔴" in val: return 'color: #FF4B4B;'
                return ''
            
            st.dataframe(disp_df.style.map(style_forward_df), use_container_width=True, hide_index=True)
        else: st.info("昨日無任何觸發建倉之訊號，或數據不足以進行實盤驗證。")
    else: st.info("💡 請按下上方啟動按鈕開始運算。")

# (Tab 5) 雲端管理
with tab_manage:
    st.header("➕ 線上新增台股至雲端清單")
    with st.form("add_tw_stock_form"):
        new_ticker = st.text_input("🎯 股票代號 (例如: 2330)").strip().upper()
        new_name = st.text_input("🏷️ 公司中文名稱 (例如: 台積電)").strip()
        if st.form_submit_button("🚀 一鍵同步新增至雲端試算表"):
            if not new_ticker: st.warning("⚠️ 請務必輸入股票代號！")
            else:
                try:
                    res = requests.post(f"https://docs.google.com/forms/d/e/{GOOGLE_FORM_ID}/formResponse", data={ENTRY_TICKER_ID: new_ticker, ENTRY_NAME_ID: new_name}, headers={"User-Agent": "Mozilla/5.0"})
                    if res.status_code == 200: st.success(f"🎉 成功寫入！已將 【{new_ticker} {new_name}】 新增至雲端！")
                    else: st.error(f"⚠️ 寫入失敗！代碼：[{res.status_code}]")
                except Exception as e: st.error(f"❌ 連線發生錯誤: {e}")
