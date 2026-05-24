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
BG     = '#0d0d1a'
CARD   = '#161628'
GREEN  = '#26de81'
RED    = '#fc5c65'
YELLOW = '#fed330'
BLUE   = '#45aaf2'
GRAY   = '#8395a7'
WHITE  = '#f5f6fa'

def col(v):   return GREEN if v >= 0 else RED
def arrow(v): return '▲' if v >= 0 else '▼'

def draw_hbar(ax, items, title):
    """畫水平長條圖（ETF / 個股）"""
    if not items:
        ax.set_facecolor(CARD)
        ax.axis('off')
        ax.set_title(title, color=YELLOW, fontsize=10, pad=6, fontweight='bold')
        return

    names  = list(items.keys())
    pcts   = [v['pct']   for v in items.values()]
    closes = [v['close'] for v in items.values()]
    ypos   = np.arange(len(names))
    colors = [col(p) for p in pcts]
    max_abs = max(abs(p) for p in pcts) if pcts else 1

    ax.set_facecolor(CARD)
    bars = ax.barh(ypos, pcts, color=colors, height=0.55, alpha=0.85)
    ax.set_yticks(ypos)
    ax.set_yticklabels(names, color=WHITE, fontsize=9)
    ax.axvline(0, color=GRAY, linewidth=0.5, alpha=0.4)
    ax.set_xlim(-max_abs * 1.5, max_abs * 3.5)
    ax.tick_params(axis='x', colors=GRAY, labelsize=7.5)
    ax.spines[:].set_color(CARD)
    ax.set_title(title, color=YELLOW, fontsize=10, pad=6, fontweight='bold')

    for i, (p, c) in enumerate(zip(pcts, closes)):
        offset = max_abs * 0.15 if p >= 0 else -max_abs * 0.15
        ha = 'left' if p >= 0 else 'right'
        ax.text(p + offset, i,
                f'{c:.2f} ({p:+.2f}%)',
                va='center', ha=ha, color=WHITE, fontsize=7.5,
                clip_on=False)

