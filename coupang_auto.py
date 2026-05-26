# -*- coding: utf-8 -*-
"""쿠팡 Wing 판매분석 자동 수집 스크립트"""
import sys, asyncio, json, re, time, subprocess, socket
from pathlib import Path
from datetime import datetime, timedelta, date

sys.stdout.reconfigure(encoding="utf-8")

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
USER_DATA   = r"C:\Temp\chrome_cp"
DEBUG_PORT  = 9225
BASE        = Path(r"C:\Users\zang0\Desktop\my-site")
HISTORY_JSON = BASE / "coupang_history.json"
HISTORY_JS   = BASE / "coupang_history.js"
LOG_FILE     = BASE / "coupang_auto_log.txt"
MAX_DAYS     = 180

SALES_URL = "https://wing.coupang.com/tenants/business-insight/sales-analysis"


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def ensure_chrome():
    """Chrome CDP 포트 확인 및 자동 실행"""
    try:
        s = socket.create_connection(("127.0.0.1", DEBUG_PORT), timeout=2)
        s.close()
        return True
    except:
        pass
    log(f"Chrome 시작 중 (포트 {DEBUG_PORT})...")
    subprocess.Popen([
        CHROME_PATH,
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={USER_DATA}",
        "--no-first-run", "--no-default-browser-check"
    ])
    time.sleep(4)
    return True


def parse_int(s):
    """'1,234' or '1.23만' -> int"""
    s = s.strip().replace(',', '')
    try:
        return int(s)
    except:
        return 0


def parse_revenue(s):
    """'4.03억', '2,345,678', '2345678' -> int"""
    s = s.strip().replace(',', '')
    if '억' in s:
        v = float(s.replace('억', '')) * 100000000
        return int(v)
    if '만' in s:
        v = float(s.replace('만', '')) * 10000
        return int(v)
    try:
        return int(s)
    except:
        return 0


def parse_products(lines):
    """innerText 줄 목록에서 상품별 데이터 파싱"""
    DELIVERY_TYPES = {'판매자 배송', '로켓그로스'}
    SEPARATOR = '검색어, 유입경로 및 상세분석 보기'

    # 1단계: '옵션목록' 이후 구간에서만 상품 찾기
    header_end = 0
    for i, line in enumerate(lines):
        if '옵션목록' in line or '트래픽 및 전환율' in line or '매출 (오늘' in line:
            header_end = i + 1
            break

    # 2단계: header_end 이후에서 첫 번째 상품 시작점
    start = header_end
    for i, line in enumerate(lines[header_end:], header_end):
        if line in DELIVERY_TYPES:
            start = i
            break
        if '등록상품 ID:' in line and i > header_end:
            start = i - 1
            break

    # 각 상품 블록 구분: separator 또는 배송 타입(새 상품 시작)
    segments = []
    current = []
    for line in lines[start:]:
        if SEPARATOR in line:
            if current:
                segments.append(current)
            current = []
        elif line in DELIVERY_TYPES and current and any('등록상품 ID:' in l for l in current):
            # 이미 상품 데이터가 있는 상태에서 새 배송타입 → 새 블록
            segments.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        segments.append(current)

    products = []
    skip_lines = {
        '판매자 배송', '로켓그로스', '상품 상태', '광고 운영 중', '광고 중지',
        '일부 광고 중지됨', '예산 모두 사용', '광고 일시중지', '판매중지',
    }

    for seg in segments:
        p = {}
        # 배송 유형
        if seg and seg[0] in ('판매자 배송', '로켓그로스'):
            p['type'] = seg[0]
            seg = seg[1:]
        else:
            p['type'] = ''

        if not seg:
            continue

        # 상품명 (첫 줄이 '등록상품'이 아니고 skip도 아닌 줄)
        name_idx = 0
        for i, line in enumerate(seg):
            if '등록상품 ID' in line or '카테고리' in line or line in skip_lines:
                break
            # 외 N개 패턴 스킵
            if re.match(r'^외 \d+개$', line):
                continue
            name_idx = i
            p['name'] = line
            break

        # 옵션 ID / 등록상품 ID
        for line in seg:
            m = re.search(r'등록상품 ID:\s*(\d+)\s*[∙·]\s*옵션 ID:\s*(\d+)', line)
            if m:
                p['product_id'] = m.group(1)
                p['option_id'] = m.group(2)
                break

        # 카테고리
        for line in seg:
            if '카테고리:' in line:
                p['category'] = line.replace('카테고리:', '').strip()
                break

        # 지표 파싱 — 레이블 앞 줄이 값
        labels = {
            '방문자': 'visitors',
            '조회': 'views',
            '장바구니': 'cart',
            '주문': 'orders',
            '판매량': 'sales',
            '매출 (원)': 'revenue',
            '구매전환율': 'cvr',
        }
        for i, line in enumerate(seg):
            if line in labels and i > 0:
                key = labels[line]
                val_str = seg[i-1]
                if key == 'revenue':
                    p[key] = parse_revenue(val_str)
                elif key == 'cvr':
                    try:
                        p[key] = float(val_str.replace('%', '')) / 100
                    except:
                        p[key] = 0.0
                else:
                    p[key] = parse_int(val_str)

        if 'name' in p and ('revenue' in p or 'sales' in p):
            products.append(p)

    return products


