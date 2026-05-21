"""
定时任务调度器 — 每个交易日 15:30 自动执行数据采集+选股+绩效更新
用法:
  python scheduler.py             前台运行（Ctrl+C 停止）
  python scheduler.py --daemon    后台守护进程
  python scheduler.py --run       立即执行一次
"""
import subprocess
import sys
import os
import time
import signal
from datetime import datetime, timezone, timedelta

BJS = timezone(timedelta(hours=8))
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# 配置
TRIGGER_HOUR = 15
TRIGGER_MINUTE = 32          # 15:32 触发（收盘后 30 分钟，数据基本到位）
CHECK_INTERVAL = 30          # 每 30 秒检查一次时间
MAX_LOGS = 30                # 保留最近 30 天日志

running = True


def log(msg):
    ts = datetime.now(BJS).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def is_trading_day(dt_bjs):
    """判断北京时间的日期是否为交易日（周一至周五，排除中国法定假日近似）"""
    return dt_bjs.weekday() < 5  # 简化: 周一到周五


def run_pipeline():
    """执行完整管线"""
    date_str = datetime.now(BJS).strftime("%Y%m%d")
    log(f"════ 开始执行 {date_str} 每日任务 ════")

    # 确定 Python 路径
    venv_python = os.path.join(PROJECT_DIR, ".venv", "bin", "python3")
    python = venv_python if os.path.exists(venv_python) else "python3"

    steps = [
        ("数据采集", ["fetch_data.py", f"--date={date_str}"]),
        ("盘面诊断", ["market_diagnosis.py", f"--date={date_str}"]),
        ("选股分析", ["stock_picker.py", f"--date={date_str}", "--top=5"]),
        ("绩效更新", ["performance.py", "--update", f"--date={date_str}"]),
        ("绩效报告", ["performance.py", "--report"]),
    ]

    for step_name, args in steps:
        log(f"  [{step_name}] 开始...")
        try:
            result = subprocess.run(
                [python] + args,
                cwd=PROJECT_DIR,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                log(f"  [{step_name}] 失败 (exit={result.returncode})")
                # 只打印最后 5 行错误
                stderr_lines = result.stderr.strip().split("\n")[-5:]
                for line in stderr_lines:
                    if line.strip():
                        log(f"    ERR: {line}")
                # 采集失败则终止，其他步骤继续
                if step_name == "数据采集":
                    return
            else:
                # 打印选股摘要（前 30 行）
                if step_name in ("选股分析", "绩效报告"):
                    outlines = result.stdout.strip().split("\n")
                    for line in outlines[:30]:
                        if line.strip():
                            log(f"    {line}")
                log(f"  [{step_name}] 完成 ✓")
        except subprocess.TimeoutExpired:
            log(f"  [{step_name}] 超时(5min)")
        except Exception as e:
            log(f"  [{step_name}] 异常: {e}")

    log(f"════ {date_str} 任务完成 ════")

    # 清理旧日志
    try:
        logdir = os.path.join(PROJECT_DIR, "logs")
        if os.path.exists(logdir):
            for f in sorted(os.listdir(logdir)):
                if f.startswith("scheduler_") and f.endswith(".log"):
                    fpath = os.path.join(logdir, f)
                    if time.time() - os.path.getmtime(fpath) > MAX_LOGS * 86400:
                        os.remove(fpath)
    except Exception:
        pass


def run_daemon():
    """后台守护进程"""
    # 创建日志目录和文件
    logdir = os.path.join(PROJECT_DIR, "logs")
    os.makedirs(logdir, exist_ok=True)
    date_str = datetime.now(BJS).strftime("%Y%m%d")
    logfile = os.path.join(logdir, f"scheduler_{date_str}.log")

    # 重定向输出到文件
    f = open(logfile, "a")
    sys.stdout = f
    sys.stderr = f

    log("调度器启动 (守护模式)")

    # 处理信号
    def handle_signal(signum, frame):
        global running
        log(f"收到信号 {signum}, 停止调度器")
        running = False

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    last_run_date = None
    while running:
        now = datetime.now(BJS)

        # 检查是否到触发时间
        if (now.hour == TRIGGER_HOUR and now.minute == TRIGGER_MINUTE
                and is_trading_day(now)):
            today_str = now.strftime("%Y%m%d")
            if last_run_date != today_str:
                run_pipeline()
                last_run_date = today_str
                time.sleep(120)  # 执行后等 2 分钟避免重复

        # 新的一天重置
        if now.hour == 0 and now.minute == 0:
            last_run_date = None

        time.sleep(CHECK_INTERVAL)

    log("调度器已停止")
    f.close()


def main():
    if "--daemon" in sys.argv:
        log("启动守护进程...")
        # 双 fork 实现守护进程
        pid = os.fork()
        if pid > 0:
            print(f"守护进程已启动 PID={pid}")
            sys.exit(0)
        os.setsid()
        run_daemon()
    elif "--run" in sys.argv:
        run_pipeline()
    else:
        # 前台运行
        log("调度器启动 (前台模式, Ctrl+C 停止)")
        log(f"每个交易日 {TRIGGER_HOUR:02d}:{TRIGGER_MINUTE:02d} 北京时间触发")
        log(f"下一个触发: 等待...")

        last_run_date = None
        try:
            while running:
                now = datetime.now(BJS)
                if (now.hour == TRIGGER_HOUR and now.minute == TRIGGER_MINUTE
                        and is_trading_day(now)):
                    today_str = now.strftime("%Y%m%d")
                    if last_run_date != today_str:
                        run_pipeline()
                        last_run_date = today_str
                        time.sleep(120)
                if now.hour == 0 and now.minute == 0:
                    last_run_date = None
                time.sleep(CHECK_INTERVAL)
        except KeyboardInterrupt:
            log("收到中断信号")


if __name__ == "__main__":
    main()
