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

# 보기 뷰 → gg_ 캠페인 목록. '스파' 뷰는 계정 전체 summary 수집용
PRODUCT_VIEWS = ['노픽', '템퍼픽션', '검색 캠페인']
SUMMARY_VIEW  = '스파'

# 캠페인명 → 제품 매핑
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
    m = re.search(r'₩([\d,]+)', text.replace('\xa0', ''))
    if m:
        return int(m.group(1).replace(',', ''))
    return 0

def parse_float(text):
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

# ── 뷰 전환 ──────────────────────────────────────────────────────
def switch_to_view(page, view_name):
    """'보기' 드롭다운에서 특정 뷰 선택. 성공 여부 반환."""
    # 드롭다운 열기 (JS 기반 — 위치 변화에 무관)
    opened = page.evaluate("""
    () => {
        const btns = [...document.querySelectorAll('[role="button"]')];
        const btn = btns.find(b => {
            const t = (b.innerText || '');
            const r = b.getBoundingClientRect();
            return (t.includes('보기') || t.includes('필터')) && r.y > 60 && r.y < 150
                   && r.x > 300 && r.x < 700;
        });
        if (btn) { btn.click(); return true; }
        return false;
    }
    """)
    if not opened:
        print(f"  '보기' 드롭다운 열기 실패")
        return False
    page.wait_for_timeout(800)

    # 뷰 아이템 클릭
    result = page.evaluate(f"""
    () => {{
        const items = [...document.querySelectorAll('material-select-item')];
        const item = items.find(i => (i.innerText || '').includes('{view_name}'));
        if (item) {{ item.click(); return true; }}
        return false;
    }}
    """)
    if not result:
        # 드롭다운 닫기
        page.keyboard.press("Escape")
        print(f"  '{view_name}' 아이템 없음")
        return False
    page.wait_for_timeout(2000)
    return True

# ── 날짜 범위 설정 ─────────────────────────────────────────────
def _picker_is_open(page):
    return page.evaluate("""
    () => {
        const opts = [...document.querySelectorAll('material-select-item[role="option"]')];
        return opts.some(o => {
            const r = o.getBoundingClientRect();
            return r.width > 0 && r.x > 1400 && r.y > 50;
        });
    }
    """)

def _open_date_picker(page):
    if _picker_is_open(page):
        return True
    clicked = page.evaluate("""
    () => {
        const btns = [...document.querySelectorAll('[role="button"]')];
        const candidates = btns.filter(b => {
            const t = b.innerText || b.textContent || '';
            const r = b.getBoundingClientRect();
            return r.y > 50 && r.y < 300 && r.x > 900 && r.width >= 80 && r.width <= 280
                   && t.includes('arrow_drop_down');
        }).sort((a, b) => a.getBoundingClientRect().width - b.getBoundingClientRect().width);
        if (candidates[0]) {
            candidates[0].click();
            const r = candidates[0].getBoundingClientRect();
            return { y: Math.round(r.y), x: Math.round(r.x), text: (candidates[0].innerText||'').substring(0,40) };
        }
        return null;
    }
    """)
    if clicked:
        print(f"  피커 클릭: y={clicked['y']} x={clicked['x']} '{clicked['text'][:30]}'")
    page.wait_for_timeout(2500)
    return clicked is not None

def _apply_date_picker(page):
    try:
        apply = page.get_by_text("적용", exact=True)
        if apply.count() > 0:
            apply.first.click()
            page.wait_for_timeout(2500)
    except:
        pass

def _get_dot_inputs(page):
    return page.evaluate("""
    () => {
        const inputs = [...document.querySelectorAll('input[type="text"]')];
        return inputs
            .filter(i => {
                const r = i.getBoundingClientRect();
                const v = i.value || '';
                return r.y > 100 && r.y < 700 && r.width > 30 && r.width < 200 && v.includes('.');
            })
            .map(i => ({ y: Math.round(i.getBoundingClientRect().y),
                         w: Math.round(i.getBoundingClientRect().width),
                         val: i.value }));
    }
    """)

