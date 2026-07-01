#!/bin/bash
# stop-orchestrator.sh — 統一 Stop Hook 協調器
#
# 負責：
#   - auto-dev-stop.sh — 僅 TDD 模式（.auto-dev/checkpoint.json 存在且 in_progress）
#
# 功能：
#   - 超時保護：每個子 hook 最多 30 秒，超時跳過並記錄
#   - 執行日誌：記錄到 ~/.claude/stop-log/YYYYMMDD-HHMMSS.log
#
# 輸出格式（若需 block）：
#   {"decision": "block", "reason": "..."}

set -uo pipefail

# Hooks 位置：預設用 orchestrator 自身所在目錄（plugin 與 CLI 安裝都對）
# 可被 CLAUDE_AUTODEV_HOOKS_DIR 環境變數覆寫（測試用）
HOOKS_DIR="${CLAUDE_AUTODEV_HOOKS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

LOG_DIR="$HOME/.claude/stop-log"
HOOK_TIMEOUT=30

mkdir -p "$LOG_DIR"
TIMESTAMP=$(date '+%Y%m%d-%H%M%S')
LOG_FILE="${LOG_DIR}/${TIMESTAMP}.log"

log() {
    echo "[$(date '+%H:%M:%S')] $*" >> "$LOG_FILE" 2>/dev/null || true
}

HOOK_OUTPUT=""

run_hook() {
    local name="$1"
    local script="$2"
    local exit_code=0
    HOOK_OUTPUT=""

    log "START ${name}"

    if [[ ! -f "${script}" ]]; then
        log "SKIP ${name} (script not found: ${script})"
        return 0
    fi

    if [[ ! -x "${script}" ]]; then
        chmod +x "${script}" 2>/dev/null || true
    fi

    HOOK_OUTPUT=$(timeout "${HOOK_TIMEOUT}" bash "${script}" 2>>"${LOG_FILE}") || exit_code=$?

    if [[ "${exit_code}" -eq 124 ]]; then
        log "TIMEOUT ${name} (exceeded ${HOOK_TIMEOUT}s)"
        HOOK_OUTPUT=""
        return 0
    fi

    log "DONE ${name} (exit=${exit_code}, output_bytes=${#HOOK_OUTPUT})"
    return "${exit_code}"
}

is_block() {
    local output="$1"
    [[ -z "${output}" ]] && return 1
    printf '%s' "${output}" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    sys.exit(0 if d.get("decision") == "block" else 1)
except Exception:
    sys.exit(1)
' 2>/dev/null
}

log "=== ORCHESTRATOR START (PWD=${PWD}) ==="

# ═══════════════════════════════════════════════════
# TDD 模式偵測
# ═══════════════════════════════════════════════════
AUTODEV_ACTIVE=false
AUTODEV_CHECKPOINT=".auto-dev/checkpoint.json"

if [[ -f "${AUTODEV_CHECKPOINT}" ]]; then
    if command -v jq &>/dev/null; then
        ad_status=$(jq -r '.status // "unknown"' "${AUTODEV_CHECKPOINT}" 2>/dev/null || echo "unknown")
    else
        ad_status=$(grep -o '"status"[[:space:]]*:[[:space:]]*"[^"]*"' "${AUTODEV_CHECKPOINT}" 2>/dev/null \
            | head -1 | sed 's/.*"\([^"]*\)"$/\1/' || echo "unknown")
    fi
    [[ "${ad_status}" == "in_progress" ]] && AUTODEV_ACTIVE=true
    log "TDD mode: status=${ad_status}, active=${AUTODEV_ACTIVE}"
else
    log "TDD mode: no checkpoint found"
fi

# ═══════════════════════════════════════════════════
# auto-dev-stop — TDD 模式時執行
# ═══════════════════════════════════════════════════
if ${AUTODEV_ACTIVE}; then
    run_hook "auto-dev-stop" "${HOOKS_DIR}/auto-dev-stop.sh" || true

    if is_block "${HOOK_OUTPUT}"; then
        log "BLOCK by auto-dev-stop"
        log "=== ORCHESTRATOR END (blocked by auto-dev) ==="
        printf '%s\n' "${HOOK_OUTPUT}"
        exit 0
    fi

    log "=== ORCHESTRATOR END (TDD mode, no block) ==="
    exit 0
fi

log "=== ORCHESTRATOR END (ok, no block) ==="
exit 0
