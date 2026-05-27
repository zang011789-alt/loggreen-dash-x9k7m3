# -*- coding: utf-8 -*-
"""통합 git push - 모든 JS 히스토리 파일을 한 번에 push"""
import subprocess, sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
NOW  = datetime.now().strftime("%Y-%m-%d %H:%M")

JS_FILES = [
    "cafe24_data.js",
    "cafe24_history.js",
    "tiktok_history.js",
    "gads_history.js",
    "coupang_history.js",
    "smartstore_history.js",
    "dashboard.html",
    "meta_dashboard.html",
]

def run(cmd, **kw):
    return subprocess.run(cmd, cwd=str(BASE), capture_output=True, text=True,
                          encoding="utf-8", errors="ignore", **kw)

def main():
    # 존재하는 파일만 add
    existing = [f for f in JS_FILES if (BASE / f).exists()]
    if not existing:
        print("추가할 파일 없음"); return

    run(["git", "add"] + existing)

    status = run(["git", "status", "--porcelain"])
    if not status.stdout.strip():
        print(f"[{NOW}] 변경사항 없음 — push 생략")
        return

    commit_msg = f"auto: {NOW} data update"
    r = run(["git", "commit", "-m", commit_msg])
    if r.returncode != 0 and "nothing to commit" in r.stdout + r.stderr:
        print(f"[{NOW}] 변경사항 없음 — push 생략")
        return

    r2 = run(["git", "push", "origin", "main"], timeout=60)
    if r2.returncode == 0:
        print(f"[{NOW}] git push 완료")
    else:
        print(f"[{NOW}] git push 실패: {r2.stderr[:200]}", file=sys.stderr)

if __name__ == "__main__":
    main()
