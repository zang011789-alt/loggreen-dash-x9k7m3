# -*- coding: utf-8 -*-
"""Google Ads 캠페인 데이터 수집 (DOM 스크래핑)
포트 9223 / user-data-dir: C:\Temp\chrome_gg
실행: python gads_auto.py [YYYY-MM-DD [YYYY-MM-DD]] [--no-push]
"""
import sys, json, os, re, time, socket, subprocess, argparse
from datetime import date, timedelta
sys.stdout.reconfigure(encoding="utf-8")

SCRIPT_DIR  = r"C:\Users\zang0\Desktop\my-site"
JSON_PATH   = os.path.join(SCRIPT_DIR, "gads_history.json")
JS_PATH     = os.path.join(SCRIPT_DIR, "gads_history.js")
CHROME_EXE  = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
USER_DATA   = r"C:\Temp\chrome_gg"
DEBUG_PORT  = 9223
MAX_DAYS    = 180

OCID = "1604778170"
BASE_URL = f"https://ads.google.com/aw/campaigns?ocid={OCID}"

# 캠페인명 → 제품 매핑 (틱톡/메타와 동일)
PRODUCT_MAP = {
    "gg_kd_": "키즈픽션",
    "gg_tp_": "템퍼픽션",
    "gg_yb_": "노즈픽션",
    "gg_np_": "노즈스파",
    "gg_ato_": "아토픽션",
    "gg_km_": "키로메디",
}

# ── Chrome 관리 ────────────────────────────────────────────────
def _port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except:
        return False

def _cdp_ok(port):
    try:
        import urllib.request
        r = urllib.request.urlopen(f"http://localhost:{port}/json/version", timeout=3)
        return r.status == 200
    except:
        return False

def _kill_chrome(port):
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             f"Get-Process chrome | Where-Object {{$_.MainWindowTitle -eq ''}} | ForEach-Object {{$_.Id}}"],
            capture_output=True, text=True, timeout=10
        )
    except:
        pass
    try:
        subprocess.run(
            ["powershell", "-Command",
             f"Get-CimInstance Win32_Process | Where-Object {{$_.CommandLine -like '*chrome_gg*'}} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}"],
            capture_output=True, timeout=10
        )
    except:
        pass

