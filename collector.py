#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
collector.py (v2) - Taiwan monthly revenue collector
=====================================================
데이터 소스 2개를 순서대로 시도합니다:
  1) MOPS 정적 파일  - 로컬 PC에서 잘 작동 (해외 서버/클라우드에서는 차단됨)
  2) FinMind API     - 무료 오픈데이터, GitHub Actions에서 작동 (토큰 불필요, 300req/hr)
     https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockMonthRevenue

사용법:
  python collector.py                     # 누락 월 자동 수집 (기본 2019-01부터)
  python collector.py --backfill 2015-01  # 특정 시점부터 백필
  python collector.py --source finmind    # FinMind만 사용
출력: data/tw_revenue.parquet, data/tw_revenue.csv
"""
import argparse, io, json, random, sys, time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PARQUET_PATH = DATA_DIR / "tw_revenue.parquet"
CSV_PATH = DATA_DIR / "tw_revenue.csv"
WATCHLIST_PATH = BASE_DIR / "watchlist.json"

HOSTS = ["https://mops.twse.com.tw", "https://mopsov.twse.com.tw"]
MARKETS = ["sii", "otc"]
SUFFIXES = ["0", "1"]
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
FINMIND_TOKEN = ""  # 선택사항: finmindtrade.com 가입 후 토큰을 넣으면 한도 600/hr로 상승

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0 Safari/537.36",
           "Accept-Language": "zh-TW,zh;q=0.9"}
COLMAP = {"公司代號": "code", "公司名稱": "name_zh", "當月營收": "revenue",
          "上月營收": "rev_prev_month", "去年當月營收": "rev_last_year",
          "上月比較增減(%)": "mom_pct", "去年同月增減(%)": "yoy_pct",
          "當月累計營收": "cum_revenue", "去年累計營收": "cum_last_year",
          "前期比較增減(%)": "cum_yoy_pct", "備註": "note"}


# ---------------- 공통 유틸 ----------------
def load_watchlist():
    with open(WATCHLIST_PATH, encoding="utf-8") as f:
        return {c["code"]: c for c in json.load(f)["companies"]}

def load_existing():
    return pd.read_parquet(PARQUET_PATH) if PARQUET_PATH.exists() else pd.DataFrame()

def month_range(start, end):
    ys, ms = map(int, start.split("-")); ye, me = map(int, end.split("-"))
    y, m = ys, ms
    while (y, m) <= (ye, me):
        yield y, m
        m += 1
        if m > 12: y, m = y + 1, 1

def latest_reportable_month():
    t = date.today(); y, m = t.year, t.month
    m -= 1 if t.day >= 10 else 2
    while m <= 0: m += 12; y -= 1
    return f"{y:04d}-{m:02d}"


# ---------------- 소스 1: MOPS ----------------
def mops_urls(year, month, market):
    y = year - 1911
    for host in HOSTS:
        for sfx in SUFFIXES:
            yield f"{host}/nas/t21/{market}/t21sc03_{y}_{month}_{sfx}.html", sfx

def parse_mops_html(text):
    try:
        tables = pd.read_html(io.StringIO(text))
    except ValueError:
        return pd.DataFrame()
    frames = []
    for t in tables:
        if isinstance(t.columns, pd.MultiIndex):
            t.columns = t.columns.get_level_values(-1)
        t.columns = [str(c).strip() for c in t.columns]
        if "公司代號" not in t.columns:
            hit = t.index[t[t.columns[0]].astype(str).str.strip() == "公司代號"]
            if len(hit) == 0: continue
            t.columns = [str(c).strip() for c in t.iloc[hit[0]]]
            t = t.iloc[hit[0] + 1:]
        if "當月營收" in t.columns: frames.append(t)
    if not frames: return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True).rename(columns=COLMAP)
    df["code"] = df["code"].astype(str).str.strip()
    df = df[df["code"].str.fullmatch(r"\d{4,6}")]
    for c in ["revenue", "mom_pct", "yoy_pct", "cum_revenue", "cum_yoy_pct"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", ""), errors="coerce")
    df = df[df["revenue"].notna()]
    keep = [c for c in ["code", "name_zh", "revenue"] if c in df.columns]
    return df[keep].reset_index(drop=True)

def fetch_mops_month(year, month, market, session):
    parts, seen = [], set()
    for url, sfx in mops_urls(year, month, market):
        if sfx in seen: continue
        try:
            r = session.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200 or len(r.content) < 500: continue
            for enc in ("big5", "utf-8"):
                text = r.content.decode(enc, errors="replace")
                if "公司代號" in text: break
            df = parse_mops_html(text)
            if not df.empty:
                parts.append(df); seen.add(sfx)
        except requests.RequestException:
            pass
        if len(seen) == len(SUFFIXES): break
    if not parts: return pd.DataFrame()
    out = pd.concat(parts, ignore_index=True).drop_duplicates("code")
    out.insert(0, "date", f"{year:04d}-{month:02d}")
    out["market"] = market
    return out

def collect_mops(targets, watch, sleep_s):
    """MOPS에서 대상 월 수집. 초반 연속 실패 시 차단으로 판단하고 조기 포기."""
    session, frames, consecutive_empty = requests.Session(), [], 0
    for i, (y, m) in enumerate(targets):
        got = False
        for market in MARKETS:
            df = fetch_mops_month(y, m, market, session)
            if not df.empty:
                frames.append(df); got = True
            time.sleep(sleep_s + random.uniform(0, 1))
        print(f"  [MOPS] {y}-{m:02d}: {'OK' if got else 'no data'}", flush=True)
        consecutive_empty = 0 if got else consecutive_empty + 1
        if consecutive_empty >= 3 and not frames:
            print("  [MOPS] 3개월 연속 응답 없음 -> 차단으로 판단, FinMind로 전환", flush=True)
            return pd.DataFrame()
    if not frames: return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    return df[df["code"].isin(watch)]


# ---------------- 소스 2: FinMind ----------------
def collect_finmind(codes, start_month, watch):
    """FinMind TaiwanStockMonthRevenue를 종목별로 조회 (종목당 1 request)."""
    headers = {"User-Agent": HEADERS["User-Agent"]}
    if FINMIND_TOKEN:
        headers["Authorization"] = f"Bearer {FINMIND_TOKEN}"
    start_date = f"{start_month}-01"
    rows = []
    for i, code in enumerate(codes):
        params = {"dataset": "TaiwanStockMonthRevenue", "data_id": code,
                  "start_date": start_date}
        try:
            r = requests.get(FINMIND_URL, params=params, headers=headers, timeout=30)
            j = r.json()
        except Exception as e:
            print(f"  [FinMind] {code}: 요청 실패 ({e})", file=sys.stderr); continue
        if j.get("status") == 402:
            print("  [FinMind] 시간당 요청 한도 초과. 1시간 후 재실행하세요.", file=sys.stderr)
            break
        data = j.get("data") or []
        print(f"  [FinMind] {code} {watch[code]['name_ko']}: {len(data)}개월", flush=True)
        for d in data:
            rows.append({"date": f"{d['revenue_year']:04d}-{d['revenue_month']:02d}",
                         "code": str(d["stock_id"]), "revenue": float(d["revenue"])})
        time.sleep(0.6)
    if not rows: return pd.DataFrame()
    df = pd.DataFrame(rows)
    # 단위 자동 감지: FinMind는 元(원 단위 NTD), MOPS는 仟元(천 NTD).
    # 대형주 월매출이 100억(1e10)을 넘으면 元 단위로 보고 천 NTD로 환산.
    if df["revenue"].max() > 1e10:
        df["revenue"] = df["revenue"] / 1000.0
    df["name_zh"] = df["code"].map(lambda c: watch[c]["name_zh"])
    df["market"] = df["code"].map(lambda c: watch[c]["market"])
    return df


# ---------------- 메인 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", metavar="YYYY-MM")
    ap.add_argument("--source", choices=["auto", "mops", "finmind"], default="auto")
    ap.add_argument("--sleep", type=float, default=3.0)
    args = ap.parse_args()

    DATA_DIR.mkdir(exist_ok=True)
    watch = load_watchlist()
    existing = load_existing()
    have = set(existing["date"].unique()) if not existing.empty else set()
    start = args.backfill or "2019-01"
    end = latest_reportable_month()
    targets = [(y, m) for y, m in month_range(start, end) if f"{y:04d}-{m:02d}" not in have]

    if not targets:
        print("수집할 신규 월이 없습니다. (최신 상태)"); return

    print(f"수집 대상: {targets[0][0]}-{targets[0][1]:02d} ~ {end} ({len(targets)}개월)")
    new = pd.DataFrame()
    if args.source in ("auto", "mops"):
        new = collect_mops(targets, watch, args.sleep)
    if new.empty and args.source in ("auto", "finmind"):
        first = f"{targets[0][0]:04d}-{targets[0][1]:02d}"
        new = collect_finmind(list(watch), first, watch)
        if not new.empty:
            new = new[~new["date"].isin(have)]
            new = new[new["date"] <= end]

    if new.empty:
        print("ERROR: 어느 소스에서도 데이터를 받지 못했습니다.", file=sys.stderr)
        sys.exit(0 if not existing.empty else 1)

    new["name_ko"] = new["code"].map(lambda c: watch[c]["name_ko"])
    new["sector"] = new["code"].map(lambda c: watch[c]["sector"])
    merged = pd.concat([existing, new], ignore_index=True)
    merged = (merged.drop_duplicates(["date", "code"], keep="last")
                    .sort_values(["code", "date"]).reset_index(drop=True))
    merged.to_parquet(PARQUET_PATH, index=False)
    merged.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
    print(f"저장 완료: {len(merged)}행, {merged['code'].nunique()}종목, "
          f"{merged['date'].min()} ~ {merged['date'].max()}")

if __name__ == "__main__":
    main()
