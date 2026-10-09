#!/usr/bin/env bash
# SPDX-License-Identifier: Elastic-2.0
# [aipdd fork delta] 构建前磁盘守卫：把「镜像建到一半 No space left on device」变成一条命令的确定性动作。
#
# 为什么要脚本化：req39 那次 `docker build -f Dockerfile -t aipdd/dramaclaw-api:2.0.96 .` 在
# uv 复制 cognee wheel 阶段直接 `No space left on device` 失败。Docker 虚拟机（本机是 Colima）
# 的数据盘只有 25 GB，一次构建要 5 GB+ 临时层，dangling 镜像 + 上一版标签很容易把它吃干。
#
# 决定性事实（别对着脚本猜）：Colima 的 datadisk 是**预分配**文件
# （~/.colima/_lima/_disks/colima/datadisk，real == apparent），把配额调大要求 macOS
# 宿主**立刻**让出等量空间；宿主余量不足时调大配额等于把 Mac 逼到写满。所以这里的长期解是
# 「每次构建前确定性地清」，而不是赌一劳永逸的扩容。
#
# 用法：
#   bash scripts/deploy_disk_guard.sh              # 默认要求 VM 可用 >= 8 GB
#   bash scripts/deploy_disk_guard.sh 12           # 指定阈值（GB）
#
# 只读为主：只删 dangling 镜像与被当前版本取代的旧标签，**绝不碰数据卷、绝不碰在跑镜像**。
#
# 变量一律小写：仓库的 env 棘轮（scripts/check_env_config.py）会把 shell 里
# 「大写下划线」形式的 ${NAME} 当作运维 env 键要求登记进 .env.example。
set -euo pipefail

min_free_gb="${1:-8}"
current_version="$(grep -E '^DRAMACLAW_VERSION=' .env 2>/dev/null | tail -1 | cut -d= -f2 || true)"
repos=(aipdd/dramaclaw-api aipdd/dramaclaw-web)

echo "=== 现状 ==="
docker system df
echo "--- 宿主 ---"
df -h /System/Volumes/Data | tail -1
echo "current DRAMACLAW_VERSION=${current_version:-（未设置）}  阈值=${min_free_gb}GB"

echo "=== 清 dangling 镜像 ==="
docker image prune -f

echo "=== 清被取代的旧标签 ==="
running="$(docker ps --format '{{.Image}}')"
for repo in "${repos[@]}"; do
  while read -r tag; do
    [ -z "$tag" ] && continue
    image="${repo}:${tag}"
    [ -n "$current_version" ] && [ "$tag" = "$current_version" ] && continue
    if echo "$running" | grep -qx "$image"; then
      echo "跳过（在跑）$image"
      continue
    fi
    echo "删除 $image"
    docker image rm "$image" >/dev/null || true
  done < <(docker images --format '{{.Tag}}' "$repo" 2>/dev/null | grep -v '^<none>$' || true)
done

echo "=== 清理后 ==="
docker system df
avail_kb="$(df -k ~/.colima 2>/dev/null | tail -1 | awk '{print $4}')"
# 用**已经在跑的容器**问 VM 还剩多少：不再额外拉起镜像（拉镜像本身就要占盘）。
probe_container="$(docker ps --format '{{.Names}}' | head -1)"
vm_avail_gb=""
if [ -n "$probe_container" ]; then
  vm_avail_gb="$(docker exec "$probe_container" df -k / 2>/dev/null | tail -1 | awk '{printf "%d", $4/1024/1024}' || true)"
fi
host_avail_gb="$(df -g /System/Volumes/Data | tail -1 | awk '{print $4}')"
echo "VM 可用约 ${vm_avail_gb:-?} GB（探测容器 ${probe_container:-无}） / 宿主可用约 ${host_avail_gb:-?} GB（~/.colima 挂载 kB=${avail_kb:-?}）"

if [ -z "${vm_avail_gb:-}" ] || [ "$vm_avail_gb" -lt "$min_free_gb" ]; then
  cat >&2 <<MSG
磁盘守卫未通过：Docker 虚拟机可用 ${vm_avail_gb:-未知} GB < 阈值 ${min_free_gb} GB。
继续构建大概率会再次 No space left on device。按顺序做：
  1) docker builder prune -f          # 清构建缓存（通常还能再腾几个 GB）
  2) docker system prune -a --volumes=false   # 清未被容器使用的镜像（别加 --volumes）
  3) 宿主腾空间后再谈扩容：Colima datadisk 预分配，调大配额要求 macOS 立刻让出等量空间。
MSG
  exit 1
fi
echo "磁盘守卫通过，可以开始 docker build" >&2
