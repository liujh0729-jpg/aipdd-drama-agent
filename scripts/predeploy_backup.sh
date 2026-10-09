#!/usr/bin/env bash
# SPDX-License-Identifier: Elastic-2.0
# [aipdd fork delta] 部署前状态库备份：把 api 容器 /data 下的全部 SQLite 与网关
# one-api.db 一致性快照到宿主机 backups/predeploy-<stamp>/，并逐个跑 integrity_check。
#
# 为什么要脚本化：容器镜像是精简构建，**容器内没有 sqlite3 CLI**，只能走 Python
# sqlite3 的 Backup API（在线备份，读写中也能拿到一致快照）。这段代码过去每次部署
# 都临时手写一遍 heredoc，容易漏库、漏校验——固化成一条命令。
#
# 用法：
#   bash scripts/predeploy_backup.sh                     # stamp 自动取时间戳
#   bash scripts/predeploy_backup.sh req36               # 指定 stamp（目录名带需求编号）
#   bash scripts/predeploy_backup.sh req36 <api容器> <网关容器>
#
# 变量一律小写：仓库的 env 棘轮（scripts/check_env_config.py）会把 shell 里
# 「大写下划线」形式的 ${NAME} 当作运维 env 键要求登记进 .env.example，
# 脚本内部量不该占那个清单。
set -euo pipefail

stamp="${1:-$(date +%Y%m%d_%H%M%S)}"
api_container="${2:-aipdd-drama-api}"
gateway_container="${3:-aipdd-dramaclaw-new-main-newapi-1}"
dest="backups/predeploy-${stamp}"
mkdir -p "$dest/api"

echo "stamp=${stamp}" | tee "$dest/_backup.log"
echo "=== api state dbs ===" | tee -a "$dest/_backup.log"
# 容器里先备份到 /tmp，再整体拷出：避免直接把 SQLite 写到 virtiofs 挂载点。
docker exec -i "$api_container" python - /tmp/predeploy-dbs <<'PY' | tee -a "$dest/_backup.log"
"""在容器内跑：对 /data 下所有 .db 做在线备份 + integrity_check，结果写 stdout。"""
import pathlib
import sqlite3
import sys

DATA = pathlib.Path("/data")
out = pathlib.Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
dbs = [DATA / "settings.db"]
dbs += sorted(DATA.rglob("*.db"))
seen = set()
for src in dbs:
    if not src.is_file() or src.resolve() in seen:
        continue
    seen.add(src.resolve())
    name = str(src.relative_to(DATA)).replace("/", "__")
    target = out / name
    try:
        with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as source, \
                sqlite3.connect(target) as tar:
            source.backup(tar)
            check = tar.execute("PRAGMA integrity_check").fetchone()[0]
        print(f"OK {src} -> {name} {target.stat().st_size} integrity={check}")
    except Exception as exc:  # noqa: BLE001  单库失败不阻断其余备份
        print(f"FAIL {src}: {type(exc).__name__}: {exc}")
PY
docker cp "$api_container":/tmp/predeploy-dbs/. "$dest/api/"
docker exec "$api_container" rm -rf /tmp/predeploy-dbs

echo "=== gateway ===" | tee -a "$dest/_backup.log"
if docker ps --format '{{.Names}}' | grep -qx "$gateway_container"; then
  docker exec "$gateway_container" sh -c \
    'cp /data/one-api.db /tmp/one-api.db && echo "snapshot=$(stat -c %s /tmp/one-api.db)"' \
    | tee -a "$dest/_backup.log"
  docker cp "$gateway_container":/tmp/one-api.db "$dest/newapi-one-api.db"
  docker exec "$gateway_container" rm -f /tmp/one-api.db
  echo "copied -> $dest/newapi-one-api.db" | tee -a "$dest/_backup.log"
else
  echo "跳过网关（容器 $gateway_container 未运行）" | tee -a "$dest/_backup.log"
fi

echo "=== integrity（宿主侧复核）===" | tee -a "$dest/_backup.log"
find "$dest" -name '*.db' ! -name '*-shm' ! -name '*-wal' -print0 |
  while IFS= read -r -d '' db; do
    printf '%s %s\n' "$db" "$(python3 -c "
import sqlite3, sys
try:
    con = sqlite3.connect(f'file:{sys.argv[1]}?mode=ro', uri=True)
    print(con.execute('PRAGMA integrity_check').fetchone()[0])
except Exception as exc:  # noqa: BLE001
    print('ERR', exc)
" "$db")"
  done | tee -a "$dest/_backup.log"

echo "备份完成：$dest" >&2
