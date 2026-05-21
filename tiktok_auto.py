# -*- coding: utf-8 -*-
"""
TikTok 광고 자동 수집 - 일별(하루씩) 저장 방식
tiktok_history[날짜][브랜드] = { scraped_at, summary, campaigns[], ads[] }
브랜드: outcoma (아웃코마) / ridermune (리더뮨)
Chrome이 디버그 모드로 열려있어야 함 (포트 9222)
"""
import json, sys, re, subprocess, os
from playwright.sync_api import sync_playwright
from datetime import datetime, date, timedelta

sys.stdout.reconfigure(encoding="utf-8")

HISTORY_JSON = r"C:\Users\zang0\Desktop\my-site\tiktok_history.json"
HISTORY_JS   = r"C:\Users\zang0\Desktop\my-site\tiktok_history.js"
SITE_DIR     = r"C:\Users\zang0\Desktop\my-site"

BRANDS = {
    "outcoma":  {
        "adv_id":   "7556508952121393153",
        "label":    "아웃코마",
        "camp_pat": r'(tk_do|TK_DO|do_|spc_)',
    },
    "ridermune": {
        "adv_id":   "7369127741796630529",
        "label":    "리더뮨",
        "camp_pat": r'tk_(kd|tp|yb|np|ato)_',
    },
}

# ── 파서 ──────────────────────────────────────────────
def parse_krw(s):
    s = str(s).strip()
    if not s or s == '-': return 0
    return int(re.sub(r'[^0-9]', '', s) or 0)

def parse_float(s):
    s = str(s).strip()
    if not s or s == '-': return 0.0
    try: return float(re.sub(r'[^0-9\.]', '', s))
    except: return 0.0

def parse_pct(s):
    s = str(s).strip()
    if not s or s == '-': return 0.0
    try: return float(s.replace('%',''))
    except: return 0.0

def parse_num(s):
    s = str(s).strip()
    if not s or s == '-': return 0
    try: return int(re.sub(r'[^0-9]', '', s))
    except: return 0

# ── UI 헬퍼 ───────────────────────────────────────────
def apply_custom_columns(page):
    try:
        page.wait_for_timeout(2000)
        page.locator('text="Custom Columns"').first.click()
        page.wait_for_timeout(1000)
        page.locator('text="장동훈"').first.click()
        page.wait_for_timeout(2500)
        return True
    except:
        return False

# ── 스크래퍼 ──────────────────────────────────────────
def scrape_campaigns(page, camp_pat):
    page.wait_for_timeout(5000)
    for _ in range(14):
        page.evaluate("window.scrollBy(0, 450)")
        page.wait_for_timeout(300)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(1500)

    text  = page.evaluate("document.body.innerText")
    lines = [l.strip() for l in text.split('\n') if l.strip()]

    campaigns = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not re.search(camp_pat, line, re.IGNORECASE):
            i += 1; continue
        try:
            name       = line
            status_raw = lines[i+1] if i+1 < len(lines) else ''
            if status_raw not in ('Active', 'Paused', 'Deleted', 'Not delivering'):
                i += 1; continue

            offset = 2
            # Budget 라인(숫자 포함)이 나올 때까지 서브상태 줄 건너뜀
            # 예: "Campaign paused", "Learning phase", "Not delivering" 등
            while i+offset < len(lines) and offset < 6:
                nxt = lines[i+offset]
                if re.search(r'[\d,]+', nxt) or 'unlimited' in nxt.lower():
                    break
                offset += 1

            def gl(n):
                idx = i + offset + n
                return lines[idx] if idx < len(lines) else '0'

            camp = {
                'name':        name,
                'status':      'active' if status_raw == 'Active' else 'paused',
                'budget':      parse_krw(gl(0)),
                'cpa':         parse_krw(gl(2)),
                'spend':       parse_krw(gl(3)),
                'revenue':     parse_krw(gl(4)),
                'roas':        parse_float(gl(5)),
                'cpc':         parse_krw(gl(6)),
                'ctr':         parse_pct(gl(7)),
                'clicks':      parse_num(gl(11)),
                'impressions': parse_num(gl(12)),
                'cpm':         parse_krw(gl(13)),
                'conversions': parse_num(gl(14)),
            }
            campaigns.append(camp)
            i += offset + 18
            continue
        except:
            pass
        i += 1

    # 중복 제거: 같은 캠페인명 → active 상태 우선, 수치 합산
    seen = {}
    for c in campaigns:
        k = c['name']
        if k not in seen:
            seen[k] = dict(c)
        else:
            m = seen[k]
            if c['status'] == 'active':
                m['status'] = 'active'
            for f in ('spend', 'revenue', 'conversions', 'clicks', 'impressions', 'cpa', 'cpc'):
                m[f] = m[f] + c[f] if m[f] == 0 else m[f]
            m['roas'] = round(m['revenue'] / m['spend'], 2) if m['spend'] else 0
    return list(seen.values())

