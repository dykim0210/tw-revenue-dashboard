#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""실제 MOPS 접속 없이 대시보드를 미리 보기 위한 합성 샘플 데이터 생성기.
   실사용 시에는 collector.py 가 이 파일을 대체합니다. (data/ 를 덮어씀)"""
import json
from pathlib import Path
import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
rng = np.random.default_rng(42)
watch = {c["code"]: c for c in json.loads((BASE / "watchlist.json").read_text(encoding="utf-8"))["companies"]}

# 대표 6종목만 샘플 생성 (기준월매출 천NTD, 연성장률, 계절성 진폭)
PROFILES = {
    "2330": (200_000_000, 0.30, 0.10),
    "2454": (40_000_000, 0.15, 0.12),
    "3711": (45_000_000, 0.08, 0.10),
    "6669": (25_000_000, 0.55, 0.15),
    "8299": (5_000_000, 0.20, 0.18),
    "3661": (3_000_000, 0.45, 0.20),
}
dates = pd.period_range("2019-01", "2026-05", freq="M")
rows = []
for code, (base, g, amp) in PROFILES.items():
    w = watch[code]
    for i, p in enumerate(dates):
        trend = base * (1 + g) ** (i / 12)
        season = 1 + amp * np.sin(2 * np.pi * (p.month - 3) / 12)
        cny = 0.78 if p.month == 2 else 1.0  # 춘절 효과
        rev = trend * season * cny * rng.normal(1, 0.05)
        rows.append({"date": str(p), "code": code, "name_zh": w["name_zh"],
                     "market": w["market"], "revenue": round(rev),
                     "name_ko": w["name_ko"], "sector": w["sector"]})
df = pd.DataFrame(rows)
(BASE / "data").mkdir(exist_ok=True)
df.to_parquet(BASE / "data" / "tw_revenue.parquet", index=False)
df.to_csv(BASE / "data" / "tw_revenue.csv", index=False, encoding="utf-8-sig")
print(f"샘플 데이터 생성: {len(df)}행, {df['code'].nunique()}종목 ({df['date'].min()}~{df['date'].max()})")
