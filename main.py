#!/usr/bin/env python3
"""
台股自動化交易計畫 TG Bot
不需要修改任何程式碼，只要設定 GitHub Secrets 就能運作
"""

import os, sys, io, datetime
import requests
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.font_manager as fm
import yfinance as yf
from groq import Groq

# ════════════════════════════════════════════════════════
#  環境變數（從 GitHub Secrets 自動帶入，不用手動填）
# ════════════════════════════════════════════════════════
MODE      = os.environ.get('MODE', 'premarket')        # premarket / postmarket
TG_TOKEN  = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TG_CHAT   = os.environ.get('TELEGRAM_CHAT_ID', '')
client    = Groq(api_key=os.environ.get('GROQ_API_KEY', ''))

# ════════════════════════════════════════════════════════
#  中文字型設定
# ════════════════════════════════════════════════════════
import glob as _glob

def _setup_cjk_font():
    """自動找系統上的 Noto CJK 字型並註冊"""
    candidates = [
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/opentype/noto/NotoSansCJKtc-Regular.otf',
        '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc',
    ]
    # 也嘗試 glob 搜尋
    candidates += _glob.glob('/usr/share/fonts/**/Noto*CJK*.tt[cf]', recursive=True)
    candidates += _glob.glob('/usr/share/fonts/**/Noto*CJK*.otf', recursive=True)

    for path in candidates:
        if os.path.exists(path):
            try:
                fm.fontManager.addfont(path)
                prop = fm.FontProperties(fname=path)
                name = prop.get_name()
                plt.rcParams['font.sans-serif'] = [name, 'DejaVu Sans']
                plt.rcParams['axes.unicode_minus'] = False
                print(f'  ✓ 字型載入成功: {name} ({path})')
                return True
            except Exception as e:
                print(f'  ⚠ 字型載入失敗 {path}: {e}')
    # fallback
    plt.rcParams['font.sans-serif'] = ['Noto Sans CJK TC', 'Noto Sans TC',
                                        'WenQuanYi Micro Hei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    print('  ⚠ 未找到 CJK 字型，使用 fallback')
    return False

_setup_cjk_font()

# ════════════════════════════════════════════════════════
#  監控標的（可自行新增修改）
# ════════════════════════════════════════════════════════
TWII = '^TWII'

ETFS = {
    '0050':   '0050.TW',
    '00878':  '00878.TW',
    '006208': '006208.TW',
    '00929':  '00929.TW',
    '0056':   '0056.TW',
}

STOCKS = {
    '台積電': '2330.TW',
    '鴻海':   '2317.TW',
    '聯發科': '2454.TW',
    '富邦金': '2881.TW',
    '國泰金': '2882.TW',
    '中鋼':   '2002.TW',
}

US_INDICES = {
    'S&P500': '^GSPC',
    'Nasdaq':  '^IXIC',
    '費半':    '^SOX',
    '日經':    '^N225',
}

# ════════════════════════════════════════════════════════
#  資料抓取
# ════════════════════════════════════════════════════════
def safe_pct(new, old):
    try:
        return float((new - old) / old * 100)
    except:
        return 0.0

def fetch(symbol, period='5d', interval='1d'):
    try:
        df = yf.Ticker(symbol).history(period=period, interval=interval)
        return df if df is not None and len(df) >= 2 else None
    except Exception as e:
        print(f'  ⚠ {symbol}: {e}')
        return None

def fetch_all():
    data = {}

    # 加權指數（抓 10 天做趨勢圖）
    df = fetch(TWII, '10d', '1d')
    if df is not None:
        c = df['Close'].values
        v = df['Volume'].values if 'Volume' in df.columns else [0]
        data['twii'] = {
            'close':   float(c[-1]),
            'prev':    float(c[-2]),
            'pct':     safe_pct(c[-1], c[-2]),
            'history': [float(x) for x in c[-5:]],
            'volume':  float(v[-1]) if len(v) > 0 else 0,
        }
    else:
        data['twii'] = {'close': 0, 'prev': 0, 'pct': 0, 'history': [], 'volume': 0}

    # ETF
    data['etfs'] = {}
    for name, sym in ETFS.items():
        df = fetch(sym)
        if df is not None:
            c = df['Close'].values
            data['etfs'][name] = {
                'close': float(c[-1]),
                'prev':  float(c[-2]),
                'pct':   safe_pct(c[-1], c[-2]),
            }

    # 個股
    data['stocks'] = {}
    for name, sym in STOCKS.items():
        df = fetch(sym)
        if df is not None:
            c = df['Close'].values
            data['stocks'][name] = {
                'close': float(c[-1]),
                'prev':  float(c[-2]),
                'pct':   safe_pct(c[-1], c[-2]),
            }

    # 美股指數
    data['us'] = {}
    for name, sym in US_INDICES.items():
        df = fetch(sym)
        if df is not None:
            c = df['Close'].values
            data['us'][name] = {
                'close': float(c[-1]),
                'prev':  float(c[-2]),
                'pct':   safe_pct(c[-1], c[-2]),
            }

    return data

# ════════════════════════════════════════════════════════
#  Claude AI 分析
# ════════════════════════════════════════════════════════
def build_prompt(data):
    twii   = data.get('twii', {})
    etfs   = data.get('etfs', {})
    stocks = data.get('stocks', {})
    us     = data.get('us', {})
    today  = datetime.datetime.now().strftime('%Y年%m月%d日')

    def fmt(d):
        return '\n'.join(f'  {k}: {v["close"]:.2f} ({v["pct"]:+.2f}%)' for k, v in d.items())

    hist_str = ' → '.join(str(int(x)) for x in twii.get('history', []))

    if MODE == 'premarket':
        return f"""你是一位專業的台股交易分析師，今天是 {today}，現在是台灣時間早上 8 點。

【最新市場數據】
加權指數: {twii.get('close', 0):.0f} 點（昨收 {twii.get('prev', 0):.0f}，漲跌 {twii.get('pct', 0):+.2f}%）
近5日走勢: {hist_str}

美股（前一日收盤）:
{fmt(us)}

熱門 ETF:
{fmt(etfs)}

熱門個股:
{fmt(stocks)}

請用繁體中文生成今日「盤前交易計畫」，格式如下，每點精簡有力：

【📊 盤前總覽】
（市場氣氛評估：偏多/偏空/盤整，100字以內）

【🌐 外資動向研判】
（依美股表現推估，50字）

【🎯 今日三大關注標的】
1. 標的：理由（30字）
2. 標的：理由（30字）
3. 標的：理由（30字）

【📈 加權指數關鍵位】
• 壓力：___點（原因）
• 支撐：___點（原因）

【⚠️ 今日風險提示】
（1~2點，50字）

【💡 操作建議】
• 保守型：___
• 積極型：___"""

    else:
        return f"""你是一位專業的台股交易分析師，今天是 {today}，現在是台灣時間下午 3 點盤後。

【今日收盤數據】
加權指數: {twii.get('close', 0):.0f} 點（昨收 {twii.get('prev', 0):.0f}，漲跌 {twii.get('pct', 0):+.2f}%）
近5日走勢: {hist_str}

美股（最新）:
{fmt(us)}

熱門 ETF 今日表現:
{fmt(etfs)}

熱門個股今日表現:
{fmt(stocks)}

請用繁體中文生成今日「盤後分析報告」，格式如下：

【📊 今日盤面總結】
（強弱評估、主流族群，100字）

【🏆 今日強勢族群】
（2~3個，含原因，50字）

【📉 今日弱勢族群】
（1~2個，含原因，50字）

【💹 資金動向分析】
（外資/投信/自營，50字）

【🔭 明日盤面展望】
（偏多/偏空/盤整，理由，50字）

【📌 明日盤前三大留意點】
1. ___
2. ___
3. ___"""

def generate_analysis(data):
    res = client.chat.completions.create(
        model='llama-3.3-70b-versatile',
        messages=[{'role': 'user', 'content': build_prompt(data)}],
        max_tokens=1200,
    )
    return res.choices[0].message.content

# ════════════════════════════════════════════════════════
#  繪圖設定
# ════════════════════════════════════════════════════════
BG      = '#0a0a18'
CARD    = '#12122a'
CARD2   = '#1a1a35'
GREEN   = '#00e676'
GREEN2  = '#1de9b6'
RED     = '#ff5252'
YELLOW  = '#ffd740'
BLUE    = '#40c4ff'
PURPLE  = '#e040fb'
GRAY    = '#607d8b'
GRAY2   = '#455a64'
WHITE   = '#eceff1'
ACCENT  = '#7c4dff'

def col(v):   return GREEN if v >= 0 else RED
def arrow(v): return '▲' if v >= 0 else '▼'
def badge_col(v): return ('#003300', GREEN) if v >= 0 else ('#330000', RED)

def rounded_rect(ax, x, y, w, h, radius=0.02, fc=CARD2, ec=GRAY2, lw=0.8, alpha=1.0):
    from matplotlib.patches import FancyBboxPatch
    p = FancyBboxPatch((x, y), w, h,
                       boxstyle=f'round,pad={radius}',
                       facecolor=fc, edgecolor=ec,
                       linewidth=lw, alpha=alpha,
                       transform=ax.transAxes, clip_on=False)
    ax.add_patch(p)

def sparkline_mini(ax, xs, ys, x0, y0, w, h, color):
    """在 ax 的 axes 座標裡畫一條迷你折線"""
    if len(ys) < 2:
        return
    ys = np.array(ys, dtype=float)
    xs = np.arange(len(ys))
    yn = (ys - ys.min()) / max(ys.max() - ys.min(), 1e-9)
    px = x0 + xs / (len(xs)-1) * w
    py = y0 + yn * h
    ax.plot(px, py, color=color, linewidth=1.2, alpha=0.8,
            transform=ax.transAxes, clip_on=False, solid_capstyle='round')

def draw_item_cards(ax, items, title, history_map=None):
    """每個標的畫一張小卡片"""
    ax.set_facecolor(BG)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')

    # 標題列
    ax.text(0.5, 0.97, title, ha='center', va='top',
            color=YELLOW, fontsize=10.5, fontweight='bold')

    if not items:
        return

    item_list = list(items.items())
    n = len(item_list)
    cols = 2
    rows = (n + 1) // 2
    card_w = 0.46
    card_h = 0.80 / rows - 0.04
    gap_x  = 0.04
    gap_y  = 0.04
    start_y = 0.90

    for i, (name, v) in enumerate(item_list):
        row = i // cols
        col_idx = i % cols
        x = col_idx * (card_w + gap_x) + 0.02
        y = start_y - (row + 1) * (card_h + gap_y)

        pct   = v.get('pct', 0)
        close = v.get('close', 0)
        prev  = v.get('prev', 0)
        diff  = close - prev
        c     = col(pct)
        bg_c  = '#0d1f0d' if pct >= 0 else '#1f0d0d'
        ec_c  = '#1a4d1a' if pct >= 0 else '#4d1a1a'

        rounded_rect(ax, x, y, card_w, card_h, radius=0.025,
                     fc=bg_c, ec=ec_c, lw=0.9)

        # 標的名稱
        ax.text(x + 0.04, y + card_h - 0.03, name,
                ha='left', va='top', color=WHITE,
                fontsize=8.5, fontweight='bold',
                transform=ax.transAxes, clip_on=False)

        # 收盤價
        ax.text(x + card_w - 0.03, y + card_h - 0.03,
                f'{close:.2f}',
                ha='right', va='top', color=WHITE,
                fontsize=9, fontweight='bold',
                transform=ax.transAxes, clip_on=False)

        # 漲跌幅 badge
        badge_txt = f'{arrow(pct)} {abs(pct):.2f}%'
        ax.text(x + 0.04, y + 0.04,
                badge_txt,
                ha='left', va='bottom', color=c,
                fontsize=9, fontweight='bold',
                transform=ax.transAxes, clip_on=False)

        # 漲跌點數
        ax.text(x + card_w - 0.03, y + 0.04,
                f'{diff:+.2f}',
                ha='right', va='bottom', color=c,
                fontsize=7.5,
                transform=ax.transAxes, clip_on=False)

        # 迷你 sparkline（如果有歷史）
        hist = (history_map or {}).get(name)
        if hist and len(hist) >= 2:
            sparkline_mini(ax, None, hist,
                           x + 0.04, y + card_h * 0.35,
                           card_w - 0.08, card_h * 0.28, c)

def draw_gauge(ax, up_ratio):
    """半圓情緒量表"""
    all_segs = [
        (0.00, 0.20, RED,    '強空'),
        (0.20, 0.40, '#ff8f00', '偏空'),
        (0.40, 0.60, YELLOW, '盤整'),
        (0.60, 0.80, GREEN2, '偏多'),
        (0.80, 1.00, GREEN,  '強多'),
    ]
    cx, cy, r_out, r_in = 0.5, 0.22, 0.38, 0.22
    for lo, hi, gc, _ in all_segs:
        thetas = np.linspace(np.pi*(1-hi), np.pi*(1-lo), 40)
        for t1, t2 in zip(thetas[:-1], thetas[1:]):
            xs = [cx+r_in*np.cos(t1), cx+r_out*np.cos(t1),
                  cx+r_out*np.cos(t2), cx+r_in*np.cos(t2)]
            ys = [cy+r_in*np.sin(t1), cy+r_out*np.sin(t1),
                  cy+r_out*np.sin(t2), cy+r_in*np.sin(t2)]
            ax.fill(xs, ys, color=gc, alpha=0.75, transform=ax.transAxes)

    # 指針
    angle = np.pi * (1 - up_ratio)
    nr = 0.32
    ax.annotate('', xy=(cx+nr*np.cos(angle), cy+nr*np.sin(angle)),
                xytext=(cx, cy),
                arrowprops=dict(arrowstyle='->', color=WHITE, lw=2.5),
                transform=ax.transAxes)
    ax.plot(cx, cy, 'o', color=WHITE, markersize=7, transform=ax.transAxes)

    if up_ratio >= 0.8:   label, lc = '強力偏多', GREEN
    elif up_ratio >= 0.6: label, lc = '偏多',    GREEN2
    elif up_ratio >= 0.4: label, lc = '盤整',    YELLOW
    elif up_ratio >= 0.2: label, lc = '偏空',    '#ff8f00'
    else:                 label, lc = '強力偏空', RED

    ax.text(cx, 0.60, label, ha='center', va='center', color=lc,
            fontsize=13, fontweight='bold', transform=ax.transAxes)
    ax.text(cx, 0.50, f'上漲 {up_ratio*100:.0f}%', ha='center',
            color=GRAY, fontsize=9, transform=ax.transAxes)
    ax.text(0.10, 0.06, '空', color=RED, fontsize=8, transform=ax.transAxes)
    ax.text(0.88, 0.06, '多', color=GREEN, fontsize=8, transform=ax.transAxes)


def draw_us_cards(ax, us):
    """美股四格小卡"""
    ax.set_facecolor(BG); ax.set_xlim(0,1); ax.set_ylim(0,1); ax.axis('off')
    ax.text(0.5, 0.97, '美股指數', ha='center', va='top',
            color=YELLOW, fontsize=10.5, fontweight='bold')
    items = list(us.items())
    cols, rows = 2, 2
    cw, ch = 0.44, 0.34
    gx, gy = 0.06, 0.06
    positions = [(0.03, 0.54), (0.53, 0.54), (0.03, 0.12), (0.53, 0.12)]
    for i, (name, v) in enumerate(items[:4]):
        x, y = positions[i]
        pct = v.get('pct', 0)
        close = v.get('close', 0)
        c = col(pct)
        bg = '#0d1f0d' if pct >= 0 else '#1f0d0d'
        ec = '#1a4d1a' if pct >= 0 else '#4d1a1a'
        rounded_rect(ax, x, y, cw, ch, radius=0.03, fc=bg, ec=ec, lw=0.8)
        ax.text(x+0.04, y+ch-0.04, name, ha='left', va='top',
                color=GRAY, fontsize=8, transform=ax.transAxes)
        ax.text(x+cw/2, y+ch*0.52, f'{arrow(pct)}{abs(pct):.2f}%',
                ha='center', va='center', color=c,
                fontsize=11, fontweight='bold', transform=ax.transAxes)
        ax.text(x+cw/2, y+0.05, f'{close:,.2f}',
                ha='center', va='bottom', color=WHITE,
                fontsize=8, transform=ax.transAxes)


def create_chart(data):
    fig = plt.figure(figsize=(18, 11), facecolor=BG)
    today = datetime.datetime.now().strftime('%Y/%m/%d')
    weekday = ['一','二','三','四','五','六','日'][datetime.datetime.now().weekday()]
    label = '盤前交易計畫' if MODE == 'premarket' else '盤後分析報告'
    time_str = '08:30' if MODE == 'premarket' else '15:00'

    twii   = data.get('twii', {})
    etfs   = data.get('etfs', {})
    stocks = data.get('stocks', {})
    us     = data.get('us', {})
    pct_val = twii.get('pct', 0)
    line_col = col(pct_val)

    # ── 頂部 header bar ──────────────────────────────────
    header = fig.add_axes([0, 0.935, 1, 0.065])
    header.set_facecolor(CARD2); header.axis('off')
    header.set_xlim(0, 1); header.set_ylim(0, 1)
    # 左：標題
    header.text(0.02, 0.5, f'台股{label}', ha='left', va='center',
                color=WHITE, fontsize=14, fontweight='bold')
    # 中：加權指數
    header.text(0.5, 0.72, f'加權指數  {twii.get("close",0):,.0f} 點',
                ha='center', va='center', color=WHITE, fontsize=13, fontweight='bold')
    header.text(0.5, 0.25, f'{arrow(pct_val)} {abs(pct_val):.2f}%  ({twii.get("prev",0):,.0f} → {twii.get("close",0):,.0f})',
                ha='center', va='center', color=line_col, fontsize=10)
    # 右：日期時間
    header.text(0.98, 0.6, f'{today}  (週{weekday})',
                ha='right', va='center', color=GRAY, fontsize=9)
    header.text(0.98, 0.25, time_str,
                ha='right', va='center', color=GRAY, fontsize=9)

    # ── 主區域 GridSpec ──────────────────────────────────
    gs = gridspec.GridSpec(3, 4, figure=fig,
                           hspace=0.55, wspace=0.35,
                           left=0.04, right=0.98,
                           top=0.925, bottom=0.04)

    # ── [0, 0:3]  加權指數折線（寬版，佔3欄）────────────
    ax1 = fig.add_subplot(gs[0, 0:3])
    ax1.set_facecolor(CARD)
    hist = twii.get('history', [])
    if hist and len(hist) >= 2:
        xs = np.arange(len(hist))
        mn = min(hist) * 0.9992
        ax1.fill_between(xs, hist, mn, alpha=0.18, color=line_col)
        ax1.plot(xs, hist, color=line_col, linewidth=2.5,
                 marker='o', markersize=6,
                 markerfacecolor=BG, markeredgecolor=line_col, markeredgewidth=2)
        for i, (xi, yi) in enumerate(zip(xs, hist)):
            ax1.annotate(f'{yi:,.0f}', xy=(xi, yi),
                         xytext=(0, 9), textcoords='offset points',
                         ha='center', color=line_col, fontsize=8, fontweight='bold')
        days = [f'D-{len(hist)-1-i}' if i<len(hist)-1 else '今日' for i in xs]
        ax1.set_xticks(xs); ax1.set_xticklabels(days, color=GRAY, fontsize=9)
        ypad = (max(hist)-min(hist)) * 0.4
        ax1.set_ylim(mn, max(hist) + ypad)
    ax1.tick_params(axis='y', colors=GRAY, labelsize=8.5)
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x,_: f'{x:,.0f}'))
    for sp in ax1.spines.values(): sp.set_color(CARD2)
    ax1.set_facecolor(CARD)
    ax1.grid(axis='y', color=GRAY2, alpha=0.15, linewidth=0.6, linestyle='--')
    ax1.set_title('近 5 日走勢', color=GRAY, fontsize=9, pad=5, loc='left')

    # ── [0, 3]  市場情緒量表 ─────────────────────────────
    ax_gauge = fig.add_subplot(gs[0, 3])
    ax_gauge.set_facecolor(CARD); ax_gauge.axis('off')
    ax_gauge.set_title('市場情緒', color=YELLOW, fontsize=10, pad=5, fontweight='bold')
    all_pcts = [v['pct'] for v in {**etfs, **stocks}.values()]
    up_ratio = sum(1 for p in all_pcts if p > 0) / max(len(all_pcts), 1)
    draw_gauge(ax_gauge, up_ratio)

    # ── [1, 0:2]  熱門 ETF 卡片 ──────────────────────────
    ax3 = fig.add_subplot(gs[1, 0:2])
    draw_item_cards(ax3, etfs, '熱門 ETF')

    # ── [1, 2:4]  熱門個股卡片 ───────────────────────────
    ax4 = fig.add_subplot(gs[1, 2:4])
    draw_item_cards(ax4, stocks, '熱門個股')

    # ── [2, 0:2]  美股指數卡片 ───────────────────────────
    ax5 = fig.add_subplot(gs[2, 0:2])
    draw_us_cards(ax5, us)

    # ── [2, 2:4]  漲跌統計 bar ───────────────────────────
    ax6 = fig.add_subplot(gs[2, 2:4])
    ax6.set_facecolor(CARD)
    ax6.set_title('今日漲跌分布', color=YELLOW, fontsize=10, pad=5, fontweight='bold')
    all_items = {**etfs, **stocks}
    sorted_items = sorted(all_items.items(), key=lambda x: x[1]['pct'], reverse=True)
    names  = [k for k,_ in sorted_items]
    pcts   = [v['pct'] for _,v in sorted_items]
    closes = [v['close'] for _,v in sorted_items]
    colors_bar = [col(p) for p in pcts]
    ypos = np.arange(len(names))
    ax6.barh(ypos, pcts, color=colors_bar, height=0.62, alpha=0.85)
    ax6.set_yticks(ypos)
    ax6.set_yticklabels(names, color=WHITE, fontsize=8.5)
    ax6.axvline(0, color=GRAY, linewidth=0.8, alpha=0.5)
    max_abs = max(abs(p) for p in pcts) if pcts else 1
    ax6.set_xlim(-max_abs*1.5, max_abs*2.8)
    for i, (p, c) in enumerate(zip(pcts, closes)):
        off = max_abs * 0.12 if p >= 0 else -max_abs*0.12
        ax6.text(p+off, i, f'{c:.1f}  {p:+.2f}%',
                 va='center', ha='left' if p>=0 else 'right',
                 color=WHITE, fontsize=7.5)
    ax6.tick_params(axis='x', colors=GRAY, labelsize=7.5)
    for sp in ax6.spines.values(): sp.set_color(CARD2)
    ax6.grid(axis='x', color=GRAY2, alpha=0.12, linewidth=0.5, linestyle='--')

    # 底部版權條
    fig.text(0.98, 0.01, 'Auto generated by TW Stock Bot',
             ha='right', color=GRAY2, fontsize=7.5)

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight',
                facecolor=BG, edgecolor='none')
    plt.close(fig)
    buf.seek(0)
    return buf.read()

