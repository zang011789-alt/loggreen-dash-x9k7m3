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

if sys.stdout is None:  # pythonw.exe(스케줄러)는 콘솔 없어 stdout=None → reconfigure/print 시 즉사(0x1)
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
else:
    sys.stdout.reconfigure(encoding="utf-8")

HISTORY_JSON = r"C:\Users\zang0\Desktop\my-site\tiktok_history.json"
HISTORY_JS   = r"C:\Users\zang0\Desktop\my-site\tiktok_history.js"
SITE_DIR     = r"C:\Users\zang0\Desktop\my-site"

BRANDS = {
    "outcoma":  {
        "adv_id":   "7642243611500806165",  # 아웃코마3 (2026-05-27 이전: 7556508952121393153)
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
        # 영문/한국어 UI 모두 지원
        btn = page.locator('text="Custom Columns"').or_(page.locator('text="사용자 지정 열"'))
        btn.first.click()
        page.wait_for_timeout(1000)
        page.locator('text="장동훈"').first.click()
        page.wait_for_timeout(2500)
        return True
    except:
        return False

# ── 스크래퍼 ──────────────────────────────────────────
STATUS_VALS = ('Active', 'Paused', 'Deleted', 'Not delivering',
               '게재 중', '일시 중지', '삭제됨', '게재 불가', '학습 중',
               '활성', '비활성', '중지', '일시중지', 'Inactive')
ACTIVE_VALS = ('Active', '게재 중', '학습 중', '활성')

def _is_value(s):
    """헤더 블록에서 요약값(숫자/KRW/%/-) 판별 — 라벨과 구분용"""
    s = s.strip()
    if s in ('-', ''):
        return True
    return bool(re.fullmatch(r'[\d,]+(\.\d+)?\s*%?(\s*KRW)?', s))

def _label_to_field(lbl):
    """헤더 라벨 → 내부 필드명 매핑. '장바구니'/'결제 시작' 등 보조 컬럼은 무시(None)."""
    if '장바구니' in lbl or '결제 시작' in lbl or '딥 퍼널' in lbl or lbl in ('결과', '결과율'):
        return None
    if '구매 금액' in lbl or '구매금액' in lbl or '전환값' in lbl or '전환 값' in lbl or '매출' in lbl or '총 전환' in lbl:
        return 'revenue'
    if 'ROAS' in lbl or '수익률' in lbl:        return 'roas'
    if '전환당' in lbl:                          return 'cpa'
    if '전환율' in lbl or 'CVR' in lbl:          return 'cvr'
    if '전환수' in lbl or '전환 수' in lbl:      return 'conversions'
    if lbl.startswith('비용') or lbl == 'Cost': return 'spend'
    if 'CPC' in lbl:                             return 'cpc'
    if 'CPM' in lbl:                             return 'cpm'
    if '노출' in lbl:                            return 'impressions'
    if '클릭' in lbl:                            return 'clicks'
    if 'CTR' in lbl:                             return 'ctr'
    return None

def _is_desc(s):
    """헤더 컬럼 툴팁/설명 문장 판별 (정렬·hover 시 노출되어 라벨로 오인되는 것 제외)"""
    return len(s) > 17 or bool(re.search(r'(입니다|합니다|됩니다|없음|않음|하세요|있습니다|보세요|클릭하여|확인)$', s))

def _parse_header_labels(lines):
    """'이름' 헤더부터 데이터 행 시작 전까지 라벨 순서 추출 (요약값/설명문 제외)"""
    start = None
    for idx, s in enumerate(lines):
        if s in ('이름', 'Name'):
            start = idx; break
    if start is None:
        return None
    labels = []
    j = start
    while j < len(lines) and len(labels) < 45:
        s = lines[j]
        # 캠페인/소재명(데이터 행) 만나면 헤더 끝
        if re.search(r'(tk_|fb_|gg_)', s, re.I) or re.match(r'^\d{3}_\d{6}_', s):
            break
        if not _is_value(s) and not _is_desc(s):
            labels.append(s)
        j += 1
    return labels if len(labels) >= 4 else None

def _metric_labels(header):
    """첫 지표 컬럼부터 헤더 끝까지의 라벨 순서.
    - 미매핑(결제시작/장바구니/결과 등) 라벨도 placeholder로 유지 → 데이터 줄 소비, 매핑은 _label_to_field로
    - 정렬 시 중복되는 지표 컬럼명(비용 2회 등)만 제거"""
    if not header:
        return []
    start = next((idx for idx, l in enumerate(header) if _label_to_field(l)), None)
    if start is None:
        return []
    seen, out = set(), []
    for l in header[start:]:
        f = _label_to_field(l)
        if f and f in seen:
            continue  # 정렬로 중복된 지표 컬럼 제거
        if f:
            seen.add(f)
        out.append(l)
    return out

def _assign(camp, field, val):
    if field in ('ctr', 'cvr'):       camp[field] = parse_pct(val)
    elif field == 'roas':             camp[field] = parse_float(val)
    elif field in ('clicks', 'impressions', 'conversions'): camp[field] = parse_num(val)
    else:                             camp[field] = parse_krw(val)

def scrape_campaigns(page, camp_pat):
    page.wait_for_timeout(5000)
    for _ in range(14):
        page.evaluate("window.scrollBy(0, 450)")
        page.wait_for_timeout(300)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(1500)

    text  = page.evaluate("document.body.innerText")
    lines = [l.strip() for l in text.split('\n') if l.strip()]

    # 헤더 라벨 동적 파싱 (지표 라벨만, 중복 필드 제거)
    labels = _parse_header_labels(lines)
    metric_labels = _metric_labels(labels)
    use_dynamic = bool(metric_labels and any(_label_to_field(l) == 'spend' for l in metric_labels))

    campaigns = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not re.search(camp_pat, line, re.IGNORECASE):
            i += 1; continue
        status_raw = lines[i+1] if i+1 < len(lines) else ''
        if status_raw not in STATUS_VALS:
            i += 1; continue

        camp = {'name': line, 'status': 'active' if status_raw in ACTIVE_VALS else 'paused',
                'budget': 0, 'cpa': 0, 'spend': 0, 'revenue': 0, 'roas': 0, 'cpc': 0,
                'ctr': 0, 'clicks': 0, 'impressions': 0, 'cpm': 0, 'conversions': 0}

        if use_dynamic:
            # 동적 매핑: name, status 다음 서브상태 줄 스킵 → 예산(+유형) → 헤더 순서대로 지표
            k = i + 2
            # 서브상태 줄/배지 숫자 등 건너뛰고 예산(KRW 금액 또는 무제한) 찾기
            while k < len(lines) and k < i + 7:
                s = lines[k]
                if 'KRW' in s or 'unlimited' in s.lower() or '무제한' in s:
                    break
                k += 1
            if k < len(lines) and ('KRW' in lines[k] or 'unlimited' in lines[k].lower() or '무제한' in lines[k]):
                camp['budget'] = parse_krw(lines[k]); k += 1
            # 예산 유형 줄("매일, 캠페인 예산" 등) 스킵
            if (k < len(lines) and not _is_value(lines[k]) and 'unlimited' not in lines[k].lower()
                    and not re.search(camp_pat, lines[k], re.I)):
                k += 1
            for lbl in metric_labels:
                if k >= len(lines): break
                f = _label_to_field(lbl)
                if f and f != 'roas': _assign(camp, f, lines[k])  # roas는 매출/소진으로 재계산
                k += 1
            camp['roas'] = round(camp['revenue'] / camp['spend'], 2) if (camp['spend'] > 0 and camp['revenue'] > 0) else 0
            campaigns.append(camp)
            i = k
            continue

        # 폴백: 기존 고정 offset 매핑
        try:
            offset = 2
            while i+offset < len(lines) and offset < 6:
                nxt = lines[i+offset]
                if re.search(r'[\d,]+', nxt) or 'unlimited' in nxt.lower():
                    break
                offset += 1
            def gl(n):
                idx = i + offset + n
                return lines[idx] if idx < len(lines) else '0'
            camp.update({
                'budget': parse_krw(gl(0)),  'cpa': parse_krw(gl(2)),
                'spend': parse_krw(gl(3)),   'revenue': parse_krw(gl(4)),
                'roas': parse_float(gl(5)),  'cpc': parse_krw(gl(6)),
                'ctr': parse_pct(gl(7)),     'clicks': parse_num(gl(11)),
                'impressions': parse_num(gl(12)), 'cpm': parse_krw(gl(13)),
                'conversions': parse_num(gl(14)),
            })
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

def _is_metric_start(s):
    """지표 값 시작 판별 (KRW/% 또는 소수). 소재행 메타 줄(소스/광고ID/그룹번호) 구분용"""
    return ('KRW' in s) or ('%' in s) or bool(re.fullmatch(r'-?\d[\d,]*\.\d+', s))

def _parse_ads_lines(lines):
    """innerText lines → 소재 리스트 파싱 (동적 헤더 매핑, 폴백: 기존 tk_ 캠페인명 방식)"""
    header = _parse_header_labels(lines)
    metric_labels = _metric_labels(header)
    use_dynamic = bool(metric_labels and any(_label_to_field(l) == 'spend' for l in metric_labels))

    ads = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not re.match(r'^\d{3}_\d{6}_', line):
            i += 1; continue
        status_raw = lines[i+1] if i+1 < len(lines) else ''
        if status_raw not in STATUS_VALS:
            i += 1; continue

        ad = {'name': line, 'campaign': '',
              'status': 'active' if status_raw in ACTIVE_VALS else 'paused',
              'cpa': 0, 'spend': 0, 'revenue': 0, 'roas': 0, 'cpc': 0, 'ctr': 0,
              'clicks': 0, 'impressions': 0, 'cpm': 0, 'conversions': 0, 'cvr': 0}

        if use_dynamic:
            # 앵커로 지표 시작점 결정: 광고ID(13자리+, 다음에 그룹이름 1줄) 또는 캠페인명(tk_, 바로 지표)
            k = i + 2
            anchor_idx, anchor_type = -1, None
            for j in range(i + 2, min(i + 11, len(lines))):
                if re.fullmatch(r'\d{13,}', lines[j].replace(',', '')):
                    anchor_idx, anchor_type = j, 'adid'; break
                if re.search(r'tk_', lines[j], re.IGNORECASE):
                    anchor_idx, anchor_type = j, 'camp'; ad['campaign'] = lines[j]; break
            if anchor_idx > 0:
                k = anchor_idx + (2 if anchor_type == 'adid' else 1)
            else:
                # 폴백: 첫 지표값(KRW/%/소수)까지 메타 줄 스킵
                while k < len(lines) and k < i + 11 and not _is_metric_start(lines[k]):
                    k += 1
            for lbl in metric_labels:
                if k >= len(lines): break
                f = _label_to_field(lbl)
                if f and f != 'roas': _assign(ad, f, lines[k])
                k += 1
            ad['roas'] = round(ad['revenue'] / ad['spend'], 2) if (ad['spend'] > 0 and ad['revenue'] > 0) else 0
            ads.append(ad)
            i = k
            continue

        # 폴백: 기존 tk_ 캠페인명 기준 매핑
        try:
            camp_name, camp_offset = '', -1
            for k in range(2, 10):
                idx = i + k
                if idx >= len(lines): break
                if re.search(r'tk_', lines[idx], re.IGNORECASE):
                    camp_name, camp_offset = lines[idx], k
                    break
            if camp_offset < 0:
                i += 1; continue
            def gv(n):
                idx = i + camp_offset + 1 + n
                return lines[idx] if idx < len(lines) else '0'
            ad['campaign'] = camp_name
            ad.update({'cpa': parse_krw(gv(0)), 'spend': parse_krw(gv(1)),
                       'revenue': parse_krw(gv(2)), 'roas': parse_float(gv(3)),
                       'cpc': parse_krw(gv(4)), 'ctr': parse_pct(gv(5)),
                       'clicks': parse_num(gv(9)), 'impressions': parse_num(gv(10))})
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
        # 영어 UI: "Cost", 한국어 UI: "비용"
        els = page.get_by_text("Cost", exact=True).or_(page.get_by_text("비용", exact=True))
        n = els.count()
        for i in range(n):
            el = els.nth(i)
            bb = el.bounding_box()
            # 테이블 헤더 행 범위: y 30~300, 너비 100 미만
            if bb and 30 < bb['y'] < 300 and bb['width'] < 150:
                el.click(); page.wait_for_timeout(1000)
                el.click(); page.wait_for_timeout(1500)  # 두 번 → 내림차순
                print(f"  Cost/비용 헤더 클릭 완료 (y={bb['y']:.0f})", flush=True)
                return True
        print("  [경고] Cost/비용 헤더 못찾음 — 정렬 없이 진행", flush=True)
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
    """단일 날짜·브랜드 하루치 수집. 로그인 페이지 감지 시 None 반환."""
    cfg   = BRANDS[brand]
    label = cfg["label"]
    url   = (f"https://ads.tiktok.com/i18n/manage/campaign"
             f"?aadvid={cfg['adv_id']}&st={target_date_str}&et={target_date_str}")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
    except Exception as e:
        print(f"  [{label}/{target_date_str}] 이동 오류(무시): {e}", flush=True)
    page.wait_for_timeout(3000)

    # 로그인 페이지 감지
    if "/login" in page.url or "/i18n/login" in page.url:
        print(f"  [{label}/{target_date_str}] ⚠️  TikTok 로그인 필요 — 수집 스킵 (chrome_tt2 프로필에서 수동 로그인 필요)", flush=True)
        return None

    apply_custom_columns(page)
    campaigns = scrape_campaigns(page, cfg["camp_pat"])

    active        = [c for c in campaigns if c['status'] == 'active']
    total_spend   = sum(c['spend']   for c in campaigns)
    total_revenue = sum(c['revenue'] for c in campaigns)
    total_roas    = round(total_revenue / total_spend, 2) if total_spend else 0

    print(f"  [{label}/{target_date_str}] 캠페인 {len(campaigns)}개(활성:{len(active)}) | 소진:{total_spend:,} | 매출:{total_revenue:,} | ROAS:{total_roas}", flush=True)

    # Ad 탭 전환 → 소재 수집 (Alt+3 단축키가 안 먹는 광고주 있어 "광고" 메뉴 클릭 폴백)
    ads = []
    try:
        page.keyboard.press("Alt+3")
        page.wait_for_timeout(1500)
        try:
            ad_menu = page.get_by_text("광고", exact=True)
            if ad_menu.count() > 0:
                ad_menu.first.click()
                page.wait_for_timeout(2500)
        except:
            pass
        page.wait_for_timeout(1000)
        apply_custom_columns(page)  # 소재 탭에서도 컬럼 프리셋 재적용
        ads = scrape_ads(page)
        # 소재 행에 캠페인명이 안 보이는 광고주(예: 아웃코마3)는 campaign='' 됨.
        # 캠페인 1개뿐이면 그 캠페인을 모든 소재에 부여 (대시보드 드릴다운 매칭용)
        if ads and campaigns and not any(a.get('campaign') for a in ads):
            if len(campaigns) == 1:
                cn = campaigns[0]['name']
                for a in ads:
                    a['campaign'] = cn
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
    with open(HISTORY_JSON, "r", encoding="utf-8-sig") as f:
        h = json.load(f)
    # 구형식 마이그레이션: h[date] 직접 scraped_at → h[date]["outcoma"]
    for date_key, v in list(h.items()):
        if isinstance(v, dict) and "scraped_at" in v:
            h[date_key] = {"outcoma": v}
    return h

MAX_DAYS = 180

def notify(title, message):
    """윈도우 토스트 알림. 실패해도 수집은 계속. (참고: memory feedback_collect_failure_alert)"""
    import subprocess
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
    print("  git push 생략 (push_all.py에서 통합 처리)", flush=True)

CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CHROME_DIR = r"C:\Temp\chrome_tt2"

def _kill_debug_chrome():
    """포트 9222로 뜬 Chrome 프로세스 강제 종료"""
    try:
        subprocess.run(
            ["powershell", "-Command",
             "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match '9222|chrome_tt2' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
            capture_output=True, timeout=15
        )
    except subprocess.TimeoutExpired:
        try:
            subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"],
                           capture_output=True, timeout=5)
        except Exception:
            pass

def _start_chrome():
    """Chrome 디버그 모드로 실행"""
    import time
    subprocess.Popen([CHROME_EXE, "--remote-debugging-port=9222",
                      f"--user-data-dir={CHROME_DIR}",
                      "--no-first-run", "--no-default-browser-check",
                      "--remote-allow-origins=*",
                      "--start-minimized", "--window-position=-9999,-9999"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
                if data is None:
                    continue  # 로그인 페이지 → 저장 스킵
                if d_str not in history:
                    history[d_str] = {}
                history[d_str][brand] = data

        # CDP attach 모드: browser.close() 호출 금지 (실제 Chrome 탭을 닫아 다음 실행을 깨뜨림).
        # with 블록 종료 시 연결만 자동으로 끊긴다.

    save_history(history)
    # 오늘 누락 브랜드 알림 (로그인 페이지 감지/연결 오류 등)
    today_iso = max((d.isoformat() if hasattr(d, "isoformat") else str(d)) for d in dates_to_collect)
    td = history.get(today_iso) or {}
    missing = set(brands_to_collect) - set(td.keys())
    if missing:
        notify("⚠️ 틱톡광고 수집 오류", f"{today_iso} 누락: {', '.join(missing)} - 9222 크롬 로그인 확인")
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
