# -*- coding: utf-8 -*-
"""
fetch_focus.py — KYY 종목 집중 모니터(FOCUS) 데이터 수집기

역할:
  - 구 쌍방울그룹 계열 5개사(종목코드 기준)에 대해
    · 재무(매출/영업이익/순이익/부채·유동비율 + 전년대비)  ← DART OpenAPI
    · 최근 30일 공시(쉬운 해설 자동 부착)                  ← DART OpenAPI list.json
    · 시세/등락/PER/PBR/외국인보유                          ← 네이버 모바일 금융(키 불필요)
    · 최근 뉴스(규칙기반 한 줄 태그 요약)                    ← 네이버 종목 뉴스(키 불필요)
  - 위를 focus 스키마(§4)의 배열로 만들어 data.json 으로 저장.

필요 키:
  - DART_API_KEY (환경변수). GitHub Actions에서는 Secrets 로 주입.
    로컬 실행: PowerShell> $env:DART_API_KEY="..." ; python fetch_focus.py

주의:
  - 5개사 모두 사명 변경 이력 → 반드시 '종목코드' 기준 조회(회사명 매칭 금지).
  - 네트워크 응답 구조(특히 네이버 비공식 JSON, DART 계정과목명)는 바뀔 수 있어
    모든 수집 단계를 try/except 로 감싸 한 곳이 실패해도 나머지는 채운다.
    필드가 비면 해당 소스 파서(주석 표시)를 먼저 점검할 것.
"""

import os
import io
import re
import sys
import json
import time
import difflib
import zipfile
import datetime as dt
import xml.etree.ElementTree as ET

import requests

# ──────────────────────────────────────────────────────────────────────────
# 0. 설정
# ──────────────────────────────────────────────────────────────────────────
DART_KEY = os.environ.get("DART_API_KEY", "").strip()

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Referer": "https://m.stock.naver.com/"}

TIMEOUT = 12
DISC_DAYS = 30            # 최근 공시 조회 범위(일)
NEWS_COUNT = 4           # 회사당 최종 노출 뉴스 개수
NEWS_FETCH = 15          # 정렬·중복제거 전 넉넉히 받아올 원본 뉴스 개수
DISC_COUNT = 4           # 회사당 공시 개수
OUT_PATH = os.path.join(os.path.dirname(__file__), "data.json")
NOTES_PATH = os.path.join(os.path.dirname(__file__), "notes.json")  # 수기 코멘트(선택)

# 대상 5개사 — 코드 기준. name/tag/biz 는 표시용 기본값(수집 실패 시 그대로 사용).
COMPANIES = [
    {"code": "002070", "market": "KOSPI",  "name": "비비안",
     "tag": "여성 이너웨어 · 자산가치주",
     "biz": "여성 이너웨어(속옷) 제조·유통 · 브랜드 비비안/샌디즈"},
    {"code": "078860", "market": "KOSDAQ", "name": "아이오케이이엔엠",
     "tag": "엔터·미디어 · 구조조정주",
     "biz": "종합 엔터테인먼트·미디어 · 아티스트/공연 IP·AI 쇼비즈니스"},
    {"code": "025620", "market": "KOSPI",  "name": "차AI헬스케어",
     "tag": "헬스케어 · AI/차바이오 테마",
     "biz": "AI 기반 헬스케어·코스메틱 · 차바이오텍(차케어스) 편입"},
    {"code": "307870", "market": "KOSDAQ", "name": "비투엔",
     "tag": "AI·빅데이터 · 데이터/스테이블코인 테마",
     "biz": "AI·빅데이터 데이터 솔루션/컨설팅(B2B·공공) · 스테이블코인 결제 진출"},
    {"code": "016670", "market": "KOSDAQ", "name": "디모아",
     "tag": "IT·클라우드 · SI",
     "biz": "IT 솔루션·클라우드 인프라 공급 · 시스템 통합(SI)"},
]