def parse_summary(lines):
    """헤더 요약 지표 파싱"""
    summary = {}
    labels = {
        '방문자': 'visitors',
        '조회': 'views',
        '장바구니': 'cart',
        '주문': 'orders',
        '판매량': 'sales',
        '매출 (원)': 'revenue',
        '구매전환율': 'cvr',
    }
    # 헤더 영역은 첫 100줄 내
    for i, line in enumerate(lines[:100]):
        if line in labels and i > 0:
            key = labels[line]
            val_str = lines[i-1]
            if key not in summary:  # 첫 번째만 (상품 테이블과 겹치지 않게)
                if key == 'revenue':
                    summary[key] = parse_revenue(val_str)
                elif key == 'cvr':
                    try:
                        summary[key] = float(val_str.replace('%', '')) / 100
                    except:
                        summary[key] = 0.0
                else:
                    summary[key] = parse_int(val_str)
    return summary


async def set_date(page, target_date: date):
    """날짜 피커에서 날짜 설정"""
    yesterday_d = date.today() - timedelta(days=1)
    today_d = date.today()

    # 1단계: 날짜 버튼 열기 (JS evaluate로 텍스트 클릭)
    opened = await page.evaluate("""() => {
        const texts = ['최근 7일', '어제', '오늘', '최근 30일', '최근 90일'];
        for (const t of texts) {
            for (const btn of document.querySelectorAll('button, span, div')) {
                if (btn.innerText && btn.innerText.trim() === t) {
                    btn.click();
                    return t;
                }
            }
        }
        return null;
    }""")
    log(f"  날짜 버튼 클릭: {opened}")
    await asyncio.sleep(1.5)

    # 2단계: 프리셋 버튼 클릭
    if target_date == yesterday_d:
        preset = "어제"  # 어제
    elif target_date == today_d:
        preset = "오늘"  # 오늘
    else:
        preset = None

    if preset:
        clicked = await page.evaluate(f"""() => {{
            const target = '{preset}';
            for (const btn of document.querySelectorAll('button')) {{
                if (btn.innerText && btn.innerText.trim() === target) {{
                    btn.click();
                    return true;
                }}
            }}
            return false;
        }}""")
        log(f"  프리셋 클릭 결과: {clicked}")
        await asyncio.sleep(0.5)

    # 3단계: 확인/선택 버튼 클릭
    confirmed = await page.evaluate("""() => {
        const keywords = ['선택', '적용', '확인'];
        const btns = Array.from(document.querySelectorAll('button'));
        for (const kw of keywords) {
            for (const btn of btns) {
                const t = btn.innerText || '';
                if (t.includes(kw) && !t.includes('취소')) {
                    btn.click();
                    return t.trim();
                }
            }
        }
        return null;
    }""")
    log(f"  확인 버튼 클릭: {confirmed}")
    await asyncio.sleep(3)


