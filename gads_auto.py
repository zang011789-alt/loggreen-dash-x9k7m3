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
ADS_URL  = f"https://ads.google.com/aw/ads?ocid={OCID}"

# 보기 뷰 → gg_ 캠페인 목록
PRODUCT_VIEWS = ['노픽', '템퍼픽션', '검색 캠페인', '디맨드젠 캠페인']

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
    # 열려 있는 드롭다운/피커 먼저 닫기 (잔류 material-select-item 간섭 방지)
    if _picker_is_open(page):
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
    # 드롭다운 버튼 위치 탐지
    pos = page.evaluate("""
    () => {
        const btns = [...document.querySelectorAll('[role="button"]')];
        const btn = btns.find(b => {
            const t = (b.innerText || '');
            const r = b.getBoundingClientRect();
            return (t.includes('보기') || t.includes('필터') || t.includes('View'))
                   && r.y > 50 && r.y < 180 && r.x > 100 && r.x < 900 && r.width > 60;
        });
        if (!btn) return null;
        const r = btn.getBoundingClientRect();
        return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
                 text: (btn.innerText || '').substring(0, 40).split('\\n').join('|') };
    }
    """)
    if not pos:
        print(f"  '보기' 드롭다운 열기 실패")
        return False
    print(f"  보기 버튼 클릭: ({pos['x']},{pos['y']}) '{pos['text']}'")
    # mouse.click → Material Design 컴포넌트는 실제 클릭 이벤트 필요
    page.mouse.click(pos['x'], pos['y'])
    # 드롭다운 아이템이 실제로 나타날 때까지 대기 (최대 5초)
    try:
        page.wait_for_selector('material-select-item', state='visible', timeout=5000)
    except:
        page.wait_for_timeout(2500)

    # 아이템 탐색
    items_info = page.evaluate(f"""
    () => {{
        const all = [...document.querySelectorAll('material-select-item')];
        const vis = all.filter(i => {{
            const r = i.getBoundingClientRect();
            return r.width > 0 && r.height > 0;
        }});
        const target = vis.find(i => (i.innerText || '').includes('{view_name}'));
        if (!target) return {{ found: false, count: vis.length,
            texts: vis.slice(0,8).map(i => (i.innerText||'').trim().substring(0,30)) }};
        const r = target.getBoundingClientRect();
        return {{ found: true, x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2) }};
    }}
    """)

    if not items_info.get('found'):
        cnt = items_info.get('count', 0)
        texts = items_info.get('texts', [])
        print(f"  '{view_name}' 아이템 없음 (드롭다운 {cnt}개: {texts})")
        if cnt == 0:
            # 드롭다운이 아예 안 열린 경우 → 재시도
            page.keyboard.press("Escape")
            page.wait_for_timeout(1000)
            page.mouse.click(pos['x'], pos['y'])
            try:
                page.wait_for_selector('material-select-item', state='visible', timeout=5000)
            except:
                page.wait_for_timeout(2500)
            items_info = page.evaluate(f"""
            () => {{
                const all = [...document.querySelectorAll('material-select-item')];
                const vis = all.filter(i => {{ const r = i.getBoundingClientRect(); return r.width > 0 && r.height > 0; }});
                const target = vis.find(i => (i.innerText || '').includes('{view_name}'));
                if (!target) return {{ found: false, count: vis.length,
                    texts: vis.slice(0,8).map(i => (i.innerText||'').trim().substring(0,30)) }};
                const r = target.getBoundingClientRect();
                return {{ found: true, x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2) }};
            }}
            """)
            if not items_info.get('found'):
                page.keyboard.press("Escape")
                page.wait_for_timeout(400)
                print(f"  '{view_name}' 재시도 실패")
                return False
        else:
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
            return False

    page.mouse.click(items_info['x'], items_info['y'])
    page.wait_for_timeout(2500)
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
    # 피커 버튼 위치 탐지 후 mouse.click (JS click으로는 드롭다운이 열리지 않음)
    pos = page.evaluate("""
    () => {
        const btns = [...document.querySelectorAll('[role="button"]')];
        const cands = btns.filter(b => {
            const t = b.innerText || b.textContent || '';
            const r = b.getBoundingClientRect();
            return r.y > 50 && r.y < 300 && r.x > 900 && r.width >= 80 && r.width <= 280
                   && t.includes('arrow_drop_down');
        }).sort((a, b) => a.getBoundingClientRect().width - b.getBoundingClientRect().width);
        if (cands[0]) {
            const r = cands[0].getBoundingClientRect();
            return { x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
                     text: (cands[0].innerText||'').substring(0,40) };
        }
        return null;
    }
    """)
    if pos:
        page.mouse.click(pos['x'], pos['y'])
        print(f"  피커 클릭: ({pos['x']},{pos['y']}) '{pos['text'][:30]}'")
    else:
        page.mouse.click(1602, 195)
        print(f"  피커 클릭 (폴백 좌표)")
    page.wait_for_timeout(2500)
    return True

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

    # 피커 버튼 텍스트 사전 체크 — 이미 단일 날짜로 설정됐으면 스킵
    target_ko = f"{target_date.year}년 {target_date.month}월 {target_date.day}일"
    quick_text = page.evaluate("""
    () => {
        const btns = [...document.querySelectorAll('[role="button"]')];
        const cand = btns.find(b => {
            const t = b.innerText || '';
            const r = b.getBoundingClientRect();
            return r.y > 50 && r.y < 300 && r.x > 900 && r.width >= 80 && t.includes('arrow_drop_down');
        });
        if (!cand) return '';
        return (cand.innerText||'').split('\\n')[0].trim();
    }
    """)
    if quick_text == target_ko:
        print(f"  날짜 이미 {target_ko} (단일), 스킵")
        return True

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

    # "맞춤" 클릭 (mouse.click 사용 — Material Design 신뢰성)
    custom_pos = page.evaluate("""
    () => {
        const items = [...document.querySelectorAll('material-select-item, [role="option"]')];
        const item = items.find(i => {
            const t = (i.innerText || '').trim();
            const r = i.getBoundingClientRect();
            return t.includes('맞춤') && r.x > 1200 && r.y > 100 && r.y < 700 && r.width > 0;
        });
        if (!item) return null;
        const r = item.getBoundingClientRect();
        return { x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2) };
    }
    """)
    if custom_pos:
        page.mouse.click(custom_pos['x'], custom_pos['y'])
        page.wait_for_timeout(1200)
        print(f"  '맞춤' 옵션 클릭 ({custom_pos['x']},{custom_pos['y']})")

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
                # [-12]=SPEND, [-11]=ROAS, [-7]=CTR%, [-4]=CONVERSIONS, [-3]=CPC, [-2]=CPA, [-1]=CONV_VALUE
                spend = parse_krw(lines[-12]) if len(lines) >= 12 and '₩' in lines[-12] else parse_krw(second_last)
                cpa   = parse_krw(second_last)
                roas  = parse_float(lines[-11]) if len(lines) >= 11 else 0.0
                ctr   = parse_float(lines[-7]) if len(lines) >= 7 else 0.0
                conversions = parse_float(lines[-4]) if len(lines) >= 4 else 0.0
                conv_value  = parse_float(lines[-6]) if len(lines) >= 6 else 0.0

            elif '₩' in last and len(lines) >= 4 and '₩' in lines[-4]:
                # DG 포맷 (디맨드젠 캠페인 뷰, 12~13줄)
                # [-1]=SPEND(₩), [-2]=conv_value, [-3]=conversions, [-4]=CPC(₩), [-5]=CTR%, [-6]=clicks
                spend      = parse_krw(last)
                conv_value = parse_float(lines[-2])
                conversions = parse_float(lines[-3])
                ctr        = parse_float(lines[-5]) if len(lines) >= 5 else 0.0
                roas       = round(conv_value / spend, 2) if spend > 0 else 0.0
                cpa        = int(spend / conversions) if conversions > 0 else 0

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