def create_chart(data):
    fig = plt.figure(figsize=(14, 9), facecolor=BG)
    today = datetime.datetime.now().strftime('%Y/%m/%d')
    label = '盤前交易計畫' if MODE == 'premarket' else '盤後分析報告'
    emoji = '🌅' if MODE == 'premarket' else '📋'

    fig.suptitle(f'{emoji}  台股{label}  ──  {today}',
                 color=WHITE, fontsize=15, fontweight='bold', y=0.97)

    gs = gridspec.GridSpec(2, 3, figure=fig,
                           hspace=0.52, wspace=0.38,
                           left=0.07, right=0.97,
                           top=0.92, bottom=0.06)

    twii   = data.get('twii', {})
    etfs   = data.get('etfs', {})
    stocks = data.get('stocks', {})
    us     = data.get('us', {})

    # ── [0, 0:2]  加權指數趨勢（寬版）──────────────────
    ax1 = fig.add_subplot(gs[0, 0:2])
    ax1.set_facecolor(CARD)
    hist = twii.get('history', [])
    pct_val = twii.get('pct', 0)
    line_col = col(pct_val)

    if hist and len(hist) >= 2:
        xs = np.arange(len(hist))
        ax1.fill_between(xs, hist, min(hist) * 0.9995,
                         alpha=0.25, color=line_col, interpolate=True)
        ax1.plot(xs, hist, color=line_col, linewidth=2.2,
                 marker='o', markersize=5,
                 markerfacecolor=BG, markeredgecolor=line_col, markeredgewidth=1.8)
        ax1.annotate(f'{hist[-1]:.0f}',
                     xy=(xs[-1], hist[-1]),
                     xytext=(5, 6), textcoords='offset points',
                     color=line_col, fontsize=9, fontweight='bold')
        days = [f'D-{len(hist)-1-i}' if i < len(hist)-1 else '今日' for i in xs]
        ax1.set_xticks(xs)
        ax1.set_xticklabels(days, color=GRAY, fontsize=8.5)

    ax1.set_title(
        f'加權指數   {twii.get("close", 0):.0f} 點   '
        f'{arrow(pct_val)} {abs(pct_val):.2f}%',
        color=line_col, fontsize=11, pad=7, fontweight='bold')
    ax1.tick_params(axis='y', colors=GRAY, labelsize=8)
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'{x:.0f}'))
    ax1.spines[:].set_color(CARD)
    ax1.grid(axis='y', color=GRAY, alpha=0.10, linewidth=0.6)

    # ── [0, 2]  美股指數 ────────────────────────────────
    ax2 = fig.add_subplot(gs[0, 2])
    ax2.set_facecolor(CARD)
    if us:
        us_names = list(us.keys())
        us_pcts  = [v['pct'] for v in us.values()]
        ypos = np.arange(len(us_names))
        max_abs = max(abs(p) for p in us_pcts) if us_pcts else 1
        ax2.barh(ypos, us_pcts, color=[col(p) for p in us_pcts],
                 height=0.52, alpha=0.85)
        ax2.set_yticks(ypos)
        ax2.set_yticklabels(us_names, color=WHITE, fontsize=9)
        ax2.set_xlim(-max_abs * 1.5, max_abs * 3.0)
        ax2.axvline(0, color=GRAY, linewidth=0.5, alpha=0.4)
        for i, p in enumerate(us_pcts):
            offset = max_abs * 0.15 if p >= 0 else -max_abs * 0.15
            ax2.text(p + offset, i, f'{p:+.2f}%',
                     va='center', ha='left' if p >= 0 else 'right',
                     color=WHITE, fontsize=7.5, clip_on=False)
    ax2.set_title('美股（前日收盤）', color=YELLOW, fontsize=10, pad=7, fontweight='bold')
    ax2.tick_params(colors=GRAY, labelsize=7.5)
    ax2.spines[:].set_color(CARD)

    # ── [1, 0]  ETF ─────────────────────────────────────
    ax3 = fig.add_subplot(gs[1, 0])
    draw_hbar(ax3, etfs, '熱門 ETF')

    # ── [1, 1]  個股 ─────────────────────────────────────
    ax4 = fig.add_subplot(gs[1, 1])
    draw_hbar(ax4, stocks, '熱門個股')

    # ── [1, 2]  市場情緒計 ──────────────────────────────
    ax5 = fig.add_subplot(gs[1, 2])
    ax5.set_facecolor(CARD)
    ax5.set_xlim(0, 1)
    ax5.set_ylim(0, 1)
    ax5.axis('off')
    ax5.set_title('市場情緒', color=YELLOW, fontsize=10, pad=7, fontweight='bold')

    all_pcts = [v['pct'] for v in {**etfs, **stocks}.values()]
    up_ratio = sum(1 for p in all_pcts if p > 0) / max(len(all_pcts), 1)

    if up_ratio >= 0.8:
        sentiment, sent_col = '強力偏多', GREEN
    elif up_ratio >= 0.6:
        sentiment, sent_col = '偏多', GREEN
    elif up_ratio >= 0.4:
        sentiment, sent_col = '盤整', YELLOW
    elif up_ratio >= 0.2:
        sentiment, sent_col = '偏空', RED
    else:
        sentiment, sent_col = '強力偏空', RED

    # 半圓量表
    r, cx, cy = 0.30, 0.5, 0.38
    theta_bg = np.linspace(np.pi, 0, 120)
    ax5.plot(cx + r * np.cos(theta_bg), cy + r * np.sin(theta_bg),
             color=GRAY, linewidth=8, alpha=0.25, solid_capstyle='round')

    theta_fg = np.linspace(np.pi, np.pi - up_ratio * np.pi, 120)
    ax5.plot(cx + r * np.cos(theta_fg), cy + r * np.sin(theta_fg),
             color=sent_col, linewidth=8, alpha=0.85, solid_capstyle='round')

    # 指針
    needle_angle = np.pi - up_ratio * np.pi
    nx = cx + 0.24 * np.cos(needle_angle)
    ny = cy + 0.24 * np.sin(needle_angle)
    ax5.annotate('', xy=(nx, ny), xytext=(cx, cy),
                 arrowprops=dict(arrowstyle='->', color=WHITE, lw=2.0))
    ax5.plot(cx, cy, 'o', color=WHITE, markersize=6)

    # 文字
    ax5.text(cx, 0.74, sentiment, ha='center', va='center',
             color=sent_col, fontsize=11, fontweight='bold')
    ax5.text(cx, 0.62, f'上漲比例 {up_ratio*100:.0f}%',
             ha='center', va='center', color=GRAY, fontsize=8.5)
    ax5.text(0.12, 0.10, '空方', ha='center', color=RED, fontsize=8)
    ax5.text(0.88, 0.10, '多方', ha='center', color=GREEN, fontsize=8)

    # 存成 bytes
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=145, bbox_inches='tight',
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