# ──────────────────────────────────────────────────────────────────────────
# 1. 공시 자동 해설 사전 (focus.html DISCLOSURE_DICT 와 동일 — 서버측 사전 부착)
# ──────────────────────────────────────────────────────────────────────────
DISCLOSURE_DICT = [
    ("무상감자",   "자본금을 줄여 누적 결손을 털어내는 조치. 주식 수가 줄어듭니다(예: 10:1). 재무구조 개선 목적이지만 주주가치 훼손 신호일 수 있어 주의."),
    ("유상증자",   "새 주식을 발행해 외부 자금을 조달. 주식 수가 늘어 기존 주주 지분이 희석될 수 있습니다. 자금 용도(운영·투자·차입금 상환)를 확인하세요."),
    ("전환사채",   "나중에 주식으로 바꿀 수 있는 채권(CB)을 발행해 자금 조달. 향후 주식 수 증가 가능성이 있습니다."),
    ("신주인수권", "정해진 가격에 새 주식을 살 수 있는 권리가 붙은 채권(BW) 발행. 향후 주식 수 증가 요인."),
    ("최대주주",   "회사의 실질적 주인(최대주주)이 바뀌거나 지분이 변동됐다는 신고. 경영권·향후 전략의 큰 변수."),
    ("대량보유",   "지분 5% 이상 주주의 보유 지분이 변동됐다는 신고(누가 사고 팔았는지)."),
    ("임원ㆍ주요주주", "경영진·대주주의 자기회사 주식 보유 수량 변동 신고."),
    ("임원·주요주주",  "경영진·대주주의 자기회사 주식 보유 수량 변동 신고."),
    ("소유상황보고",   "경영진·대주주의 자기회사 주식 보유 수량 변동 신고."),
    ("타법인",     "다른 회사의 지분·증권을 사거나 판다는 결정(M&A·사업 확장·지분 정리)."),
    ("단일판매",   "단일 규모의 대형 공급·납품 계약 체결. 매출로 이어지는 실적 재료."),
    ("공급계약",   "대형 공급·납품 계약 체결. 매출로 이어지는 실적 재료."),
    ("수주",       "대형 수주 확보. 향후 매출 반영 여부와 규모를 확인."),
    ("감사보고서", "회계법인이 재무제표를 검증한 결과(적정/한정/의견거절). '적정'이 아니면 상장 리스크 신호."),
    ("사업보고서", "1년 치 실적·재무·사업 현황을 담은 정기 보고서(연간)."),
    ("반기보고서", "상반기 실적·재무를 담은 정기 보고서."),
    ("분기보고서", "분기(3개월) 실적·재무를 담은 정기 보고서."),
    ("주요사항보고", "합병·분할·증자·감자 등 회사에 중대한 변화가 생겼다는 보고."),
    ("합병",       "다른 회사와 합쳐지는 결정. 사업·지분 구조가 크게 바뀝니다."),
    ("분할",       "사업 일부를 떼어 별도 회사로 나누는 결정."),
    ("횡령",       "회사 자금 횡령·배임 관련 사안. 상장 유지·신뢰에 직접적 악재."),
    ("소송",       "회사가 얽힌 소송의 제기·판결. 규모·승패에 따라 재무 영향."),
    ("자율공시",   "의무는 아니지만 회사가 자발적으로 알리는 사항(ESG·IR 등)."),
    ("현금ㆍ현물배당", "주주에게 배당을 지급한다는 결정."),
    ("배당",       "주주에게 이익을 나눠주는 배당 관련 결정."),
    ("자기주식",   "회사가 자사 주식을 사거나(취득) 파는(처분) 결정. 취득은 대개 주가 방어 신호."),
    ("거래정지",   "주식 거래가 일시 정지됨. 사유(감자·감사의견 등)를 반드시 확인."),
    ("관리종목",   "상장폐지 우려 등으로 관리종목 지정. 고위험 신호."),
]