# ── 소재별 추출 ───────────────────────────────────────────────
def extract_ads(page):
    """소재(광고) 행 파싱. 마지막 7줄 = CPA/비용/ROAS/CTR/전환당비용/전환수/전환가치"""
    ads = []
    try:
        rows = page.evaluate("""
        () => {
            const results = [];
            for (const row of document.querySelectorAll('[role="row"]')) {
                const t = (row.innerText || '').trim();
                if (!t.match(/\\d{3}_\\d{6}_/)) continue;
                const lines = t.split('\\n').map(l => l.trim()).filter(l => l.length > 0);
                results.push(lines);
            }
            return results;
        }
        """)
        for lines in rows:
            if len(lines) < 14:
                continue
            ad_name = lines[0]

            # 캠페인명: "애셋 세부정보 보기" 다음 줄
            asset_idx = next((i for i, l in enumerate(lines) if '애셋 세부정보' in l), -1)
            campaign  = lines[asset_idx + 1] if asset_idx >= 0 and asset_idx + 1 < len(lines) else ''
            adgroup   = lines[asset_idx + 2] if asset_idx >= 0 and asset_idx + 2 < len(lines) else ''

            # gg_ 캠페인 소속 소재만 수집
            if 'gg_' not in campaign:
                continue

            # 소재 행 끝 6줄: 비용(₩) / ?? / CTR% / CPA(₩) / 전환수 / 전환가치
            spend       = parse_krw(lines[-6])
            ctr         = parse_float(lines[-4])
            conversions = parse_float(lines[-2])
            conv_value  = parse_float(lines[-1])
            # roas/cpa 직접 계산 (lines[-5]는 ROAS가 아닌 다른 지표)
            roas        = round(conv_value / spend, 2) if spend > 0 else 0.0
            cpa         = int(spend / conversions) if conversions > 0 else 0

            ads.append({
                "name":        ad_name,
                "campaign":    campaign,
                "adgroup":     adgroup,
                "product":     guess_product(campaign),
                "spend":       spend,
                "roas":        roas,
                "ctr":         ctr,
                "cpa":         cpa,
                "conv_value":  conv_value,
                "conversions": conversions,
            })
    except Exception as e:
        print(f"  ads 추출 실패: {e}")
    return ads