def _start_chrome():
    subprocess.Popen([
        CHROME_EXE,
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={USER_DATA}",
        "--no-first-run",
        "--no-default-browser-check",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(4)

def ensure_chrome():
    if _port_open(DEBUG_PORT) and _cdp_ok(DEBUG_PORT):
        return
    print(f"  Chrome(포트 {DEBUG_PORT}) 재시작 중...")
    _kill_chrome(DEBUG_PORT)
    time.sleep(2)
    _start_chrome()
    for _ in range(15):
        if _cdp_ok(DEBUG_PORT):
            return
        time.sleep(1)
    raise RuntimeError(f"Chrome 포트 {DEBUG_PORT} 기동 실패")

# ── 파싱 유틸 ──────────────────────────────────────────────────
def parse_krw(text):
    """₩1,234,567 → 1234567"""
    m = re.search(r'₩([\d,]+)', text.replace('\xa0', ''))
    if m:
        return int(m.group(1).replace(',', ''))
    return 0

def parse_float(text):
    """숫자 추출 (%, — 처리)"""
    text = text.strip().replace('%', '').replace(',', '').replace('—', '0').replace('\xa0', '')
    try:
        return float(text)
    except:
        return 0.0

def guess_product(name):
    for prefix, prod in PRODUCT_MAP.items():
        if prefix in name:
            return prod
    return "기타"

# ── 날짜 범위 설정 ─────────────────────────────────────────────
def set_date_range(page, target_date):
    """Google Ads 날짜 피커에서 target_date 단일 날짜로 설정"""
    today = date.today()
    yesterday = today - timedelta(days=1)

    # 현재 날짜 범위 텍스트 확인
    try:
        current_date_text = page.evaluate("""
        () => {
            const spans = [...document.querySelectorAll('span')];
            for (const s of spans) {
                const t = s.innerText ? s.innerText.trim() : '';
                if (t.match(/\\d{4}년 \\d+월 \\d+일/)) return t;
            }
            return '';
        }
        """)
        td_str = f"{target_date.year}년 {target_date.month}월 {target_date.day}일"
        if td_str in current_date_text and ('~' not in current_date_text or
                                             current_date_text.strip().startswith(td_str)):
            print(f"  날짜 이미 {td_str} 설정됨 → 스킵")
            return True
    except:
        pass

    # 날짜 피커 SPAN 클릭 (텍스트에 '년'과 '월' 포함된 SPAN)
    try:
        date_spans = page.get_by_text(re.compile(r'\d{4}년 \d+월 \d+일'))
        for i in range(date_spans.count()):
            el = date_spans.nth(i)
            bb = el.bounding_box()
            if bb and bb['y'] < 400:
                el.click()
                page.wait_for_timeout(1200)
                break
    except:
        pass

    # 프리셋 버튼 찾기
    preset = None
    if target_date == today:
        preset = "오늘"
    elif target_date == yesterday:
        preset = "어제"

    if preset:
        try:
            btn = page.get_by_text(preset, exact=True)
            if btn.count() > 0:
                btn.first.click()
                page.wait_for_timeout(1500)
                try:
                    apply = page.get_by_text("적용", exact=True)
                    if apply.count() > 0:
                        apply.first.click()
                        page.wait_for_timeout(2000)
                except:
                    pass
                return True
        except:
            pass

    return False

# ── 데이터 추출 ────────────────────────────────────────────────
def extract_summary(page):
    """상단 KPI 카드 추출"""
    try:
        result = page.evaluate("""
        () => {
            const cards = {};
            // KPI 카드: data-metric-id 또는 인접 텍스트 노드 기반
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            let node;
            const labels = ['비용', '노출수', '전환수', '전환 가치', '클릭수'];
            let lastLabel = null;
            while (node = walker.nextNode()) {
                const t = node.textContent.trim();
                if (labels.includes(t)) {
                    lastLabel = t;
                } else if (lastLabel && t.match(/^[₩\d,\.]+$/) && t.length > 1) {
                    if (!cards[lastLabel]) cards[lastLabel] = t;
                    lastLabel = null;
                } else if (lastLabel && t === '') {
                    // 빈 노드는 스킵
                } else {
                    lastLabel = null;
                }
            }
            return cards;
        }
        """)
        spend = parse_krw(result.get('비용', '0'))
        impressions = int(result.get('노출수', '0').replace(',', '')) if result.get('노출수') else 0
        conv_val_str = result.get('전환 가치', '0').replace(',', '')
        conv_value = int(float(conv_val_str)) if conv_val_str else 0
        conversions = parse_float(result.get('전환수', '0'))
        return {
            "spend": spend,
            "impressions": impressions,
            "conv_value": conv_value,
            "conversions": conversions,
        }
    except Exception as e:
        print(f"  summary 추출 실패: {e}")
        return {}

def extract_campaigns(page):
    """캠페인 테이블 행 추출"""
    campaigns = []
    try:
        rows = page.evaluate("""
        () => {
            const results = [];
            const rows = document.querySelectorAll('[role="row"]');
            for (const row of rows) {
                // A 태그 중 gg_ 로 시작하는 것 찾기 (href 빈 경우도 처리)
                const links = [...row.querySelectorAll('a')];
                const link = links.find(a => a.innerText.trim().startsWith('gg_'));
                if (!link) continue;
                const name = link.innerText.trim();

                // 행 전체 텍스트를 줄 단위로 분리
                const rawLines = row.innerText.split('\\n')
                    .map(l => l.trim())
                    .filter(l => l.length > 0 && l !== 'settings');

                results.push({ name, rawLines });
            }
            return results;
        }
        """)

        for row in rows:
            name = row['name']
            product = guess_product(name)
            lines = row['rawLines']  # ['gg_...', '₩100,000/일', '예산 제약 있음', '—', '₩540,465', '—', '—']

            # budget: 첫 번째 /일 포함 줄
            budget = 0
            budget_idx = -1
            for i, l in enumerate(lines):
                if '₩' in l and '일' in l:
                    budget = parse_krw(l)
                    budget_idx = i
                    break

            # 뒤에서: CTR[-1], ROAS[-2], spend[-3], CPA[-4]
            ctr  = parse_float(lines[-1]) if len(lines) >= 1 else 0.0
            roas = parse_float(lines[-2]) if len(lines) >= 2 else 0.0
            spend_raw = lines[-3] if len(lines) >= 3 else '0'
            cpa_raw   = lines[-4] if len(lines) >= 4 else '0'

            spend = parse_krw(spend_raw) if '₩' in spend_raw else parse_float(spend_raw)
            cpa   = parse_krw(cpa_raw)   if '₩' in cpa_raw   else 0

            # status: budget 이후 ~ spend 이전의 줄들
            status_lines = []
            if budget_idx >= 0:
                for l in lines[budget_idx+1:-4]:
                    if '—' not in l and '₩' not in l:
                        status_lines.append(l)
            status = ', '.join(status_lines) if status_lines else ''

            campaigns.append({
                "name": name,
                "product": product,
                "status": status,
                "budget": budget,
                "spend": spend,
                "cpa": cpa,
                "roas": roas,
                "ctr": ctr,
            })

    except Exception as e:
        print(f"  campaigns 추출 실패: {e}")

    return campaigns

# ── 메인 수집 ──────────────────────────────────────────────────
def collect_day(page, target_date):
    ds = target_date.isoformat()
    url = BASE_URL
    print(f"  Google Ads [{ds}] 수집 중...")

    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)

    # 날짜 설정 시도
    date_set = set_date_range(page, target_date)
    if not date_set:
        print(f"  날짜 설정 실패 → 현재 보이는 데이터 그대로 수집")
    page.wait_for_timeout(3000)

    # 스크롤하여 모든 행 로드 (가상 스크롤 대응)
    for _ in range(20):
        page.evaluate("window.scrollBy(0, 600)")
        page.wait_for_timeout(300)
    page.wait_for_timeout(500)
    page.evaluate("window.scrollTo(0,0)")
    page.wait_for_timeout(1000)

    summary = extract_summary(page)
    campaigns = extract_campaigns(page)

    total_spend = summary.get("spend", 0)
    active = sum(1 for c in campaigns if "운영 가능" in c.get("status", ""))
    print(f"  [{ds}] 캠페인 {len(campaigns)}개(활성:{active}) | 소진:{total_spend:,} | 전환:{summary.get('conversions',0)}")

    return {
        "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "date": ds,
        "summary": summary,
        "campaigns": campaigns,
    }

def save_history(history):
    # 180일 초과 삭제
    keys = sorted(history.keys())
    while len(keys) > MAX_DAYS:
        del history[keys.pop(0)]

    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    js_content = "// auto-generated\nwindow.GADS_HISTORY = " + json.dumps(history, ensure_ascii=False) + ";\n"
    with open(JS_PATH, "w", encoding="utf-8") as f:
        f.write(js_content)
    print(f"  저장 완료 ({len(history)}개 날짜)")

def git_push():
    try:
        subprocess.run(["git", "-C", SCRIPT_DIR, "add", "gads_history.js"], timeout=15)
        subprocess.run(["git", "-C", SCRIPT_DIR, "commit", "-m", "auto: gads history update"], timeout=15)
        subprocess.run(["git", "-C", SCRIPT_DIR, "push"], timeout=30)
        print("  git push 완료")
    except Exception as e:
        print(f"  git push 실패: {e}")

# ── 진입점 ────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("start", nargs="?", help="YYYY-MM-DD")
    parser.add_argument("end",   nargs="?", help="YYYY-MM-DD")
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--port",    type=int, default=DEBUG_PORT)
    args = parser.parse_args()

    if args.port != DEBUG_PORT:
        globals()['DEBUG_PORT'] = args.port

    if args.start:
        start = date.fromisoformat(args.start)
        end   = date.fromisoformat(args.end) if args.end else start
    else:
        start = end = date.today()

    dates = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    print(f"=== Google Ads 수집 {start}~{end} ({len(dates)}일) ===")

    ensure_chrome()

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{DEBUG_PORT}", timeout=30000)
        except Exception:
            print("  CDP 연결 실패 → Chrome 재시작")
            _kill_chrome(DEBUG_PORT)
            _start_chrome()
            time.sleep(3)
            browser = p.chromium.connect_over_cdp(f"http://localhost:{DEBUG_PORT}", timeout=30000)

        ctx  = browser.contexts[0]
        page = next((pg for pg in ctx.pages if "ads.google.com" in pg.url), None)
        if page is None:
            page = ctx.new_page()

        # 기존 히스토리 로드
        history = {}
        if os.path.exists(JSON_PATH):
            try:
                with open(JSON_PATH, encoding="utf-8") as f:
                    history = json.load(f)
            except:
                pass

        for target_date in dates:
            ds = target_date.isoformat()
            try:
                data = collect_day(page, target_date)
                history[ds] = data
            except Exception as e:
                print(f"  [{ds}] 오류: {e}")

        browser.close()

    save_history(history)
    if not args.no_push:
        git_push()
    print("완료")

if __name__ == "__main__":
    main()
