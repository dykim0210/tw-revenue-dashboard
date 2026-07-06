# -*- coding: utf-8 -*-
"""MOPS HTML 구조를 모사한 픽스처로 parse_month_html 검증 (네트워크 불필요)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collector import parse_month_html

FIXTURE = """
<html><body>
<h2>上市公司115年5月份(累計與當月)營業收入統計表</h2>
<table border=1>
<tr><th>公司代號</th><th>公司名稱</th><th>當月營收</th><th>上月營收</th>
<th>去年當月營收</th><th>上月比較增減(%)</th><th>去年同月增減(%)</th>
<th>當月累計營收</th><th>去年累計營收</th><th>前期比較增減(%)</th><th>備註</th></tr>
<tr><td>2330</td><td>台積電</td><td>320,516,000</td><td>349,566,000</td>
<td>229,620,000</td><td>-8.31</td><td>39.58</td>
<td>1,619,000,000</td><td>1,148,000,000</td><td>41.02</td><td>-</td></tr>
<tr><td>2454</td><td>聯發科</td><td>55,232,000</td><td>50,101,000</td>
<td>45,111,000</td><td>10.24</td><td>22.43</td>
<td>260,000,000</td><td>230,000,000</td><td>13.04</td><td>-</td></tr>
<tr><td>合計</td><td></td><td>3,500,000,000</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
</table>
</body></html>
"""

def test_parse():
    df = parse_month_html(FIXTURE)
    assert len(df) == 2, f"2행이어야 하는데 {len(df)}행"
    tsmc = df[df["code"] == "2330"].iloc[0]
    assert tsmc["revenue"] == 320_516_000
    assert abs(tsmc["yoy_pct"] - 39.58) < 1e-6
    assert tsmc["name_zh"] == "台積電"
    print("parse_month_html OK:", df[["code","name_zh","revenue","yoy_pct"]].to_dict("records"))

if __name__ == "__main__":
    test_parse()
