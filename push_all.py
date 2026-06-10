# -*- coding: utf-8 -*-
"""통합 git push - 모든 JS 히스토리/대시보드 파일을 한 번에 push.

설계 원칙 (memory feedback_automation_resilience / feedback_collect_failure_alert):
- push 실패를 절대 삼키지 않는다 → 재시도 + 토스트 알림 + exit(1)로 스케줄러에 전파.
- 변경이 없어도 origin보다 ahead면 밀린 커밋을 끝까지 밀어낸다(누적 방지).
- 모든 결과를 push_all_log.txt에 남긴다.
"""
import os, subprocess, sys, time
from datetime import datetime
from pathlib import Path

# pythonw로 실행되면 stdout/stderr가 None → print() 즉사(0x1). devnull로 가드.
# (memory feedback_automation_resilience)
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

BASE = Path(__file__).parent
NOW  = datetime.now().strftime("%Y-%m-%d %H:%M")
LOG  = BASE / "push_all_log.txt"

JS_FILES = [
    "cafe24_data.js",
    "cafe24_history.js",
    "tiktok_history.js",
    "gads_history.js",
    "coupang_history.js",
    "smartstore_history.js",
    "dashboard.html",
    "meta_dashboard.html",
    "diostock_data.js",
    "products_data.js",
    "diostock.html",
    "cafe24_callback.html",
]


def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=str(BASE), capture_output=True, text=True,
                          encoding="utf-8", errors="ignore", **kw)


def notify(title, message):
    """윈도우 토스트 알림. 실패해도 진행. (memory feedback_collect_failure_alert)"""
    title = str(title).replace('"', "'")
    message = str(message).replace('"', "'")
    ps = (
        '[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null;'
        '$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);'
        '$x=$t.GetElementsByTagName("text");'
        f'$x.Item(0).AppendChild($t.CreateTextNode("{title}")) > $null;'
        f'$x.Item(1).AppendChild($t.CreateTextNode("{message}")) > $null;'
        '$n=[Windows.UI.Notifications.ToastNotification]::new($t);'
        '[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("로그린푸시").Show($n);'
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], timeout=15,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def ahead_count():
    """origin/main 대비 로컬이 앞선 커밋 수. 실패 시 -1."""
    r = run(["git", "rev-list", "--count", "origin/main..HEAD"])
    try:
        return int(r.stdout.strip())
    except Exception:
        return -1


def try_push():
    """push 1회 시도. (성공여부, stderr) 반환."""
    r = run(["git", "push", "origin", "main"], timeout=90)
    return r.returncode == 0, (r.stderr or r.stdout or "").strip()


def main():
    # 1) 존재하는 데이터 파일 stage + 변경 있으면 commit
    existing = [f for f in JS_FILES if (BASE / f).exists()]
    if existing:
        run(["git", "add"] + existing)
        status = run(["git", "status", "--porcelain"] + existing)
        if status.stdout.strip():
            r = run(["git", "commit", "-m", f"auto: {NOW} data update"])
            if r.returncode == 0:
                log("commit 생성")
            elif "nothing to commit" not in (r.stdout + r.stderr):
                log(f"commit 실패: {(r.stderr or r.stdout)[:200]}")

    # 2) origin 최신 정보 갱신 후 ahead 판정 (변경 없어도 밀린 커밋 밀어냄)
    run(["git", "fetch", "origin", "main"], timeout=60)
    ahead = ahead_count()
    if ahead == 0:
        log("ahead 0 — push 불필요 (이미 최신)")
        return
    if ahead < 0:
        log("ahead 계산 실패 — push 강행 시도")

    # 3) push (재시도 3회, non-fast-forward면 rebase 후 재시도)
    last_err = ""
    for attempt in range(1, 4):
        ok, err = try_push()
        if ok:
            log(f"git push 완료 (ahead {ahead}, {attempt}회차)")
            return
        last_err = err
        log(f"push {attempt}회차 실패: {err[:200]}")
        low = err.lower()
        if "non-fast-forward" in low or "fetch first" in low or "rejected" in low:
            rb = run(["git", "pull", "--rebase", "origin", "main"], timeout=90)
            log(f"pull --rebase: {'성공' if rb.returncode == 0 else (rb.stderr or rb.stdout)[:150]}")
        time.sleep(5)

    # 4) 3회 다 실패 → 알림 + 비정상 종료(스케줄러가 0x1로 인지)
    remain = ahead_count()
    msg = f"git push 3회 실패 (미반영 {remain if remain>=0 else ahead}커밋). 대시보드 갱신 안됨"
    log(f"❌ {msg} | {last_err[:200]}")
    notify("⚠️ 대시보드 push 실패", msg)
    sys.exit(1)


if __name__ == "__main__":
    main()
