#!/usr/bin/env python3
"""
Telegram 指令監聽器
每 10 分鐘由 GitHub Actions 執行一次，檢查有沒有新指令
"""
import os, sys, json, subprocess, datetime
import requests
import yfinance as yf
from groq import Groq

TG_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TG_CHAT  = os.environ.get('TELEGRAM_CHAT_ID', '')
GH_TOKEN = os.environ.get('GITHUB_TOKEN', '')
GH_REPO  = os.environ.get('GITHUB_REPOSITORY', '')  # owner/repo

groq_client = Groq(api_key=os.environ.get('GROQ_API_KEY', ''))
BASE_URL = f'https://api.telegram.org/bot{TG_TOKEN}'


# ════════════════════════════════════════════════════════
#  股票名稱對照表（台股常用）
# ════════════════════════════════════════════════════════
NAME_TO_CODE = {
    '台積電': '2330', '鴻海': '2317', '聯發科': '2454',
    '富邦金': '2881', '國泰金': '2882', '中鋼': '2002',
    '台塑': '1301', '南亞': '1303', '台化': '1326',
    '台達電': '2308', '廣達': '2382', '華碩': '2357',
    '緯創': '3231', '日月光': '3711', '聯電': '2303',
    '瑞昱': '2379', '群聯': '8299', '威剛': '3260',
    '元大台灣50': '0050', '0050': '0050',
    '元大高股息': '0056', '0056': '0056',
    '國泰永續高股息': '00878', '00878': '00878',
    '富邦台50': '006208', '006208': '006208',
    '中信關鍵半導體': '00891', '00891': '00891',
    '群益台灣精選高息': '00919', '00919': '00919',
    '復華台灣科技優息': '00929', '00929': '00929',
}

def resolve_symbol(query):
    """把名稱或代號轉成 yfinance symbol"""
    query = query.strip()
    # 先查對照表
    code = NAME_TO_CODE.get(query)
    if not code:
        # 嘗試直接當代號用（純數字 or 以00開頭）
        if query.isdigit() or query.startswith('00'):
            code = query
    if code:
        # 台股加 .TW
        sym = code + '.TW' if not code.endswith('.TW') else code
        return sym, code
    return None, None

def fetch_stock_info(symbol):
    """抓單一股票資料"""
    try:
        tk = yf.Ticker(symbol)
        df = tk.history(period='5d', interval='1d')
        if df is None or len(df) < 2:
            return None
        info = {}
        try:
            meta = tk.info
            info['name']   = meta.get('longName') or meta.get('shortName', symbol)
            info['sector'] = meta.get('sector', '')
            info['pe']     = meta.get('trailingPE')
            info['pb']     = meta.get('priceToBook')
            info['mktcap'] = meta.get('marketCap')
            info['week52h'] = meta.get('fiftyTwoWeekHigh')
            info['week52l'] = meta.get('fiftyTwoWeekLow')
            info['avg_vol'] = meta.get('averageVolume')
        except:
            info['name'] = symbol
        c = df['Close'].values
        v = df['Volume'].values if 'Volume' in df.columns else [0]*len(c)
        info['close']   = float(c[-1])
        info['prev']    = float(c[-2])
        info['pct']     = float((c[-1]-c[-2])/c[-2]*100)
        info['diff']    = float(c[-1]-c[-2])
        info['history'] = [float(x) for x in c]
        info['volume']  = float(v[-1])
        return info
    except Exception as e:
        print(f'  fetch_stock_info error: {e}')
        return None

