# -*- coding: utf-8 -*-
"""
check_health.py — [KYY_FOCUS_HEALTH_20260813] 수집 결과의 건강상태 게이트.

왜 필요한가:
  fetch_focus.py 는 한 소스가 죽어도 나머지를 채워야 하므로 성공(exit 0)으로 끝난다.
  그래서 2026-08-13 00:53Z 처럼 DART 가 통째로 비어도 워크플로는 초록불이었고,
  사이트만 조용히 빈 카드가 됐다. 이 스크립트를 커밋 단계 뒤에 두어
  "데이터는 배포하되, 결손이 오래가면 워크플로를 빨갛게" 만든다.
  (GitHub 은 예약 워크플로가 실패하면 저장소 소유자에게 메일을 보낸다.)

판정:
  - 모든 소스 정상            → exit 0
  - 결손이 STALE_ALERT_HOURS 미만 → ::warning 만 남기고 exit 0 (한 번 삐끗한 것)
  - 결손이 STALE_ALERT_HOURS 이상 → ::error + exit 1 (진짜 고장 → 알림)
"""

import json
import os
import sys
import datetime as dt

STALE_ALERT_HOURS = 3
DATA = os.path.join(os.path.dirname(__file__), "data.json")
LABEL = {"dart": "DART(재무·공시)", "naver": "네이버(시세·뉴스)"}


def annotate(level, title, msg):
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::{level} title={title}::{str(msg)[:400]}")
    else:
        print(f"[{level}] {title}: {msg}")


def summary(md):
    """실행 요약 페이지에도 남긴다(로그를 열지 않아도 보이게)."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(md + "\n")
    except Exception:
        pass


def hours_since(stamp):
    """'YYYY-MM-DD HH:MM' → 지금까지 몇 시간. 파싱 실패 시 None."""
    try:
        return (dt.datetime.now() - dt.datetime.strptime(stamp, "%Y-%m-%d %H:%M")).total_seconds() / 3600
    except Exception:
        return None


def main():
    try:
        with open(DATA, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        annotate("error", "data.json 을 읽을 수 없음", e)
        return 1

    health = data.get("health") or {}
    if not health:
        annotate("warning", "health 없음",
                 "이전 버전 fetch_focus.py 가 만든 data.json 입니다(건강상태 판정 불가).")
        return 0

    hard_fail = []
    for name, h in health.items():
        label = LABEL.get(name, name)
        if h.get("ok"):
            print(f"[ok] {label} {h.get('got')}")
            continue
        since = h.get("since")
        elapsed = hours_since(since) if since else None
        errs = " ; ".join(h.get("err") or []) or "사유 미상"
        detail = f"since {since} / {errs}"
        if elapsed is not None and elapsed >= STALE_ALERT_HOURS:
            hard_fail.append(f"{label} {elapsed:.1f}시간째 결손 — {detail}")
            annotate("error", f"{label} 연속 결손", detail)
        else:
            annotate("warning", f"{label} 이번 실행 결손", detail)

    if hard_fail:
        summary("### ⚠ KYY FOCUS 데이터 수집 실패\n\n"
                + "\n".join(f"- {x}" for x in hard_fail)
                + f"\n\n사이트는 마지막 정상값을 '지난 값' 표시와 함께 계속 보여줍니다."
                  f"\n기준시각: `{data.get('asOf')}`\n")
        print("\n".join(hard_fail), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
