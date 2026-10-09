#!/usr/bin/env bash
# 模块验收执行器 —— 人/CI 可独立运行，全程留日志证据（docs/oss-split/modules/ 验收清单的承载体）
# 用法:
#   scripts/acceptance/run.sh M01 ce      # CE 模式验收 M01
#   scripts/acceptance/run.sh M02 ee      # EE 模式（需先起 PG/Redis 并设 ST_CONTROL_PLANE_DSN/ST_REDIS_URL）
# 日志: acceptance-logs/<模块>-<模式>-<时间戳>.log（验收证据，归档不进 git）
set -uo pipefail

MODULE=${1:?用法: run.sh <M01..M10> <ce|ee>}
MODE=${2:?用法: run.sh <M01..M10> <ce|ee>}
MODE_UPPER=$(printf '%s' "$MODE" | tr '[:lower:]' '[:upper:]')
PORT=${ST_ACCEPT_PORT:-8780}
TS=$(date +%Y%m%d-%H%M%S)
LOG_DIR=acceptance-logs
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/${MODULE}-${MODE}-${TS}.log"
PASS=0; FAIL=0

log()   { echo "[$(date +%T)] $*" | tee -a "$LOG"; }
check() { # check <检查项名称> <命令...>  —— 命令输出全部进日志
  local name=$1; shift
  log "CHECK ▶ $name"
  if "$@" >>"$LOG" 2>&1; then
    log "PASS  ✔ $name"; PASS=$((PASS+1))
  else
    log "FAIL  ✘ ${name}（详见日志同名段落）"; FAIL=$((FAIL+1))
  fi
}

log "=== 验收 $MODULE · ${MODE_UPPER} 模式 · HEAD $(git rev-parse --short HEAD) · $(date '+%F %T') ==="

# ---- 1. 环境（对应 modules/INDEX.md 运行与验证手册） ----
if [ "$MODE" = ce ]; then
  export ST_EDITION=ce
  # 必须显式置空串而非 unset：config.py import 时 load_project_dotenv(override=False)
  # 会用仓库根 .env 回填未设置的变量；空串经 Settings.from_env 的 `or None` 归一为 None。
  export ST_CONTROL_PLANE_DSN= ST_REDIS_URL= ST_CELERY_BROKER_URL= ST_CELERY_RESULT_BACKEND=
  log "环境: ST_EDITION=ce, DSN/Redis/Celery 显式置空（压制 .env 回填）"
else
  : "${ST_CONTROL_PLANE_DSN:?EE 模式需设 ST_CONTROL_PLANE_DSN}"
  : "${ST_REDIS_URL:?EE 模式需设 ST_REDIS_URL}"
  log "环境: EE (DSN=${ST_CONTROL_PLANE_DSN%%@*}@***)"
fi

# ---- 2. 起后端并等就绪 ----
log "启动后端 :$PORT ..."
uv run novelvideo api --port "$PORT" >>"$LOG" 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT
READY=0
for _ in $(seq 1 30); do
  if curl -sf "localhost:$PORT/openapi.json" >/dev/null 2>&1; then READY=1; break; fi
  sleep 1
done
if [ "$READY" = 1 ]; then
  log "后端就绪"
else
  log "FAIL  ✘ 后端 30s 未就绪（若 CE 模式且 T1/T9 未落地，此为预期红）"
  FAIL=$((FAIL+1))
fi

# ---- 3. pytest 模块标记集（机器证据主体；标记注册见 testing.md §5） ----
MARKER=$(echo "$MODULE" | tr '[:upper:]' '[:lower:]')
check "pytest -m ${MARKER}（$MODE 集）" uv run pytest -m "$MARKER and not e2e" -q

# ---- 4. 模块专属 curl 冒烟（checks/<module>.sh，可按 \$MODE 分支） ----
CHECKS="$(dirname "$0")/checks/${MARKER}.sh"
if [ -f "$CHECKS" ]; then
  # shellcheck source=/dev/null
  source "$CHECKS"
else
  log "SKIP  - 无 ${CHECKS}（该模块冒烟检查待随模块 PR 落地）"
fi

# ---- 5. 汇总 ----
log "=== 结果: PASS=$PASS FAIL=$FAIL · 证据日志: $LOG ==="
[ "$FAIL" -eq 0 ]
