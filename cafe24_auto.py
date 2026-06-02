# -*- coding: utf-8 -*-
"""
카페24 애널리틱스 자동 수집기
- 매일 09:00 실행 (Windows 작업 스케줄러)
- 리더뮨 + 아웃코마 각각 로그인 -> JWT 토큰 발급 -> 어제 데이터 수집
- 캠페인별 / 소재별 데이터 저장
"""
import sys, io, asyncio, json, aiohttp, logging, subprocess
from datetime import date, timedelta, datetime
from pathlib import Path
from playwright.async_api import async_playwright

# pythonw.exe(스케줄러)는 콘솔이 없어 stdout/stderr가 None → .buffer 접근 시 즉사(0x1). 가드 필수.
if sys.stdout is not None:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
if sys.stderr is not None:
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

OUTPUT_DIR = Path("C:/Users/zang0/Desktop/my-site")
LOG_FILE   = OUTPUT_DIR / "cafe24_auto_log.txt"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger(__name__)

ACCOUNTS = [
    {"name": "ridermune", "mall_id": "garonge",  "pw": "qkfwjswhswnd1@@"},
    {"name": "outcoma",   "mall_id": "outcoma",  "pw": "eldhtmxhrwm1@"},
]

CA_BASE = "https://ca-internal.cafe24data.com"

async def get_token(page, mall_id):
    """카페24 로그인 후 ca-internal JWT 토큰 발급"""
    token_holder = [None]

    async def on_response(res):
        if "/auth/ca-token" in res.url:
            ct = res.headers.get("content-type", "")
            if "json" in ct:
                try:
                    body = await res.json()
                    if isinstance(body, dict) and "token" in body:
                        token_holder[0] = body["token"]
                except:
                    pass

    page.on("response", on_response)

    # 카페24 애널리틱스 페이지 로드 (토큰 자동 발급)
    url = f"https://{mall_id}.cafe24.com/disp/admin/shop1/menu/cafe24analytics"
    await page.goto(url, wait_until="domcontentloaded")
    # 토큰 올 때까지 최대 15초 대기
    for _ in range(30):
        if token_holder[0]:
            break
        await page.wait_for_timeout(500)

    page.remove_listener("response", on_response)
    return token_holder[0]

