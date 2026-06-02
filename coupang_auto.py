# -*- coding: utf-8 -*-
"""쿠팡 Wing 판매분석 자동 수집 스크립트"""
import sys, asyncio, json, re, time, subprocess, socket
from pathlib import Path
from datetime import datetime, timedelta, date

if sys.stdout is None:  # pythonw.exe(스케줄러)는 콘솔 없어 stdout=None → reconfigure/print 시 즉사(0x1)
    import os
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
else:
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


def notify(title, message):
    """윈도우 토스트 알림 (모듈 의존성 없음). 실패해도 수집은 계속."""
    ps = (
        '[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null;'
        '$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent('
        '[Windows.UI.Notifications.ToastTemplateType]::ToastText02);'
        '$x=$t.GetElementsByTagName("text");'
        f'$x.Item(0).AppendChild($t.CreateTextNode("{title}")) > $null;'
        f'$x.Item(1).AppendChild($t.CreateTextNode("{message}")) > $null;'
        '$n=[Windows.UI.Notifications.ToastNotification]::new($t);'
        '[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("쿠팡수집").Show($n);'
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        # 토스트 실패 시 메시지박스 폴백
        try:
            box = ('Add-Type -AssemblyName System.Windows.Forms > $null;'
                   f'[System.Windows.Forms.MessageBox]::Show("{message}","{title}") > $null;')
            subprocess.Popen(["powershell", "-NoProfile", "-Command", box])
        except Exception:
            pass


def is_logged_out(page_url, lines):
    """매출 페이지가 로그인/인증 페이지로 튕겼는지 판별"""
    u = (page_url or "").lower()
    if "xauth.coupang.com" in u or "/sso/login" in u or "login=true" in u:
        return True
    head = " ".join(lines[:15])
    return ("판매자 로그인" in head) and ("판매자 회원가입" in head or "비밀번호 찾기" in head)


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
        "--no-first-run", "--no-default-browser-check",
        "--start-minimized", "--window-position=-9999,-9999",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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


def _norm(s):
    """공백/유니코드 차이 무시용 정규화"""
    return re.sub(r'\s+', '', (s or '')).strip()

# 배송유형 라벨은 쿠팡이 텍스트/공백을 자주 바꾼다 → 정규화 후 prefix 매칭
_DELIVERY_NORMS = ('판매자배송', '로켓그로스', '로켓배송')

def is_delivery_type(line):
    n = _norm(line)
    return any(n.startswith(d) for d in _DELIVERY_NORMS)

def parse_products(lines):
    """innerText 줄 목록에서 상품별 데이터 파싱"""
    DELIVERY_TYPES = {'판매자 배송', '판매자배송', '로켓그로스'}
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
        if is_delivery_type(line):
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
        elif is_delivery_type(line) and current and any('등록상품 ID:' in l for l in current):
            # 이미 상품 데이터가 있는 상태에서 새 배송타입 → 새 블록
            segments.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        segments.append(current)

    products = []
    skip_lines = {
        '판매자 배송', '판매자배송', '로켓그로스', '상품 상태', '광고 운영 중', '광고 중지',
        '일부 광고 중지됨', '예산 모두 사용', '광고 일시중지', '판매중지',
    }

    skip_norms = {_norm(s) for s in skip_lines}

    for seg in segments:
        p = {}
        # 배송 유형 (라벨 텍스트 변동 대응: 정규화 매칭)
        if seg and is_delivery_type(seg[0]):
            p['type'] = seg[0]
            seg = seg[1:]
        else:
            p['type'] = ''

        if not seg:
            continue

        # 상품명 (첫 줄이 '등록상품'이 아니고 skip도 아닌 줄)
        name_idx = 0
        for i, line in enumerate(seg):
            if '등록상품 ID' in line or '카테고리' in line or _norm(line) in skip_norms or is_delivery_type(line):
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

    # 1단계: 날짜 피커 트리거 렌더링 대기 후 클릭
    try:
        await page.wait_for_selector('[class*="context-trigger-filter"]', timeout=15000)
    except Exception:
        log("  날짜 피커 트리거 대기 실패 — 5초 추가 대기")
        await asyncio.sleep(5)

    trigger = page.locator('[class*="context-trigger-filter"]').first
    try:
        await trigger.click(timeout=5000)
        log("  날짜 피커 열기 완료 (locator)")
    except Exception:
        # fallback: JS evaluate
        opened = await page.evaluate("""() => {
            for (const el of document.querySelectorAll('[class*="context-trigger-filter"]')) {
                if (el.offsetParent !== null) { el.click(); return el.className; }
            }
            const texts = ['최근 7일', '어제', '오늘', '최근 30일', '최근 90일'];
            for (const t of texts) {
                for (const el of document.querySelectorAll('button, span, div')) {
                    if ((el.innerText || '').trim() === t && el.offsetParent !== null) {
                        el.click(); return t;
                    }
                }
            }
            return null;
        }""")
        log(f"  날짜 피커 열기(fallback): {opened}")
    await asyncio.sleep(1.5)

    # 2단계: 프리셋 버튼 클릭
    if target_date == yesterday_d:
        preset = "어제"
    elif target_date == today_d:
        preset = "오늘"
    else:
        preset = None

    if preset:
        try:
            btn = page.get_by_role("button", name=preset).or_(
                page.locator(f'button:has-text("{preset}")')
            ).first
            await btn.click(timeout=5000)
            log(f"  프리셋 클릭 완료: {preset}")
        except Exception:
            clicked = await page.evaluate(f"""() => {{
                for (const btn of document.querySelectorAll('button')) {{
                    if ((btn.innerText || '').trim() === '{preset}') {{
                        btn.click(); return true;
                    }}
                }}
                return false;
            }}""")
            log(f"  프리셋 클릭(fallback): {clicked}")
        await asyncio.sleep(0.5)

    # 3단계: 날짜 선택 확인 버튼 클릭 (날짜 피커 내부 버튼만)
    confirmed = await page.evaluate("""() => {
        const keywords = ['선택', '적용', '확인'];
        const btns = Array.from(document.querySelectorAll('button'));
        // 날짜 피커 내부 버튼 우선 (class에 date/calendar/picker 포함)
        for (const kw of keywords) {
            for (const btn of btns) {
                const t = (btn.innerText || '').trim();
                const cls = btn.className || '';
                const inPicker = btn.closest('[class*="date"],[class*="calendar"],[class*="picker"],[class*="period"]');
                if (t.includes(kw) && !t.includes('취소') && inPicker && btn.offsetParent !== null) {
                    btn.click(); return t;
                }
            }
        }
        // 피커 외부에서도 시도 (선택완료 형태)
        for (const kw of keywords) {
            for (const btn of btns) {
                const t = (btn.innerText || '').trim();
                if ((t.includes(kw) || t.includes('선택 완료')) && !t.includes('취소') && btn.offsetParent !== null) {
                    btn.click(); return t;
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

        # 다음 페이지 버튼 — JS evaluate로 직접 탐색 (disabled 무시, 텍스트/aria 우선)
        clicked = await page.evaluate("""() => {
            const allBtns = Array.from(document.querySelectorAll('button'));

            // 1) aria-label에 '다음' 포함 & 비활성화 아님
            for (const btn of allBtns) {
                const label = (btn.getAttribute('aria-label') || '');
                if (label.includes('다음') && !btn.disabled && btn.getAttribute('disabled') === null) {
                    btn.click(); return 'aria-label:' + label;
                }
            }

            // 2) 텍스트가 정확히 '다음'인 버튼
            for (const btn of allBtns) {
                const t = (btn.innerText || btn.textContent || '').trim();
                if (t === '다음' && !btn.disabled && btn.getAttribute('disabled') === null) {
                    btn.click(); return 'text:다음';
                }
            }

            // 3) class에 next/Next 포함하는 버튼/링크
            for (const el of document.querySelectorAll('[class*="next"],[class*="Next"],[class*="pagination"]')) {
                const tag = el.tagName.toLowerCase();
                if ((tag === 'button' || tag === 'a') && !el.disabled && el.getAttribute('disabled') === null) {
                    el.click(); return 'class:' + el.className.slice(0, 40);
                }
            }

            // 4) 페이지 번호 버튼 중 현재 활성보다 큰 숫자 클릭
            const active = allBtns.find(b =>
                b.getAttribute('aria-current') === 'page' ||
                b.classList.contains('active') ||
                b.classList.contains('is-active')
            );
            if (active) {
                const curNum = parseInt(active.innerText.trim(), 10);
                for (const btn of allBtns) {
                    const n = parseInt((btn.innerText || '').trim(), 10);
                    if (n === curNum + 1 && !btn.disabled) {
                        btn.click(); return 'page-num:' + n;
                    }
                }
            }

            return null;
        }""")

        if not clicked:
            log(f"  다음 버튼 없음 — {page_num}페이지에서 종료")
            break

        log(f"  다음 페이지 클릭 ({clicked})")
        page_num += 1

        if page_num > 20:
            log("  안전 상한(20페이지) 도달")
            break

    return all_products


def _still_login_page(u):
    return ("xauth.coupang.com" in u) or ("/sso/login" in u) or ("login=true" in u)


async def try_auto_login(page):
    """로그인 페이지로 튕겼을 때 키스트로크 우선으로 무인 재로그인 시도.
    자격증명이 브라우저에 저장돼 있으면 Enter만으로 제출됨. 사람처럼 키 입력.
    비밀번호 재입력/문자인증이 필요하면 실패로 두고 사람에게 넘긴다(기존 동작 fallback).
    참고: memory feedback_login_automation / reference_login_urls"""
    # 1) 키스트로크: 비밀번호 칸에 포커스 → Enter 로 폼 제출 (사람처럼)
    try:
        pw = page.locator('input[type="password"]')
        if await pw.count() > 0:
            await pw.first.click(timeout=3000)   # 포커스만
            await page.keyboard.press("Enter")
        else:
            await page.keyboard.press("Enter")   # 폼이 이미 포커스된 경우
        await asyncio.sleep(6)
        if not _still_login_page(page.url):
            log("  자동 로그인: Enter 키로 통과")
            return True
    except Exception:
        pass

    # 2) fallback: 로그인 버튼 클릭 후보 (개편 대비 여러 셀렉터)
    for sel in ['button[type="submit"]', 'button:has-text("로그인")',
                'a:has-text("로그인")', '#kc-login', '.login__button', 'input[type="submit"]']:
        try:
            btn = page.locator(sel)
            if await btn.count() == 0:
                continue
            log(f"  자동 로그인: '{sel}' 클릭 시도")
            await btn.first.click(timeout=5000)
            await asyncio.sleep(6)
            if not _still_login_page(page.url):
                return True
        except Exception:
            continue
    return False


async def collect(target_date: date = None):
    if target_date is None:
        target_date = date.today()  # 기본: 오늘

    date_str = target_date.strftime("%Y-%m-%d")
    log(f"=== 쿠팡 Wing 수집 시작: {date_str} ===")

    ensure_chrome()

    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{DEBUG_PORT}", timeout=30000)
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        # 날짜 파라미터 포함한 URL로 직접 이동 (날짜 피커 조작 불필요)
        url_with_date = f"{SALES_URL}?start_date={date_str}&end_date={date_str}"
        await page.goto(url_with_date, timeout=30000, wait_until="domcontentloaded")
        await asyncio.sleep(5)
        log(f"  페이지 로드 완료: {date_str}")

        # 로그인 만료 감지: 인증 페이지로 튕겼으면 기존 데이터 보존하고 중단 + 알림
        precheck = await page.inner_text("body")
        pre_lines = [l.strip() for l in precheck.split('\n') if l.strip()]
        if is_logged_out(page.url, pre_lines):
            log("  ⚠️ 쿠팡 Wing 로그아웃 감지 — 자동 로그인 시도")
            if await try_auto_login(page):
                log("  ✓ 자동 로그인 성공 — 매출 페이지 재이동")
                await page.goto(url_with_date, timeout=30000, wait_until="domcontentloaded")
                await asyncio.sleep(5)
                precheck = await page.inner_text("body")
                pre_lines = [l.strip() for l in precheck.split('\n') if l.strip()]
            # 자동 로그인 후에도 여전히 로그아웃이면 사람에게 넘김
            if is_logged_out(page.url, pre_lines):
                log("  ⚠️ 자동 로그인 실패(비번 재입력/인증 필요 추정) — 수집 중단, 기존 데이터 보존")
                notify("쿠팡 Wing 로그인 만료",
                       "9225 크롬 창에서 다시 로그인해 주세요. 재로그인 전까지 쿠팡 데이터가 갱신되지 않습니다.")
                return

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

        # git push는 push_all.py가 통합 처리
        log("  JS 저장 완료 (push는 push_all.py에서 처리)")

        return {"date": date_str, "products": products, "summary": summary}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        # python coupang_auto.py 2026-05-25
        target = date.fromisoformat(sys.argv[1])
    else:
        target = date.today()

    asyncio.run(collect(target))
