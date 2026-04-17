#!/usr/bin/env python3
"""
HM マニュアル 月次更新スクリプト
========================================
使い方:
  python3 update_manual.py [オプション]

オプション（更新したいPDFだけ指定すればOK）:
  --fuzokuhin  PATH   HM付属品一覧 PDFのパス
  --rice       PATH   ライス盛付・ラベル・小袋 PDFのパス
  --chouri     PATH   調理手順シート PDFのパス
  --hansoku    PATH   販促計画書 PDFのパス（当月分）
  --month      TEXT   当月ラベル（例: "2026年6月"）省略時は自動検出

例:
  python3 update_manual.py --hansoku ~/Downloads/2026年06月販促計画書.pdf --month "2026年6月"
  python3 update_manual.py --fuzokuhin ~/Downloads/付属品一覧.pdf --rice ~/Downloads/ライス盛付.pdf
"""

import argparse
import base64
import difflib
import hashlib
import os
import re
import shutil
import subprocess
import html
import json
from datetime import datetime, timedelta

# ===== 設定 =====
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IMAGES_DIR = os.path.join(SCRIPT_DIR, "images")
CONFIG_PATH = os.path.join(SCRIPT_DIR, "manual_config.json")
OUTPUT_HTML = os.path.join(SCRIPT_DIR, "index.html")
HANSOKU_PAGES = 20  # 販促計画書: 抽出ページ数

# ===== ユーティリティ =====

def img_to_data_uri(img_path):
    """画像ファイルをBase64 data URIに変換"""
    abs_path = os.path.join(SCRIPT_DIR, img_path) if not os.path.isabs(img_path) else img_path
    if not os.path.exists(abs_path):
        return img_path  # ファイルがなければそのまま返す
    with open(abs_path, "rb") as f:
        data = base64.b64encode(f.read()).decode("ascii")
    ext = os.path.splitext(abs_path)[1].lower()
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(ext.lstrip("."), "image/png")
    return f"data:{mime};base64,{data}"

def run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  警告: {' '.join(cmd)}\n  {result.stderr[:200]}")
    return result

def pdf_to_images(pdf_path, prefix, first=None, last=None, dpi=150):
    """PDFをPNG画像に変換してimagesフォルダへ保存"""
    cmd = ["pdftoppm", "-r", str(dpi), "-png"]
    if first:
        cmd += ["-f", str(first)]
    if last:
        cmd += ["-l", str(last)]
    out_prefix = os.path.join(IMAGES_DIR, prefix)
    cmd += [pdf_path, out_prefix]
    run(cmd)
    # 生成されたファイル一覧を返す
    files = sorted([f for f in os.listdir(IMAGES_DIR) if f.startswith(prefix + "-") or f.startswith(prefix + "_cur_")])
    return files

def extract_text(pdf_path, first=None, last=None):
    """PDFからテキスト抽出（ページ区切り付き）"""
    cmd = ["pdftotext", "-layout"]
    if first:
        cmd += ["-f", str(first)]
    if last:
        cmd += ["-l", str(last)]
    cmd += [pdf_path, "-"]
    result = run(cmd)
    pages = result.stdout.split("\f")
    return [p.strip() for p in pages if p.strip()]

def detect_month_from_filename(path):
    """ファイル名から年月を検出（例: 2026年05月 → "2026年5月"）"""
    name = os.path.basename(path)
    m = re.search(r'(\d{4})[年_](\d{1,2})月?', name)
    if m:
        return f"{m.group(1)}年{int(m.group(2))}月"
    return None

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    # デフォルト設定
    return {
        "fuzokuhin": {"month": "2026年5月", "pages": 3},
        "rice":      {"month": "2026年5月", "pages": 2},
        "chouri":    {"month": "2026年5月", "pages": 7},
        "chouri_zen": {"month": "2026年3月", "pages": 0},
        "hansoku_cur":  {"month": "2026年5月", "pages": HANSOKU_PAGES},
        "hansoku_prev": {"month": None, "pages": 0},
    }

def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

def detect_changed_pages(old_text_file, new_texts, threshold=0.95):
    """旧テキストファイルと新テキストをページごとに比較し、変更のあったページ番号のsetを返す"""
    changed = set()
    old_texts = []
    if os.path.exists(old_text_file):
        with open(old_text_file, "r", encoding="utf-8") as f:
            old_texts = [p.strip() for p in f.read().split("\f") if p.strip()]

    for i, new_page in enumerate(new_texts):
        page_num = i + 1
        if i >= len(old_texts):
            # 新しいページ（旧データにない）→ 変更あり
            changed.add(page_num)
        else:
            ratio = difflib.SequenceMatcher(None, old_texts[i], new_page).ratio()
            if ratio < threshold:
                changed.add(page_num)
    # 旧の方がページ数多い場合（ページ削除）も検知
    if len(old_texts) > len(new_texts):
        for i in range(len(new_texts), len(old_texts)):
            changed.add(i + 1)
    return changed

# ===== HTML生成 =====

def escape_for_html_attr(text):
    return html.escape(text or "", quote=True)

def split_by_page(text_list):
    """既に分割済みリストをそのまま返す（互換性用）"""
    return text_list

def build_page_blocks(section_id, images, texts, changed_pages=None, embed_images=False):
    """ページブロックHTMLを生成"""
    changed_pages = changed_pages or set()
    parts = []
    total = len(images)
    for i, img_path in enumerate(images):
        page_num = i + 1
        page_text = texts[i] if i < len(texts) else ""
        safe_text = escape_for_html_attr(page_text)
        is_changed = page_num in changed_pages
        changed_attr = ' data-changed="true"' if is_changed else ''
        new_badge = ' <span class="new-badge">🆕 変更あり</span>' if is_changed else ''
        img_src = img_to_data_uri(img_path) if embed_images else img_path
        # ライトボックス用: 埋め込み時はthis.querySelector('img').srcを使う
        if embed_images:
            lb_onclick = f"openLightbox(this.querySelector('img').src, '{section_id} - ページ {page_num}')"
        else:
            lb_onclick = f"openLightbox('{img_path}', '{section_id} - ページ {page_num}')"
        parts.append(f'''
        <div class="page-block" data-section="{section_id}" data-page="{page_num}"
             data-searchtext="{safe_text}"{changed_attr}>
          <div class="page-header">
            <span class="page-badge">{page_num} / {total}{new_badge}</span>
          </div>
          <div class="page-img-wrap" onclick="{lb_onclick}">
            <img src="{img_src}" alt="ページ{page_num}" loading="lazy" class="page-img" />
            <div class="zoom-hint">🔍 タップで拡大</div>
          </div>
        </div>''')
    return "\n".join(parts)