async def fetch_data(session, token, endpoint, params):
    """ca-internal API 호출"""
    url = CA_BASE + endpoint
    headers = {"Authorization": f"Bearer {token}"}
    async with session.get(url, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        if resp.status != 200:
            log.warning(f"API {resp.status}: {endpoint}")
            return None
        return await resp.json()

async def collect_brand(account, yesterday_str, today_str):
    """브랜드별 데이터 수집 (어제 확정 + 오늘 실시간)"""
    log.info(f"[{account['name']}] 수집 시작 - 어제: {yesterday_str}, 오늘: {today_str}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, timeout=30000)
        ctx  = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await ctx.new_page()

        # 로그인
        await page.goto("https://eclogin.cafe24.com/Shop/", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)
        await page.locator("input[type='text']").first.fill(account["mall_id"])
        await page.locator("input[type='password']").first.fill(account["pw"])
        await page.wait_for_timeout(300)
        await page.click("button.btnStrong")
        await page.wait_for_load_state("networkidle", timeout=20000)
        await page.wait_for_timeout(2000)
        log.info(f"[{account['name']}] 로그인 완료")

        # JWT 토큰 발급
        token = await get_token(page, account["mall_id"])

        # outcoma 전용 실매출 수집 (임시 변수에 저장)
        report_today_data = None
        if account["mall_id"] == "outcoma":
            report_today_data = await scrape_report_today_outcoma(page, account["mall_id"])

        await browser.close()


    if not token:
        log.error(f"[{account['name']}] 토큰 발급 실패")
        return None

    log.info(f"[{account['name']}] 토큰 발급 완료")

    # API 호출
    base_params = {
        "device_type": "total",
        "sort": "order_amount",
        "order": "desc",
        "offset": 0,
        "limit": 200,
        "conversion_timeframe": "2h",
    }
    params_yesterday = {**base_params, "start_date": yesterday_str, "end_date": yesterday_str}
    params_today     = {**base_params, "start_date": today_str,     "end_date": today_str}

    result = {
        "brand": account["name"],
        "mall_id": account["mall_id"],
        "date": yesterday_str,
        "collected_at": today_str,
    }

    async with aiohttp.ClientSession() as session:
        # ── 어제 확정 데이터 ──
        data = await fetch_data(session, token, "/ca2/adsources/campaigns", params_yesterday)
        if data:
            result["campaigns_yesterday"] = data.get("campaigns", [])
            log.info(f"[{account['name']}] campaigns(어제): {len(result['campaigns_yesterday'])}개")

        data = await fetch_data(session, token, "/ca2/adsources/terms", params_yesterday)
        if data:
            result["contents_yesterday"] = data.get("terms", [])
            log.info(f"[{account['name']}] terms(어제): {len(result['contents_yesterday'])}개")

        data = await fetch_data(session, token, "/ca2/adsources/channels", params_yesterday)
        if data:
            result["channels_yesterday"] = data.get("channels", [])

        data = await fetch_data(session, token, "/ca2/sales/highlights", params_yesterday)
        if data:
            result["sales_yesterday"] = data.get("highlights", [])

        prod_params_yesterday = {"device_type": "total", "sort": "order_amount", "order": "desc", "offset": 0, "limit": 100, "start_date": yesterday_str, "end_date": yesterday_str}
        data = await fetch_data(session, token, "/ca2/products/sales", prod_params_yesterday)
        if data:
            result["products_yesterday"] = data.get("sales", [])
            log.info(f"[{account['name']}] products(어제): {len(result['products_yesterday'])}개")

        # ── 오늘 실시간 데이터 ──
        data = await fetch_data(session, token, "/ca2/adsources/campaigns", params_today)
        if data:
            result["campaigns_today"] = data.get("campaigns", [])
            log.info(f"[{account['name']}] campaigns(오늘): {len(result['campaigns_today'])}개")

        data = await fetch_data(session, token, "/ca2/adsources/terms", params_today)
        if data:
            result["contents_today"] = data.get("terms", [])
            log.info(f"[{account['name']}] terms(오늘): {len(result['contents_today'])}개")

        data = await fetch_data(session, token, "/ca2/adsources/channels", params_today)
        if data:
            result["channels_today"] = data.get("channels", [])

        data = await fetch_data(session, token, "/ca2/sales/highlights", params_today)
        if data:
            result["sales_today"] = data.get("highlights", [])

        prod_params_today = {"device_type": "total", "sort": "order_amount", "order": "desc", "offset": 0, "limit": 100, "start_date": today_str, "end_date": today_str}
        data = await fetch_data(session, token, "/ca2/products/sales", prod_params_today)
        if data:
            result["products_today"] = data.get("sales", [])
            log.info(f"[{account['name']}] products(오늘): {len(result['products_today'])}개")

        # outcoma 실매출 결과 주입
        if report_today_data:
            result["report_today"] = report_today_data

    return result

async def scrape_report_today_outcoma(page, mall_id):
    """outcoma 전용 - /report/Today 실매출 스크래핑"""
    import re
    try:
        await page.goto(f"https://{mall_id}.cafe24.com/disp/admin/shop1/report/Today",
                        wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(6000)
        text = await page.inner_text("body")

        # 결제금액
        m = re.search(r'결제금액\s*([\d,]+)', text)
        revenue = int(m.group(1).replace(',', '')) if m else 0

        # 결제건수
        m2 = re.search(r'결제건수\s*([\d,]+)', text)
        order_count = int(m2.group(1).replace(',', '')) if m2 else 0

        # 환급금액
        m3 = re.search(r'환급금액\s*([\d,]+)', text)
        refund = int(m3.group(1).replace(',', '')) if m3 else 0

        log.info(f"[outcoma] report/Today 실매출: {revenue:,}원 / {order_count}건")
        return {"revenue": revenue, "order_count": order_count, "refund": refund}
    except Exception as e:
        log.warning(f"[outcoma] report/Today 스크래핑 실패: {e}")
        return None

HISTORY_FILE = OUTPUT_DIR / "cafe24_history.json"
MAX_DAYS = 180

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


async def main():
    today     = date.today()
    yesterday = today - timedelta(days=1)
    today_str     = today.strftime("%Y-%m-%d")
    yesterday_str = yesterday.strftime("%Y-%m-%d")

    log.info(f"=== 카페24 자동 수집 시작 ({today_str}) ===")

    all_results = {}
    for account in ACCOUNTS:
        # 일시적 로그인/토큰 발급 실패 대비 최대 2회 시도 (한 회차에 한 브랜드라도 안 빠지게)
        for attempt in (1, 2):
            try:
                result = await asyncio.wait_for(
                    collect_brand(account, yesterday_str, today_str),
                    timeout=180.0
                )
                if result:
                    all_results[account["name"]] = result
                    break
                log.warning(f"[{account['name']}] 결과 비어있음 (시도 {attempt}/2)")
            except asyncio.TimeoutError:
                log.error(f"[{account['name']}] 타임아웃 (시도 {attempt}/2)")
            except Exception as e:
                log.error(f"[{account['name']}] 오류 (시도 {attempt}/2): {e}")
            if attempt < 2:
                await asyncio.sleep(3)

    # 누적 히스토리 로드
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, encoding="utf-8") as f:
            history = json.load(f)
    else:
        history = {}

    # 어제 확정 데이터 저장 (성공한 브랜드만 업데이트, 기존 데이터 보존)
    if yesterday_str not in history:
        history[yesterday_str] = {}
    for brand_name, result in all_results.items():
        history[yesterday_str][brand_name] = {
            "campaigns": result.get("campaigns_yesterday", []),
            "contents":  result.get("contents_yesterday", []),
            "channels":  result.get("channels_yesterday", []),
            "sales":     result.get("sales_yesterday", []),
            "products":  result.get("products_yesterday", []),
        }

    # 오늘 실시간 데이터 저장 (성공한 브랜드만 업데이트, 기존 데이터 보존)
    if today_str not in history:
        history[today_str] = {}
    for brand_name, result in all_results.items():
        entry = {
            "campaigns": result.get("campaigns_today", []),
            "contents":  result.get("contents_today", []),
            "channels":  result.get("channels_today", []),
            "sales":     result.get("sales_today", []),
            "products":  result.get("products_today", []),
        }
        if result.get("report_today"):
            entry["report_today"] = result["report_today"]
        history[today_str][brand_name] = entry
    log.info(f"오늘({today_str}) 실시간 데이터 저장 완료")

    # 90일 초과 항목 제거
    cutoff = (today - timedelta(days=MAX_DAYS)).strftime("%Y-%m-%d")
    history = {k: v for k, v in history.items() if k >= cutoff}

    # 히스토리 JSON 저장
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2, default=str)
    log.info(f"히스토리 저장 완료: {HISTORY_FILE} ({len(history)}일치)")

    # 히스토리 JS 저장 (대시보드에서 로드)
    hist_js = OUTPUT_DIR / "cafe24_history.js"
    with open(hist_js, "w", encoding="utf-8") as f:
        f.write("window.CAFE24_HISTORY = ")
        json.dump(history, f, ensure_ascii=False, default=str)
        f.write(";")
    log.info(f"히스토리 JS 저장 완료: {hist_js}")

    # 하위 호환: 기존 cafe24_data.js도 최신 날짜 데이터로 유지
    js_file = OUTPUT_DIR / "cafe24_data.js"
    with open(js_file, "w", encoding="utf-8") as f:
        f.write("window.CAFE24_DATA = ")
        json.dump(all_results, f, ensure_ascii=False, default=str)
        f.write(";")
    log.info("파일 저장 완료")

    # git push는 push_all.py가 통합 처리
    log.info("JS 저장 완료 (push는 push_all.py에서 처리)")

    # 수집 실패(2회 재시도 후도 실패) 브랜드 알림
    expected = {a["name"] for a in ACCOUNTS}
    missing = expected - set(all_results.keys())
    if missing:
        notify("⚠️ 카페24 수집 오류", f"{today_str} 누락: {', '.join(missing)} - 로그인/토큰 확인")

    log.info(f"=== 수집 완료 ===")

asyncio.run(main())
