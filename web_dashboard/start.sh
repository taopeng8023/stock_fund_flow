#!/bin/bash
# A股量化系统 — Web Dashboard 启动脚本
# 用法:
#   ./web_dashboard/start.sh              → 开发模式 (默认端口 8000)
#   ./web_dashboard/start.sh --port 3000  → 指定端口
#   ./web_dashboard/start.sh --no-open    → 不自动打开浏览器
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── 参数解析 ──────────────────────────────────────
PORT=8000
OPEN_BROWSER=true

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)   PORT="$2"; shift 2 ;;
        --no-open) OPEN_BROWSER=false; shift ;;
        -h|--help)
            echo "用法: $0 [选项]"
            echo ""
            echo "选项:"
            echo "  --port N     指定端口 (默认 8000)"
            echo "  --no-open    不自动打开浏览器"
            echo "  -h, --help   显示帮助"
            exit 0
            ;;
        *) echo "未知参数: $1"; exit 1 ;;
    esac
done

# ── 环境检查 ──────────────────────────────────────
echo "============================================"
echo "  A股量化系统 Web Dashboard"
echo "  端口: $PORT"
echo "============================================"

# 激活虚拟环境 (venv 在项目根目录)
if [ -f "$PROJECT_DIR/.venv/bin/activate" ]; then
    source "$PROJECT_DIR/.venv/bin/activate"
    echo "[venv] 已激活 .venv"
else
    echo "[警告] 未找到 .venv，使用系统 Python"
fi

# 检查依赖
echo "[检查] Reflex..."
if ! python -c "import reflex" 2>/dev/null; then
    echo "[错误] Reflex 未安装，请运行: pip install reflex"
    exit 1
fi
REFLEX_VER=$(python -c "from importlib.metadata import version; print(version('reflex'))" 2>/dev/null || true)
echo "  Reflex ${REFLEX_VER:-?}"

echo "[检查] Plotly..."
if ! python -c "import plotly" 2>/dev/null; then
    echo "[错误] Plotly 未安装，请运行: pip install plotly"
    exit 1
fi

# ── 端口检测 ──────────────────────────────────────
if lsof -i ":$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
    PID=$(lsof -i ":$PORT" -sTCP:LISTEN -t | head -1)
    PROC=$(ps -p "$PID" -o comm= 2>/dev/null || echo "unknown")
    echo ""
    echo "============================================"
    echo "  端口 $PORT 已被占用"
    echo "  进程: $PROC (PID $PID)"
    echo "============================================"
    echo ""
    echo "选项:"
    echo "  1) 杀掉占用进程，重新启动"
    echo "  2) 使用其他端口 (当前端口+1)"
    echo "  3) 退出"
    echo ""
    read -r -p "请选择 [1/2/3]: " CHOICE

    case "$CHOICE" in
        1)
            echo "[操作] 终止 PID $PID..."
            kill "$PID" 2>/dev/null || kill -9 "$PID" 2>/dev/null
            sleep 1
            echo "  ✓ 已释放端口 $PORT"
            ;;
        2)
            PORT=$((PORT + 1))
            echo "[操作] 切换到端口 $PORT"
            ;;
        3|*)
            echo "  已取消"
            exit 0
            ;;
    esac
fi

# ── 启动 ──────────────────────────────────────────
echo ""
echo "[启动] Reflex 开发服务器..."
echo "  → http://localhost:$PORT"
echo "  → 按 Ctrl+C 停止"
echo ""

if [ "$OPEN_BROWSER" = true ]; then
    ( sleep 3 && open "http://localhost:$PORT" 2>/dev/null || true ) &
fi

# reflex run 需要在 rxconfig.py 所在目录执行
# 使用 --single-port 让前后端共用同一端口
cd "$SCRIPT_DIR"
# reflex run 需要在 rxconfig.py 所在目录执行
# 前端绑定用户端口，后端绑定内部端口 (PORT+1)
cd "$SCRIPT_DIR"
reflex run --frontend-port "$PORT" --backend-port "$((PORT + 1))"
