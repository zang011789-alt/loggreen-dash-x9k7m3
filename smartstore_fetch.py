# -*- coding: utf-8 -*-
"""스마트스토어 판매 통계 수집 (Chrome CDP + API 인터셉트)
핵심 API 1: /api/v1/sellers/dashboards/account/sales-summary-full-chart
           → 일별 결제금액(payAmount) + 주문건수(orderCount) 최근 14일 반환
핵심 API 2: /biz_iframe/api/v3/sites/{siteId}/report?useIndex=sales-product-unit
           → 상품별 매출 (어제 확정 데이터, 쿠팡과 동일 패턴)

사전 조건:
  - Chrome 포트 9224 실행 (user-data-dir=C:\Temp\chrome_ss)
  - Chrome에서 sell.smartstore.naver.com 수동 로그인 (최초 1회)
실행: python smartstore_fetch.py [--no-push] [--brand BRAND_KEY]
브랜드 키: leadermune / ridermune / nifgen / dermacollection
"""
import sys, json, os, time, socket, subprocess, logging
from datetime import date, datetime, timedelta
from playwright.sync_api import sync_playwright

if sys.stdout is None:  # pythonw.exe(스케줄러)는 콘솔 없어 stdout=None → reconfigure/StreamHandler 시 즉사(0x1)
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
else:
    sys.stdout.reconfigure(encoding="utf-8")

SCRIPT_DIR = r"C:\Users\zang0\Desktop\my-site"
JSON_PATH  = os.path.join(SCRIPT_DIR, "smartstore_history.json")
JS_PATH    = os.path.join(SCRIPT_DIR, "smartstore_history.js")
LOG_FILE   = os.path.join(SCRIPT_DIR, "smartstore_auto_log.txt")
CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
USER_DATA  = r"C:\Temp\chrome_ss"
DEBUG_PORT = 9224
MAX_DAYS   = 180

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger(__name__)

SS_BASE   = "https://sell.smartstore.naver.com"
CHART_API = "/api/v1/sellers/dashboards/account/sales-summary-full-chart"

# ── 브랜드 설정 ─────────────────────────────────────────────────
# channelNo: /api/login/channels 응답으로 확인 (2026-05-26)
# site_id: /biz_iframe/api/v1/bizUnits 응답으로 확인 (2026-05-26)
BRANDS = {
    "leadermune": {
        "label":       "리더뮨(leadermune)",
        "channel_no":  102637557,
        "store_label": "leadermune",
        "site_id":     "s_17bf7d7541e97",
    },
    "ridermune": {
        "label":       "리더뮨",
        "channel_no":  101898573,
        "store_label": "리더뮨",
        "site_id":     "s_1a4349a454b5f",
    },
    "nifgen": {
        "label":       "니프젠",
        "channel_no":  102373930,
        "store_label": "니프젠",
        "site_id":     "s_312be5108d795",
    },
    "dermacollection": {
        "label":       "더마콜렉션",
        "channel_no":  102790771,
        "store_label": "더마콜렉션",
        "site_id":     "s_15556952b2d8e",
    },
}

# ── Chrome 관리 ────────────────────────────────────────────────
def _port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2): return True
    except: return False

def _cdp_ok(port):
    try:
        import urllib.request
        r = urllib.request.urlopen(f"http://localhost:{port}/json/version", timeout=3)
        return r.status == 200
    except: return False