def explain_disclosure(title: str) -> str:
    for kw, desc in DISCLOSURE_DICT:
        if kw in title:
            return desc
    return "정기·수시 공시 사항. 원문에서 상세 내용을 확인하세요."

# ──────────────────────────────────────────────────────────────────────────
# 2. 뉴스 규칙기반 한 줄 태그 사전 (제목 키워드 → 쉬운 요약. LLM 미사용)
#    위에서부터 순서대로 매칭되므로 더 구체적인 키워드를 먼저 둔다.
# ──────────────────────────────────────────────────────────────────────────
NEWS_TAG_DICT = [
    ("무상감자",  "자본구조 재편(감자) 이슈. 주식 수 급변에 주의."),
    ("유상증자",  "자금 조달(증자) 재료. 규모·용도와 지분 희석 여부 확인."),
    ("전환사채",  "메자닌(CB) 발행 관련. 향후 주식 수 증가 가능성."),
    ("최대주주",  "지배구조 변동. 경영권·전략 방향의 큰 변수."),
    ("인수",      "M&A·지분 인수 이슈. 사업 확장/재편 신호."),
    ("지분",      "지분 취득·매각 이슈. 지배구조·투자 방향 확인."),
    ("계약",      "공급·납품 계약 재료. 실제 매출 반영 규모 확인."),
    ("수주",      "수주 확보 재료. 매출 기여 시점·규모 확인."),
    ("협업",      "제휴·협업 뉴스. 테마성 강할 수 있어 실적 기여는 별도 확인."),
    ("맞손",      "제휴·협업 뉴스. 실제 계약·매출 전환 여부 확인."),
    ("제휴",      "제휴 뉴스. 실적 기여 여부를 숫자로 확인 필요."),
    ("흑자",      "실적 개선(흑자) 이슈. 지속성 여부 확인."),
    ("적자",      "실적 부진(적자) 이슈. 원인·개선 계획 확인."),
    ("실적",      "실적 관련 뉴스. 매출·이익 방향 확인."),
    ("감사의견",  "감사의견 관련. '적정' 여부가 상장 유지에 중요."),
    ("론칭",      "신규 브랜드·제품 출시. 실제 매출 전환은 추적 필요."),
    ("출시",      "신제품·서비스 출시. 매출 기여 여부 확인."),
    ("진출",      "신사업·신시장 진출. 테마성 vs 실적 기여 구분 필요."),
    ("확장",      "사업 확장 뉴스. 자금·수익 모델 확인."),
    ("독립",      "그룹 분리·독립 경영 이슈. 자금 여력·전략 방향 확인."),
    ("해체",      "그룹 해체 관련. 계열 재편·최대주주 변동 맥락."),
]

def tag_news(title: str) -> str:
    for kw, desc in NEWS_TAG_DICT:
        if kw in title:
            return desc
    return "관련 뉴스. 실제 실적·공시로 이어지는지 확인 필요."

# ──────────────────────────────────────────────────────────────────────────
# 3. 유틸
# ──────────────────────────────────────────────────────────────────────────
def _get(url, **kw):
    kw.setdefault("headers", UA)
    kw.setdefault("timeout", TIMEOUT)
    r = requests.get(url, **kw)
    r.raise_for_status()
    return r

def to_int(s):
    """'1,234', '-1,234', '' → int (실패 시 None)."""
    if s is None:
        return None
    s = str(s).replace(",", "").strip()
    if s in ("", "-", "N/A"):
        return None
    try:
        return int(float(s))
    except ValueError:
        return None

def won_to_eok(v):
    """원(int) → '1,180억' 문자열. None → '-'."""
    if v is None:
        return "-"
    eok = v / 1e8
    return f"{round(eok):,}억"

def yoy_pct(cur, prev):
    """전년대비 % (금액용). 부호 전환/0분모 → None('전환')."""
    if cur is None or prev is None or prev == 0:
        return None
    if (cur >= 0) != (prev >= 0):
        return None  # 흑자↔적자 전환은 %가 무의미 → '전환' 표기
    return round((cur - prev) / abs(prev) * 100, 1)

