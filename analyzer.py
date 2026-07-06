#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyzer.py — 월매출 파생지표 계산 + 대시보드 데이터(data.js) 생성
=================================================================
입력 : data/tw_revenue.parquet (collector.py 산출물)
출력 : docs/data.js  (window.REVENUE_DATA = {...})
       콘솔 요약 리포트(최신월 YoY 랭킹, 사상 최대 매출 경신 종목)

파생지표 (MOPS 공시치 그대로 쓰지 않고 원천 매출액에서 재계산):
  yoy / mom / cum_yoy / ma3(3개월 이동평균) / ttm(12개월 합계) /
  ttm_yoy / record(사상 최대 매출 경신 여부)
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
PARQUET_PATH = BASE_DIR / "data" / "tw_revenue.parquet"
OUT_JS = BASE_DIR / "docs" / "data.js"


def compute_metrics(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["code", "date"]).copy()
    df["revenue"] = df["revenue"].astype(float)  # 천 NTD
    g = df.groupby("code")["revenue"]
    df["mom"] = g.pct_change(1) * 100
    df["yoy"] = g.pct_change(12) * 100
    df["ma3"] = g.transform(lambda s: s.rolling(3).mean())
    df["ttm"] = g.transform(lambda s: s.rolling(12).sum())
    df["ttm_yoy"] = df.groupby("code")["ttm"].pct_change(12) * 100
    df["record"] = df["revenue"] >= g.transform(lambda s: s.cummax())
    # 연누계 및 누계 YoY (같은 해 1월~해당월 합계 재계산)
    df["year"] = df["date"].str[:4].astype(int)
    df["month"] = df["date"].str[5:7].astype(int)
    df["cum"] = df.groupby(["code", "year"])["revenue"].cumsum()
    prev = df[["code", "year", "month", "cum"]].copy()
    prev["year"] += 1
    prev = prev.rename(columns={"cum": "cum_prev"})
    df = df.merge(prev, on=["code", "year", "month"], how="left")
    df["cum_yoy_calc"] = (df["cum"] / df["cum_prev"] - 1) * 100
    return df


def build_payload(df: pd.DataFrame) -> dict:
    companies = []
    for code, sub in df.groupby("code"):
        sub = sub.sort_values("date")
        last = sub.iloc[-1]
        companies.append({
            "code": code,
            "name_ko": str(last.get("name_ko", "") or ""),
            "name_zh": str(last.get("name_zh", "") or ""),
            "market": str(last.get("market", "") or ""),
            "sector": str(last.get("sector", "") or ""),
            "dates": sub["date"].tolist(),
            "revenue": [round(v) for v in sub["revenue"]],           # 천 NTD
            "yoy": [None if pd.isna(v) else round(v, 1) for v in sub["yoy"]],
            "mom": [None if pd.isna(v) else round(v, 1) for v in sub["mom"]],
            "ma3": [None if pd.isna(v) else round(v) for v in sub["ma3"]],
            "ttm": [None if pd.isna(v) else round(v) for v in sub["ttm"]],
            "cum_yoy": [None if pd.isna(v) else round(v, 1) for v in sub["cum_yoy_calc"]],
            "record": [bool(v) for v in sub["record"]],
        })
    companies.sort(key=lambda c: -(c["revenue"][-1] if c["revenue"] else 0))
    return {
        "generated_at": pd.Timestamp.now(tz="Asia/Seoul").strftime("%Y-%m-%d %H:%M KST"),
        "unit": "천 NTD",
        "latest_month": df["date"].max(),
        "companies": companies,
    }


def print_report(df: pd.DataFrame):
    latest = df["date"].max()
    snap = df[df["date"] == latest].copy()
    snap["label"] = snap["name_ko"].fillna("") + "(" + snap["code"] + ")"
    snap = snap.sort_values("yoy", ascending=False)
    print(f"\n===== {latest} 월매출 YoY 랭킹 =====")
    for _, r in snap.iterrows():
        rec = " ★사상최대" if r["record"] else ""
        yoy = "n/a" if pd.isna(r["yoy"]) else f"{r['yoy']:+7.1f}%"
        mom = "n/a" if pd.isna(r["mom"]) else f"{r['mom']:+6.1f}%"
        print(f"  {r['label']:<28} YoY {yoy} | MoM {mom} | "
              f"{r['revenue']/1e6:>10.1f}십억NTD{rec}")


def main():
    if not PARQUET_PATH.exists():
        raise SystemExit("data/tw_revenue.parquet 가 없습니다. collector.py 를 먼저 실행하세요.")
    df = compute_metrics(pd.read_parquet(PARQUET_PATH))
    OUT_JS.parent.mkdir(exist_ok=True)
    payload = build_payload(df)
    OUT_JS.write_text("window.REVENUE_DATA = " +
                      json.dumps(payload, ensure_ascii=False) + ";",
                      encoding="utf-8")
    print(f"대시보드 데이터 생성: {OUT_JS} "
          f"({len(payload['companies'])}종목, 최신월 {payload['latest_month']})")
    print_report(df)


if __name__ == "__main__":
    main()
