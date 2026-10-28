#!/usr/bin/env python3
"""查一个模型名在本地环境里还残留哪些地方（只读）。

改名 / 下架一个模型后，最难受的不是它本身没了，而是**名字还散落在各处**：网关的
`channels.models` 里还留着、别名表的 target 还指着、功能位实测缓存里还记着、项目
data.db 的历史任务里还带着它 —— 于是下一次调用又撞回
``No available channel for model X under group default (distributor)``（HTTP 503，
报错来自网关，用户完全无从下手）。

这个脚本把「名字到底还写在哪」一次列全，并给出对应的清理动作。它**只读**：不写任何
库、不改任何配置，判定完就退出。

用法：

    # 本机（默认按 config.STATE_DIR 找 settings / 网关库）
    uv run python scripts/scan_model_remnants.py Acme-IMG-2

    # 容器里（部署环境的真源都在 /data）
    docker exec aipdd-drama-api /app/.venv/bin/python /app/scripts/scan_model_remnants.py \
        Acme-IMG-2 --state-dir /data/state

    # 多个名字、额外指定网关库
    uv run python scripts/scan_model_remnants.py ap-old-llm DC-old-slot \
        --gateway-db /path/to/localapi.db

退出码：0 = 一处都没找到；1 = 有残留（按行打印位置与清理动作）。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

# 网关（localapi）库里值得看的表与列：名字可能出现在渠道声明、映射、路由能力位、
# 令牌白名单、异步任务与自定义模型元数据里。``logs`` 只看数量（历史记录，不作为
# 可清理项报告 —— 清它没有意义）。
GATEWAY_SCAN: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("channels", ("name", "models", "model_mapping", "test_model", "other", "settings")),
    ("abilities", ("model",)),
    ("models", ("name", "description", "properties")),
    ("vendors", ("name",)),
    ("prefill_groups", ("name", "items")),
    ("tokens", ("name", "models", "allowed_models")),
    ("tasks", ("data", "payload")),
    ("options", ("key", "value")),
)

# settings.db 里与模型名相关的键（runtime_settings 表，值多为 JSON）。
SETTINGS_TABLE_SQL = "select key, value from runtime_settings"

# 各位置对应的清理动作：告诉用户去哪儿点，而不是让他去写 SQL。
CLEAN_HINTS: dict[str, str] = {
    "gateway.channels": "「设置 → 模型与渠道」里对应渠道：把该名字从渠道模型清单删掉，"
    "或直接把渠道删掉重配（渠道的 models / model_mapping 由系统按目录 + 绑定重新投影）",
    "gateway.abilities": "改完渠道后重启网关（ abilities 是渠道投影出来的路由表，"
    "手工删会被下一次同步重建）",
    "gateway.models": "「设置 → 模型与渠道」的自定义模型 / 供应商元数据里删掉该名字",
    "gateway.prefill_groups": "网关的分组预填里删掉该名字",
    "gateway.tokens": "令牌（API Key）的模型白名单里删掉该名字",
    "gateway.tasks": "排队 / 进行中的异步任务里还带着它：取消对应任务后重提",
    "gateway.logs": "历史调用日志（只影响你翻旧账看到它，不影响调用）：可按需清理日志",
    "settings.alias": "「设置 → 模型与渠道」里把绑到这个模型的功能位改选别的（别名表 target）",
    "settings.media": "「设置 → 模型与渠道」的默认图片 / 视频 / 向量模型里换掉它",
    "settings.channels": "「设置 → 模型与渠道」的渠道存档里删掉该名字，然后重新同步清单",
    "settings.catalog": "「设置 → 模型与渠道」对应渠道点「同步模型清单」覆盖快照",
    "settings.probe": "「设置 → 模型与渠道」的工具页实测缓存：重新探测一次即可覆盖",
    "settings.other": "「设置 → 模型与渠道」里核对这个键，改选或删除",
    "project.config": "该项目 project_config.json 里记录了它：在项目设置里改选模型",
    "project.db": "项目历史任务 / 产物里带着它（只影响重放旧任务）：重新生成即可，"
    "无需改库",
    "env": "启动配置（.env / compose / 环境变量）里写死了它：改掉这一行并重启容器",
}

# settings.db 键名 → 清理动作分类（前缀匹配）。
SETTINGS_KEY_HINT: tuple[tuple[str, str], ...] = (
    ("custom_newapi_model_alias_mappings", "settings.alias"),
    ("custom_newapi_media_model_mappings", "settings.media"),
    ("custom_newapi_embedding", "settings.media"),
    ("custom_newapi_default_media_model", "settings.media"),
    ("custom_newapi_provider_channels", "settings.channels"),
    ("custom_newapi_upstream_models", "settings.catalog"),
    ("_model_probe", "settings.probe"),
)


@dataclass
class Finding:
    """一处残留：位置、命中次数、上下文片段、清理动作。"""

    source: str
    location: str
    hits: int
    sample: str
    hint: str

    def line(self) -> str:
        sample = self.sample.replace("\n", " ")[:120]
        return (
            f"[{self.source}] {self.location} ×{self.hits}\n"
            f"    片段: {sample}\n"
            f"    清理: {self.hint}"
        )


def _iter_json_paths(value: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    """把嵌套结构摊成 ``(dotted.path, 标量值)``，便于指到具体哪一项命中。"""

    if isinstance(value, dict):
        for key, sub in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield from _iter_json_paths(sub, path)
    elif isinstance(value, list):
        for index, sub in enumerate(value):
            yield from _iter_json_paths(sub, f"{prefix}[{index}]")
    else:
        yield prefix, value


def _matches(text: str, names: list[str]) -> int:
    lowered = text.lower()
    return sum(lowered.count(name.lower()) for name in names)


def _parse_json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001 - 非 JSON 的值按纯文本处理
        return None


def _settings_hint(key: str) -> str:
    for prefix, hint in SETTINGS_KEY_HINT:
        if prefix in key or key.endswith(prefix):
            return CLEAN_HINTS[hint]
    return CLEAN_HINTS["settings.other"]


def scan_settings_db(path: Path, names: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    if not path.exists():
        return findings
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = list(con.execute(SETTINGS_TABLE_SQL))
    except sqlite3.Error:  # 表不存在 / 库不是这套 schema
        con.close()
        return findings
    con.close()
    for key, value in rows:
        raw = str(value or "")
        if not _matches(raw, names):
            continue
        parsed = _parse_json(raw)
        locations: list[str] = []
        if isinstance(parsed, (dict, list)):
            for dot_path, scalar in _iter_json_paths(parsed):
                if isinstance(scalar, str) and _matches(scalar, names):
                    locations.append(dot_path)
                elif scalar is not None and _matches(str(scalar), names):
                    locations.append(dot_path)
        hits = _matches(raw, names)
        sample = ", ".join(locations[:4]) if locations else raw[:120]
        findings.append(
            Finding(
                source="settings",
                location=f"{path.name}:{key} → {sample}",
                hits=hits,
                sample=sample or raw[:120],
                hint=_settings_hint(key),
            )
        )
    return findings


def scan_gateway_db(path: Path, names: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    if not path.exists():
        return findings
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {
            row[0]
            for row in con.execute("select name from sqlite_master where type='table'")
        }
    except sqlite3.Error:
        con.close()
        return findings
    for table, columns in GATEWAY_SCAN:
        if table not in tables:
            continue
        cols = [
            row[1]
            for row in con.execute(f'pragma table_info("{table}")')
            if row[1] in columns
        ]
        for column in cols:
            try:
                rows = list(
                    con.execute(
                        f'select rowid, "{column}" from "{table}" '
                        f'where lower(cast("{column}" as text)) like ?',
                        (f"%{names[0].lower()}%",),
                    )
                )
            except sqlite3.Error:
                continue
            for rowid, cell in rows:
                text = str(cell or "")
                hits = _matches(text, names)
                if not hits:
                    continue
                findings.append(
                    Finding(
                        source="gateway",
                        location=f"{path.name}:{table}.{column}#rowid={rowid}",
                        hits=hits,
                        sample=text,
                        hint=CLEAN_HINTS.get(
                            f"gateway.{table}", CLEAN_HINTS["gateway.channels"]
                        ),
                    )
                )
        if table == "logs" and "logs" in tables:
            # 日志单独统计：不作为残留报出，只在摘要里说明「历史里出现过 N 次」。
            total = 0
            for name in names:
                try:
                    total += con.execute(
                        'select count(*) from logs where lower(cast(model_name as text)) like ?',
                        (f"%{name.lower()}%",),
                    ).fetchone()[0]
                except sqlite3.Error:
                    continue
            if total:
                findings.append(
                    Finding(
                        source="gateway",
                        location=f"{path.name}:logs.model_name（历史调用记录）",
                        hits=total,
                        sample="历史记录，不参与路由",
                        hint=CLEAN_HINTS["gateway.logs"],
                    )
                )
    con.close()
    return findings


def scan_project_state(state_dir: Path, names: list[str]) -> list[Finding]:
    """项目层：project_config.json 与每个项目的 data.db。"""

    findings: list[Finding] = []
    for config_path in sorted(state_dir.glob("local/*/project_config.json")):
        try:
            raw = config_path.read_text(encoding="utf-8")
        except OSError:
            continue
        if _matches(raw, names):
            parsed = _parse_json(raw) or {}
            spots = [
                dot_path
                for dot_path, scalar in _iter_json_paths(parsed)
                if isinstance(scalar, str) and _matches(scalar, names)
            ]
            findings.append(
                Finding(
                    source="project",
                    location=f"{config_path} → {', '.join(spots[:4]) or 'raw'}",
                    hits=_matches(raw, names),
                    sample=raw[:120],
                    hint=CLEAN_HINTS["project.config"],
                )
            )
    for db_path in sorted(state_dir.glob("local/*/data.db")):
        findings.extend(scan_settings_like_db(db_path, names))
    return findings


def scan_settings_like_db(path: Path, names: list[str]) -> list[Finding]:
    """项目 data.db：全表全文扫，只报命中次数（历史产物，不需要手工改）。"""

    findings: list[Finding] = []
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [
            row[0]
            for row in con.execute("select name from sqlite_master where type='table'")
        ]
    except sqlite3.Error:
        con.close()
        return findings
    for table in tables:
        cols = [row[1] for row in con.execute(f'pragma table_info("{table}")')]
        for column in cols:
            hits = 0
            sample = ""
            for name in names:
                try:
                    found = con.execute(
                        f'select count(*), min(cast("{column}" as text)) from "{table}" '
                        f'where lower(cast("{column}" as text)) like ?',
                        (f"%{name.lower()}%",),
                    ).fetchone()
                except sqlite3.Error:
                    continue
                if found and found[0]:
                    hits += int(found[0])
                    sample = sample or str(found[1] or "")[:120]
            if hits:
                findings.append(
                    Finding(
                        source="project",
                        location=f"{path.parent.name}:{path.name}:{table}.{column}",
                        hits=hits,
                        sample=sample,
                        hint=CLEAN_HINTS["project.db"],
                    )
                )
    con.close()
    return findings


def scan_text_files(paths: list[Path], names: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        if not path or not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            hits = _matches(line, names)
            if not hits:
                continue
            # 注释行同样要报：compose 里注释掉的默认值也会被解注释后复活。
            findings.append(
                Finding(
                    source="env",
                    location=f"{path}:{number}",
                    hits=hits,
                    sample=line.strip(),
                    hint=CLEAN_HINTS["env"],
                )
            )
    return findings


def default_state_dir() -> Path:
    try:
        sys.path.insert(0, str(REPO_ROOT / "src"))
        from novelvideo import config  # type: ignore

        return Path(config.STATE_DIR)
    except Exception:  # noqa: BLE001 - 读不到 config 就退回常见相对路径
        return REPO_ROOT / "state"


REPO_ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__ or "")
    parser.add_argument("names", nargs="+", help="要追的模型名（可多个）")
    parser.add_argument("--state-dir", default="", help="状态目录（默认 config.STATE_DIR）")
    parser.add_argument(
        "--gateway-db",
        default="",
        help="网关 localapi.db 路径（默认 <state>/newapi/localapi.db 与 <state>/_shared/oneapi.db）",
    )
    parser.add_argument(
        "--text-file",
        action="append",
        default=[],
        help="额外全文检查的文件（可重复；默认查 .env / docker-compose*.yml）",
    )
    parser.add_argument(
        "--skip-projects", action="store_true", help="跳过项目 data.db（库大时用）"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    names = [str(n).strip() for n in args.names if str(n).strip()]
    state_dir = Path(args.state_dir or default_state_dir()).resolve()

    settings_paths = [
        state_dir / "local" / "settings.db",
        state_dir / "settings.db",
        state_dir / "_shared" / "settings.db",
    ]
    gateway_paths = (
        [Path(args.gateway_db)]
        if args.gateway_db
        else [
            state_dir / "newapi" / "localapi.db",
            state_dir / "_shared" / "oneapi.db",
        ]
    )
    text_paths = [
        Path(p)
        for p in (
            list(args.text_file)
            + [
                str(REPO_ROOT / ".env"),
                str(REPO_ROOT / ".env.example"),
                *[str(p) for p in sorted(REPO_ROOT.glob("docker-compose*.yml"))],
            ]
        )
    ]

    findings: list[Finding] = []
    for path in settings_paths:
        findings.extend(scan_settings_db(path, names))
    for path in gateway_paths:
        findings.extend(scan_gateway_db(path, names))
    if not args.skip_projects:
        findings.extend(scan_project_state(state_dir, names))
    findings.extend(scan_text_files(text_paths, names))

    print(f"扫描目录: {state_dir}")
    print(f"名字: {', '.join(names)}")
    actionable = [f for f in findings if "历史记录" not in f.location]
    if not findings:
        print("✓ 一处都没找到：这个名字在本地已经没有残留。")
        return 0
    for finding in findings:
        print(finding.line())
    print(
        f"\n合计 {len(findings)} 处命中（其中 {len(actionable)} 处需要你处理，"
        f"其余是历史日志 / 历史产物）。"
    )
    return 1 if actionable else 0


if __name__ == "__main__":
    raise SystemExit(main())
