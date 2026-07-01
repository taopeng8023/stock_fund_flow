#!/bin/bash
# Auto-Dev Stop Hook — quality-gate 驅動版
#
# 設計原則：
#   - quality-gate（build + test）是 source of truth，不是 checkpoint.json
#   - checkpoint.json 退化為純紀錄（給人看），hook 只讀 status 欄位
#   - hook 不寫 checkpoint，消除競爭條件
#   - signal file（.auto-dev/done）作為 Claude 宣告完成的信號
#
# 流程：
#   1. 沒有 .auto-dev/ 目錄 → 放行（非 TDD 模式）
#   2. .auto-dev/stop 存在 → 放行（手動中斷）
#   3. checkpoint.status == "completed" → 放行
#   4. .auto-dev/done 存在 → 跑 quality-gate → 通過放行 / 失敗 block
#   5. 以上都不符合 → block（Claude 還在工作中，提醒繼續）
#
# Quality-gate 指令來源優先順序：
#   1. .auto-dev/config.json 的 build/test 欄位（user override）
#   2. 偵測 package.json + tsconfig.json → tsc --noEmit + npm test（Node default）
#   3. 兩者皆無 → 跳過該檢查

set -euo pipefail

STATE_DIR=".auto-dev"
CHECKPOINT_FILE="$STATE_DIR/checkpoint.json"
CONFIG_FILE="$STATE_DIR/config.json"
STOP_FILE="$STATE_DIR/stop"
DONE_FILE="$STATE_DIR/done"

# ── 非 TDD 模式 → 放行 ──
if [[ ! -d "$STATE_DIR" ]]; then
    exit 0
fi

# ── 手動中斷信號 → 放行 ──
if [[ -f "$STOP_FILE" ]]; then
    rm -f "$STOP_FILE"
    exit 0
fi

# ── 讀取 checkpoint status（唯一讀取的欄位）──
status="unknown"
if [[ -f "$CHECKPOINT_FILE" ]]; then
    if command -v jq &>/dev/null; then
        status=$(jq -r '.status // "unknown"' "$CHECKPOINT_FILE" 2>/dev/null || echo "unknown")
    else
        status=$(grep -o '"status"[[:space:]]*:[[:space:]]*"[^"]*"' "$CHECKPOINT_FILE" 2>/dev/null \
            | head -1 | sed 's/.*"\([^"]*\)"$/\1/' || echo "unknown")
    fi
fi

# ── 已完成或已中止 → 放行 ──
if [[ "$status" == "completed" ]] || [[ "$status" == "aborted" ]]; then
    exit 0
fi

# ── 讀取自訂 quality-gate 指令（若有）──
custom_build=""
custom_test=""
if [[ -f "$CONFIG_FILE" ]] && command -v jq &>/dev/null; then
    custom_build=$(jq -r '.build // ""' "$CONFIG_FILE" 2>/dev/null || echo "")
    custom_test=$(jq -r '.test // ""' "$CONFIG_FILE" 2>/dev/null || echo "")
fi

# ── Claude 宣告做完（done file）→ 跑 quality-gate 驗證 ──
if [[ -f "$DONE_FILE" ]]; then
    qg_failed=""
    repo_root=$(git rev-parse --show-toplevel 2>/dev/null || pwd)

    # Build check
    if [[ -n "$custom_build" ]]; then
        if ! (cd "$repo_root" && timeout 60 bash -c "$custom_build" >/dev/null 2>&1); then
            qg_failed="${qg_failed}Build failed (\`$custom_build\`). "
        fi
    elif [[ -f "$repo_root/package.json" && -f "$repo_root/tsconfig.json" ]]; then
        if ! (cd "$repo_root" && timeout 30 npx tsc --noEmit 2>/dev/null); then
            qg_failed="${qg_failed}TypeScript compilation failed. "
        fi
    fi

    # Test check
    if [[ -n "$custom_test" ]]; then
        if ! (cd "$repo_root" && timeout 120 bash -c "$custom_test" >/dev/null 2>&1); then
            qg_failed="${qg_failed}Tests failed (\`$custom_test\`). "
        fi
    elif [[ -f "$repo_root/package.json" ]] && grep -q '"test"' "$repo_root/package.json" 2>/dev/null; then
        if ! (cd "$repo_root" && timeout 60 npm test --silent 2>/dev/null); then
            qg_failed="${qg_failed}Tests failed. "
        fi
    fi

    if [[ -n "$qg_failed" ]]; then
        # quality-gate 失敗 → 刪除 done file → block
        rm -f "$DONE_FILE"

        reason="Quality gate failed: ${qg_failed}Fix the issues, then touch .auto-dev/done to retry."
        json_reason=$(printf '%s' "$reason" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' 2>/dev/null) \
            || json_reason="\"${reason}\""

        cat <<QGEOF
{
  "decision": "block",
  "reason": $json_reason
}
QGEOF
        exit 0
    fi

    # quality-gate 通過 → 放行
    rm -f "$DONE_FILE"
    exit 0
fi

# ── 還在工作中 → block，提醒繼續 ──
request=""
if [[ -f "$CHECKPOINT_FILE" ]] && command -v jq &>/dev/null; then
    request=$(jq -r '.request // ""' "$CHECKPOINT_FILE" 2>/dev/null || echo "")
fi

prompt="Auto-dev is active but not yet complete.

Task: ${request:-（見 .auto-dev/checkpoint.json）}

When you are done:
1. Update .auto-dev/checkpoint.json status to \"completed\"
2. Or touch .auto-dev/done to trigger quality-gate verification

To abort: touch .auto-dev/stop"

json_reason=$(printf '%s' "$prompt" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' 2>/dev/null) \
    || json_reason="\"Auto-dev active, not yet complete. Touch .auto-dev/done when ready.\""

cat <<EOF
{
  "decision": "block",
  "reason": $json_reason
}
EOF