def collect_ads(page, target_date):
    """소재별 데이터 수집. /aw/ads URL 사용."""
    page.goto(ADS_URL, wait_until="load", timeout=40000)
    # date-picker 컴포넌트가 렌더링될 때까지 대기 (goto 후 약 4~6초 소요)
    try:
        page.wait_for_selector('date-picker', timeout=12000)
    except:
        pass
    page.wait_for_timeout(2000)

    date_ok = set_date_range(page, target_date)
    if not date_ok:
        print("  [소재] 날짜 설정 실패")
    if _picker_is_open(page):
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
    page.wait_for_timeout(1500)

    # 스크롤로 전체 로드
    for _ in range(30):
        page.evaluate("window.scrollBy(0, 600)")
        page.wait_for_timeout(100)
    page.evaluate("window.scrollTo(0,0)")
    page.wait_for_timeout(800)

    ads = extract_ads(page)
    print(f"  [소재] {len(ads)}개 수집")
    return ads

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
    seen_idx = {}  # name → all_campaigns 인덱스 (conv>0 데이터로 업데이트 허용)

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
        # 날짜 설정 후 페이지 안정화 대기
        try:
            page.wait_for_selector('[role="row"]', state="visible", timeout=5000)
        except:
            pass
        page.wait_for_timeout(1500)

        camps = _scroll_and_collect(page)
        new_count = 0
        upd_count = 0
        for c in camps:
            if c['name'] not in seen_idx:
                seen_idx[c['name']] = len(all_campaigns)
                all_campaigns.append(c)
                new_count += 1
            elif c.get('conversions', 0) > 0 and all_campaigns[seen_idx[c['name']]].get('conversions', 0) == 0:
                # 이전 수집이 conv=0 (SHORT 포맷)이고 이번엔 conv>0 → 덮어쓰기
                all_campaigns[seen_idx[c['name']]] = c
                upd_count += 1
        print(f"  [{view_name}] {len(camps)}개 수집 ({new_count}개 신규, {upd_count}개 갱신)")

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

    # 소재별 수집
    try:
        ads = collect_ads(page, target_date)
    except Exception as e:
        print(f"  [소재] 수집 실패: {e}")
        ads = []

    # 캠페인 뷰로 복귀 (다음 날짜 처리를 위해)
    page.goto(BASE_URL, wait_until="load", timeout=40000)
    try:
        page.wait_for_selector('[role="columnheader"]', timeout=12000)
    except:
        pass
    page.wait_for_timeout(4000)

    return {
        "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "date": ds,
        "summary": summary,
        "campaigns": all_campaigns,
        "ads": ads,
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
        page.wait_for_timeout(4000)  # 초기 로드 충분히 대기 (뷰 드롭다운 렌더링)

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