def _kill_chrome_ss():
    try:
        subprocess.run(
            ["powershell", "-Command",
             "Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -like '*chrome_ss*'} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
            capture_output=True, timeout=10
        )
    except: pass

def _start_chrome_ss():
    subprocess.Popen([
        CHROME_EXE, f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={USER_DATA}", "--no-first-run", "--no-default-browser-check",
        "--start-minimized", "--window-position=-9999,-9999",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(5)

def ensure_chrome():
    if _port_open(DEBUG_PORT) and _cdp_ok(DEBUG_PORT):
        return
    log.info(f"Chrome(포트 {DEBUG_PORT}) 재시작 중...")
    _kill_chrome_ss()
    time.sleep(2)
    _start_chrome_ss()
    for _ in range(20):
        if _cdp_ok(DEBUG_PORT): return
        time.sleep(1)
    raise RuntimeError(f"Chrome 포트 {DEBUG_PORT} 기동 실패 — C:\\Temp\\chrome_ss에서 수동 로그인 필요")

# ── 스토어 전환 ────────────────────────────────────────────────
def switch_store(page, channel_no, store_label):
    """헤더 팝업 → 스토어 선택으로 채널 전환

    팝업 구조: ul.seller-list-border 안 li[ng-repeat] 엘리먼트
    클릭 방식: 좌표 기반 mouse.click (Angular ng-change 트리거)
    """
    log.info(f"  스토어 전환: {store_label} (channelNo={channel_no})")

    # 메인 페이지 확인
    if "/api/" in page.url or "channel-select" in page.url:
        page.goto(f"{SS_BASE}/", wait_until="domcontentloaded")
        page.wait_for_timeout(5000)

    # 헤더 스토어 이동 버튼 열기
    try:
        btn = page.locator('[data-action-location-id="selectStore"]')
        btn.wait_for(state="visible", timeout=10000)
        btn.dispatch_event("click")
        page.wait_for_timeout(2500)
    except Exception as e:
        log.warning(f"  팝업 열기 실패: {e}")
        return False

    # 팝업 내 target 스토어 LI의 bounding box 획득
    rect = page.evaluate(f"""
        () => {{
            const target = '{store_label}';
            const lis = [...document.querySelectorAll('li[ng-repeat]')];
            for (const li of lis) {{
                if (li.innerText.includes(target)) {{
                    const r = li.getBoundingClientRect();
                    return {{ x: r.x, y: r.y, w: r.width, h: r.height }};
                }}
            }}
            const wraps = [...document.querySelectorAll('.select-area-wrap')];
            for (const wrap of wraps) {{
                if (wrap.innerText.includes(target)) {{
                    const r = wrap.getBoundingClientRect();
                    return {{ x: r.x, y: r.y, w: r.width, h: r.height, fallback: true }};
                }}
            }}
            return null;
        }}
    """)

    if rect and rect.get("y", -1) > 0:
        cx = rect["x"] + rect["w"] / 2
        cy = rect["y"] + rect["h"] / 2
        log.info(f"  LI bounding box: y={rect['y']:.0f}, 클릭 ({cx:.0f}, {cy:.0f})")
        page.mouse.click(cx, cy)
    else:
        log.warning(f"  '{store_label}' LI를 팝업에서 찾지 못함 (rect={rect})")
        return False

    page.wait_for_timeout(5000)
    log.info(f"  전환 후 URL: {page.url}")
    return True

# ── 차트 API 응답 파싱 ─────────────────────────────────────────
def parse_chart(body):
    """sales-summary-full-chart 응답에서 일별 데이터 추출"""
    result = {}
    try:
        pay_items   = body["payAmount"]["dailyChartData"]["items"]
        order_items = body["orderCount"]["dailyChartData"]["items"]
        avg_items   = body.get("orderAverageAmount", {}).get("dailyChartData", {}).get("items", [])

        pay_map   = {item["label"]: int(item["value"]) for item in pay_items}
        order_map = {item["label"]: int(item["value"]) for item in order_items}
        avg_map   = {item["label"]: int(item["value"]) for item in avg_items}

        for d in pay_map:
            result[d] = {
                "revenue":   pay_map.get(d, 0),
                "orders":    order_map.get(d, 0),
                "avg_order": avg_map.get(d, 0),
            }
    except (KeyError, TypeError) as e:
        log.warning(f"차트 파싱 오류: {e}")
    return result

# ── 브랜드별 차트 수집 ──────────────────────────────────────────
def collect_brand(page, brand_key):
    cfg = BRANDS[brand_key]
    log.info(f"[{brand_key}] {cfg['label']} 차트 수집 시작")
    chart_data = {}

    def on_response(resp):
        if CHART_API not in resp.url:
            return
        ct = resp.headers.get("content-type", "")
        if "json" not in ct:
            return
        try:
            body = resp.json()
            parsed = parse_chart(body)
            chart_data.update(parsed)
            log.info(f"  [{brand_key}] 차트 API 수신 ({len(parsed)}일치)")
        except Exception as e:
            log.warning(f"  [{brand_key}] 응답 파싱 실패: {e}")

    # 스토어 전환 먼저 → 전환 완료 후 listener 설정 (이전 스토어 데이터 혼입 방지)
    switch_store(page, cfg["channel_no"], cfg["store_label"])

    page.on("response", on_response)

    # 전환 후 대시보드 재로드 → 현재 스토어 chart API 호출
    page.goto(f"{SS_BASE}/", wait_until="domcontentloaded")
    page.wait_for_timeout(8000)

    # 데이터가 없으면 isRefresh 파라미터로 재시도
    if not chart_data:
        page.goto(f"{SS_BASE}/#/home?isRefresh=true", wait_until="domcontentloaded")
        page.wait_for_timeout(6000)

    page.remove_listener("response", on_response)
    return chart_data

# ── biz_iframe 인증 토큰 취득 ──────────────────────────────────
def get_biz_auth_token(page):
    """판매분석 페이지 방문 → Authorization Bearer 토큰 캡처"""
    auth_token = []

    def on_req(req):
        if "biz_iframe/api/v3" in req.url and not auth_token:
            h = req.headers
            if "authorization" in h:
                auth_token.append(h["authorization"])

    page.on("request", on_req)
    page.goto(f"{SS_BASE}/#/bizadvisor/sales", wait_until="domcontentloaded")
    page.wait_for_timeout(8000)
    page.remove_listener("request", on_req)

    if auth_token:
        log.info(f"  biz_iframe 토큰 취득 완료")
        return auth_token[0]
    else:
        log.warning(f"  biz_iframe 토큰 취득 실패")
        return None

# ── 상품별 매출 수집 (biz_iframe API) ──────────────────────────
def collect_products_biz(page, auth_token, brand_keys):
    """sales-product-unit API로 스토어별 상품 매출 수집
    스토어 전환 불필요 - site_id로 직접 호출
    어제 확정 데이터 수집 (쿠팡/카페24와 동일 패턴)
    """
    if not auth_token:
        log.warning("  biz_iframe 토큰 없음 → 상품별 수집 스킵")
        return {}

    today = date.today()
    results = {}

    for brand_key in brand_keys:
        cfg = BRANDS[brand_key]
        site_id = cfg["site_id"]
        log.info(f"  [{brand_key}] 상품별 수집 (site_id={site_id})")

        brand_products = {}

        # 오늘부터 최대 3일 전까지 데이터 있는 날짜 탐색
        for delta in range(4):
            target_date = (today - timedelta(days=delta)).strftime("%Y-%m-%d")
            url = (
                f"{SS_BASE}/biz_iframe/api/v3/sites/{site_id}/report"
                f"?useIndex=sales-product-unit&startDate={target_date}&endDate={target_date}"
                f"&dimensions=product_name"
                f"&metrics=pay_amount&metrics=num_purchase&metrics=product_quantity"
                f"&size=100&sort=pay_amount&order=desc"
                f"&service=biz_advisor"
            )
            try:
                result = page.evaluate(f"""
                    async () => {{
                        const r = await fetch('{url}', {{
                            credentials: 'include',
                            headers: {{
                                'Accept': 'application/json',
                                'Authorization': '{auth_token}'
                            }}
                        }});
                        if (!r.ok) return null;
                        return await r.json();
                    }}
                """)
                if result and isinstance(result, list) and len(result) > 0:
                    # 실제 상품명 있는 항목만 (product_name != '-' and pay_amount > 0)
                    products = [
                        {
                            "name":    item.get("product_name", ""),
                            "revenue": int(item.get("pay_amount", 0)),
                            "orders":  int(item.get("num_purchase", 0)),
                        }
                        for item in result
                        if item.get("pay_amount", 0) > 0 and item.get("product_name", "-") != "-"
                    ]
                    if products:
                        brand_products[target_date] = products
                        log.info(f"    [{brand_key}] {target_date}: {len(products)}개 상품 수집")
                        break  # 데이터 있는 가장 최근 날짜만 사용
            except Exception as e:
                log.warning(f"    [{brand_key}] {target_date} 오류: {e}")

        results[brand_key] = brand_products

    return results

# ── 오늘 실시간 수집 (dailyReport API) ────────────────────────
def collect_today_daily(page, auth_token, brand_keys):
    """dailyReport API로 오늘 실시간 상품별 매출 수집
    API: /biz_iframe/api/v3/sites/{siteId}/dailyReport?targetDateTime={현재시각}
         &dimensions=product_name&metrics=pay_amount&useIndex=sales-product-unit
    스토어 전환 불필요 - site_id로 직접 호출 (confirm 데이터 아님, 실시간)
    """
    if not auth_token:
        log.warning("  biz_iframe 토큰 없음 → 오늘 실시간 수집 스킵")
        return {}

    today_str = date.today().strftime("%Y-%m-%d")
    now_str   = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    results   = {}

    for brand_key in brand_keys:
        cfg     = BRANDS[brand_key]
        site_id = cfg["site_id"]
        log.info(f"  [{brand_key}] 오늘 실시간 수집 (targetDateTime={now_str})")

        try:
            # 상품별 매출
            prod_url = (
                f"{SS_BASE}/biz_iframe/api/v3/sites/{site_id}/dailyReport"
                f"?targetDateTime={now_str}"
                f"&dimensions=product_name&metrics=pay_amount"
                f"&useIndex=sales-product-unit&service=biz_advisor"
            )
            auth_hdr = auth_token
            prod_result = page.evaluate(f"""
                async () => {{
                    const r = await fetch('{prod_url}', {{
                        credentials: 'include',
                        headers: {{
                            'Accept': 'application/json',
                            'Authorization': '{auth_hdr}'
                        }}
                    }});
                    if (!r.ok) return null;
                    return await r.json();
                }}
            """)

            # 총합 매출
            total_url = (
                f"{SS_BASE}/biz_iframe/api/v3/sites/{site_id}/dailyReport"
                f"?targetDateTime={now_str}"
                f"&metrics=pay_amount&useIndex=sales-hourly&service=biz_advisor"
            )
            total_result = page.evaluate(f"""
                async () => {{
                    const r = await fetch('{total_url}', {{
                        credentials: 'include',
                        headers: {{
                            'Accept': 'application/json',
                            'Authorization': '{auth_hdr}'
                        }}
                    }});
                    if (!r.ok) return null;
                    return await r.json();
                }}
            """)

            total_rev = 0
            if total_result and isinstance(total_result, list) and len(total_result) > 0:
                total_rev = int(total_result[0].get("pay_amount", 0))

            products = []
            if prod_result and isinstance(prod_result, list):
                products = [
                    {
                        "name":    item.get("product_name", ""),
                        "revenue": int(item.get("pay_amount", 0)),
                        "orders":  0,
                    }
                    for item in prod_result
                    if item.get("pay_amount", 0) > 0
                ]

            log.info(f"    [{brand_key}] 오늘 총합: {total_rev:,}원, 상품: {len(products)}개")
            results[brand_key] = {
                today_str: {
                    "summary":  {"revenue": total_rev, "orders": 0, "avg_order": 0},
                    "products": products,
                    "is_today": True,
                }
            }

        except Exception as e:
            log.warning(f"    [{brand_key}] 오늘 데이터 오류: {e}")

    return results

# ── 저장 ─────────────────────────────────────────────────────
def notify(title, message):
    """윈도우 토스트 알림. 실패해도 수집은 계속. (참고: memory feedback_collect_failure_alert)"""
    ps = (
        '[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null;'
        '$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);'
        '$x=$t.GetElementsByTagName("text");'
        f'$x.Item(0).AppendChild($t.CreateTextNode("{title}")) > $null;'
        f'$x.Item(1).AppendChild($t.CreateTextNode("{message}")) > $null;'
        '$n=[Windows.UI.Notifications.ToastNotification]::new($t);'
        '[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("로그린수집").Show($n);'
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], timeout=15,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def ss_ensure_login(page):
    """스마트스토어 로그인 상태 확인 → 풀렸으면 키스트로크로 무인 재로그인 시도.
    자격증명이 브라우저에 저장돼 있으면 Enter만으로 제출됨.
    참고: memory feedback_login_automation / reference_login_urls"""
    page.goto(f"{SS_BASE}/#/home/dashboard", wait_until="domcontentloaded")
    page.wait_for_timeout(5000)
    if "accounts.commerce.naver.com" not in page.url and "/login" not in page.url:
        return True  # 이미 로그인 상태
    log.warning("스마트스토어 로그아웃 감지 — 키스트로크 자동 로그인 시도")
    try:
        pw = page.locator('input[type="password"]')
        if pw.count() > 0:
            pw.first.click()            # 비밀번호 칸 포커스
        page.keyboard.press("Enter")    # 저장 자격증명으로 폼 제출 (사람처럼)
        page.wait_for_timeout(7000)
    except Exception as e:
        log.warning(f"  자동 로그인 키 입력 실패: {e}")
    # 재확인
    page.goto(f"{SS_BASE}/#/home/dashboard", wait_until="domcontentloaded")
    page.wait_for_timeout(5000)
    ok = "accounts.commerce.naver.com" not in page.url and "/login" not in page.url
    if ok:
        log.info("스마트스토어 자동 로그인 성공 — 수집 계속")
    else:
        log.warning("스마트스토어 자동 로그인 실패(문자인증 등 추정) — 9224 크롬에서 수동 로그인 필요")
    return ok


def save_history(brand_key, date_map, products_by_date=None, is_today_dates=None):
    if os.path.exists(JSON_PATH):
        with open(JSON_PATH, "r", encoding="utf-8") as f:
            history = json.load(f)
    else:
        history = {}

    scraped_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    for date_str, metrics in date_map.items():
        if date_str not in history:
            history[date_str] = {}
        if brand_key not in history[date_str]:
            history[date_str][brand_key] = {}

        history[date_str][brand_key]["scraped_at"] = scraped_at
        history[date_str][brand_key]["summary"]    = metrics
        history[date_str][brand_key]["is_today"]   = date_str in (is_today_dates or set())

        # 상품별 데이터: 해당 날짜에 수집된 게 있으면 업데이트
        if products_by_date and date_str in products_by_date:
            history[date_str][brand_key]["products"] = products_by_date[date_str]
        elif "products" not in history[date_str][brand_key]:
            history[date_str][brand_key]["products"] = []

    all_dates = sorted(history.keys())
    if len(all_dates) > MAX_DAYS:
        for d in all_dates[:-MAX_DAYS]:
            del history[d]

    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    with open(JS_PATH, "w", encoding="utf-8") as f:
        f.write(f"// auto-generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("window.SS_HISTORY = ")
        json.dump(history, f, ensure_ascii=False)
        f.write(";\n")

    log.info(f"  저장 완료 ({len(date_map)}일) → {JS_PATH}")

def git_push():
    log.info("git push 생략 (push_all.py에서 통합 처리)")

# ── 데이터 마이그레이션 (구버전 → 신버전) ─────────────────────
def migrate_history():
    """
    이전 버전에서 'ridermune' 키로 leadermune(channelNo=102637557) 데이터를
    저장했던 것을 'leadermune' 키로 이전.
    """
    if not os.path.exists(JSON_PATH):
        return
    with open(JSON_PATH, "r", encoding="utf-8") as f:
        history = json.load(f)

    migrated_count = 0
    for date_str, day_data in history.items():
        if "ridermune" in day_data and "leadermune" not in day_data:
            day_data["leadermune"] = day_data.pop("ridermune")
            migrated_count += 1

    if migrated_count > 0:
        log.info(f"마이그레이션: ridermune → leadermune ({migrated_count}일치 변환)")
        with open(JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

# ── 메인 ─────────────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--brand",   default=None, choices=list(BRANDS.keys()))
    args = parser.parse_args()

    migrate_history()
    ensure_chrome()
    brand_keys = [args.brand] if args.brand else list(BRANDS.keys())

    with sync_playwright() as p:
        # CDP 연결: 포트는 살아도 드라이버가 응답 안 하는 경우가 있어(ws connected 후 timeout)
        # 1회 실패 시 Chrome 재시작 후 재연결 (참고: memory feedback_automation_resilience)
        try:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{DEBUG_PORT}", timeout=15000)
        except Exception:
            log.warning("CDP 연결 실패 → Chrome 재시작 후 재연결")
            _kill_chrome_ss()
            _start_chrome_ss()
            time.sleep(4)
            browser = p.chromium.connect_over_cdp(f"http://localhost:{DEBUG_PORT}", timeout=30000)
        ctx  = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()

        # 0. 로그인 상태 확인 + 키스트로크 자동 로그인 (실패해도 계속 진행)
        ss_ensure_login(page)

        # 1. biz_iframe 토큰 취득 (상품별 수집용)
        log.info("=== biz_iframe 토큰 취득 ===")
        auth_token = get_biz_auth_token(page)

        # 2. 오늘 실시간 수집 (dailyReport API)
        log.info("=== 오늘 실시간 수집 ===")
        today_str   = date.today().strftime("%Y-%m-%d")
        today_daily = collect_today_daily(page, auth_token, brand_keys)
        for bk, bdata in today_daily.items():
            if bdata and today_str in bdata:
                d = bdata[today_str]
                save_history(bk,
                             {today_str: d["summary"]},
                             products_by_date={today_str: d["products"]},
                             is_today_dates={today_str})

        # 3. 어제 확정 상품별 수집 (biz_iframe report API)
        log.info("=== 상품별 매출 수집 ===")
        products_by_brand = collect_products_biz(page, auth_token, brand_keys)

        # 4. 차트 API 수집 (스토어 전환 필요)
        log.info("=== 차트 API 수집 ===")
        page.goto(f"{SS_BASE}/", wait_until="domcontentloaded")
        page.wait_for_timeout(5000)

        ok_brands = []
        for brand_key in brand_keys:
            try:
                date_map = collect_brand(page, brand_key)
                if date_map:
                    save_history(brand_key, date_map,
                                 products_by_date=products_by_brand.get(brand_key, {}))
                    log.info(f"[{brand_key}] 완료: {sorted(date_map.keys())}")
                    ok_brands.append(brand_key)
                else:
                    log.warning(f"[{brand_key}] 데이터 없음 — 스마트스토어 로그인 확인 필요")
            except Exception as e:
                log.error(f"[{brand_key}] 오류: {e}")

        # 수집 실패 브랜드 알림 (로그인 만료/연결 오류 등)
        failed = [b for b in brand_keys if b not in ok_brands]
        if failed:
            notify("⚠️ 스마트스토어 수집 오류", f"누락: {', '.join(failed)} - 9224 크롬 로그인 확인")

        # CDP attach 모드: browser.close() 호출 금지 (실제 Chrome 탭을 닫아 다음 실행을 깨뜨림).
        # with 블록 종료 시 연결만 자동으로 끊긴다.

    if not args.no_push:
        git_push()

if __name__ == "__main__":
    main()
