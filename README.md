# KYY 종목 집중 모니터 (FOCUS)

구 쌍방울그룹 계열 5개사(비비안·아이오케이이엔엠·차AI헬스케어·비투엔·디모아)의
**재무상태 + 최근 공시(쉬운 해설) + 최근 뉴스(규칙기반 한 줄 요약)** 를 한 페이지로 보여주는
정적 웹페이지 + 자동 수집기.

- 배포: **GitHub Pages** (서버 없음)
- 데이터 갱신: **GitHub Actions cron (매시 정각)** — DART OpenAPI + 네이버 모바일 금융
- 종목 조회는 **종목코드 기준**(5개사 모두 사명 변경 이력 있어 이름 매칭 금지)

## 파일 구조

```
kyy-focus/                     ← 신규 전용 public repo
├── index.html                 정적 화면. ./data.json 을 fetch 해서 렌더
├── fetch_focus.py             수집기. data.json 생성 (DART 키 1개만 필요)
├── notes.json                 수기 코멘트(finNote/summary). 자동 갱신에도 유지
├── requirements.txt           requests
├── data.json                  (자동 생성물. 첫 실행 후 커밋됨)
└── .github/workflows/update.yml   매시 정각 cron + 수동 실행
```

## 최초 셋업 (한 번만)

1. **DART API 키 발급** — https://opendart.fss.or.kr → 인증키 신청/관리 (무료).

2. **새 public repo 생성** (예: `kyy-focus`).
   - Pages 무료 + Actions 무제한 조건을 위해 **반드시 public**.
   - 이 폴더의 파일 전부 업로드(`data.json` 제외 — 첫 실행 때 자동 생성).

3. **Secret 등록** — repo → **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `DART_API_KEY`  /  Value: 발급받은 키
   - (키는 코드·커밋에 절대 넣지 않는다.)

4. **Pages 활성화** — **Settings → Pages → Build and deployment**
   - Source: **Deploy from a branch** → Branch: `main` / `/(root)` → Save
   - 배포 URL: `https://<계정>.github.io/kyy-focus/`

5. **첫 데이터 생성** — **Actions 탭 → update-focus-data → Run workflow** (수동 1회 실행).
   - `fetch_focus.py` 가 돌아 `data.json` 을 만들고 커밋 → Pages 자동 갱신.
   - 이후에는 매시 정각에 자동 실행.

> 첫 실행 전(=`data.json` 없음)에는 페이지가 **샘플 데이터 + 상단 경고줄**로 뜬다.
> 정상 동작이며, 첫 실행이 끝나면 실데이터로 바뀌고 경고줄은 사라진다.

## 갱신 주기

- `.github/workflows/update.yml` 의 `cron: "0 * * * *"` → **매시 정각**.
- GitHub cron은 **정각(0분)이 가장 혼잡**해 수 분 지연될 수 있다(정상). 더 안정적으로
  돌리려면 분을 뒤로 미룬다: 예) `7 * * * *`.
- **`data.json` 이 실제로 바뀔 때만 커밋**하므로, 변화 없는 시각에는 커밋이 쌓이지 않는다.

## 수기 코멘트 편집 (`notes.json`)

`finNote`(재무 한 줄 평)와 `summary`(🟢 쉬운 종합 요약)는 규칙기반으로 자동 생성되지 않는다.
`notes.json` 에서 **종목코드별로** 직접 편집하면 매시 자동 갱신에도 유지된다.
비워 두면 해당 블록은 화면에서 자동으로 숨겨진다.

```json
{ "002070": { "finNote": "...", "summary": "..." } }
```

## 데이터가 비어 보일 때 점검 포인트

네이버 비공식 JSON과 DART 계정과목명은 종종 바뀐다. 특정 필드만 `-`/빈칸으로 나오면
`fetch_focus.py` 에서 아래 위치를 먼저 본다(Actions 로그의 `[warn]` 줄도 참고):

- **시세·등락·PER·PBR·외국인** → `fetch_naver_quote()` (`/basic`, `/integration` 파서)
- **최근 뉴스** → `fetch_naver_news()` (`/news/stock/{code}` 응답 구조)
- **매출/영업이익/순이익/부채·유동비율** → `fetch_financials()` + `_ACC` 계정과목명 매칭,
  `_period_candidates()` 의 (연도, 보고서코드) 후보. 연결(CFS)↔별도(OFS) 폴백 포함.

## 로컬 실행 (수동 갱신도 가능)

```powershell
# Windows PowerShell
$env:DART_API_KEY = "발급키"
pip install -r requirements.txt
python fetch_focus.py        # data.json 생성
git add data.json ; git commit -m "update data" ; git push
```

---

공시 해설·뉴스 태그는 규칙 기반 자동 변환이며 투자 판단의 근거가 아닌 참고용입니다.
