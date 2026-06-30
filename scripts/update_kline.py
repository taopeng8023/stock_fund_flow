#!/usr/bin/env python3
"""
K线增量更新 — 多线程并发 (10 workers)
用法: python scripts/update_kline.py
"""
import json
import os
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KLINE_DIR = PROJECT_ROOT / "kline_data"
TENCENT_URL = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
TIMEOUT = 8  # API超时
WORKERS = 20  # 并发数

def get_prefix(code: str) -> str:
    if code.startswith(("60", "68")): return "sh"
    if code.startswith(("00", "30")): return "sz"
    if code.startswith(("83", "87", "92")): return "bj"
    return "sh"

def fetch_recent(code: str, days: int = 4) -> list[dict]:
    """拉取最近N天日线"""
    prefix = get_prefix(code)
    param = f"{prefix}{code},day,,,{days},qfq"
    try:
        resp = requests.get(TENCENT_URL, params={"param": param}, headers=HEADERS, timeout=TIMEOUT)
        data = resp.json()
        key = f"{prefix}{code}"
        raw = data.get("data", {}).get(key, {}).get("qfqday")
        if not raw: return []
        bars = []
        for k in raw:
            if len(k) < 6: continue
            bars.append({
                "date": str(k[0]),
                "open": float(k[1]), "close": float(k[2]),
                "high": float(k[3]), "low": float(k[4]),
                "volume": float(k[5]) * 100 if k[5] else 0,
            })
        return bars
    except Exception:
        return []

def update_one(fp: Path) -> tuple[str, int]:
    """更新单只股票: 返回 (code, new_bars_count, 0=无新/1=有更新/-1=失败)"""
    code = fp.stem
    try:
        with open(fp) as f:
            data = json.load(f)
        existing_bars = data.get("bars", [])
        existing_dates = {b["date"] for b in existing_bars}
    except Exception:
        return code, -1

    recent = fetch_recent(code, days=4)
    new_bars = [b for b in recent if b["date"] not in existing_dates]
    if not new_bars:
        return code, 0

    existing_bars.extend(new_bars)
    existing_bars.sort(key=lambda x: x["date"])
    data["bars"] = existing_bars
    data["count"] = len(existing_bars)
    with open(fp, "w") as f:
        json.dump(data, f, ensure_ascii=False)
    return code, len(new_bars)

def main():
    files = sorted(
        f for f in KLINE_DIR.glob("*.json")
        if not f.name.startswith("_")
    )
    print(f"已有 {len(files)} 个K线文件")

    sample = json.load(open(files[0]))
    last_date = sample["bars"][-1]["date"]
    print(f"最新日期: {last_date}")
    print(f"开始并发更新 ({WORKERS} workers)...")

    updated = 0
    no_new = 0
    failed = 0
    t0 = time.time()
    new_dates = set()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(update_one, fp): fp.stem for fp in files}
        for i, future in enumerate(as_completed(futures)):
            code, result = future.result()
            if result == -1:
                failed += 1
            elif result == 0:
                no_new += 1
            else:
                updated += 1
                if result > 0:
                    new_dates.add(result)

            if (i + 1) % 500 == 0:
                elapsed = time.time() - t0
                rate = (i + 1) / elapsed
                eta = (len(files) - i - 1) / rate
                print(f"  [{i+1}/{len(files)}] ✓{updated} ↷{no_new} ✗{failed}  {rate:.0f}只/s ETA:{eta:.0f}s")

    elapsed = time.time() - t0
    print(f"\n完成: ✓{updated}新增  ↷{no_new}无变化  ✗{failed}失败  耗时{elapsed:.0f}s  ({len(files)/elapsed:.1f}只/s)")

    # 验证
    sample = json.load(open(files[0]))
    bars = sample["bars"]
    print(f"最新日期: {bars[-1]['date']}  ({bars[0]['date']}~{bars[-1]['date']}, {len(bars)}根)")

if __name__ == "__main__":
    main()
