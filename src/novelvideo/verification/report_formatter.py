"""验证报告格式化与持久化。"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def format_verification_report(result: dict, beat_num: int, verify_type: str) -> str:
    """将 VerificationResult 格式化为 markdown 可读文本。

    Args:
        result: VerificationResult.model_dump() 的字典
        beat_num: beat 编号
        verify_type: "sketch" 或 "frame"
    """
    passed = result.get("passed", False)
    score = result.get("score", 0.0)
    type_label = "草图验证" if verify_type == "sketch" else "首帧验证"
    icon = "✅" if passed else "❌"
    status = "通过" if passed else "未通过"

    lines = [f"{icon} Beat {beat_num} {type_label}{status} ({score:.1f}/10)"]

    issues = result.get("issues", [])
    if not issues:
        lines.append("无事实错误")
    else:
        for issue in issues:
            severity = issue.get("severity", "info")
            desc = issue.get("description", "")
            lines.append(f"- [{severity}] {desc}")

    action = result.get("suggested_action", "none")
    if action and action != "none":
        lines.append(f"建议操作: {action}")

    return "\n".join(lines)


def format_consistency_report(data: dict, episode_num: int) -> str:
    """将 consistency verify 结果格式化为 markdown 表格。

    Args:
        data: verify_consistency 返回的字典
        episode_num: 集数
    """
    characters = data.get("characters", [])
    if not characters:
        return f"## 角色一致性报告 (EP{episode_num})\n\n无角色需要检查"

    lines = [
        f"## 角色一致性报告 (EP{episode_num})",
        "",
        "| 角色 | 脸部 | 服装 | 检查Beat | 结果 |",
        "|------|------|------|----------|------|",
    ]

    passed_count = 0
    for char in characters:
        name = char.get("character", "?")
        face = char.get("face_score", 0.0)
        clothing = char.get("clothing_score", 0.0)
        beats = char.get("beats_checked", [])
        beats_str = ",".join(str(b) for b in beats)
        char_passed = char.get("passed", False)
        icon = "✅" if char_passed else "❌"
        if char_passed:
            passed_count += 1
        lines.append(f"| {name} | {face:.1f} | {clothing:.1f} | {beats_str} | {icon} |")

    total = len(characters)
    lines.append("")
    lines.append(f"总结: {passed_count}/{total} 角色通过")

    return "\n".join(lines)


def format_color_verify_report(data: dict, episode_num: int) -> str:
    """将颜色验证结果格式化为 markdown 表格。"""
    beat_results = data.get("beat_results", [])
    if not beat_results:
        return f"## 草图颜色验证报告 (EP{episode_num})\n\n无 beat 需要检查"

    lines = [
        f"## 草图颜色验证报告 (EP{episode_num})",
        "",
        "| Beat | 状态 | 预期角色 | 检测到 | 缺失 | 多余 |",
        "|------|------|----------|--------|------|------|",
    ]
    status_icons = {"pass": "✅", "fail": "❌", "warn": "⚠️"}

    for br in beat_results:
        beat_num = br.get("beat_number", "?")
        status = br.get("status", "pass")
        icon = status_icons.get(status, "?")
        expected = ", ".join(br.get("expected", [])) or "-"
        detected = ", ".join(br.get("detected", [])) or "-"
        missing_names = ", ".join(m.get("identity_id", "") for m in br.get("missing", [])) or "-"
        extra_names = ", ".join(e.get("identity_id", "") for e in br.get("extra", [])) or "-"
        lines.append(
            f"| {beat_num} | {icon} | {expected} | {detected} | {missing_names} | {extra_names} |"
        )

    passed = data.get("passed_beats", 0)
    total = data.get("total_beats", 0)
    failed = data.get("failed_beats", 0)
    warned = data.get("warned_beats", 0)
    failed_nums = data.get("failed_beat_numbers", [])
    lines.append("")
    summary_parts = [f"{passed}/{total} beats 通过"]
    if failed:
        summary_parts.append(f"{failed} beats 失败 {failed_nums}")
    if warned:
        summary_parts.append(f"{warned} beats 警告")
    lines.append(f"总结: {', '.join(summary_parts)}")
    return "\n".join(lines)


def format_episode_overview_report(data: dict, episode_num: int) -> str:
    """将整集分镜总览结果格式化为 markdown。"""
    passed = data.get("overall_passed", False)
    total = data.get("total", 0.0)
    icon = "✅" if passed else "❌"
    status = "通过" if passed else "未通过"

    lines = [
        f"## 全局分镜审片 (EP{episode_num}) {icon} {status}",
        "",
        "| 维度 | 评分 |",
        "|------|------|",
        f"| 视觉节奏 | {data.get('visual_rhythm', 0):.1f} |",
        f"| 构图多样性 | {data.get('composition_diversity', 0):.1f} |",
        f"| 叙事弧线 | {data.get('narrative_arc', 0):.1f} |",
        f"| 风格统一 | {data.get('style_unity', 0):.1f} |",
        f"| **总分** | **{total:.1f}** |",
    ]

    issues = data.get("issues", [])
    if issues:
        lines.append("")
        lines.append("### Issues")
        for issue in issues:
            severity = issue.get("severity", "warning")
            beat = issue.get("beat_number", "?")
            desc = issue.get("description", "")
            action = issue.get("suggested_action", "info")
            related = issue.get("related_beats", [])
            related_str = f" (related: {related})" if related else ""
            lines.append(f"- [{severity}] Beat {beat}: {desc} → {action}{related_str}")
    else:
        lines.append("")
        lines.append("无全局性问题")

    scene_dist = data.get("scene_distribution", {})
    if scene_dist:
        lines.append("")
        lines.append("### 场景分配")
        for scene, beat_nums in scene_dist.items():
            lines.append(f"- {scene}: beats {beat_nums} ({len(beat_nums)} 个)")

    summary = data.get("summary", "")
    if summary:
        lines.append("")
        lines.append(f"总评: {summary}")
    return "\n".join(lines)


def save_verify_report(
    project_dir: Path,
    episode_num: int,
    beat_num: int | None,
    verify_type: str,
    data: dict,
) -> Path:
    """持久化报告到 verify_reports/ 目录，返回保存路径。

    文件只保留最新一条，覆盖写入。内部记录 timestamp 字段。

    Args:
        project_dir: 项目目录
        episode_num: 集数
        beat_num: beat 编号，consistency 时为 None
        verify_type: "sketch" | "frame" | "consistency"
        data: 完整的响应 data 字典
    """
    reports_dir = project_dir / "verify_reports" / f"ep{episode_num:03d}"
    reports_dir.mkdir(parents=True, exist_ok=True)

    if verify_type == "consistency":
        filename = "consistency.json"
    elif verify_type == "frame_consistency":
        filename = "frame_consistency.json"
    elif verify_type == "sketch_colors":
        filename = "sketch_colors.json"
    elif verify_type == "episode_overview":
        filename = "episode_overview.json"
    elif verify_type in ("continuity", "similarity", "sketch_select"):
        filename = f"{verify_type}.json"
    else:
        filename = f"beat_{beat_num:02d}_{verify_type}.json"

    report_path = reports_dir / filename

    # 附加 timestamp
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **data,
    }

    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Report saved: %s", report_path)
    return report_path
