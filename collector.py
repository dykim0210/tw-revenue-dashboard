#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
collector.py — 대만 MOPS(公開資訊觀測站) 월매출 수집기
======================================================
대만 상장사는 매월 10일까지 전월 매출을 공시하며, MOPS가 시장별 월별
집계 HTML 파일을 정적 경로로 제공합니다:

    https://mops.twse.com.tw/nas/t21/{market}/t21sc03_{ROC년도}_{월}_{suffix}.html

  - market : sii(TWSE 상장) / otc(TPEx 상장) / rotc(興櫃) / pub(공개발행)
  - ROC년도: 서기연도 - 1911  (예: 2026 → 115)
  - suffix : 0 = 국내기업, 1 = 해외(-KY) 기업
  - 금액 단위: 仟元 (천 NTD)

사용법
------
  python collector.py                          # 워치리스트 기준 누락분 자동 수집(증분)
  python collector.py --backfill 2019-01      # 2019-01부터 현재까지 백필
  python collector.py --months 2026-05 2026-06 # 특정 월만 수집
  python collector.py --all-companies         # 워치리스트 무시, 전 종목 저장

출력: data/tw_revenue.parquet, data/tw_revenue.csv
"""
import argparse
import io
import json
import random
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PARQUET_PATH = DATA_DIR / "tw_revenue.parquet"
CSV_PATH = DATA_DIR / "tw_revenue.csv"
WATCHLIST_PATH = BASE_DIR / "watchlist.json"

HOSTS = ["https://mops.twse.com.tw", "https://mopsov.twse.com.tw"]  # 신규 개편 후 구버전 미러
MARKETS = ["sii", "otc"]
SUFFIXES = ["0", "1"]  # 0=국내, 1=해외(-KY)

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}

# MOPS 원본 컬럼 → 표준 컬럼 매핑
COLMAP = {
    "公司代號": "code",
    "公司名稱": "name_zh",
    "當月營收": "revenue",            # 천 NTD
    "上月營收": "rev_prev_month",
    "去年當月營收": "rev_last_year",
    "上月比較增減(%)": "mom_pct",
    "去年同月增減(%)": "yoy_pct",
    "當月累計營收": "cum_revenue",
    "去年累計營收": "cum_last_year",
    "前期比較增減(%)": "cum_yoy_pct",
    "備註": "note",
}


def roc(year: int) -> int:
    return year - 1911


def month_urls(year: int, month: int, market: str):
    """해당 연월/시장의 후보 URL 목록 (호스트 × suffix)."""
    y = roc(year)
    for host in HOSTS:
        for sfx in SUFFIXES:
            if y <= 98:  # 2009년 이전은 suffix 없음
                yield f"{host}/nas/t21/{market}/t21sc03_{y}_{month}.html", sfx
                break
            yield f"{host}/nas/t21/{market}/t21sc03_{y}_{month}_{sfx}.html", sfx


def decode_response(r: requests.Response) -> str:
    """MOPS 파일은 대부분 Big5, 일부 UTF-8. '公司'가 보이는 인코딩을 채택."""
    for enc in ("big5", "utf-8", "cp950"):
        try:
            text = r.content.decode(enc, errors="replace")
            if "公司代號" in text or "公司" in text:
                return text
        except Exception:
            continue
    return r.text


def parse_month_html(text: str) -> pd.DataFrame:
    """MOPS 월매출 HTML → 표준 DataFrame."""
    try:
        tables = pd.read_html(io.StringIO(text))
    except ValueError:
        return pd.DataFrame()

    frames = []
    for t in tables:
        # 멀티레벨 헤더 평탄화
        if isinstance(t.columns, pd.MultiIndex):
            t.columns = t.columns.get_level_values(-1)
        cols = [str(c).strip() for c in t.columns]
        t.columns = cols
        if "公司代號" not in cols:
            # 헤더가 첫 행에 있는 경우
            first_col = t.columns[0]
            hit = t.index[t[first_col].astype(str).str.strip() == "公司代號"]
            if len(hit) == 0:
                continue
            t.columns = [str(c).strip() for c in t.iloc[hit[0]]]
            t = t.iloc[hit[0] + 1:]
        if "當月營收" not in t.columns:
            continue
        frames.append(t)

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df = df.rename(columns={k: v for k, v in COLMAP.items() if k in df.columns})

    # 유효 종목코드(4~6자리 숫자)만
    df["code"] = df["code"].astype(str).str.strip()
    df = df[df["code"].str.fullmatch(r"\d{4,6}")]

    num_cols = ["revenue", "rev_prev_month", "rev_last_year",
                "mom_pct", "yoy_pct", "cum_revenue", "cum_last_year", "cum_yoy_pct"]
    for c in num_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(
                df[c].astype(str).str.replace(",", "").str.strip(), errors="coerce")
    df = df[df["revenue"].notna()]

    keep = ["code", "name_zh"] + [c for c in num_cols if c in df.columns]
    if "note" in df.columns:
        keep.append("note")
    return df[keep].reset_index(drop=True)


def fetch_month(year: int, month: int, market: str,
                session: requests.Session, timeout: int = 20) -> pd.DataFrame:
    """한 연월·시장의 국내+해외 파일을 모두 받아 병합."""
    parts, seen_sfx = [], set()
    for url, sfx in month_urls(year, month, market):
        if sfx in seen_sfx:
            continue
        try:
            r = session.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code != 200 or len(r.content) < 500:
                continue
            df = parse_month_html(decode_response(r))
            if not df.empty:
                parts.append(df)
                seen_sfx.add(sfx)
        except requests.RequestException as e:
            print(f"    [warn] {url} → {e}", file=sys.stderr)
        if len(seen_sfx) == len(SUFFIXES):
            break

    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts, ignore_index=True).drop_duplicates(subset="code")
    out.insert(0, "date", f"{year:04d}-{month:02d}")
    out.insert(3, "market", market)
    return out


def load_watchlist() -> dict:
    with open(WATCHLIST_PATH, encoding="utf-8") as f:
        return {c["code"]: c for c in json.load(f)["companies"]}


def load_existing() -> pd.DataFrame:
    if PARQUET_PATH.exists():
        return pd.read_parquet(PARQUET_PATH)
    return pd.DataFrame()


def month_range(start: str, end: str):
    ys, ms = map(int, start.split("-"))
    ye, me = map(int, end.split("-"))
    y, m = ys, ms
    while (y, m) <= (ye, me):
        yield y, m
        m += 1
        if m > 12:
            y, m = y + 1, 1


def latest_reportable_month() -> str:
    """오늘 기준 공시가 완료됐을 가능성이 높은 최신 대상월(공시 마감 매월 10일)."""
    today = date.today()
    y, m = today.year, today.month
    # 전월 데이터: 10일 이후면 전월까지, 이전이면 전전월까지 확실
    m -= 1 if today.day >= 10 else 2
    while m <= 0:
        m += 12
        y -= 1
    return f"{y:04d}-{m:02d}"


def main():
    ap = argparse.ArgumentParser(description="MOPS 월매출 수집기")
    ap.add_argument("--backfill", metavar="YYYY-MM", help="이 월부터 최신월까지 수집")
    ap.add_argument("--months", nargs="+", metavar="YYYY-MM", help="특정 월만 수집")
    ap.add_argument("--all-companies", action="store_true",
                    help="워치리스트로 필터링하지 않고 전 종목 저장")
    ap.add_argument("--sleep", type=float, default=3.0, help="요청 간 대기(초), 기본 3")
    args = ap.parse_args()

    DATA_DIR.mkdir(exist_ok=True)
    watch = load_watchlist()
    existing = load_existing()
    have = set(existing["date"].unique()) if not existing.empty else set()

    if args.months:
        targets = [tuple(map(int, m.split("-"))) for m in args.months]
    else:
        start = args.backfill or (min(have) if have else "2019-01")
        targets = [(y, m) for y, m in month_range(start, latest_reportable_month())
                   if f"{y:04d}-{m:02d}" not in have]

    if not targets:
        print("수집할 신규 월이 없습니다. (최신 상태)")
        return

    print(f"수집 대상: {len(targets)}개월 × {len(MARKETS)}개 시장")
    session = requests.Session()
    new_frames = []
    for y, m in targets:
        for market in MARKETS:
            print(f"  {y}-{m:02d} [{market}] ...", end=" ", flush=True)
            df = fetch_month(y, m, market, session)
            print(f"{len(df)}개 종목" if not df.empty else "없음/미공시")
            if not df.empty:
                new_frames.append(df)
            time.sleep(args.sleep + random.uniform(0, 1.5))

    if not new_frames:
        print("신규 데이터가 없습니다.")
        return

    new = pd.concat(new_frames, ignore_index=True)
    if not args.all_companies:
        new = new[new["code"].isin(watch)]
        new["name_ko"] = new["code"].map(lambda c: watch[c]["name_ko"])
        new["sector"] = new["code"].map(lambda c: watch[c]["sector"])

    merged = pd.concat([existing, new], ignore_index=True)
    merged = (merged.drop_duplicates(subset=["date", "code"], keep="last")
                    .sort_values(["code", "date"]).reset_index(drop=True))
    merged.to_parquet(PARQUET_PATH, index=False)
    merged.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
    print(f"\n저장 완료: {PARQUET_PATH} ({len(merged)}행, "
          f"{merged['code'].nunique()}종목, {merged['date'].min()}~{merged['date'].max()})")


if __name__ == "__main__":
    main()