async def get_all_pages(page):
    """페이지네이션 처리하며 전체 상품 수집"""
    all_products = []
    page_num = 1

    while True:
        log(f"  페이지 {page_num} 수집 중...")
        await asyncio.sleep(2)

        body_text = await page.inner_text("body")
        lines = [l.strip() for l in body_text.split('\n') if l.strip()]
        products = parse_products(lines)
        log(f"  → {len(products)}개 상품")
        all_products.extend(products)

        # 다음 페이지 버튼
        next_btn = page.locator('button[aria-label*="다음"], button:has-text("다음"), [class*="next"]:not([disabled])')
        count = await next_btn.count()
        if count == 0:
            break

        # 비활성화 확인
        disabled = await next_btn.first.get_attribute("disabled")
        if disabled is not None:
            break

        await next_btn.first.click()
        page_num += 1

        if page_num > 20:
            log("  안전 상한(20페이지) 도달")
            break

    return all_products


async def collect(target_date: date = None):
    if target_date is None:
        target_date = date.today() - timedelta(days=1)  # 기본: 어제

    date_str = target_date.strftime("%Y-%m-%d")
    log(f"=== 쿠팡 Wing 수집 시작: {date_str} ===")

    ensure_chrome()

    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{DEBUG_PORT}", timeout=30000)
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        # 판매분석 페이지 이동
        await page.goto(SALES_URL, timeout=30000, wait_until="domcontentloaded")
        await asyncio.sleep(5)

        # 날짜 설정
        await set_date(page, target_date)

        # 판매된 옵션 탭 클릭 (데이터 있는 것만)
        try:
            sold_tab = page.get_by_text(re.compile(r"판매된 옵션"))
            if await sold_tab.count() > 0:
                await sold_tab.first.click()
                await asyncio.sleep(2)
        except:
            pass

        # 헤더 요약
        body_text = await page.inner_text("body")
        lines = [l.strip() for l in body_text.split('\n') if l.strip()]
        summary = parse_summary(lines)
        log(f"  요약: 판매량={summary.get('sales', 0):,} 매출={summary.get('revenue', 0):,}원")

        # 전체 상품 수집
        products = await get_all_pages(page)
        log(f"  전체 상품 수: {len(products)}")

        # 히스토리 저장
        history = {}
        if HISTORY_JSON.exists():
            try:
                history = json.loads(HISTORY_JSON.read_text(encoding="utf-8"))
            except:
                pass

        if date_str not in history:
            history[date_str] = {}

        history[date_str] = {
            "scraped_at": datetime.now().isoformat(),
            "date": date_str,
            "summary": summary,
            "products": products,
        }

        # 180일 초과 삭제
        all_dates = sorted(history.keys())
        while len(all_dates) > MAX_DAYS:
            del history[all_dates.pop(0)]

        HISTORY_JSON.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

        # JS 파일 생성
        js_content = f"// coupang_history.js — auto-generated {datetime.now().isoformat()}\n"
        js_content += f"var COUPANG_HISTORY = {json.dumps(history, ensure_ascii=False)};\n"
        HISTORY_JS.write_text(js_content, encoding="utf-8")

        log(f"  저장 완료: {HISTORY_JSON}")

        # 수집 결과 미리보기
        if products:
            log(f"  상위 5개 상품:")
            for pp in sorted(products, key=lambda x: x.get('revenue', 0), reverse=True)[:5]:
                log(f"    {pp.get('name','')[:40]} 판매량={pp.get('sales',0)} 매출={pp.get('revenue',0):,}원")

        # git push
        import subprocess as sp
        try:
            sp.run(["git", "add", "coupang_history.js"], cwd=str(BASE), capture_output=True)
            sp.run(["git", "commit", "-m", f"coupang: {date_str}"], cwd=str(BASE), capture_output=True)
            sp.run(["git", "push"], cwd=str(BASE), capture_output=True)
            log("  git push 완료")
        except Exception as e:
            log(f"  git push 실패: {e}")

        return {"date": date_str, "products": products, "summary": summary}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        # python coupang_auto.py 2026-05-25
        target = date.fromisoformat(sys.argv[1])
    else:
        target = date.today()

    asyncio.run(collect(target))