def analyse_stock(query):
    """查詢單一股票並用 AI 分析，回傳 TG 訊息文字"""
    sym, code = resolve_symbol(query)
    if not sym:
        return f'❌ 找不到「{query}」，請輸入台股代號（如 2330）或名稱（如 台積電）'

    tg_send(f'🔍 正在查詢 <b>{query}</b>，請稍候...')

    info = fetch_stock_info(sym)
    if not info:
        return f'❌ 無法取得 <b>{query}</b> 的資料，請確認代號是否正確'

    arrow = '▲' if info['pct'] >= 0 else '▼'
    col   = '🟢' if info['pct'] >= 0 else '🔴'

    # 組成 AI 分析 prompt
    mktcap_str = ''
    if info.get('mktcap'):
        mc = info['mktcap'] / 1e8
        mktcap_str = f'市值：{mc:.0f} 億'

    hist_str = ' → '.join(f'{x:.1f}' for x in info.get('history', []))

    prompt = f"""你是台股分析師，請針對以下股票做簡短分析（繁體中文，200字以內）：

股票：{info.get('name', query)}（{code}）
收盤價：{info['close']:.2f}（昨收 {info['prev']:.2f}，{arrow}{abs(info['pct']):.2f}%）
近5日走勢：{hist_str}
本益比：{info.get('pe', 'N/A')}　股價淨值比：{info.get('pb', 'N/A')}
52週高點：{info.get('week52h', 'N/A')}　52週低點：{info.get('week52l', 'N/A')}
{mktcap_str}

請包含：①現況判斷 ②技術面觀察 ③短期展望 ④操作建議（一句話）
格式精簡，每點一行。"""

    try:
        res = groq_client.chat.completions.create(
            model='llama-3.3-70b-versatile',
            messages=[{'role': 'user', 'content': prompt}],
            max_tokens=500,
        )
        ai_text = res.choices[0].message.content
    except Exception as e:
        ai_text = f'（AI 分析暫時不可用：{e}）'

    # 組成回覆訊息
    week52h = info.get('week52h')
    week52l = info.get('week52l')
    pos_pct = ''
    if week52h and week52l and week52h > week52l:
        pos = (info['close'] - week52l) / (week52h - week52l) * 100
        pos_pct = f'（52週位置 {pos:.0f}%）'

    lines = [
        f'{col} <b>{info.get("name", query)}（{code}）</b>',
        '━━━━━━━━━━━━━━━━━━',
        f'💰 收盤：<b>{info["close"]:.2f}</b>  {arrow} {info["diff"]:+.2f}（{info["pct"]:+.2f}%）',
        f'📊 52週：{week52l or "N/A"} ～ {week52h or "N/A"} {pos_pct}',
        f'📈 本益比：{info.get("pe") or "N/A"}　淨值比：{info.get("pb") or "N/A"}',
        '━━━━━━━━━━━━━━━━━━',
        '🤖 <b>AI 分析</b>',
        ai_text,
    ]
    return '\n'.join(lines)


HELP_TEXT = """🤖 <b>台股 Bot 指令列表</b>

📊 <b>立即產生報告：</b>
/盤前  →  盤前交易計畫
/盤後  →  盤後分析報告

🔍 <b>個股查詢：</b>
/查 2330      →  用代號查詢
/查 台積電    →  用名稱查詢
/查 00878     →  ETF 也可以

ℹ️ <b>其他：</b>
/狀態  →  系統目前狀態
/說明  →  顯示此說明

⏰ <b>自動發送時間：</b>
• 週一~五 08:00  盤前計畫
• 週一~五 15:00  盤後報告"""

# ════════════════════════════════════════════════════════
#  GitHub Variables API（儲存上次讀取的 update_id）
# ════════════════════════════════════════════════════════
VAR_NAME = 'TG_LAST_OFFSET'
GH_API   = f'https://api.github.com/repos/{GH_REPO}/actions/variables/{VAR_NAME}'
GH_HEADERS = {
    'Authorization': f'Bearer {GH_TOKEN}',
    'Accept': 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
}

def get_offset():
    try:
        r = requests.get(GH_API, headers=GH_HEADERS, timeout=10)
        if r.status_code == 200:
            return int(r.json().get('value', '0'))
    except:
        pass
    return 0

def set_offset(offset):
    try:
        # 先嘗試 PATCH（更新）
        r = requests.patch(GH_API, headers=GH_HEADERS,
                           json={'name': VAR_NAME, 'value': str(offset)}, timeout=10)
        if r.status_code == 404:
            # 不存在就 POST（新增）
            url = f'https://api.github.com/repos/{GH_REPO}/actions/variables'
            requests.post(url, headers=GH_HEADERS,
                          json={'name': VAR_NAME, 'value': str(offset)}, timeout=10)
    except Exception as e:
        print(f'  ⚠ 儲存 offset 失敗: {e}')

# ════════════════════════════════════════════════════════
#  Telegram 操作
# ════════════════════════════════════════════════════════
def tg_send(text):
    requests.post(f'{BASE_URL}/sendMessage',
                  json={'chat_id': TG_CHAT, 'text': text, 'parse_mode': 'HTML'},
                  timeout=15)

