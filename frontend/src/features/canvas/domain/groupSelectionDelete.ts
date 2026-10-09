// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { CANVAS_NODE_TYPES, type CanvasNode } from "./canvasNodes";
import { isPresetManagedNode } from "./mainlineNodeFlags";

/**
 * 批量删除时,「所选节点」实际可删的 id 集合。
 *
 * 为什么不能直接用 selected 节点:画布的框选(见 Canvas 的自定义 marquee)会把
 * 「包住其它命中节点的组」从选择里剔除——这样拖动多选时父子不会双重位移。代价是
 * 用户框选整个组时,组本身的 `selected` 始终为假,只有组内子节点被选中。若批量删除
 * 只删 selected,就会把子节点删光、留下空组框(#62 后续 bug)。
 *
 * 这里补回那些「即将被清空」的组:某个组的每个子节点都在选择内 ⇒ 删除会清空它 ⇒
 * 把这个空壳组也一并删掉。preset/主线锁定的组(及含锁定子节点、无法真正清空的组)
 * 保持排除。store 的 deleteNodes 会自动级联删除后代,故只需把组 id 补进来即可。
 */
export function collectBatchDeletableIds(
  nodes: CanvasNode[],
  selectedIds: Iterable<string>,
): string[] {
  const selectedSet = new Set(selectedIds);

  const deletable = new Set<string>();
  for (const node of nodes) {
    if (selectedSet.has(node.id) && !isPresetManagedNode(node)) {
      deletable.add(node.id);
    }
  }

  // 一次遍历统计每个父节点的子节点总数 / 已选数 / 是否含锁定子节点。
  const childTally = new Map<
    string,
    { total: number; selected: number; hasLocked: boolean }
  >();
  for (const node of nodes) {
    if (!node.parentId) {
      continue;
    }
    const tally =
      childTally.get(node.parentId) ?? { total: 0, selected: 0, hasLocked: false };
    tally.total += 1;
    if (selectedSet.has(node.id)) {
      tally.selected += 1;
    }
    if (isPresetManagedNode(node)) {
      tally.hasLocked = true;
    }
    childTally.set(node.parentId, tally);
  }

  // 补回会被清空的普通组:全部子节点都被选中、无锁定子节点、组本身未锁定。
  for (const node of nodes) {
    if (node.type !== CANVAS_NODE_TYPES.group) {
      continue;
    }
    if (deletable.has(node.id) || isPresetManagedNode(node)) {
      continue;
    }
    const tally = childTally.get(node.id);
    if (tally && tally.total > 0 && tally.total === tally.selected && !tally.hasLocked) {
      deletable.add(node.id);
    }
  }

  return Array.from(deletable);
}