def build_hansoku_section(config, changed_pages=None, embed_images=False):
    """当月・前月の販促計画書セクションHTMLを生成"""
    changed_pages = changed_pages or set()
    cur = config.get("hansoku_cur", {})
    prev = config.get("hansoku_prev", {})
    cur_month = cur.get("month", "当月")
    prev_month = prev.get("month")
    cur_pages = cur.get("pages", HANSOKU_PAGES)
    prev_pages = prev.get("pages", 0)

    cur_images = [f"images/hansoku_cur_{i:02d}.png" for i in range(1, cur_pages + 1)]
    cur_images = [f for f in cur_images if os.path.exists(os.path.join(SCRIPT_DIR, f))]
    cur_texts = []
    if os.path.exists(os.path.join(IMAGES_DIR, "hansoku_cur_text.txt")):
        with open(os.path.join(IMAGES_DIR, "hansoku_cur_text.txt"), "r", encoding="utf-8") as f:
            cur_texts = [p.strip() for p in f.read().split("\f") if p.strip()]

    prev_images = [f"images/hansoku_prev_{i:02d}.png" for i in range(1, prev_pages + 1)]
    prev_images = [f for f in prev_images if os.path.exists(os.path.join(SCRIPT_DIR, f))]
    prev_texts = []
    if os.path.exists(os.path.join(IMAGES_DIR, "hansoku_prev_text.txt")):
        with open(os.path.join(IMAGES_DIR, "hansoku_prev_text.txt"), "r", encoding="utf-8") as f:
            prev_texts = [p.strip() for p in f.read().split("\f") if p.strip()]

    cur_blocks = build_page_blocks("hansoku_cur", cur_images, cur_texts, changed_pages, embed_images)

    has_prev = bool(prev_images) and prev_month
    prev_tab_html = ""
    prev_blocks_html = ""
    if has_prev:
        prev_blocks = build_page_blocks("hansoku_prev", prev_images, prev_texts, embed_images=embed_images)
        prev_tab_html = f'<button class="month-tab" onclick="switchMonth(\'prev\')" id="month-tab-prev">📅 前月（{prev_month}）</button>'
        prev_blocks_html = f'''
        <div id="hansoku-prev-content" class="swipe-container" style="display:none">
          {prev_blocks}
        </div>'''

    return f'''
  <section id="section-hansoku" class="section" style="display:none">
    <div class="section-header">
      <h2>📣 販促計画書（関西・淡路含む）</h2>
      <p class="section-desc">販促カレンダー・商品案内・マニュアル変更（関西エリア）</p>
    </div>
    <div class="month-tabs">
      <button class="month-tab active" onclick="switchMonth(\'cur\')" id="month-tab-cur">📅 当月（{cur_month}）</button>
      {prev_tab_html}
    </div>
    <div id="hansoku-cur-content" class="swipe-container">
      {cur_blocks}
    </div>
    {prev_blocks_html}
  </section>'''

