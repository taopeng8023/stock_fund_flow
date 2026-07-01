#!/usr/bin/env python3
"""
K线数据格式转换: baostock CSV → kline_data JSON
使 daily_pipeline/score.py 的 _load_kline() 可用

用法:
    python baostock_data/convert_to_kline_json.py --date 20260701
    python baostock_data/convert_to_kline_json.py --date 20260701 --output-dir kline_data
"""
import argparse
import json
import os
import sys
from glob import glob

import pandas as pd

COL_MAP = {
    "日期": "date", "开盘": "open", "最高": "high",
    "最低": "low", "收盘": "close", "成交量": "volume",
    "成交额": "amount", "换手率": "turnover",
}


def convert_csv_to_bars(filepath: str) -> list[dict]:
    """Convert single CSV to list of bar dicts."""
    try:
        df = pd.read_csv(filepath)
    except Exception:
        return []

    # Rename and keep only needed columns
    df = df.rename(columns=COL_MAP)
    bars = []
    for _, row in df.iterrows():
        bar = {"date": str(row["date"])}
        for key in ("open", "high", "low", "close", "volume"):
            try:
                bar[key] = float(row[key])
            except (ValueError, TypeError):
                bar[key] = 0.0
        bars.append(bar)

    # Sort by date ascending
    bars.sort(key=lambda b: b["date"])
    return bars


def main():
    parser = argparse.ArgumentParser(description="转换 baostock K线CSV → kline_data JSON")
    parser.add_argument("--date", required=True, help="数据日期 YYYYMMDD")
    parser.add_argument("--output-dir", default=None, help="输出目录 (默认: 项目根/kline_data)")
    parser.add_argument("--limit", type=int, default=0, help="限制转换数量 (0=全部)")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    baostock_root = script_dir  # baostock_data/
    project_root = os.path.dirname(baostock_root)
    data_dir = os.path.join(baostock_root, "data", args.date, "daily")

    if not os.path.isdir(data_dir):
        print(f"错误: 数据目录不存在: {data_dir}")
        sys.exit(1)

    output_dir = args.output_dir or os.path.join(project_root, "kline_data")
    os.makedirs(output_dir, exist_ok=True)

    csv_files = sorted(glob(os.path.join(data_dir, "sh.*.csv")) +
                       glob(os.path.join(data_dir, "sz.*.csv")))

    if args.limit > 0:
        csv_files = csv_files[:args.limit]

    total = len(csv_files)
    converted = 0
    skipped = 0

    for fp in csv_files:
        code = os.path.splitext(os.path.basename(fp))[0]
        bars = convert_csv_to_bars(fp)

        if len(bars) < 20:
            skipped += 1
            continue

        output = {"code": code, "bars": bars}
        out_path = os.path.join(output_dir, f"{code}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False)

        converted += 1
        if converted % 500 == 0:
            print(f"  进度: {converted}/{total}")

    print(f"完成: 转换 {converted} 只, 跳过 {skipped} 只 (K线不足)")
    print(f"输出目录: {output_dir}")


if __name__ == "__main__":
    main()