def _parse_ads_lines(lines):
    """innerText lines → 소재 리스트 파싱"""
    ads = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not re.match(r'^\d{3}_\d{6}_', line):
            i += 1; continue
        try:
            name       = line
            status_raw = lines[i+1] if i+1 < len(lines) else ''
            if status_raw not in ('Active', 'Paused', 'Deleted', 'Not delivering'):
                i += 1; continue

            camp_name   = ''
            camp_offset = -1
            for k in range(2, 10):
                idx = i + k
                if idx >= len(lines): break
                if re.search(r'tk_', lines[idx], re.IGNORECASE):
                    camp_name   = lines[idx]
                    camp_offset = k
                    break

            if camp_offset < 0:
                i += 1; continue

            def gv(n):
                idx = i + camp_offset + 1 + n
                return lines[idx] if idx < len(lines) else '0'

            ad = {
                'name':        name,
                'campaign':    camp_name,
                'status':      'active' if status_raw == 'Active' else 'paused',
                'cpa':         parse_krw(gv(0)),
                'spend':       parse_krw(gv(1)),
                'revenue':     parse_krw(gv(2)),
                'roas':        parse_float(gv(3)),
                'cpc':         parse_krw(gv(4)),
                'ctr':         parse_pct(gv(5)),
                'clicks':      parse_num(gv(9)),
                'impressions': parse_num(gv(10)),
            }
            ads.append(ad)
            i += camp_offset + 12
            continue
        except:
            pass
        i += 1
    return ads

def _sort_by_cost_desc(page):
    """소재 탭 Cost 열 내림차순 정렬.
    틱톡 헤더는 Shadow DOM 안 SPAN으로 렌더링 → bounding box로 위치 판별.
    첫 클릭=오름차순, 두 번째 클릭=내림차순.
    """
    try:
        page.wait_for_timeout(500)
        els = page.get_by_text("Cost", exact=True)
        n = els.count()
        for i in range(n):
            el = els.nth(i)
            bb = el.bounding_box()
            # 테이블 헤더 행 범위: y 30~300, 너비 100 미만
            if bb and 30 < bb['y'] < 300 and bb['width'] < 150:
                el.click(); page.wait_for_timeout(1000)
                el.click(); page.wait_for_timeout(1500)  # 두 번 → 내림차순
                print(f"  Cost 헤더 클릭 완료 (y={bb['y']:.0f})", flush=True)
                return True
        print("  [경고] Cost 헤더 못찾음 — 정렬 없이 진행", flush=True)
        return False
    except Exception as e:
        print(f"  Cost 정렬 실패(무시): {e}", flush=True)
        return False

def _go_next_page(page):
    """페이지네이션 다음 페이지 버튼 클릭.
    틱톡 페이지네이션도 Shadow DOM이므로 여러 방법 시도.
    """
    try:
        # 방법 1: aria-label
        for sel in ['[aria-label="Next page"]', '[aria-label="next"]',
                    'button[title="Next page"]', 'button[title="Next"]']:
            btn = page.locator(sel).first
            if btn.count() > 0 and btn.is_enabled():
                btn.click(); page.wait_for_timeout(2500)
                return True
        # 방법 2: 텍스트 기반 (>, ›, Next)
        for txt_val in ('>', '›', '»'):
            btn = page.get_by_text(txt_val, exact=True).last
            if btn.count() > 0:
                bb = btn.bounding_box()
                if bb and bb['y'] > 400:  # 페이지 하단 페이지네이션
                    btn.click(); page.wait_for_timeout(2500)
                    return True
        # 방법 3: JS로 페이지네이션 버튼 찾기
        clicked = page.evaluate("""
        () => {
            const btns = [...document.querySelectorAll('button, [role="button"]')];
            const next = btns.find(b => {
                const txt = b.textContent.trim();
                const rect = b.getBoundingClientRect();
                return (txt === '>' || txt === '›') && rect.y > 400 && !b.disabled;
            });
            if (next) { next.click(); return true; }
            return false;
        }
        """)
        if clicked:
            page.wait_for_timeout(2500)
            return True
    except Exception as e:
        print(f"  다음 페이지 실패(무시): {e}", flush=True)
    return False

