# 대만 월매출 트래커 (MOPS Monthly Revenue Tracker)

대만 상장사는 **매월 10일까지 전월 매출을 공시**하며, MOPS(公開資訊觀測站)가 시장별 월간 집계표를 정적 HTML로 제공합니다. 이 프로젝트는 그 파일을 자동 수집 → 파생지표 계산 → 정적 대시보드(GitHub Pages)로 만드는 파이프라인입니다.

```
watchlist.json ─→ collector.py ─→ data/tw_revenue.parquet(.csv)
                                       │
                                  analyzer.py ─→ docs/data.js ─→ docs/index.html (대시보드)
```

## 데이터 소스

```
https://mops.twse.com.tw/nas/t21/{market}/t21sc03_{ROC연도}_{월}_{suffix}.html
```
- `market`: `sii`(TWSE 상장) / `otc`(TPEx 상장) — 필요 시 collector.py의 `MARKETS`에 `rotc`(흥궤), `pub` 추가 가능
- `ROC연도` = 서기 − 1911 (2026 → 115)
- `suffix`: `0` 국내기업, `1` 해외(-KY)기업 — 둘 다 수집 후 병합
- 금액 단위: **仟元(천 NTD)**, 인코딩은 Big5(일부 UTF-8, 자동 감지)
- 접속 차단 시 구버전 미러 `mopsov.twse.com.tw`로 자동 폴백

## 빠른 시작

```bash
pip install pandas lxml pyarrow requests

# 1) 2019년 1월부터 백필 (이후에는 인자 없이 실행하면 증분 수집)
python collector.py --backfill 2019-01

# 2) 지표 계산 + 대시보드 데이터 생성
python analyzer.py

# 3) 로컬 확인
open docs/index.html          # 또는 python -m http.server -d docs
```

네트워크 없이 대시보드만 먼저 보려면: `python make_sample_data.py && python analyzer.py` (합성 샘플 6종목)

## 종목 관리

`watchlist.json`에 종목을 추가/삭제하면 수집·대시보드에 자동 반영됩니다. `--all-companies` 옵션으로 전 종목(약 1,800개)을 저장할 수도 있습니다(파일 크기 주의).

## 파생지표 (analyzer.py)

MOPS 공시치의 반올림 오차를 피하기 위해 원천 매출액에서 재계산합니다.

| 지표 | 정의 |
|---|---|
| YoY / MoM | 전년동월·전월 대비 증감률 |
| 연누계 YoY | 당해 1월~해당월 누계 vs 전년 동기 |
| 3M MA | 3개월 이동평균 |
| TTM / TTM YoY | 최근 12개월 합계 및 그 증감률 |
| ★ 사상최대 | 해당 월매출이 상장 후 최대치 경신 여부 |

## 자동화 (GitHub Actions + Pages)

1. 이 폴더를 GitHub 저장소로 푸시
2. Settings → Pages → Source를 `main` 브랜치 `/docs` 폴더로 지정
3. `.github/workflows/update.yml`이 매월 5~13일 KST 21:00에 증분 수집 → `docs/data.js` 갱신 → 자동 커밋
4. `https://<계정>.github.io/<저장소>/` 에서 대시보드 접속

## 주의사항

- 요청 간 3초+ 대기(기본값)를 유지하세요. 과도한 요청 시 MOPS가 IP를 일시 차단합니다.
- 2월 매출은 춘절(설) 효과로 MoM 급감이 정상이므로 YoY·누계 YoY 중심으로 해석하세요.
- 매출은 연결 기준(자회사 없는 경우 개별)이며, TSMC처럼 달러 매출 비중이 큰 기업은 NTD 환율 효과가 YoY에 섞입니다.