def _fill_date_inputs(page, date_dot_str):
    inputs = _get_dot_inputs(page)
    if not inputs:
        return False
    all_inputs = page.locator('input[type="text"]')
    target_inp = inputs[0]
    for i in range(all_inputs.count()):
        el = all_inputs.nth(i)
        bb = el.bounding_box()
        if bb and abs(bb['y'] - target_inp['y']) < 5 and abs(bb['width'] - target_inp['w']) < 5:
            el.click(click_count=3)
            page.wait_for_timeout(200)
            el.fill(date_dot_str)
            page.wait_for_timeout(400)
            page.keyboard.press("Tab")
            page.wait_for_timeout(300)
            page.keyboard.press("Control+a")
            page.wait_for_timeout(100)
            page.keyboard.type(date_dot_str, delay=30)
            page.wait_for_timeout(400)
            page.keyboard.press("Enter")
            page.wait_for_timeout(300)
            print(f"  시작일/종료일 {date_dot_str} 입력 완료")
            return True
    return False

def set_date_range(page, target_date):
    date_dot_str = f"{target_date.year}. {target_date.month}. {target_date.day}."

    if not _open_date_picker(page):
        print("  날짜 피커 열기 실패")
        return False

    # 이미 올바른 날짜면 피커 닫고 바로 반환
    existing = _get_dot_inputs(page)
    if existing and existing[0]['val'] == date_dot_str:
        print(f"  날짜 이미 {date_dot_str}, 스킵")
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        return True

    # "맞춤" 클릭 (x>1200 제약 — 우측 date picker 영역, view selector와 분리)
    clicked = page.evaluate("""
    () => {
        const items = [...document.querySelectorAll('material-select-item, [role="option"]')];
        const item = items.find(i => {
            const t = (i.innerText || '').trim();
            const r = i.getBoundingClientRect();
            return t === '맞춤' && r.x > 1200 && r.y > 100 && r.y < 700;
        });
        if (item) { item.click(); return true; }
        return false;
    }
    """)
    if clicked:
        page.wait_for_timeout(1200)
        print(f"  '맞춤' 옵션 클릭")

    inputs = _get_dot_inputs(page)
    print(f"  dot inputs: {len(inputs)}개 {inputs[:1]}")

    if inputs:
        ok = _fill_date_inputs(page, date_dot_str)
        if ok:
            page.wait_for_timeout(500)
            _apply_date_picker(page)
            page.wait_for_timeout(2000)
            return True

    # 폴백: "시작일" 클릭 후 타이핑
    print("  시작일 클릭 폴백")
    try:
        start_pos = page.evaluate("""
        () => {
            const spans = [...document.querySelectorAll('div, span')];
            const el = spans.find(e => {
                const t = (e.innerText || '').trim();
                const r = e.getBoundingClientRect();
                return t === '시작일' && r.y > 80 && r.y < 300 && r.width > 20 && r.width < 200;
            });
            if (!el) return null;
            const r = el.getBoundingClientRect();
            return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) };
        }
        """)
        if start_pos:
            print(f"  시작일 클릭: ({start_pos['x']}, {start_pos['y']})")
            page.mouse.click(start_pos['x'], start_pos['y'])
            page.wait_for_timeout(400)
            page.keyboard.press("Control+a")
            page.wait_for_timeout(100)
            page.keyboard.type(date_dot_str, delay=30)
            page.wait_for_timeout(400)
            page.keyboard.press("Tab")
            page.wait_for_timeout(300)
            page.keyboard.press("Control+a")
            page.wait_for_timeout(100)
            page.keyboard.type(date_dot_str, delay=30)
            page.wait_for_timeout(400)
            page.keyboard.press("Enter")
            page.wait_for_timeout(300)
            page.wait_for_timeout(500)
            _apply_date_picker(page)
            page.wait_for_timeout(2000)
            return True
    except Exception as e:
        print(f"  시작일 클릭 실패: {e}")

    return False