def _read_page_ads(page):
    """현재 페이지 스크롤 + 파싱"""
    for _ in range(30):
        page.evaluate("window.scrollBy(0, 400)")
        page.wait_for_timeout(200)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(1000)
    text  = page.evaluate("document.body.innerText")
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    return _parse_ads_lines(lines)

def scrape_ads(page):
    page.wait_for_timeout(3000)
    _sort_by_cost_desc(page)

    all_ads = []
    page_num = 1

    while page_num <= 10:  # 안전 상한
        page_ads = _read_page_ads(page)

        if not page_ads:
            break

        all_ads.extend(page_ads)
        min_spend = min(a['spend'] for a in page_ads)

        if min_spend == 0:
            break  # 0원 소재 등장 → 이후는 전부 0원, 수집 종료

        # 현재 페이지 소재가 전부 spend > 0 → 다음 페이지에도 있을 수 있음
        print(f"  [알림] 소재 p{page_num} 최소소진={min_spend:,}원 — 다음 페이지 수집 시도", flush=True)
        if not _go_next_page(page):
            break
        page_num += 1

    return all_ads

# ── 수집 ──────────────────────────────────────────────
def collect_day(page, target_date_str, brand="outcoma"):
    """단일 날짜·브랜드 하루치 수집"""
    cfg   = BRANDS[brand]
    label = cfg["label"]
    url   = (f"https://ads.tiktok.com/i18n/manage/campaign"
             f"?aadvid={cfg['adv_id']}&st={target_date_str}&et={target_date_str}")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
    except Exception as e:
        print(f"  [{label}/{target_date_str}] 이동 오류(무시): {e}", flush=True)
    page.wait_for_timeout(3000)

    apply_custom_columns(page)
    campaigns = scrape_campaigns(page, cfg["camp_pat"])

    active        = [c for c in campaigns if c['status'] == 'active']
    total_spend   = sum(c['spend']   for c in campaigns)
    total_revenue = sum(c['revenue'] for c in campaigns)
    total_roas    = round(total_revenue / total_spend, 2) if total_spend else 0

    print(f"  [{label}/{target_date_str}] 캠페인 {len(campaigns)}개(활성:{len(active)}) | 소진:{total_spend:,} | 매출:{total_revenue:,} | ROAS:{total_roas}", flush=True)

    # Ad 탭 전환 (Alt+3) → 소재 수집
    ads = []
    try:
        page.keyboard.press("Alt+3")
        page.wait_for_timeout(2500)
        apply_custom_columns(page)  # 소재 탭에서도 컬럼 프리셋 재적용
        ads = scrape_ads(page)
        print(f"  [{label}/{target_date_str}] 소재 {len(ads)}개", flush=True)
    except Exception as e:
        print(f"  [{label}/{target_date_str}] 소재 수집 실패(무시): {e}", flush=True)

    return {
        "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "summary":   {"spend": total_spend, "revenue": total_revenue, "roas": total_roas},
        "campaigns": campaigns,
        "ads":       ads,
    }

# ── 히스토리 ──────────────────────────────────────────
def load_history():
    if not os.path.exists(HISTORY_JSON):
        return {}
    with open(HISTORY_JSON, "r", encoding="utf-8") as f:
        h = json.load(f)
    # 구형식 마이그레이션: h[date] 직접 scraped_at → h[date]["outcoma"]
    for date_key, v in list(h.items()):
        if isinstance(v, dict) and "scraped_at" in v:
            h[date_key] = {"outcoma": v}
    return h

MAX_DAYS = 180