def today():
    return dt.date.today()

# ──────────────────────────────────────────────────────────────────────────
# 4. DART — corp_code 매핑 (종목코드 → corp_code)
# ──────────────────────────────────────────────────────────────────────────
def load_corp_map():
    """corpCode.xml(zip) 다운로드 → {stock_code: corp_code}."""
    url = f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={DART_KEY}"
    r = _get(url)
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    xml = zf.read(zf.namelist()[0])
    root = ET.fromstring(xml)
    m = {}
    for item in root.iter("list"):
        stock = (item.findtext("stock_code") or "").strip()
        corp = (item.findtext("corp_code") or "").strip()
        if stock and corp:
            m[stock] = corp
    return m

# ──────────────────────────────────────────────────────────────────────────
# 5. DART — 최근 공시
# ──────────────────────────────────────────────────────────────────────────
def fetch_disclosures(corp_code):
    end = today()
    bgn = end - dt.timedelta(days=DISC_DAYS)
    url = "https://opendart.fss.or.kr/api/list.json"
    params = {
        "crtfc_key": DART_KEY, "corp_code": corp_code,
        "bgn_de": bgn.strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d"),
        "page_count": 100,
    }
    r = _get(url, params=params)
    js = r.json()
    out = []
    if js.get("status") == "000":
        for it in js.get("list", []):
            nm = (it.get("report_nm") or "").strip()
            d = it.get("rcept_dt", "")  # YYYYMMDD
            date = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d
            rcept_no = (it.get("rcept_no") or "").strip()
            url = (f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
                   if rcept_no else "")
            out.append({"date": date, "title": nm,
                        "plain": explain_disclosure(nm), "url": url})
            if len(out) >= DISC_COUNT:
                break
    return out

# ──────────────────────────────────────────────────────────────────────────
# 6. DART — 재무(매출/영업이익/순이익/부채·자본/유동자산·부채)
# ──────────────────────────────────────────────────────────────────────────
def _period_candidates():
    """오늘 기준 최신 보고서부터 시도할 (사업연도, 보고서코드) 목록."""
    y = today().year
    # 11013:1Q, 11012:반기, 11014:3Q, 11011:사업보고서(연간)
    return [
        (y, "11014"), (y, "11012"), (y, "11013"), (y - 1, "11011"),
        (y - 1, "11014"), (y - 1, "11012"), (y - 1, "11013"), (y - 2, "11011"),
    ]

# 계정과목명 매칭 후보(정확 일치 우선)
_ACC = {
    "매출액":   ["매출액", "수익(매출액)", "영업수익", "매출"],
    "영업이익": ["영업이익", "영업이익(손실)"],
    "순이익":   ["당기순이익", "당기순이익(손실)", "분기순이익", "반기순이익",
                "당기순이익(손실)"],
    "부채총계": ["부채총계"],
    "자본총계": ["자본총계"],
    "유동자산": ["유동자산"],
    "유동부채": ["유동부채"],
}

def _pick(rows, keys, sj_allow):
    """rows에서 keys 중 하나와 정확 일치하는 계정의 (당기, 전기) 금액 반환.

    1분기 보고서(11013)는 손익계산서에 누적 전기 비교값인 frmtrm_amount가
    없고 전년 동일분기 단일값인 frmtrm_q_amount만 내려온다(1분기는 누적=단일
    분기이므로 동일한 의미). frmtrm_amount가 없으면 frmtrm_q_amount로 폴백.
    """
    for want in keys:
        for r in rows:
            if r.get("sj_div") not in sj_allow:
                continue
            nm = (r.get("account_nm") or "").strip()
            if nm == want:
                prev_raw = r.get("frmtrm_amount")
                if prev_raw is None or str(prev_raw).strip() == "":
                    prev_raw = r.get("frmtrm_q_amount")
                return (to_int(r.get("thstrm_amount")), to_int(prev_raw))
    return (None, None)