def build_html(config, changed_pages_map=None, password_hash=None, expires_date=None, embed_images=False):
    """index.html を生成
    changed_pages_map: { 'fuzokuhin': {1,3}, 'rice': set(), ... } 変更のあったページ番号のセット
    password_hash: SHA-256ハッシュ文字列（Noneなら認証なし）
    expires_date: 有効期限 "YYYY-MM-DD"（Noneなら無期限）
    embed_images: Trueなら画像をBase64 data URIとして埋め込み（1ファイル完結）
    """
    changed_pages_map = changed_pages_map or {}

    def get_section_images_texts(prefix, page_count, text_file):
        # pdftoppm は総ページ数の桁数に応じてゼロ埋めする（10+ → 2桁、100+ → 3桁）
        pad = max(1, len(str(page_count)))
        images = []
        for i in range(1, page_count + 1):
            for candidate in [f"images/{prefix}-{i:0{pad}d}.png", f"images/{prefix}-{i}.png"]:
                if os.path.exists(os.path.join(SCRIPT_DIR, candidate)):
                    images.append(candidate)
                    break
        texts = []
        tf = os.path.join(IMAGES_DIR, text_file)
        if os.path.exists(tf):
            with open(tf, "r", encoding="utf-8") as f:
                texts = [p.strip() for p in f.read().split("\f") if p.strip()]
        return images, texts

    fuz_pages = config.get("fuzokuhin", {}).get("pages", 3)
    rice_pages = config.get("rice", {}).get("pages", 2)
    cho_pages = config.get("chouri", {}).get("pages", 7)
    chozen_pages = config.get("chouri_zen", {}).get("pages", 0)
    fuz_month = config.get("fuzokuhin", {}).get("month", "")
    rice_month = config.get("rice", {}).get("month", "")
    cho_month = config.get("chouri", {}).get("month", "")
    chozen_month = config.get("chouri_zen", {}).get("month", "")

    fuz_imgs, fuz_texts = get_section_images_texts("fuzokuhin", fuz_pages, "fuzokuhin_text.txt")
    rice_imgs, rice_texts = get_section_images_texts("rice", rice_pages, "rice_text.txt")
    cho_imgs, cho_texts = get_section_images_texts("chouri", cho_pages, "chouri_text.txt")
    chozen_imgs, chozen_texts = get_section_images_texts("chouri_zen", chozen_pages, "chouri_zen_text.txt")

    fuz_blocks = build_page_blocks("fuzokuhin", fuz_imgs, fuz_texts, changed_pages_map.get("fuzokuhin", set()), embed_images)
    rice_blocks = build_page_blocks("rice", rice_imgs, rice_texts, changed_pages_map.get("rice", set()), embed_images)
    cho_blocks = build_page_blocks("chouri", cho_imgs, cho_texts, changed_pages_map.get("chouri", set()), embed_images)
    chozen_blocks = build_page_blocks("chouri_zen", chozen_imgs, chozen_texts, changed_pages_map.get("chouri_zen", set()), embed_images)
    hansoku_section = build_hansoku_section(config, changed_pages_map.get("hansoku_cur", set()), embed_images)

    cur_month = config.get("hansoku_cur", {}).get("month", "")
    updated = datetime.now().strftime("%Y年%m月%d日")

    # 認証関連のHTML/CSS/JS生成
    auth_enabled = bool(password_hash)
    lock_css = ""
    lock_html = ""
    lock_js = ""
    content_hidden_style = ""

    if auth_enabled:
        expires_js = f'"{expires_date}"' if expires_date else 'null'
        content_hidden_style = ' style="display:none"'

        lock_css = """
    /* ===== ロック画面 ===== */
    #lockScreen {
      position: fixed; inset: 0; z-index: 9999;
      background: linear-gradient(135deg, #c0392b 0%, #e74c3c 50%, #d35400 100%);
      display: flex; flex-direction: column;
      align-items: center; justify-content: center;
      padding: 20px;
    }
    #lockScreen.hidden { display: none; }
    .lock-card {
      background: white; border-radius: 16px; padding: 32px 28px;
      max-width: 360px; width: 100%; box-shadow: 0 8px 32px rgba(0,0,0,0.3);
      text-align: center;
    }
    .lock-icon { font-size: 48px; margin-bottom: 12px; }
    .lock-title { font-size: 18px; font-weight: 700; color: #333; margin-bottom: 4px; }
    .lock-subtitle { font-size: 13px; color: #888; margin-bottom: 20px; }
    .lock-input {
      width: 100%; padding: 12px 16px; border: 2px solid #ddd;
      border-radius: 10px; font-size: 16px; text-align: center;
      outline: none; -webkit-appearance: none; margin-bottom: 12px;
    }
    .lock-input:focus { border-color: #c0392b; }
    .lock-btn {
      width: 100%; padding: 12px; background: #c0392b; color: white;
      border: none; border-radius: 10px; font-size: 16px; font-weight: 700;
      cursor: pointer; transition: background 0.2s;
    }
    .lock-btn:hover { background: #a93226; }
    .lock-btn:disabled { background: #ccc; cursor: not-allowed; }
    .lock-error {
      color: #e74c3c; font-size: 13px; margin-top: 10px;
      min-height: 20px;
    }
    .lock-expired {
      background: #fff3cd; border: 1px solid #f39c12; border-radius: 10px;
      padding: 16px; margin-bottom: 16px; font-size: 13px; color: #856404;
    }"""

        lock_html = f"""
<div id="lockScreen">
  <div class="lock-card">
    <div class="lock-icon">🔒</div>
    <div class="lock-title">HM マニュアル</div>
    <div class="lock-subtitle">パスワードを入力してください</div>
    <div id="expiredMsg" class="lock-expired" style="display:none">
      ⚠️ このマニュアルの有効期限が切れています。<br>最新版を管理者から受け取ってください。
    </div>
    <form id="lockForm" onsubmit="return handleLogin(event)">
      <input type="password" id="lockPassword" class="lock-input"
             placeholder="パスワード" autocomplete="off" autofocus>
      <button type="submit" class="lock-btn" id="lockBtn">ログイン</button>
    </form>
    <div class="lock-error" id="lockError"></div>
  </div>
</div>"""

        lock_js = f"""<script>
// ===== 認証 =====
(function() {{
  var AUTH_HASH = '{password_hash}';
  var EXPIRES = {expires_js};
  var MAX_ATTEMPTS = 5;
  var attempts = 0;

  function sha256(text) {{
    function rr(n,x){{return(x>>>n)|(x<<(32-n))}}
    var K=[0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
      0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
      0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
      0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
      0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
      0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
      0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
      0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
    var i, off;
    var bytes = [];
    for (i = 0; i < text.length; i++) {{
      var c = text.charCodeAt(i);
      if (c < 0x80) bytes.push(c);
      else if (c < 0x800) {{ bytes.push(0xc0|(c>>6)); bytes.push(0x80|(c&0x3f)); }}
      else {{ bytes.push(0xe0|(c>>12)); bytes.push(0x80|((c>>6)&0x3f)); bytes.push(0x80|(c&0x3f)); }}
    }}
    var len = bytes.length;
    var bits = len * 8;
    var padLen = ((len + 9 + 63) & ~63);
    var pad = [];
    for (i = 0; i < padLen; i++) pad[i] = 0;
    for (i = 0; i < len; i++) pad[i] = bytes[i];
    pad[len] = 0x80;
    pad[padLen-4] = (bits >>> 24) & 0xff;
    pad[padLen-3] = (bits >>> 16) & 0xff;
    pad[padLen-2] = (bits >>> 8) & 0xff;
    pad[padLen-1] = bits & 0xff;
    var h0=0x6a09e667,h1=0xbb67ae85,h2=0x3c6ef372,h3=0xa54ff53a;
    var h4=0x510e527f,h5=0x9b05688c,h6=0x1f83d9ab,h7=0x5be0cd19;
    for (off = 0; off < padLen; off += 64) {{
      var w = [];
      for (i = 0; i < 16; i++) w[i] = (pad[off+i*4]<<24)|(pad[off+i*4+1]<<16)|(pad[off+i*4+2]<<8)|pad[off+i*4+3];
      for (i = 16; i < 64; i++) {{
        var s0 = (rr(7,w[i-15])^rr(18,w[i-15])^(w[i-15]>>>3));
        var s1 = (rr(17,w[i-2])^rr(19,w[i-2])^(w[i-2]>>>10));
        w[i] = (w[i-16]+s0+w[i-7]+s1)|0;
      }}
      var a=h0,b=h1,c=h2,d=h3,e=h4,f=h5,g=h6,h=h7;
      for (i = 0; i < 64; i++) {{
        var S1=rr(6,e)^rr(11,e)^rr(25,e);
        var ch=(e&f)^(~e&g);
        var t1=(h+S1+ch+K[i]+w[i])|0;
        var S0=rr(2,a)^rr(13,a)^rr(22,a);
        var maj=(a&b)^(a&c)^(b&c);
        var t2=(S0+maj)|0;
        h=g;g=f;f=e;e=(d+t1)|0;d=c;c=b;b=a;a=(t1+t2)|0;
      }}
      h0=(h0+a)|0;h1=(h1+b)|0;h2=(h2+c)|0;h3=(h3+d)|0;
      h4=(h4+e)|0;h5=(h5+f)|0;h6=(h6+g)|0;h7=(h7+h)|0;
    }}
    var hex = '';
    var vals = [h0,h1,h2,h3,h4,h5,h6,h7];
    for (i = 0; i < 8; i++) {{
      var v = (vals[i] >>> 0).toString(16);
      while (v.length < 8) v = '0' + v;
      hex += v;
    }}
    return hex;
  }}

  function sGet(k) {{ try {{ return sessionStorage.getItem(k); }} catch(e) {{ return null; }} }}
  function sSet(k,v) {{ try {{ sessionStorage.setItem(k,v); }} catch(e) {{}} }}
  function lGet(k) {{ try {{ return localStorage.getItem(k); }} catch(e) {{ return null; }} }}
  function lSet(k,v) {{ try {{ localStorage.setItem(k,v); }} catch(e) {{}} }}
  function lDel(k) {{ try {{ localStorage.removeItem(k); }} catch(e) {{}} }}

  function checkExpiry() {{
    if (!EXPIRES) return false;
    var now = new Date();
    var exp = new Date(EXPIRES + 'T23:59:59');
    return now > exp;
  }}

  function showContent() {{
    document.getElementById('lockScreen').classList.add('hidden');
    document.getElementById('appContent').style.display = '';
  }}

  function initAuth() {{
    var expired = checkExpiry();
    if (expired) {{
      document.getElementById('expiredMsg').style.display = 'block';
      document.getElementById('lockForm').style.display = 'none';
      document.getElementById('lockError').textContent = '';
      return;
    }}
    if (sGet('hm_manual_auth') === 'ok') {{
      showContent();
      return;
    }}
    var lockout = lGet('hm_manual_lockout');
    if (lockout) {{
      var lockTime = parseInt(lockout);
      var elapsed = Date.now() - lockTime;
      if (elapsed < 300000) {{
        var remain = Math.ceil((300000 - elapsed) / 60000);
        document.getElementById('lockError').textContent =
          '試行回数超過。' + remain + '分後に再試行してください。';
        document.getElementById('lockBtn').disabled = true;
        document.getElementById('lockPassword').disabled = true;
        return;
      }} else {{
        lDel('hm_manual_lockout');
      }}
    }}
  }}

  window.handleLogin = function(e) {{
    e.preventDefault();
    var pw = document.getElementById('lockPassword').value;
    if (!pw) return false;
    var hash = sha256(pw);
    if (hash === AUTH_HASH) {{
      sSet('hm_manual_auth', 'ok');
      attempts = 0;
      lDel('hm_manual_lockout');
      showContent();
    }} else {{
      attempts++;
      var remain = MAX_ATTEMPTS - attempts;
      if (remain <= 0) {{
        lSet('hm_manual_lockout', Date.now().toString());
        document.getElementById('lockError').textContent =
          '試行回数超過。5分間ロックされます。';
        document.getElementById('lockBtn').disabled = true;
        document.getElementById('lockPassword').disabled = true;
      }} else {{
        document.getElementById('lockError').textContent =
          'パスワードが違います（残り' + remain + '回）';
      }}
      document.getElementById('lockPassword').value = '';
    }}
    return false;
  }};

  // 初期化
  if (document.readyState === 'loading') {{
    document.addEventListener('DOMContentLoaded', initAuth);
  }} else {{
    initAuth();
  }}
}})();
</script>"""

    html_content = f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0">
  <title>HM マニュアル {cur_month}版</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Hiragino Sans', 'Meiryo', sans-serif;
      background: #f5f5f5; color: #222; min-height: 100vh;
      overflow-x: hidden;
    }}
    .app-header {{
      background: linear-gradient(135deg, #c0392b 0%, #e74c3c 100%);
      color: white; padding: 12px 16px 0;
      position: sticky; top: 0; z-index: 100;
      box-shadow: 0 2px 8px rgba(0,0,0,0.2);
    }}
    .app-title {{
      font-size: 15px; font-weight: 700; letter-spacing: 0.5px;
      margin-bottom: 8px; display: flex; align-items: center; gap: 6px;
    }}
    .app-title span.ver {{
      font-size: 11px; background: rgba(255,255,255,0.25);
      padding: 2px 6px; border-radius: 10px; font-weight: normal;
    }}
    .app-title span.updated {{ font-size: 10px; opacity: 0.7; margin-left: auto; }}
    .search-bar {{ display: flex; gap: 6px; margin-bottom: 8px; }}
    .search-wrap {{ position: relative; flex: 1; }}
    .search-wrap::before {{
      content: '🔍'; position: absolute; left: 10px; top: 50%;
      transform: translateY(-50%); font-size: 14px; pointer-events: none;
    }}
    #searchInput {{
      width: 100%; padding: 8px 36px 8px 32px; border: none;
      border-radius: 20px; font-size: 15px; background: rgba(255,255,255,0.95);
      color: #333; outline: none; -webkit-appearance: none;
    }}
    #searchInput:focus {{ background: #fff; box-shadow: 0 0 0 2px #f39c12; }}
    #clearBtn {{
      background: rgba(255,255,255,0.25); border: none; color: white;
      padding: 6px 12px; border-radius: 16px; font-size: 13px;
      cursor: pointer; white-space: nowrap;
    }}
    #clearBtn:hover {{ background: rgba(255,255,255,0.4); }}
    .tabs {{
      display: flex; gap: 2px; overflow-x: auto;
      -webkit-overflow-scrolling: touch; scrollbar-width: none;
    }}
    .tabs::-webkit-scrollbar {{ display: none; }}
    .tab-btn {{
      background: rgba(255,255,255,0.15); border: none; color: rgba(255,255,255,0.8);
      padding: 8px 14px; border-radius: 6px 6px 0 0; font-size: 13px;
      cursor: pointer; white-space: nowrap; transition: all 0.2s; flex-shrink: 0;
      position: relative;
    }}
    .tab-btn:hover {{ background: rgba(255,255,255,0.25); color: white; }}
    .tab-btn.active {{ background: #f5f5f5; color: #c0392b; font-weight: 700; }}
    .tab-badge {{
      display: none; position: absolute; top: 2px; right: 2px;
      background: #f39c12; color: white; font-size: 10px; font-weight: 700;
      min-width: 16px; height: 16px; border-radius: 8px;
      line-height: 16px; text-align: center; padding: 0 3px;
    }}
    .tab-badge.show {{ display: block; }}
    @media (max-width: 400px) {{
      .tab-label {{ display: none; }}
      .tab-btn {{ padding: 8px 10px; font-size: 16px; }}
    }}
    .main {{ max-width: 900px; margin: 0 auto; padding: 12px; }}
    .section-header {{
      background: white; border-radius: 10px; padding: 14px 16px;
      margin-bottom: 12px; box-shadow: 0 1px 4px rgba(0,0,0,0.08);
    }}
    .section-header h2 {{ font-size: 16px; color: #c0392b; margin-bottom: 4px; }}
    .section-desc {{ font-size: 12px; color: #666; }}
    .month-tabs {{ display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }}
    .month-tab {{
      background: white; border: 2px solid #ddd; color: #555;
      padding: 8px 16px; border-radius: 20px; font-size: 13px;
      cursor: pointer; transition: all 0.2s; font-weight: 600;
    }}
    .month-tab.active {{ background: #c0392b; border-color: #c0392b; color: white; }}
    .month-tab:hover:not(.active) {{ border-color: #c0392b; color: #c0392b; }}

    /* ===== スワイプ ===== */
    .swipe-container {{ position: relative; }}
    .swipe-nav {{
      display: flex; align-items: center; justify-content: center;
      gap: 12px; margin-bottom: 12px; user-select: none;
    }}
    .swipe-nav-btn {{
      background: #c0392b; color: white; border: none;
      width: 36px; height: 36px; border-radius: 50%;
      font-size: 18px; cursor: pointer; display: flex;
      align-items: center; justify-content: center;
      transition: opacity 0.2s;
    }}
    .swipe-nav-btn:disabled {{ opacity: 0.3; cursor: default; }}
    .swipe-nav-btn:hover:not(:disabled) {{ background: #a93226; }}
    .swipe-counter {{ font-size: 14px; font-weight: 600; color: #555; min-width: 70px; text-align: center; }}
    .page-nums {{
      display: flex; gap: 4px; overflow-x: auto; padding: 4px 0 8px;
      -webkit-overflow-scrolling: touch; scrollbar-width: none;
      justify-content: flex-start;
    }}
    .page-nums::-webkit-scrollbar {{ display: none; }}
    .page-num-btn {{
      background: #eee; border: none; color: #555;
      min-width: 32px; height: 32px; border-radius: 16px;
      font-size: 13px; font-weight: 600; cursor: pointer;
      flex-shrink: 0; transition: all 0.15s;
    }}
    .page-num-btn:hover {{ background: #ddd; }}
    .page-num-btn.active {{ background: #c0392b; color: white; }}
    .page-block {{
      background: white; border-radius: 10px;
      box-shadow: 0 1px 4px rgba(0,0,0,0.1);
      overflow: hidden; transition: box-shadow 0.2s;
    }}
    .page-block.highlight {{
      box-shadow: 0 0 0 3px #f39c12, 0 4px 12px rgba(0,0,0,0.15);
    }}
    .swipe-container .page-block {{ margin-bottom: 16px; }}
    .swipe-container .page-block.swipe-hidden {{ display: none; }}
    .page-header {{
      background: #f8f8f8; padding: 6px 14px; border-bottom: 1px solid #eee;
      display: flex; align-items: center; justify-content: space-between;
    }}
    .page-badge {{ font-size: 12px; color: #888; font-weight: 600; }}
    .new-badge {{
      background: #e74c3c; color: white; font-size: 10px;
      padding: 2px 6px; border-radius: 8px; margin-left: 8px;
      animation: pulse 2s infinite;
    }}
    @keyframes pulse {{ 0%,100% {{ opacity: 1; }} 50% {{ opacity: 0.6; }} }}
    .page-block[data-changed="true"] {{
      border: 2px solid #e74c3c;
    }}
    .page-img-wrap {{ position: relative; cursor: zoom-in; overflow: hidden; }}
    .page-img {{ width: 100%; height: auto; display: block; transition: transform 0.2s; }}
    .page-img-wrap:hover .page-img {{ transform: scale(1.01); }}
    .zoom-hint {{
      position: absolute; bottom: 8px; right: 8px;
      background: rgba(0,0,0,0.55); color: white;
      font-size: 11px; padding: 3px 8px; border-radius: 10px;
      pointer-events: none; opacity: 0; transition: opacity 0.2s;
    }}
    .page-img-wrap:hover .zoom-hint {{ opacity: 1; }}
    @media (hover: none) {{ .zoom-hint {{ opacity: 1 !important; }} }}

    /* ===== 検索バナー ===== */
    #searchBanner {{
      display: none; background: #fff3cd; border: 1px solid #f39c12;
      border-radius: 8px; padding: 10px 14px; margin-bottom: 12px;
      font-size: 13px; color: #856404;
    }}
    #searchBanner .result-count {{ font-weight: 700; }}
    #searchBanner .result-nav {{ display: flex; gap: 8px; margin-top: 6px; flex-wrap: wrap; }}
    .result-chip {{
      background: #f39c12; color: white; border: none;
      padding: 3px 10px; border-radius: 12px; font-size: 12px; cursor: pointer;
    }}
    .result-chip:hover {{ background: #e67e22; }}

    /* ===== ライトボックス ===== */
    #lightbox {{
      display: none; position: fixed; inset: 0;
      background: rgba(0,0,0,0.92); z-index: 999;
      flex-direction: column; align-items: center;
      justify-content: flex-start; overflow: hidden;
    }}
    #lightbox.open {{ display: flex; }}
    #lbToolbar {{
      width: 100%; background: rgba(0,0,0,0.7);
      display: flex; align-items: center; justify-content: space-between;
      padding: 8px 14px; flex-shrink: 0;
    }}
    #lbTitle {{ color: white; font-size: 13px; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    #lbClose {{
      background: none; border: 1px solid rgba(255,255,255,0.4);
      color: white; padding: 5px 12px; border-radius: 14px;
      cursor: pointer; font-size: 13px; margin-left: 8px; flex-shrink: 0;
    }}
    #lbZoomControls {{ display: flex; gap: 6px; margin-left: 8px; }}
    .lb-zoom-btn {{
      background: rgba(255,255,255,0.15); border: none; color: white;
      width: 32px; height: 32px; border-radius: 50%;
      font-size: 16px; cursor: pointer; display: flex;
      align-items: center; justify-content: center;
    }}
    .lb-zoom-btn:hover {{ background: rgba(255,255,255,0.3); }}
    #lbImgContainer {{
      flex: 1; overflow: auto; width: 100%;
      display: flex; align-items: flex-start; justify-content: center;
      padding: 10px; -webkit-overflow-scrolling: touch;
    }}
    #lbImg {{ width: 100%; max-width: 100%; height: auto; display: block; transition: width 0.15s; }}
{lock_css}
  </style>
</head>
<body>
{lock_html}
<div id="appContent"{content_hidden_style}>
<header class="app-header">
  <div class="app-title">
    🍱 HM マニュアル <span class="ver">{cur_month}版</span>
    <span class="updated">更新: {updated}</span>
  </div>
  <div class="search-bar">
    <div class="search-wrap">
      <input type="search" id="searchInput" placeholder="キーワードで検索（例：南蛮、ライス、調理手順）"
             oninput="handleSearch()" autocomplete="off" autocorrect="off" spellcheck="false">
    </div>
    <button id="clearBtn" onclick="clearSearch()">クリア</button>
  </div>
  <div class="tabs">
    <button class="tab-btn active" onclick="showSection('fuzokuhin')" id="tab-fuzokuhin">📋 <span class="tab-label">付属品</span><span class="tab-badge" id="badge-fuzokuhin"></span></button>
    <button class="tab-btn" onclick="showSection('rice')" id="tab-rice">🍚 <span class="tab-label">ライス盛付</span><span class="tab-badge" id="badge-rice"></span></button>
    <button class="tab-btn" onclick="showSection('chouri')" id="tab-chouri">🍳 <span class="tab-label">調理(季節)</span><span class="tab-badge" id="badge-chouri"></span></button>
    <button class="tab-btn" onclick="showSection('chouri_zen')" id="tab-chouri_zen">🍳 <span class="tab-label">調理(全国)</span><span class="tab-badge" id="badge-chouri_zen"></span></button>
    <button class="tab-btn" onclick="showSection('hansoku')" id="tab-hansoku">📣 <span class="tab-label">販促計画</span><span class="tab-badge" id="badge-hansoku"></span></button>
  </div>
</header>

<main class="main">
  <div id="searchBanner">
    <span>🔍 "<span id="searchKeyword"></span>" の検索結果：<span class="result-count" id="resultCount">0</span>件</span>
    <div class="result-nav" id="resultNav"></div>
  </div>

  <section id="section-fuzokuhin" class="section" style="display:block">
    <div class="section-header">
      <h2>📋 HM付属品一覧</h2>
      <p class="section-desc">{fuz_month}版 メニュー別付属品（小袋・シール等）一覧</p>
    </div>
    <div class="swipe-container" id="swipe-fuzokuhin">
      {fuz_blocks}
    </div>
  </section>

  <section id="section-rice" class="section" style="display:none">
    <div class="section-header">
      <h2>🍚 ライス盛付・ラベル・小袋貼付位置</h2>
      <p class="section-desc">{rice_month}版 ライス盛付量・ラベル貼付位置・小袋貼付位置</p>
    </div>
    <div class="swipe-container" id="swipe-rice">
      {rice_blocks}
    </div>
  </section>

  <section id="section-chouri" class="section" style="display:none">
    <div class="section-header">
      <h2>🍳 調理手順シート（季節商品）</h2>
      <p class="section-desc">{cho_month}版 季節商品の調理・盛付手順</p>
    </div>
    <div class="swipe-container" id="swipe-chouri">
      {cho_blocks}
    </div>
  </section>

  <section id="section-chouri_zen" class="section" style="display:none">
    <div class="section-header">
      <h2>🍳 調理手順シート（全国版）</h2>
      <p class="section-desc">{chozen_month}版 全国共通メニューの調理・盛付手順</p>
    </div>
    <div class="swipe-container" id="swipe-chouri_zen">
      {chozen_blocks}
    </div>
  </section>

{hansoku_section}
</main>

<div id="lightbox" onclick="closeLightboxOutside(event)">
  <div id="lbToolbar">
    <span id="lbTitle"></span>
    <div id="lbZoomControls">
      <button class="lb-zoom-btn" onclick="lbZoom(-0.25)">－</button>
      <button class="lb-zoom-btn" onclick="lbZoom(+0.25)">＋</button>
    </div>
    <button id="lbClose" onclick="closeLightbox()">✕ 閉じる</button>
  </div>
  <div id="lbImgContainer">
    <img id="lbImg" src="" alt="">
  </div>
</div>

<script>
// ===== スワイプページめくり =====
const swipeState = {{}};

function initSwipe(containerId) {{
  const container = document.getElementById(containerId);
  if (!container) return;
  const blocks = Array.from(container.querySelectorAll('.page-block'));
  if (blocks.length <= 1) return;

  const state = {{ current: 0, total: blocks.length, blocks, containerId }};
  swipeState[containerId] = state;

  // ナビ挿入
  const nav = document.createElement('div');
  nav.className = 'swipe-nav';
  nav.id = 'swipe-nav-' + containerId;
  nav.innerHTML = `
    <button class="swipe-nav-btn" onclick="swipeTo('${{containerId}}', -1)">◀</button>
    <span class="swipe-counter" id="swipe-count-${{containerId}}">1 / ${{blocks.length}}</span>
    <button class="swipe-nav-btn" onclick="swipeTo('${{containerId}}', +1)">▶</button>`;
  container.insertBefore(nav, container.firstChild);

  // ページ番号ボタン列
  const nums = document.createElement('div');
  nums.className = 'page-nums';
  nums.id = 'page-nums-' + containerId;
  for (let i = 0; i < blocks.length; i++) {{
    const btn = document.createElement('button');
    btn.className = 'page-num-btn' + (i === 0 ? ' active' : '');
    btn.textContent = i + 1;
    btn.onclick = (function(idx) {{ return function() {{ swipeGoTo(containerId, idx); }}; }})(i);
    nums.appendChild(btn);
  }}
  nav.after(nums);

  // 初期表示: 最初のページのみ
  blocks.forEach((b, i) => b.classList.toggle('swipe-hidden', i !== 0));
  updateSwipeButtons(containerId);

  // タッチスワイプ
  let startX = 0, startY = 0, tracking = false;
  container.addEventListener('touchstart', e => {{
    startX = e.touches[0].clientX;
    startY = e.touches[0].clientY;
    tracking = true;
  }}, {{ passive: true }});
  container.addEventListener('touchend', e => {{
    if (!tracking) return;
    tracking = false;
    const dx = e.changedTouches[0].clientX - startX;
    const dy = e.changedTouches[0].clientY - startY;
    if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.5) {{
      swipeTo(containerId, dx < 0 ? 1 : -1);
    }}
  }}, {{ passive: true }});
}}

function swipeTo(containerId, dir) {{
  const s = swipeState[containerId];
  if (!s) return;
  const next = s.current + dir;
  if (next < 0 || next >= s.total) return;
  s.blocks[s.current].classList.add('swipe-hidden');
  s.current = next;
  s.blocks[s.current].classList.remove('swipe-hidden');
  updateSwipeButtons(containerId);
  window.scrollTo({{ top: s.blocks[s.current].offsetTop - 180, behavior: 'smooth' }});
}}

function swipeGoTo(containerId, pageIdx) {{
  const s = swipeState[containerId];
  if (!s || pageIdx < 0 || pageIdx >= s.total) return;
  s.blocks[s.current].classList.add('swipe-hidden');
  s.current = pageIdx;
  s.blocks[s.current].classList.remove('swipe-hidden');
  updateSwipeButtons(containerId);
  window.scrollTo({{ top: s.blocks[s.current].offsetTop - 180, behavior: 'smooth' }});
}}

function updateSwipeButtons(containerId) {{
  const s = swipeState[containerId];
  if (!s) return;
  const counter = document.getElementById('swipe-count-' + containerId);
  if (counter) counter.textContent = (s.current + 1) + ' / ' + s.total;
  const nav = document.getElementById('swipe-nav-' + containerId);
  if (nav) {{
    const btns = nav.querySelectorAll('.swipe-nav-btn');
    if (btns[0]) btns[0].disabled = s.current === 0;
    if (btns[1]) btns[1].disabled = s.current === s.total - 1;
  }}
  // ページ番号ボタンのアクティブ更新
  const nums = document.getElementById('page-nums-' + containerId);
  if (nums) {{
    const numBtns = nums.querySelectorAll('.page-num-btn');
    numBtns.forEach(function(b, i) {{ b.classList.toggle('active', i === s.current); }});
    // アクティブボタンが見えるように手動スクロール
    var activeBtn = numBtns[s.current];
    if (activeBtn) {{
      var numsEl = nums;
      setTimeout(function() {{
        var target = activeBtn.offsetLeft - numsEl.clientWidth / 2 + activeBtn.offsetWidth / 2;
        numsEl.scrollLeft = target;
      }}, 50);
    }}
  }}
}}

// 全コンテナ初期化
document.addEventListener('DOMContentLoaded', () => {{
  document.querySelectorAll('.swipe-container').forEach(c => initSwipe(c.id));
}});

// ===== タブ切替 =====
function showSection(id) {{
  document.querySelectorAll('.section').forEach(s => s.style.display = 'none');
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('section-' + id).style.display = 'block';
  document.getElementById('tab-' + id).classList.add('active');
  const q = document.getElementById('searchInput').value.trim();
  if (q) highlightSection(id, q);
  // 表示後にページ番号ボタンのスクロール位置を更新
  setTimeout(function() {{
    var section = document.getElementById('section-' + id);
    if (section) {{
      section.querySelectorAll('.swipe-container').forEach(function(c) {{
        if (swipeState[c.id]) updateSwipeButtons(c.id);
      }});
    }}
  }}, 60);
}}

// ===== 販促計画書 月切替 =====
function switchMonth(which) {{
  const curContent = document.getElementById('hansoku-cur-content');
  const prevContent = document.getElementById('hansoku-prev-content');
  document.querySelectorAll('.month-tab').forEach(b => b.classList.remove('active'));
  document.getElementById('month-tab-' + which)?.classList.add('active');
  if (which === 'cur') {{
    if (curContent) curContent.style.display = 'block';
    if (prevContent) prevContent.style.display = 'none';
  }} else {{
    if (curContent) curContent.style.display = 'none';
    if (prevContent) prevContent.style.display = 'block';
  }}
}}

// ===== 検索 =====
let searchTimer = null;
function handleSearch() {{
  clearTimeout(searchTimer);
  searchTimer = setTimeout(doSearch, 200);
}}

function doSearch() {{
  const q = document.getElementById('searchInput').value.trim();
  if (!q) {{ clearSearch(); return; }}
  const lower = q.toLowerCase();
  const results = [];
  const sectionCounts = {{}};

  document.querySelectorAll('.page-block').forEach(block => {{
    const text = (block.dataset.searchtext || '').toLowerCase();
    const hasMatch = text.includes(lower);
    block.classList.toggle('highlight', hasMatch);
    if (hasMatch) {{
      const sid = block.dataset.section;
      results.push({{ sectionId: sid, page: parseInt(block.dataset.page), el: block }});
      const mainSid = sid.startsWith('hansoku') ? 'hansoku' : sid;
      sectionCounts[mainSid] = (sectionCounts[mainSid] || 0) + 1;
    }}
  }});

  // タブにバッジ表示
  ['fuzokuhin', 'rice', 'chouri', 'chouri_zen', 'hansoku'].forEach(sid => {{
    const badge = document.getElementById('badge-' + sid);
    if (badge) {{
      const count = sectionCounts[sid] || 0;
      badge.textContent = count;
      badge.classList.toggle('show', count > 0);
    }}
  }});

  const banner = document.getElementById('searchBanner');
  const nav = document.getElementById('resultNav');
  document.getElementById('searchKeyword').textContent = q;
  document.getElementById('resultCount').textContent = results.length;
  nav.innerHTML = '';
  banner.style.display = 'block';

  if (results.length > 0) {{
    const sectionTitles = {{
      fuzokuhin: '付属品', rice: 'ライス盛付', chouri: '調理(季節)', chouri_zen: '調理(全国)',
      hansoku_cur: '販促(当月)', hansoku_prev: '販促(前月)'
    }};
    const groups = {{}};
    results.forEach(r => {{
      if (!groups[r.sectionId]) groups[r.sectionId] = [];
      groups[r.sectionId].push(r);
    }});
    Object.entries(groups).forEach(([sid, rs]) => {{
      rs.forEach(r => {{
        const btn = document.createElement('button');
        btn.className = 'result-chip';
        btn.textContent = (sectionTitles[sid] || sid) + ' p.' + r.page;
        btn.onclick = () => {{
          const mainSection = sid.startsWith('hansoku') ? 'hansoku' : sid;
          showSection(mainSection);
          if (sid === 'hansoku_prev') switchMonth('prev');
          else if (sid === 'hansoku_cur') switchMonth('cur');
          // スワイプで該当ページに移動
          const cid = sid.startsWith('hansoku') ? sid.replace('hansoku', 'hansoku') + '-content' : 'swipe-' + mainSection;
          const pageIdx = r.page - 1;
          if (swipeState[cid]) swipeGoTo(cid, pageIdx);
          else if (swipeState['swipe-' + mainSection]) swipeGoTo('swipe-' + mainSection, pageIdx);
          setTimeout(() => r.el.scrollIntoView({{ behavior: 'smooth', block: 'center' }}), 50);
        }};
        nav.appendChild(btn);
      }});
    }});

    // 最初の結果のセクションに遷移
    const firstResult = results[0];
    const mainSection = firstResult.sectionId.startsWith('hansoku') ? 'hansoku' : firstResult.sectionId;
    showSection(mainSection);
    if (firstResult.sectionId === 'hansoku_prev') switchMonth('prev');
    // スワイプページ移動
    const firstCid = 'swipe-' + mainSection;
    if (swipeState[firstCid]) swipeGoTo(firstCid, firstResult.page - 1);
    setTimeout(() => firstResult.el.scrollIntoView({{ behavior: 'smooth', block: 'center' }}), 100);
  }} else {{
    nav.innerHTML = '<span style="font-size:12px;color:#888;">見つかりませんでした</span>';
  }}
}}

function highlightSection(sectionId, q) {{
  const lower = q.toLowerCase();
  const selector = sectionId === 'hansoku'
    ? '.page-block[data-section^="hansoku"]'
    : `.page-block[data-section="${{sectionId}}"]`;
  document.querySelectorAll(selector).forEach(block => {{
    block.classList.toggle('highlight', (block.dataset.searchtext || '').toLowerCase().includes(lower));
  }});
}}

function clearSearch() {{
  document.getElementById('searchInput').value = '';
  document.querySelectorAll('.page-block').forEach(b => b.classList.remove('highlight'));
  document.getElementById('searchBanner').style.display = 'none';
  // バッジ非表示
  document.querySelectorAll('.tab-badge').forEach(b => b.classList.remove('show'));
}}

// ===== ライトボックス =====
let lbScale = 1.0;

function openLightbox(imgSrc, title) {{
  lbScale = 1.0;
  const img = document.getElementById('lbImg');
  document.getElementById('lbTitle').textContent = title;
  img.src = imgSrc;
  img.style.width = '100%';
  img.style.maxWidth = '100%';
  img.onerror = () => {{
    img.alt = '画像を読み込めませんでした';
    img.style.width = 'auto'; img.style.padding = '40px'; img.style.opacity = '0.4';
  }};
  document.getElementById('lightbox').classList.add('open');
  document.body.style.overflow = 'hidden';
}}

function closeLightbox() {{
  document.getElementById('lightbox').classList.remove('open');
  document.body.style.overflow = '';
}}

function closeLightboxOutside(e) {{
  if (e.target === document.getElementById('lightbox') ||
      e.target === document.getElementById('lbImgContainer')) closeLightbox();
}}

function lbZoom(delta) {{
  lbScale = Math.min(5, Math.max(0.5, lbScale + delta));
  const img = document.getElementById('lbImg');
  img.style.width = Math.round(lbScale * 100) + '%';
  img.style.maxWidth = 'none';
}}

document.addEventListener('keydown', e => {{
  if (e.key === 'Escape') closeLightbox();
  if (e.key === '+' || e.key === '=') lbZoom(+0.25);
  if (e.key === '-') lbZoom(-0.25);
  // 左右キーでスワイプ
  const activeSection = document.querySelector('.section[style*="display:block"], .section[style*="display: block"]');
  if (activeSection) {{
    const sc = activeSection.querySelector('.swipe-container');
    if (sc && swipeState[sc.id]) {{
      if (e.key === 'ArrowLeft') swipeTo(sc.id, -1);
      if (e.key === 'ArrowRight') swipeTo(sc.id, +1);
    }}
  }}
}});


</script>
</div><!-- /appContent -->

{lock_js}

</body>
</html>"""
    return html_content

# ===== 更新処理 =====

def update_section(name, pdf_path, prefix, config, first=None, last=None):
    """通常セクション（付属品・ライス盛付・調理手順）を更新。変更ページのsetを返す"""
    print(f"\n  📄 {name} を更新中...")
    text_file = os.path.join(IMAGES_DIR, f"{prefix}_text.txt")
    # テキスト抽出
    texts = extract_text(pdf_path, first, last)
    # 差分検出（旧テキストと比較）
    changed = detect_changed_pages(text_file, texts)
    if changed:
        print(f"  📝 変更検出: ページ {sorted(changed)}")
    else:
        print(f"  📝 変更なし（全ページ同一内容）")
    # 既存画像を削除
    for f in os.listdir(IMAGES_DIR):
        if f.startswith(prefix + "-"):
            os.remove(os.path.join(IMAGES_DIR, f))
    # 新しい画像を生成
    pdf_to_images(pdf_path, prefix, first, last)
    # テキスト保存
    with open(text_file, "w", encoding="utf-8") as f:
        f.write("\f".join(texts))
    page_count = len([f for f in os.listdir(IMAGES_DIR) if f.startswith(prefix + "-")])
    print(f"  ✅ {page_count}ページ変換完了")
    return page_count, changed

def update_hansoku(pdf_path, month_label, config):
    """販促計画書を更新（前月へローテーション）。変更ページのsetを返す"""
    print(f"\n  📣 販促計画書を更新中（当月: {month_label}）...")
    cur_txt = os.path.join(IMAGES_DIR, "hansoku_cur_text.txt")
    prev_txt = os.path.join(IMAGES_DIR, "hansoku_prev_text.txt")
    # テキスト抽出（差分検出用に先に行う）
    texts = extract_text(pdf_path, 1, HANSOKU_PAGES)
    changed = detect_changed_pages(cur_txt, texts)
    if changed:
        print(f"  📝 変更検出: ページ {sorted(changed)}")
    # 前月データをクリア
    for f in os.listdir(IMAGES_DIR):
        if f.startswith("hansoku_prev_"):
            os.remove(os.path.join(IMAGES_DIR, f))
    # 当月 → 前月へ移動
    prev_count = 0
    for f in sorted(os.listdir(IMAGES_DIR)):
        if f.startswith("hansoku_cur_") and not f.endswith("_text.txt"):
            new_name = f.replace("hansoku_cur_", "hansoku_prev_")
            os.rename(os.path.join(IMAGES_DIR, f), os.path.join(IMAGES_DIR, new_name))
            prev_count += 1
    # テキストも移動
    if os.path.exists(cur_txt):
        if os.path.exists(prev_txt):
            os.remove(prev_txt)
        os.rename(cur_txt, prev_txt)
    prev_month = config.get("hansoku_cur", {}).get("month")
    config["hansoku_prev"] = {"month": prev_month, "pages": prev_count}
    print(f"  📦 前月（{prev_month}）: {prev_count}ページ を保存")
    # 新しい当月画像を生成
    pdf_to_images(pdf_path, "hansoku_cur", 1, HANSOKU_PAGES)
    with open(cur_txt, "w", encoding="utf-8") as f:
        f.write("\f".join(texts))
    cur_count = len([f for f in os.listdir(IMAGES_DIR) if f.startswith("hansoku_cur_") and not f.endswith("_text.txt")])
    config["hansoku_cur"] = {"month": month_label, "pages": cur_count}
    print(f"  ✅ 当月（{month_label}）: {cur_count}ページ 変換完了")
    return changed

# ===== メイン =====

def main():
    parser = argparse.ArgumentParser(
        description="HMマニュアル 月次更新スクリプト",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("--fuzokuhin", help="HM付属品一覧 PDFのパス")
    parser.add_argument("--rice",      help="ライス盛付 PDFのパス")
    parser.add_argument("--chouri",    help="調理手順シート（季節商品）PDFのパス")
    parser.add_argument("--chouri-zen", dest="chouri_zen", help="調理手順シート（全国版）PDFのパス")
    parser.add_argument("--hansoku",   help="販促計画書 PDFのパス（当月）")
    parser.add_argument("--month",     help='当月ラベル（例: "2026年6月"）')
    parser.add_argument("--password",  help='閲覧パスワード（SHA-256ハッシュ化して埋め込み）')
    parser.add_argument("--expires",   help='有効期限 YYYY-MM-DD（省略時は更新日+45日）')
    parser.add_argument("--embed",     action="store_true", help='画像をBase64埋め込み（HTMLファイル1つで完結）')
    args = parser.parse_args()

    if not any([args.fuzokuhin, args.rice, args.chouri, args.chouri_zen, args.hansoku]):
        print("❌ 更新するPDFを最低1つ指定してください。")
        print("   使い方: python3 update_manual.py --help")
        return

    print("🍱 HMマニュアル 更新開始...")
    os.makedirs(IMAGES_DIR, exist_ok=True)
    config = load_config()
    changed_pages_map = {}

    # 付属品一覧
    if args.fuzokuhin:
        month = args.month or detect_month_from_filename(args.fuzokuhin) or "不明"
        pages, changed = update_section("HM付属品一覧", args.fuzokuhin, "fuzokuhin", config)
        config["fuzokuhin"] = {"month": month, "pages": pages}
        changed_pages_map["fuzokuhin"] = changed

    # ライス盛付
    if args.rice:
        month = args.month or detect_month_from_filename(args.rice) or "不明"
        pages, changed = update_section("ライス盛付", args.rice, "rice", config)
        config["rice"] = {"month": month, "pages": pages}
        changed_pages_map["rice"] = changed

    # 調理手順
    if args.chouri:
        month = args.month or detect_month_from_filename(args.chouri) or "不明"
        pages, changed = update_section("調理手順シート", args.chouri, "chouri", config)
        config["chouri"] = {"month": month, "pages": pages}
        changed_pages_map["chouri"] = changed

    # 調理手順（全国版）
    if args.chouri_zen:
        month = args.month or detect_month_from_filename(args.chouri_zen) or "不明"
        pages, changed = update_section("調理手順シート（全国版）", args.chouri_zen, "chouri_zen", config)
        config["chouri_zen"] = {"month": month, "pages": pages}
        changed_pages_map["chouri_zen"] = changed

    # 販促計画書
    if args.hansoku:
        month = args.month or detect_month_from_filename(args.hansoku) or "不明"
        changed = update_hansoku(args.hansoku, month, config)
        changed_pages_map["hansoku_cur"] = changed

    # パスワード・有効期限の処理
    password_hash = None
    expires_date = None
    if args.password:
        password_hash = hashlib.sha256(args.password.encode('utf-8')).hexdigest()
        print(f"\n  🔒 パスワード保護: 有効")
        if args.expires:
            expires_date = args.expires
        else:
            expires_date = (datetime.now() + timedelta(days=45)).strftime("%Y-%m-%d")
        print(f"  📅 有効期限: {expires_date}")
    elif args.expires:
        # パスワードなしでも有効期限だけ設定は無意味なので警告
        print("\n  ⚠️ --expires は --password と一緒に指定してください（無視されます）")

    # configにパスワード設定状態を保存（ハッシュと有効期限）
    if password_hash:
        config["auth"] = {"hash": password_hash, "expires": expires_date}
    elif "auth" in config:
        # 前回のパスワード設定を引き継ぐ
        password_hash = config["auth"].get("hash")
        expires_date = config["auth"].get("expires")
        if password_hash:
            print(f"\n  🔒 前回のパスワード設定を引き継ぎ（有効期限: {expires_date}）")

    # HTML再生成
    embed = getattr(args, 'embed', False)
    if embed:
        print("\n  📦 画像埋め込みモード（HTMLファイル1つで完結）")
    print("  🔨 index.html を再生成中...")
    html = build_html(config, changed_pages_map, password_hash, expires_date, embed_images=embed)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    size = os.path.getsize(OUTPUT_HTML)
    if size > 1024 * 1024:
        print(f"  ✅ index.html 生成完了 ({size//(1024*1024)}MB)")
    else:
        print(f"  ✅ index.html 生成完了 ({size//1024}KB)")

    save_config(config)
    print(f"\n✨ 更新完了！ ブラウザで index.html を開いてください。")
    print(f"   open \"{OUTPUT_HTML}\"")

if __name__ == "__main__":
    main()
