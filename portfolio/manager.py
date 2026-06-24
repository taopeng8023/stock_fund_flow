"""
持仓管理 — 记录持仓 / 更新价格 / 生成卖出信号

用法:
  python -m portfolio.manager add --code=000001 --name=平安银行 --price=12.50 --date=20260624
  python -m portfolio.manager remove --code=000001
  python -m portfolio.manager list                          # 列出所有持仓
  python -m portfolio.manager update --date=20260624        # 更新当日价格
  python -m portfolio.manager check --date=20260624         # 检查卖出信号
  python -m portfolio.manager sell --code=000001 --price=13.00 --date=20260624  # 手动卖出
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).parent.parent
POSITIONS_FILE = PROJECT_ROOT / "portfolio" / "positions.json"

# ---------------------------------------------------------------------------
# 数据层
# ---------------------------------------------------------------------------

def _load() -> dict:
    """加载持仓 JSON。"""
    if not POSITIONS_FILE.exists():
        return {"positions": [], "trades": []}
    with open(POSITIONS_FILE, encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict):
    POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_active_positions() -> list:
    """返回所有活跃持仓。"""
    return [p for p in _load()["positions"] if p.get("status") == "active"]


def get_position(code: str) -> Optional[dict]:
    """查找单个持仓。"""
    for p in _load()["positions"]:
        if p["code"] == code and p.get("status") == "active":
            return p
    return None


# ---------------------------------------------------------------------------
# 操作
# ---------------------------------------------------------------------------

def add_position(code: str, name: str, price: float, date: str,
                 shares: int = 0, stop_loss_pct: float = -5.0,
                 take_profit_pct: float = 15.0, notes: str = ""):
    """新增持仓。"""
    data = _load()

    # 检查重复
    for p in data["positions"]:
        if p["code"] == code and p.get("status") == "active":
            print(f"❌ {code} {name} 已持仓，先卖出再重新买入")
            return

    pos = {
        "code": code, "name": name,
        "entry_date": date, "entry_price": round(price, 2),
        "shares": shares,
        "stop_loss_pct": stop_loss_pct,
        "take_profit_pct": take_profit_pct,
        "trailing_stop_peak": round(price, 2),
        "status": "active",
        "close_date": None, "close_price": None,
        "pnl_pct": None,
        "notes": notes,
    }
    data["positions"].append(pos)
    data["trades"].append({
        "date": date, "code": code, "name": name,
        "action": "BUY", "price": round(price, 2), "shares": shares,
    })
    _save(data)
    print(f"✅ 已买入: {code} {name} @ {price} (止损{stop_loss_pct:+.0f}% 止盈{take_profit_pct:+.0f}%)")


def close_position(code: str, price: float, date: str, reason: str = "手动卖出"):
    """平仓。"""
    data = _load()
    for p in data["positions"]:
        if p["code"] == code and p.get("status") == "active":
            pnl = (price - p["entry_price"]) / p["entry_price"] * 100
            p["status"] = "closed"
            p["close_date"] = date
            p["close_price"] = round(price, 2)
            p["pnl_pct"] = round(pnl, 2)
            data["trades"].append({
                "date": date, "code": code, "name": p["name"],
                "action": "SELL", "price": round(price, 2),
                "reason": reason, "pnl_pct": round(pnl, 2),
            })
            _save(data)
            print(f"✅ 已卖出: {code} {p['name']} @ {price}  盈亏{pnl:+.2f}% ({reason})")
            return
    print(f"❌ 未找到活跃持仓: {code}")


def update_price(code: str, price: float):
    """更新当日价格和移动止盈峰值。"""
    data = _load()
    for p in data["positions"]:
        if p["code"] == code and p.get("status") == "active":
            p["current_price"] = round(price, 2)
            pnl = (price - p["entry_price"]) / p["entry_price"] * 100
            p["current_pnl_pct"] = round(pnl, 2)
            # 更新移动止盈峰值
            if price > p.get("trailing_stop_peak", 0):
                p["trailing_stop_peak"] = round(price, 2)
            _save(data)
            return
    print(f"⚠️ {code} 非活跃持仓")


def update_all_prices(date_str: str):
    """从当日 fund_flow.json 批量更新所有持仓价格。"""
    rows = _load_fund_flow(date_str)
    if not rows:
        print(f"❌ {date_str} 无 fund_flow 数据")
        return

    price_map = {}
    for r in rows:
        code = r.get("f12", "")
        price = r.get("f2")
        if code and isinstance(price, (int, float)) and price > 0:
            price_map[code] = price

    updated = 0
    for p in get_active_positions():
        if p["code"] in price_map:
            update_price(p["code"], price_map[p["code"]])
            updated += 1

    print(f"✅ 价格更新: {updated}/{len(get_active_positions())} 只持仓")


# ---------------------------------------------------------------------------
# 卖出信号
# ---------------------------------------------------------------------------

SELL_RULES = {
    "SL-1": {"name": "硬止损", "urgency": "URGENT",
             "desc": "现价 ≤ 成本价 × (1 + 止损%)"},
    "SL-2": {"name": "移动止盈回撤", "urgency": "HIGH",
             "desc": "从最高点回撤 ≥ 8%（盈利 > 10% 后激活）"},
    "TP-1": {"name": "目标止盈", "urgency": "MEDIUM",
             "desc": "现价 ≥ 成本价 × (1 + 止盈%)"},
    "TP-2": {"name": "快盈锁仓", "urgency": "MEDIUM",
             "desc": "持有 ≤ 3 天且涨幅 ≥ 8%"},
    "TE-1": {"name": "死钱退出", "urgency": "MEDIUM",
             "desc": "持有 ≥ 10 天且涨跌幅 ≤ 2%"},
    "TE-2": {"name": "水下过久", "urgency": "HIGH",
             "desc": "持有 ≥ 5 天且亏损 ≥ 2%"},
}


def check_sell_signals(date_str: str, enable_notify: bool = True) -> list:
    """检查所有持仓的卖出信号。

    Returns:
        [{"code": str, "signals": [...]}, ...]
    """
    update_all_prices(date_str)

    # 检查黑天鹅
    bs_level = 0
    try:
        from portfolio.black_swan import BlackSwanDetector
        bs = BlackSwanDetector(date_str).check()
        bs_level = bs["level"]
        if bs_level >= 2:
            print(f"⚠️ 黑天鹅 Level {bs_level} — 建议全部审查")
    except Exception as e:
        print(f"⚠️ 黑天鹅检测失败: {e}")

    results = []
    today = datetime.strptime(date_str, "%Y%m%d")

    for p in get_active_positions():
        price = p.get("current_price")
        if not price:
            continue

        entry = p["entry_price"]
        entry_dt = datetime.strptime(p["entry_date"], "%Y%m%d")
        hold_days = (today - entry_dt).days
        pnl = (price - entry) / entry * 100
        peak = p.get("trailing_stop_peak", price)
        sl = p.get("stop_loss_pct", -5)
        tp = p.get("take_profit_pct", 15)
        signals = []

        # SL-1 硬止损
        if pnl <= sl:
            signals.append({"rule_id": "SL-1", "name": "硬止损", "urgency": "URGENT",
                            "reason": f"亏损{pnl:.1f}% ≥ 止损{sl:.0f}%"})

        # SL-2 移动止盈回撤
        if pnl > 10 and price <= peak * 0.92:
            drawdown = (peak - price) / peak * 100
            signals.append({"rule_id": "SL-2", "name": "移动止盈回撤", "urgency": "HIGH",
                            "reason": f"从峰值{peak:.2f}回撤{drawdown:.1f}%"})

        # TP-1 目标止盈
        if pnl >= tp:
            signals.append({"rule_id": "TP-1", "name": "目标止盈", "urgency": "MEDIUM",
                            "reason": f"盈利{pnl:.1f}% ≥ 止盈{tp:.0f}%"})

        # TP-2 快盈锁仓
        if hold_days <= 3 and pnl >= 8:
            signals.append({"rule_id": "TP-2", "name": "快盈锁仓", "urgency": "MEDIUM",
                            "reason": f"持有{hold_days}天涨{pnl:.1f}%，快盈建议锁仓"})

        # TE-1 死钱退出
        if hold_days >= 10 and abs(pnl) <= 2:
            signals.append({"rule_id": "TE-1", "name": "死钱退出", "urgency": "MEDIUM",
                            "reason": f"持有{hold_days}天，涨跌仅{pnl:+.1f}%，资金效率低"})

        # TE-2 水下过久
        if hold_days >= 5 and pnl <= -2:
            signals.append({"rule_id": "TE-2", "name": "水下过久", "urgency": "HIGH",
                            "reason": f"持有{hold_days}天亏损{pnl:.1f}%，建议止损"})

        # 黑天鹅强制审查
        if bs_level >= 2 and pnl < 0:
            signals.append({"rule_id": "BS-FORCE", "name": "黑天鹅强制", "urgency": "URGENT",
                            "reason": f"BS Level {bs_level}，强制审查亏损持仓"})

        if signals:
            results.append({
                "code": p["code"], "name": p["name"],
                "price": price, "pnl_pct": round(pnl, 2),
                "hold_days": hold_days, "entry_price": entry,
                "trailing_peak": peak,
                "signals": signals,
            })

    # 输出
    if not results:
        print("✅ 所有持仓正常，无卖出信号")
    else:
        _print_sell_signals(results)

    # 通知
    if enable_notify and results:
        _notify_sell_signals(date_str, results, bs_level)

    return results


def _print_sell_signals(results: list):
    """终端输出卖出信号。"""
    print(f"\n{'='*60}")
    print(f"  🚨 卖出信号 — {len(results)} 只持仓触发")
    print(f"{'='*60}")

    urgency_order = {"URGENT": 0, "HIGH": 1, "MEDIUM": 2}
    for r in results:
        max_urgency = min((s["urgency"] for s in r["signals"]),
                          key=lambda u: urgency_order.get(u, 9))
        emoji = {"URGENT": "🔴", "HIGH": "🟠", "MEDIUM": "🟡"}.get(max_urgency, "⚪")

        print(f"\n  {emoji} {r['code']} {r['name']}")
        print(f"     入场 {r['entry_price']}  现价 {r['price']}  "
              f"盈亏 {r['pnl_pct']:+.1f}%  持有 {r['hold_days']}天")
        for s in r["signals"]:
            u_emoji = {"URGENT": "🔴", "HIGH": "🟠", "MEDIUM": "🟡"}.get(s["urgency"], "")
            print(f"     {u_emoji} {s['rule_id']} {s['name']}: {s['reason']}")
    print()


def _notify_sell_signals(date_str: str, results: list, bs_level: int):
    """推送卖出信号到企业微信。"""
    try:
        from notify.wecom_sender import send_markdown
    except ImportError:
        return

    lines = [f"## 🚨 卖出信号 — {date_str}"]
    if bs_level >= 2:
        lines.append(f"> ⚠️ 黑天鹅 Level {bs_level}，强制审查")
    lines.append("")

    for r in results:
        lines.append(f"### {r['name']}（{r['code']}）")
        lines.append(f"> 入场 {r['entry_price']}  现价 {r['price']}  "
                     f"盈亏 **{r['pnl_pct']:+.1f}%**  持有 {r['hold_days']}天")
        for s in r["signals"][:3]:
            lines.append(f"> {s['rule_id']} {s['name']}: {s['reason']}")
        lines.append("")

    send_markdown("\n".join(lines))
    print("  📤 已推送企业微信")


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------

def _load_fund_flow(date_str: str) -> Optional[list]:
    path = PROJECT_ROOT / "data" / date_str / "fund_flow.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_add(args):
    add_position(args.code, args.name, args.price, args.date,
                 shares=args.shares, stop_loss_pct=args.sl,
                 take_profit_pct=args.tp, notes=args.notes or "")


def cmd_remove(args):
    close_position(args.code, args.price or 0, args.date or "", "手动移除")


def cmd_list(args):
    positions = _load()["positions"]
    active = [p for p in positions if p.get("status") == "active"]
    closed = [p for p in positions if p.get("status") == "closed"]

    print(f"\n{'='*70}")
    print(f"  持仓管理 — 活跃 {len(active)} 只 | 已平仓 {len(closed)} 只")
    print(f"{'='*70}")

    if active:
        print(f"\n  {'代码':<8s} {'名称':<8s} {'入场价':<8s} {'现价':<8s} {'盈亏':<8s} "
              f"{'持有':<5s} {'止损':<6s} {'止盈':<6s}")
        print(f"  {'─'*70}")
        for p in active:
            pnl = p.get("current_pnl_pct")
            pnl_str = f"{pnl:+.1f}%" if pnl is not None else "—"
            cp = p.get("current_price")
            cp_str = f"{cp:.2f}" if cp else "—"
            entry_dt = datetime.strptime(p["entry_date"], "%Y%m%d")
            hold = (datetime.now() - entry_dt).days
            print(f"  {p['code']:<8s} {p['name']:<8s} {p['entry_price']:<8.2f} {cp_str:<8s} "
                  f"{pnl_str:<8s} {hold}天{'':>1s} {p['stop_loss_pct']:+.0f}%{'':>2s} "
                  f"{p['take_profit_pct']:+.0f}%")

    if not active and not closed:
        print("\n  (空 — 使用 add 添加第一笔持仓)")


def cmd_update(args):
    update_all_prices(args.date)


def cmd_check(args):
    check_sell_signals(args.date, enable_notify=not args.no_notify)


def cmd_sell(args):
    close_position(args.code, args.price, args.date, args.reason or "手动卖出")


def main():
    parser = argparse.ArgumentParser(description="持仓管理")
    sub = parser.add_subparsers(dest="cmd")

    # add
    p_add = sub.add_parser("add", help="新增持仓")
    p_add.add_argument("--code", required=True)
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--price", type=float, required=True)
    p_add.add_argument("--date", required=True)
    p_add.add_argument("--shares", type=int, default=0)
    p_add.add_argument("--sl", type=float, default=-5.0, help="止损%")
    p_add.add_argument("--tp", type=float, default=15.0, help="止盈%")
    p_add.add_argument("--notes", default="")

    # remove
    p_rm = sub.add_parser("remove", help="移除持仓")
    p_rm.add_argument("--code", required=True)
    p_rm.add_argument("--price", type=float)
    p_rm.add_argument("--date")

    # list
    sub.add_parser("list", help="列出持仓")

    # update
    p_up = sub.add_parser("update", help="更新当日价格")
    p_up.add_argument("--date", required=True)

    # check
    p_chk = sub.add_parser("check", help="检查卖出信号")
    p_chk.add_argument("--date", required=True)
    p_chk.add_argument("--no-notify", action="store_true")

    # sell
    p_sell = sub.add_parser("sell", help="手动卖出")
    p_sell.add_argument("--code", required=True)
    p_sell.add_argument("--price", type=float, required=True)
    p_sell.add_argument("--date", required=True)
    p_sell.add_argument("--reason", default="手动卖出")

    args = parser.parse_args()

    if args.cmd == "add":
        cmd_add(args)
    elif args.cmd == "remove":
        cmd_remove(args)
    elif args.cmd == "list":
        cmd_list(args)
    elif args.cmd == "update":
        cmd_update(args)
    elif args.cmd == "check":
        cmd_check(args)
    elif args.cmd == "sell":
        cmd_sell(args)
    else:
        # 默认显示持仓列表
        cmd_list(args)


if __name__ == "__main__":
    main()