def save_history(history):
    # 180일 초과 시 오래된 날짜부터 삭제
    dates = sorted(history.keys())
    while len(dates) > MAX_DAYS:
        del history[dates.pop(0)]

    with open(HISTORY_JSON, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    with open(HISTORY_JS, "w", encoding="utf-8") as f:
        f.write("window.TIKTOK_HISTORY = ")
        json.dump(history, f, ensure_ascii=False, indent=2)
        f.write(";\n")
    print(f"  저장 완료 ({len(history)}개 날짜)", flush=True)

def git_push():
    try:
        subprocess.run(["git", "-C", SITE_DIR, "add", "tiktok_history.js"], capture_output=True, timeout=30)
        subprocess.run(["git", "-C", SITE_DIR, "commit", "-m", f"tiktok auto {datetime.now().strftime('%Y-%m-%d %H:%M')}"], capture_output=True, timeout=30)
        r = subprocess.run(["git", "-C", SITE_DIR, "push"], capture_output=True, text=True, timeout=60)
        print(f"  git push: {'OK' if r.returncode == 0 else r.stderr[:80]}", flush=True)
    except Exception as e:
        print(f"  git push 실패: {e}", flush=True)

CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CHROME_DIR = r"C:\Temp\chrome_tt2"

def _kill_debug_chrome():
    """포트 9222로 뜬 Chrome 프로세스 강제 종료"""
    import wmi
    try:
        c = wmi.WMI()
        for p in c.Win32_Process(name="chrome.exe"):
            if p.CommandLine and ("9222" in p.CommandLine or "chrome_tt2" in p.CommandLine):
                p.Terminate()
    except Exception:
        subprocess.run(
            ["powershell", "-Command",
             "Get-WmiObject Win32_Process | Where-Object { $_.CommandLine -match '9222|chrome_tt2' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -EA SilentlyContinue }"],
            capture_output=True, timeout=10
        )

def _start_chrome():
    """Chrome 디버그 모드로 실행"""
    import time
    subprocess.Popen([CHROME_EXE, "--remote-debugging-port=9222",
                      f"--user-data-dir={CHROME_DIR}",
                      "--no-first-run", "--no-default-browser-check"])
    time.sleep(5)

def ensure_chrome():
    """포트 + HTTP /json 응답까지 확인, 실패 시 Chrome 재시작"""
    import socket, time, urllib.request
    # 1단계: 소켓 체크
    s = socket.socket()
    try:
        s.connect(("localhost", 9222)); s.close()
    except:
        s.close()
        print("  Chrome 없음 → 실행", flush=True)
        _start_chrome()
        return
    # 2단계: HTTP /json 응답 체크 (CDP 통신 가능 여부)
    try:
        urllib.request.urlopen("http://localhost:9222/json", timeout=5)
        return  # 정상
    except Exception as e:
        print(f"  Chrome /json 응답 없음({e}) → 재시작", flush=True)
    _kill_debug_chrome()
    time.sleep(2)
    _start_chrome()

def run(dates_to_collect, brands_to_collect=None, skip_push=False):
    if brands_to_collect is None:
        brands_to_collect = list(BRANDS.keys())
    history = load_history()
    ensure_chrome()

    with sync_playwright() as p:
        import time
        def _connect():
            return p.chromium.connect_over_cdp("http://localhost:9222", timeout=30000)
        try:
            browser = _connect()
        except Exception as e:
            print(f"  CDP 연결 실패({e}) → Chrome 재시작 후 재시도", flush=True)
            _kill_debug_chrome()
            time.sleep(2)
            _start_chrome()
            browser = _connect()
        ctx     = browser.contexts[0]
        page    = next((pg for pg in ctx.pages if "ads.tiktok.com" in pg.url), ctx.pages[0])

        for brand in brands_to_collect:
            for d in dates_to_collect:
                d_str = d.isoformat() if hasattr(d, 'isoformat') else d
                data  = collect_day(page, d_str, brand)
                if d_str not in history:
                    history[d_str] = {}
                history[d_str][brand] = data

        browser.close()

    save_history(history)
    if not skip_push:
        git_push()

if __name__ == "__main__":
    args       = sys.argv[1:]
    skip_push  = "--no-push" in args
    args       = [a for a in args if not a.startswith("--")]

    # --brand outcoma / --brand ridermune 지원
    brand_flag = None
    for a in sys.argv[1:]:
        if a.startswith("--brand="):
            brand_flag = a.split("=", 1)[1]
        elif a == "--brand":
            idx = sys.argv.index("--brand")
            if idx + 1 < len(sys.argv):
                brand_flag = sys.argv[idx + 1]
    brands = [brand_flag] if brand_flag and brand_flag in BRANDS else None

    today     = date.today()
    yesterday = today - timedelta(days=1)

    if len(args) == 0:
        dates = [yesterday, today]
        print(f"=== TikTok 일별 수집 {datetime.now().strftime('%Y-%m-%d %H:%M')} ===", flush=True)
    elif len(args) == 1:
        dates = [args[0]]
        print(f"=== TikTok 수집 {args[0]} ===", flush=True)
    elif len(args) == 2:
        from datetime import datetime as dt
        st = dt.strptime(args[0], "%Y-%m-%d").date()
        et = dt.strptime(args[1], "%Y-%m-%d").date()
        dates = []
        d = st
        while d <= et:
            dates.append(d)
            d += timedelta(days=1)
        print(f"=== TikTok 백필 {args[0]}~{args[1]} ({len(dates)}일) ===", flush=True)
    else:
        dates = [yesterday, today]

    run(dates, brands_to_collect=brands, skip_push=skip_push)
    print("완료", flush=True)