# ════════════════════════════════════════════════════════
#  發送 Telegram
# ════════════════════════════════════════════════════════
def tg_photo(img_bytes, caption):
    r = requests.post(
        f'https://api.telegram.org/bot{TG_TOKEN}/sendPhoto',
        files={'photo': ('report.png', img_bytes, 'image/png')},
        data={'chat_id': TG_CHAT, 'caption': caption[:1024], 'parse_mode': 'HTML'},
        timeout=60,
    )
    if not r.ok:
        print(f'❌ sendPhoto 失敗: {r.status_code} {r.text[:200]}')
    return r.ok

def tg_msg(text):
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        requests.post(
            f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage',
            json={'chat_id': TG_CHAT, 'text': chunk, 'parse_mode': 'HTML'},
            timeout=30,
        )

# ════════════════════════════════════════════════════════
#  主程式
# ════════════════════════════════════════════════════════
def main():
    ts    = datetime.datetime.now().strftime('%H:%M:%S')
    label = '盤前交易計畫' if MODE == 'premarket' else '盤後分析報告'
    emoji = '🌅' if MODE == 'premarket' else '📋'
    today = datetime.datetime.now().strftime('%Y/%m/%d')
    print(f'[{ts}] ▶ MODE={MODE} ({label})')

    print('📡 抓取市場資料...')
    data = fetch_all()
    twii = data.get('twii', {})
    print(f'   加權指數: {twii.get("close", 0):.0f} ({twii.get("pct", 0):+.2f}%)')
    print(f'   ETF: {len(data.get("etfs", {}))} 檔  個股: {len(data.get("stocks", {}))} 檔')

    print('🤖 呼叫 Claude 生成分析...')
    analysis = generate_analysis(data)
    print(f'   分析完成，{len(analysis)} 字')

    print('📊 繪製圖表...')
    img = create_chart(data)
    print(f'   圖檔: {len(img)//1024} KB')

    print('📤 發送 Telegram...')
    caption = f'{emoji} <b>台股{label} {today}</b>'
    ok = tg_photo(img, caption)

    if ok:
        tg_msg(f'{emoji} <b>台股{label} {today}</b>\n\n{analysis}')
        print('✅ 完成！')
    else:
        print('❌ 圖片發送失敗，改發純文字...')
        tg_msg(f'{emoji} <b>台股{label} {today}</b>\n\n{analysis}')
        sys.exit(1)

if __name__ == '__main__':
    main()