def fetch_financials(corp_code):
    """DART fnlttSinglAcntAll 로 재무 수집. CFS(연결)→OFS(별도) 폴백."""
    url = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
    for (yr, rc) in _period_candidates():
        for fs in ("CFS", "OFS"):
            try:
                params = {"crtfc_key": DART_KEY, "corp_code": corp_code,
                          "bsns_year": str(yr), "reprt_code": rc, "fs_div": fs}
                js = _get(url, params=params).json()
            except Exception:
                continue
            if js.get("status") != "000":
                continue
            rows = js.get("list", [])
            if not rows:
                continue
            rev_c, rev_p = _pick(rows, _ACC["매출액"], ("IS", "CIS"))
            if rev_c is None:  # 매출 못 찾으면 다음 후보로
                continue
            op_c,  op_p  = _pick(rows, _ACC["영업이익"], ("IS", "CIS"))
            ni_c,  ni_p  = _pick(rows, _ACC["순이익"],   ("IS", "CIS"))
            li_c,  li_p  = _pick(rows, _ACC["부채총계"], ("BS",))
            eq_c,  eq_p  = _pick(rows, _ACC["자본총계"], ("BS",))
            ca_c,  ca_p  = _pick(rows, _ACC["유동자산"], ("BS",))
            cl_c,  cl_p  = _pick(rows, _ACC["유동부채"], ("BS",))

            def ratio(n, d):
                return round(n / d * 100) if (n is not None and d) else None
            debt_c = ratio(li_c, eq_c); debt_p = ratio(li_p, eq_p)
            cur_c  = ratio(ca_c, cl_c); cur_p  = ratio(ca_p, cl_p)
            roe = (round(ni_c / eq_c * 100, 1)
                   if (ni_c is not None and eq_c) else None)

            fin = [
                {"k": "매출액",   "v": won_to_eok(rev_c), "yoy": yoy_pct(rev_c, rev_p)},
                {"k": "영업이익", "v": won_to_eok(op_c),  "yoy": yoy_pct(op_c, op_p)},
                {"k": "당기순이익", "v": won_to_eok(ni_c), "yoy": yoy_pct(ni_c, ni_p)},
                {"k": "부채비율", "v": f"{debt_c}%" if debt_c is not None else "-",
                 "yoy": (round(debt_c - debt_p, 1) if (debt_c is not None and debt_p is not None) else None)},
                {"k": "유동비율", "v": f"{cur_c}%" if cur_c is not None else "-",
                 "yoy": (round(cur_c - cur_p, 1) if (cur_c is not None and cur_p is not None) else None)},
            ]
            return {
                "fin": fin,
                "period": f"{yr} {rc} ({fs})",
                "debt": f"{debt_c}%" if debt_c is not None else None,
                "current": f"{cur_c}%" if cur_c is not None else None,
                "ROE": f"{roe}%" if roe is not None else None,
            }
    return None