# ── 데이터 추출 ────────────────────────────────────────────────
def extract_summary(page):
    """상단 KPI 카드 추출 (비용/노출수/전환수/전환가치)"""
    try:
        result = page.evaluate("""
        () => {
            const cards = {};
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
                    // skip
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
        return {"spend": spend, "impressions": impressions, "conv_value": conv_value, "conversions": conversions}
    except Exception as e:
        print(f"  summary 추출 실패: {e}")
        return {}

def extract_campaigns(page):
    """캠페인 테이블 행 추출 (gg_ 캠페인만)
    포맷 감지:
      SHORT (8줄/템퍼픽션 DMG): lines[-1] ends with '%'
        → ctr=[-1], roas=[-2], spend=[-3](₩), cpa=[-4]
      LONG (18-20줄/노픽 DMG): lines[-2] has ₩
        → spend=[-2](₩), cpa=[-3](₩), roas=[-5], ctr=[-7]
      GSA (16줄/검색 캠페인): lines[-1] has ₩, lines[-2] is float
        → conv_value=[-1](₩), conversions=[-2], spend=[-5](₩), ctr=[-7]
    """
    campaigns = []
    try:
        rows = page.evaluate("""
        () => {
            const results = [];
            const rows = document.querySelectorAll('[role="row"]');
            for (const row of rows) {
                const links = [...row.querySelectorAll('a')];
                const link = links.find(a => a.innerText.trim().startsWith('gg_'));
                if (!link) continue;
                const name = link.innerText.trim();
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
            lines = row['rawLines']

            if len(lines) < 5:
                continue

            budget = 0
            budget_idx = -1
            for i, l in enumerate(lines):
                if '₩' in l and '일' in l:
                    budget = parse_krw(l)
                    budget_idx = i
                    break

            # 상태 추출
            status = ''
            if budget_idx >= 0:
                status_kws = ['운영 가능', '예산 제약', '제한', '일시정지', '삭제']
                for l in lines[budget_idx+1:budget_idx+5]:
                    if any(kw in l for kw in status_kws):
                        status = l
                        break

            last = lines[-1]
            second_last = lines[-2]

            conv_value  = 0.0
            conversions = 0.0

            if last.endswith('%'):
                # SHORT 포맷 (템퍼픽션 DMG 등, 7줄)
                # [-3]=SPEND(₩), [-2]=ROAS, [-1]=CTR%
                ctr   = parse_float(last)
                roas  = parse_float(second_last)
                spend = parse_krw(lines[-3]) if len(lines) >= 3 and '₩' in lines[-3] else 0
                cpa   = 0  # SHORT 포맷엔 ₩CPA 없음
                conv_value = round(spend * roas, 2)

            elif '₩' in second_last:
                # LONG 포맷 (노픽 DMG 등, 15~19줄)
                # 고정 12열 블록 (끝에서):
                # [-12]=SPEND, [-11]=ROAS, [-7]=CTR%, [-4]=CONVERSIONS, [-3]=CPC, [-2]=CPA, [-1]=CONV_VALUE
                spend = parse_krw(lines[-12]) if len(lines) >= 12 and '₩' in lines[-12] else parse_krw(second_last)
                cpa   = parse_krw(second_last)
                roas  = parse_float(lines[-11]) if len(lines) >= 11 else 0.0
                ctr   = parse_float(lines[-7]) if len(lines) >= 7 else 0.0
                conversions = parse_float(lines[-4]) if len(lines) >= 4 else 0.0
                conv_value  = parse_float(lines[-6]) if len(lines) >= 6 else 0.0

            else:
                # GSA 포맷 (검색 캠페인, 15줄)
                # [-7]=CTR%, [-6]=CPC(₩), [-5]=SPEND(₩), [-4]=bidding, [-3]=CVR%, [-2]=CONV_CNT, [-1]=CONV_VALUE(₩)
                conv_value  = parse_krw(last) if '₩' in last else 0.0
                spend       = parse_krw(lines[-5]) if len(lines) >= 5 and '₩' in lines[-5] else 0
                ctr         = parse_float(lines[-7]) if len(lines) >= 7 else 0.0
                conv_cnt    = parse_float(second_last)
                conversions = conv_cnt
                roas = round(conv_value / spend, 2) if spend > 0 else 0.0
                cpa  = int(spend / conv_cnt) if conv_cnt > 0 else 0

            campaigns.append({
                "name": name,
                "product": product,
                "status": status,
                "budget": budget,
                "spend": spend,
                "cpa": cpa,
                "roas": roas,
                "ctr": ctr,
                "conv_value": conv_value,
                "conversions": conversions,
            })

    except Exception as e:
        print(f"  campaigns 추출 실패: {e}")

    return campaigns

# ── 메인 수집 ──────────────────────────────────────────────────
def _scroll_and_collect(page):
    """스크롤로 virtual scroll 완전 로드 후 캠페인 추출"""
    if _picker_is_open(page):
        page.keyboard.press("Escape")
        page.wait_for_timeout(800)
    try:
        page.wait_for_selector('[role="row"]', state="visible", timeout=8000)
    except:
        pass
    page.wait_for_timeout(1500)
    for _ in range(20):
        page.evaluate("window.scrollBy(0, 600)")
        page.wait_for_timeout(200)
    page.evaluate("window.scrollTo(0,0)")
    page.wait_for_timeout(800)
    return extract_campaigns(page)

def collect_day(page, target_date):
    """모든 뷰를 순회하며 gg_ 캠페인 수집. 각 뷰 KPI를 집계해 summary 생성."""
    ds = target_date.isoformat()
    all_campaigns = []

    for view_name in PRODUCT_VIEWS:
        switched = switch_to_view(page, view_name)
        if not switched:
            continue
        try:
            page.wait_for_selector('[role="columnheader"]', timeout=8000)
        except:
            pass
        page.wait_for_timeout(1000)

        date_ok = set_date_range(page, target_date)
        if not date_ok:
            print(f"  [{view_name}] 날짜 설정 실패")
        if _picker_is_open(page):
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
        page.wait_for_timeout(1000)

        camps = _scroll_and_collect(page)
        all_campaigns.extend(camps)
        print(f"  [{view_name}] {len(camps)}개")

    # summary: 수집된 gg_ 캠페인 합산
    total_spend = sum(c["spend"] for c in all_campaigns)
    total_conv  = sum(c["conversions"] for c in all_campaigns)
    total_cv    = sum(c["conv_value"] for c in all_campaigns)
    summary = {
        "spend": total_spend,
        "impressions": 0,
        "conv_value": int(total_cv),
        "conversions": round(total_conv, 2),
    }

    active = sum(1 for c in all_campaigns if "운영 가능" in c.get("status", ""))
    print(f"  [{ds}] 총 {len(all_campaigns)}개(활성:{active}) | 소진:{total_spend:,} | 전환:{total_conv}")

    return {
        "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "date": ds,
        "summary": summary,
        "campaigns": all_campaigns,
    }

def save_history(history):
    keys = sorted(history.keys())
    while len(keys) > MAX_DAYS:
        del history[keys.pop(0)]

    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    js_content = "// auto-generated\nwindow.GADS_HISTORY = " + json.dumps(history, ensure_ascii=True) + ";\n"
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

        history = {}
        if os.path.exists(JSON_PATH):
            try:
                with open(JSON_PATH, encoding="utf-8") as f:
                    history = json.load(f)
            except:
                pass

        # 페이지 초기 로드 (1회)
        page.goto(BASE_URL, wait_until="load", timeout=40000)
        try:
            page.wait_for_selector('[role="columnheader"]', timeout=12000)
        except:
            pass
        page.wait_for_timeout(2500)

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
