---
name: auto-dev
description: >
  TDD 自動開發循環。利用 Stop Hook 驅動 Red-Green-Refactor 迭代，
  quality-gate 為完成判斷的 source of truth，不依賴 checkpoint AC 逐項追蹤。
  當用戶說「自動開發」「auto dev」「自動 TDD」「自動循環開發」
  「幫我用 TDD 自動做完」「auto loop」時觸發。
user-invocable: true
---

# Auto-Dev: TDD 自動開發循環

利用 Stop Hook + quality-gate 驅動 TDD 開發。
quality-gate（build + test）是 source of truth，checkpoint.json 是純紀錄。

## Usage

```bash
# 基本用法
/auto-dev "實作用戶登入

Acceptance Criteria:
- [ ] Login form (email + password)
- [ ] JWT token generation
- [ ] Error handling with user-friendly messages
"

# 恢復中斷的 session
/auto-dev --resume

# 查看狀態
/auto-dev --status

# 強制重新開始
/auto-dev --force "New task description"
```

## How It Works

```
┌───────────────────────────────────────────────────┐
│  /auto-dev "task with ACs"                        │
│                                                   │
│  1. mkdir .auto-dev/ + 寫 checkpoint.json（紀錄） │
│  2. Claude 執行 TDD (Red-Green-Refactor)          │
│  3. Claude 做完 → touch .auto-dev/done            │
│  4. Stop Hook 觸發：                              │
│     ├─ .auto-dev/done 存在?                       │
│     │   → 跑 quality-gate (build + test)          │
│     │   → 通過 → 放行                             │
│     │   → 失敗 → block + 刪 done + 要求修正      │
│     ├─ .auto-dev/stop 存在? → 放行（手動中斷）    │
│     ├─ checkpoint.status == completed? → 放行      │
│     └─ 以上都不符合 → block（提醒繼續）           │
│  5. 重複 2-4 直到 quality-gate 通過               │
└───────────────────────────────────────────────────┘
```

## Key Principle

**Hook 不寫 checkpoint，只讀 status 欄位。** 這消除了 Claude 和 Hook 的競爭條件。

- `checkpoint.json`：Claude 自己維護，純紀錄（給人看進度）
- `.auto-dev/done`：Claude 完成時 touch，觸發 quality-gate
- `.auto-dev/stop`：手動中斷信號
- `.auto-dev/config.json`：自訂 build/test 指令（可選，未提供時用 default）
- quality-gate exit code：Hook 的唯一決策依據

## Execution

When user runs `/auto-dev "$ARGUMENTS"`:

### 1. Handle Flags

```bash
STATE_DIR=".auto-dev"
CHECKPOINT="$STATE_DIR/checkpoint.json"

# --status
if [[ "$ARGUMENTS" == *"--status"* ]]; then
    if [ -f "$CHECKPOINT" ]; then
        cat "$CHECKPOINT" | jq '.'
    else
        echo "No active auto-dev session."
    fi
    exit 0
fi

# --resume
if [[ "$ARGUMENTS" == *"--resume"* ]]; then
    if [ ! -f "$CHECKPOINT" ]; then
        echo "No session to resume."
        exit 1
    fi
    echo "Resuming..."
fi

# --force
if [[ "$ARGUMENTS" == *"--force"* ]]; then
    rm -rf "$STATE_DIR"
fi
```

### 2. Initialize State

```bash
mkdir -p "$STATE_DIR"
```

Write checkpoint.json (for human tracking only):

```json
{
  "request": "<user's full request>",
  "status": "in_progress",
  "started_at": "2026-03-20T10:00:00Z",
  "acceptance_criteria": [
    { "id": 1, "description": "Login form", "done": false }
  ]
}
```

### 3. Execute TDD

Follow Red-Green-Refactor for all ACs. You may:
- Work on multiple ACs in parallel if they are independent
- Complete all ACs in one iteration if efficient
- There is no forced "one AC per iteration" constraint

### 4. Signal Completion

When done:

```bash
# Option A: Touch done file → Hook runs quality-gate automatically
touch .auto-dev/done

# Option B: Set status directly (Hook checks this too)
jq '.status = "completed"' .auto-dev/checkpoint.json > tmp && mv tmp .auto-dev/checkpoint.json
```

The Stop Hook will:
- If `.auto-dev/done` exists → run quality-gate → pass = release, fail = block
- If `status == "completed"` → release immediately

### 5. Update Checkpoint (Optional)

Update AC status in checkpoint.json as you go — this is for human reference only, the Hook does not check individual AC done flags.

## Emergency Stop

```bash
touch .auto-dev/stop
```

## Customizing Quality Gate

Default 行為偵測到 `package.json` + `tsconfig.json` 跑 `npx tsc --noEmit`，偵測到 `npm test` 就跑測試。
非 Node 專案請建立 `.auto-dev/config.json`：

```json
{
  "build": "cargo check",
  "test": "cargo test"
}
```

支援任何回傳 exit code 的指令。Hook 只看 exit code，不解析輸出。

## Integration

| Tool | Role |
|------|------|
| `tdd-guide` | TDD methodology (RED-GREEN-REFACTOR) |
| `quality-gate` | Source of truth for completion (build + types + lint + tests) |
| `code-reviewer` | Optional review before marking complete |

## Checkpoint Schema

```json
{
  "request": "string — original user request",
  "status": "in_progress | completed | aborted",
  "started_at": "ISO 8601 timestamp",
  "acceptance_criteria": [
    {
      "id": "number",
      "description": "string",
      "done": "boolean — for human tracking only"
    }
  ]
}
```

## Related

- [tdd](/tdd) — TDD enforcement agent
- [quality-gate](/quality-gate) — Build/lint/test verification