# ──────────────────────────────────────────────────────────────────────────
# 7. 네이버 모바일 금융 — 시세/등락/PER/PBR/외국인 (키 불필요, 비공식 JSON)
#    구조가 바뀌면 여기(basic / integration 파서)를 먼저 점검.
# ──────────────────────────────────────────────────────────────────────────
def fetch_naver_quote(code):
    out = {"price": None, "changePct": None,
           "PER": None, "PBR": None, "foreign": None, "name": None}
    # 7-1. 시세/등락/종목명
    try:
        js = _get(f"https://m.stock.naver.com/api/stock/{code}/basic").json()
        out["name"] = js.get("stockName") or js.get("stockNameEng")
        out["price"] = to_int(js.get("closePrice"))
        # 등락률: fluctuationsRatio(부호 포함 문자열) 우선, 없으면 계산
        fr = js.get("fluctuationsRatio")
        if fr not in (None, ""):
            try:
                out["changePct"] = round(float(str(fr).replace(",", "")), 2)
            except ValueError:
                pass
        # 하락 시 부호 보정: compareToPreviousPrice.code 02=상승,05=하락
        cmp = (js.get("compareToPreviousPrice") or {})
        code2 = cmp.get("code")
        if out["changePct"] is not None and code2 in ("05", "03"):
            out["changePct"] = -abs(out["changePct"])
        elif out["changePct"] is not None and code2 in ("02", "01"):
            out["changePct"] = abs(out["changePct"])
    except Exception as e:
        print(f"  [warn] naver basic {code}: {e}", file=sys.stderr)

    # 7-2. PER/PBR/외국인 — integration.totalInfos
    #   응답 항목은 식별자가 "code"(예: per/pbr/foreignRate), 표시 라벨이
    #   "key"(예: "PER"/"외국인비율")에 들어있다. "name" 필드는 존재하지 않음.
    try:
        js = _get(f"https://m.stock.naver.com/api/stock/{code}/integration").json()
        for it in js.get("totalInfos", []):
            cid = (it.get("code") or "").lower()
            label = (it.get("key") or "")
            val = (it.get("value") or "").strip()
            if val in ("", "-", "N/A"):
                continue
            if cid == "per" or label == "PER":
                out["PER"] = val.replace("배", "")
            elif cid == "pbr" or label == "PBR":
                out["PBR"] = val.replace("배", "")
            elif "foreign" in cid or "외국인" in label:
                out["foreign"] = val if "%" in val else val + "%"
    except Exception as e:
        print(f"  [warn] naver integration {code}: {e}", file=sys.stderr)
    return out