def get_updates(offset):
    r = requests.get(f'{BASE_URL}/getUpdates',
                     params={'offset': offset, 'timeout': 5, 'limit': 10},
                     timeout=15)
    if r.ok:
        return r.json().get('result', [])
    return []

# ════════════════════════════════════════════════════════
#  執行主程式
# ════════════════════════════════════════════════════════
def run_report(mode):
    label = '盤前交易計畫' if mode == 'premarket' else '盤後分析報告'
    emoji = '🌅' if mode == 'premarket' else '📋'
    tg_send(f'⏳ 收到指令，正在產生{label}，請稍候約 30 秒...')
    env = os.environ.copy()
    env['MODE'] = mode
    result = subprocess.run([sys.executable, 'main.py'], env=env,
                            capture_output=True, text=True)
    if result.returncode != 0:
        tg_send(f'❌ 執行失敗\n<pre>{result.stderr[-500:]}</pre>')
    # main.py 成功會自己發 TG，這裡不用重複發

# ════════════════════════════════════════════════════════
#  指令對應表
# ════════════════════════════════════════════════════════
COMMANDS = {
    '/盤前':  ('premarket',  None),
    '/pre':   ('premarket',  None),
    '/盤後':  ('postmarket', None),
    '/post':  ('postmarket', None),
    '/狀態':  (None, 'status'),
    '/status':(None, 'status'),
    '/說明':  (None, 'help'),
    '/help':  (None, 'help'),
    '/start': (None, 'help'),
    # /查 是特殊指令，在 handle() 裡單獨處理
}

def handle(text):
    raw = text.strip()
    lower = raw.lower()
    # 支援指令帶 @botname 尾綴
    cmd = lower.split('@')[0].split(' ')[0]
    parts = raw.split(' ', 1)  # 保留原始大小寫供查詢用

    # ── /查 個股查詢（特殊處理）────────────────────────
    if cmd in ('/查', '/q', '/stock'):
        if len(parts) < 2 or not parts[1].strip():
            tg_send('❓ 請輸入股票代號或名稱\n例如：/查 2330  或  /查 台積電')
        else:
            query = parts[1].strip()
            result = analyse_stock(query)
            tg_send(result)
        return True

    # ── 其他指令 ────────────────────────────────────────
    for key, (mode, action) in COMMANDS.items():
        if cmd == key.lower():
            if mode:
                run_report(mode)
            elif action == 'status':
                now = datetime.datetime.now().strftime('%Y/%m/%d %H:%M')
                tg_send(f'✅ <b>系統正常運作</b>\n\n'
                        f'📅 現在時間：{now}\n'
                        f'⏰ 下次自動推送：\n'
                        f'• 盤前：週一~五 08:00\n'
                        f'• 盤後：週一~五 15:00\n\n'
                        f'輸入 /說明 查看所有指令')
            elif action == 'help':
                tg_send(HELP_TEXT)
            return True
    return False

# ════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════
def main():
    print(f'[{datetime.datetime.now():%H:%M:%S}] 🔍 檢查 Telegram 新訊息...')
    offset = get_offset()
    print(f'  上次 offset: {offset}')

    updates = get_updates(offset)
    print(f'  收到 {len(updates)} 則更新')

    new_offset = offset
    for u in updates:
        uid = u.get('update_id', 0)
        new_offset = max(new_offset, uid + 1)

        msg = u.get('message') or u.get('edited_message', {})
        if not msg:
            continue

        # 只回應來自設定 chat_id 的訊息（防止陌生人觸發）
        chat_id = str(msg.get('chat', {}).get('id', ''))
        if chat_id != str(TG_CHAT):
            print(f'  ⚠ 忽略來自 {chat_id} 的訊息（非授權）')
            continue

        text = msg.get('text', '')
        if not text:
            continue

        print(f'  📨 收到訊息: {text!r}')
        matched = handle(text)
        if not matched:
            print(f'  → 非指令，忽略')

    if new_offset != offset:
        set_offset(new_offset)
        print(f'  ✓ offset 更新為 {new_offset}')
    print('✅ 完成')

if __name__ == '__main__':
    main()