# ──────────────────────────────────────────────────────────────────────────
# 8. 네이버 종목 뉴스 — 제목 기반 규칙 요약 (키 불필요)
# ──────────────────────────────────────────────────────────────────────────
def _strip_tags(s):
    s = re.sub(r"<[^>]+>", "", s or "")
    return (s.replace("&quot;", '"').replace("&amp;", "&")
             .replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'")).strip()

def _norm_date(s):
    s = str(s or "")
    m = re.search(r"(\d{4})[-.\s]?(\d{2})[-.\s]?(\d{2})", s)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else s[:10]

def _norm_title_key(title):
    """중복 비교용 정규화: 앞쪽 매체 태그([ET특징주] 등) 제거 + 공백·특수문자 제거."""
    t = re.sub(r"^\[[^\]]{1,12}\]", "", title or "")
    t = re.sub(r"[^\w가-힣]", "", t)
    return t.strip().lower()

def _is_dup_title(a, b):
    """정규화된 제목 둘이 사실상 같은 기사(동일/유사 제목)인지 판단."""
    if not a or not b:
        return False
    if a == b:
        return True
    # 서로 다른 매체가 같은 사건을 다르게 표현해도 앞부분이 거의 같거나
    # 전체적으로 매우 유사하면 중복으로 간주.
    prefix_len = min(len(a), len(b), 14)
    if prefix_len >= 8 and a[:prefix_len] == b[:prefix_len]:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.72

def fetch_naver_news(code):
    out = []
    try:
        js = _get(f"https://m.stock.naver.com/api/news/stock/{code}"
                  f"?pageSize={NEWS_FETCH}&page=1&clusterId=").json()
        # 응답은 [ {items:[...]}, ... ] 또는 [ {..기사..}, ... ] 형태 모두 대응
        flat = []
        data = js if isinstance(js, list) else js.get("items", js)
        for grp in (data or []):
            if isinstance(grp, dict) and isinstance(grp.get("items"), list):
                flat.extend(grp["items"])
            elif isinstance(grp, dict):
                flat.append(grp)

        items = []
        for it in flat:
            title = _strip_tags(it.get("title"))
            if not title:
                continue
            raw_dt = str(it.get("datetime") or "")  # 예: "202606231303" (YYYYMMDDHHmm)
            url = (it.get("mobileNewsUrl") or "").strip()
            if not url:
                oid, aid = it.get("officeId"), it.get("articleId")
                if oid and aid:
                    url = f"https://n.news.naver.com/mnews/article/{oid}/{aid}"
            items.append({
                "date": _norm_date(raw_dt or it.get("officeName") or ""),
                "title": title, "sum": tag_news(title), "url": url,
                "_sort": raw_dt,
            })

        # 최신순 정렬 — datetime이 YYYYMMDDHHmm 문자열이라 그대로 내림차순 비교 가능.
        items.sort(key=lambda x: x["_sort"], reverse=True)

        # 같은 사건을 다룬 중복(유사) 기사 제거 — 먼저 온(최신) 기사를 남긴다.
        seen_keys = []
        for it in items:
            key = _norm_title_key(it["title"])
            if any(_is_dup_title(key, s) for s in seen_keys):
                continue
            seen_keys.append(key)
            it.pop("_sort", None)
            out.append(it)
            if len(out) >= NEWS_COUNT:
                break
    except Exception as e:
        print(f"  [warn] naver news {code}: {e}", file=sys.stderr)
    return out

# ──────────────────────────────────────────────────────────────────────────
# 9. 회사별 조립
# ──────────────────────────────────────────────────────────────────────────
def load_notes():
    """notes.json = { "코드": {"finNote": "...", "summary": "..."} } (선택)."""
    try:
        with open(NOTES_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def build_company(base, corp_map, notes):
    code = base["code"]
    print(f"[collect] {base['name']} ({code})")
    corp = corp_map.get(code)

    fin_block = fetch_financials(corp) if corp else None
    disc = fetch_disclosures(corp) if corp else []
    quote = fetch_naver_quote(code)
    news = fetch_naver_news(code)

    metrics = {
        "ROE": (fin_block or {}).get("ROE"),
        "PBR": quote.get("PBR"),
        "PER": quote.get("PER"),
        "debt": (fin_block or {}).get("debt"),
        "current": (fin_block or {}).get("current"),
        "foreign": quote.get("foreign"),
    }
    metrics = {k: v for k, v in metrics.items() if v}  # None 제거 → 칩 자동 생략

    return {
        "name": base["name"], "code": code, "market": base["market"],
        "tag": base["tag"], "biz": base["biz"],
        "price": quote.get("price") or 0,
        "changePct": quote.get("changePct") if quote.get("changePct") is not None else 0,
        "metrics": metrics,
        "fin": (fin_block or {}).get("fin", []),
        # finNote / summary 는 notes.json 의 수기 코멘트에서 병합(자동 갱신에도 유지).
        "finNote": (notes.get(code) or {}).get("finNote", ""),
        "disc": disc,
        "news": news,
        "summary": (notes.get(code) or {}).get("summary", ""),
    }

# ──────────────────────────────────────────────────────────────────────────
# 10. main
# ──────────────────────────────────────────────────────────────────────────
def main():
    if not DART_KEY:
        print("ERROR: 환경변수 DART_API_KEY 가 없습니다.", file=sys.stderr)
        sys.exit(1)

    try:
        corp_map = load_corp_map()
        print(f"[corpCode] {len(corp_map)}개 매핑 로드")
    except Exception as e:
        print(f"ERROR: corpCode 로드 실패: {e}", file=sys.stderr)
        corp_map = {}

    notes = load_notes()
    items = []
    for base in COMPANIES:
        try:
            items.append(build_company(base, corp_map, notes))
        except Exception as e:
            print(f"  [error] {base['name']}: {e}", file=sys.stderr)
        time.sleep(0.4)  # 소스 예의상 간격

    payload = {
        "asOf": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": "DART · 네이버",
        "items": items,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[done] {OUT_PATH} 저장 ({len(items)}개사)")


if __name__ == "__main__":
    main()
