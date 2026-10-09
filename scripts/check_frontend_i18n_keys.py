#!/usr/bin/env python3
"""前端 i18n **键命中**检查（补 check_frontend_i18n.py 的另一半）。

用法：
    python scripts/check_frontend_i18n_keys.py            # 对照 baseline 检查
    python scripts/check_frontend_i18n_keys.py --write    # 重新生成 baseline（消化完存量时才用）
    python scripts/check_frontend_i18n_keys.py --list     # 打印全部未命中，不比对 baseline

扫的是 `frontend/src` 下 .ts/.tsx 里**字面量** `t("a.b.c")` 的键，逐个到
`frontend/public/locales/{zh,en,vi}/translation.json` 里查路径是否存在；任一语种
没命中就算一处。

两个口径（都是踩过坑后定的，别顺手改严）：

* **复数形式算命中**：i18next 对带 `count` 的调用会去找 `key_one` / `key_other`，
  基名反而不存在（`canvas.assetLibrary.itemCount` 就是这样）。只盯基名会把
  正常的复数键报成缺失。
* **带 `defaultValue` 的调用不算硬失败**：那类 key 没命中时界面会回落到代码里的
  兜底文案，不是把键原文渲染出来；它们单独计数（翻译缺口），不阻断。
  真正会当场把 `settings.mediaStorage.fields.aipddApiKey` 这种原文贴到页面上的，
  是没有兜底的那些。


为什么要有这个关卡（req44 复盘）：给「设置 · 媒体存储」加 AIPDD API Key 输入时，
两个 label 键 `settings.mediaStorage.fields.aipddApiKey` / `.aipddBaseUrl` 被脚本
插到了**同名但不同父**的 `assets.scenes.fields` 里（那里也有一个 `"fields": {`）。
三语同样错位，于是「三语 key 集合一致」这类检查一路全绿，页面却把键原文当标签渲染
出来——只有真打开页面才看得见。所以命中检查必须按**调用点字面量**去验，而不是只比
较三份 locale 互相是否一致。

baseline 是棘轮：里面的条数只许减不许增，新出现的未命中键直接失败。动态拼键
（``t(`a.${x}`)``）扫不到，不在本关卡范围内。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

FRONTEND_SRC = os.path.join("frontend", "src")
LOCALE_DIR = os.path.join("frontend", "public", "locales")
LANGS = ("zh", "en", "vi")
BASELINE = os.path.join("scripts", "frontend_i18n_keys_allowlist.json")

# 只认引号里的字面量键；模板串/变量一律跳过（见模块 docstring）。
CALL = re.compile(r"""\bt\(\s*(["'])([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)\1""")
BLOCK_COMMENT = re.compile(r"(?<![\w'\"/])/\*.*?\*/", re.S)
# i18next 复数后缀（intl-pluralrules 的 category）。
PLURAL_SUFFIXES = ("zero", "one", "two", "few", "many", "other")
# `t(key, { … defaultValue … })`：没命中也有兜底文案，不贴键原文。
SOFT_LOOKAHEAD = 12


def _strip_comments(text: str) -> str:
    # 块注释按**原样长度**抹掉但**保留换行**，否则多行块注释会把后面的行号往上拽偏。
    text = BLOCK_COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    kept = []
    for line in text.splitlines():
        if re.match(r"^\s*(//|\*)", line):
            kept.append("")
            continue
        # 行尾 // 注释（跳过字符串里的 ://）
        kept.append(re.sub(r"(?<!:)//[^\n]*", "", line))
    return "\n".join(kept)


def collect_used_keys() -> tuple[dict[str, list[str]], dict[str, dict[str, bool]]]:
    """返回 (每个键的调用点, 每个键的调用形态)。

    形态里两个标记都是**按真实调用**判的，不能笼统往后看几行：同一个键既可能被
    `t(key)` 裸调、也可能 `t(key, { count })` 复数调，只要有一个裸调没兜底就会贴原文。
    所以这里取「任一调用点带 defaultValue」= soft、「任一调用点带 count」= plural，
    命中判定时宁可宽进（这两个标记只用来**降级**报告级别，不会掩护真缺失）。
    """
    used: dict[str, list[str]] = {}
    meta: dict[str, dict[str, bool]] = {}
    for root, _dirs, files in os.walk(FRONTEND_SRC):
        parts = root.split(os.sep)
        if "node_modules" in parts or "__tests__" in parts:
            # 跳过测试目录：里面的 t("…") 多为契约断言的样本键，不代表界面真会渲染。
            continue
        for name in files:
            if not name.endswith((".ts", ".tsx")):
                continue
            path = os.path.join(root, name)
            try:
                text = _strip_comments(
                    open(path, encoding="utf-8", errors="ignore").read()
                )
            except OSError:
                continue
            lines = text.splitlines()
            for lineno, line in enumerate(lines, 1):
                for match in CALL.finditer(line):
                    key = match.group(2)
                    used.setdefault(key, []).append(f"{path}:{lineno}")
                    tail = _call_tail(lines, lineno - 1, match.start())
                    flags = meta.setdefault(key, {"soft": False, "plural": False})
                    if "defaultValue" in tail:
                        flags["soft"] = True
                    if re.search(r"[{,]\s*count\s*:", tail):
                        flags["plural"] = True
    return used, meta


def _call_tail(lines: list[str], index: int, start_col: int) -> str:
    """取 `t(` 开头到括号配平为止的那段实参（最多 20 行，防注把整文件吃掉）。"""
    depth = 0
    chunks: list[str] = []
    for offset in range(0, 20):
        line = lines[index + offset][start_col:] if offset == 0 else (
            lines[index + offset] if index + offset < len(lines) else ""
        )
        chunks.append(line)
        depth += line.count("(") - line.count(")")
        if depth <= 0:
            break
    return "\n".join(chunks)


def load_locales() -> dict[str, dict]:
    out = {}
    for lang in LANGS:
        path = os.path.join(LOCALE_DIR, lang, "translation.json")
        with open(path, encoding="utf-8") as fh:
            out[lang] = json.load(fh)
    return out


def resolve(tree: dict, key: str, *, plural: bool = False) -> bool:
    """基名命中即算有；只有带 `count` 的调用才允许用**复数后缀**充数。"""
    if _has(tree, key):
        return True
    return bool(plural) and any(
        _has(tree, f"{key}_{suffix}") for suffix in PLURAL_SUFFIXES
    )


def _has(tree: dict, key: str) -> bool:
    node: object = tree
    for part in key.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return False
    return True


def find_missing(
    used: dict[str, list[str]],
    meta: dict[str, dict[str, bool]],
    locales: dict[str, dict],
) -> dict[str, list[str]]:
    return {
        key: sites
        for key, sites in sorted(used.items())
        if not all(
            resolve(locales[lang], key, plural=meta[key]["plural"])
            for lang in LANGS
        )
    }


def _absent_langs(locales: dict[str, dict], key: str, plural: bool) -> list[str]:
    return [
        lang for lang in LANGS if not resolve(locales[lang], key, plural=plural)
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="重写 baseline")
    parser.add_argument("--list", action="store_true", help="只打印未命中")
    args = parser.parse_args()

    if not os.path.isdir(LOCALE_DIR):
        print(f"✗ 找不到 locale 目录：{LOCALE_DIR}", file=sys.stderr)
        return 2
    used, meta = collect_used_keys()
    locales = load_locales()
    missing = find_missing(used, meta, locales)
    # soft：调用点带了 defaultValue，没命中也只是回落代码里的兜底文案（翻译缺口）；
    # hard：裸调没兜底 → 界面当场把键原文贴出来，这类才是本关卡要拦的。
    soft = {k for k in missing if meta[k]["soft"]}
    hard = {k: v for k, v in missing.items() if k not in soft}

    if args.list:
        for key, sites in hard.items():
            absent = _absent_langs(locales, key, meta[key]["plural"])
            print(f"✗ 裸调缺键 {key}  ({'/'.join(absent)} 缺)  ← {', '.join(sites[:3])}")
        for key in sorted(soft):
            absent = _absent_langs(locales, key, meta[key]["plural"])
            print(
                f"○ 兜底缺口 {key}  ({'/'.join(absent)} 缺)"
                f"  ← {', '.join(used[key][:3])}"
            )
        print(
            f"合计：裸调缺键 {len(hard)} 个 / 带兜底 {len(soft)} 个"
            f"（字面量键共 {len(used)} 个）。"
        )
        return 0

    if args.write:
        payload = {
            "_comment": (
                "已知「裸调无兜底」的未命中 i18n 字面量键（棘轮：只许减不许增）。"
                "带 defaultValue 的调用不记在这里——它们不贴键原文，只是翻译缺口。"
                "消化完请跑 --write 重生成，别顺手放宽。"
            ),
            "keys": sorted(hard),
            "sites": {k: v[:1] for k, v in sorted(hard.items())},
        }
        with open(BASELINE, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print(f"✓ 已写入 {BASELINE}（{len(hard)} 条）。")
        return 0

    allowed: set[str] = set()
    if os.path.exists(BASELINE):
        with open(BASELINE, encoding="utf-8") as fh:
            allowed = set(json.load(fh).get("keys") or [])

    new = sorted(set(hard) - allowed)
    fixed = sorted(allowed - set(hard))
    for key in new:
        absent = _absent_langs(locales, key, meta[key]["plural"])
        print(
            f"✗ 新增裸调缺键：{key}  ({'/'.join(absent)} 缺)  ← "
            + ", ".join(hard[key][:3]),
            file=sys.stderr,
        )
    if fixed:
        print("（以下存量键已被修好，可跑 --write 收紧 baseline）：" + ", ".join(fixed))
    if new:
        print(
            f"✗ i18n 键命中检查失败：新增 {len(new)} 个裸调缺键"
            f"（存量 {len(allowed & set(hard))} 个 / 带兜底缺口 {len(soft)} 个"
            f" / 字面量键共 {len(used)} 个）。",
            file=sys.stderr,
        )
        return 1
    print(
        f"✓ i18n 键命中检查通过：字面量键 {len(used)} 个，裸调缺键 {len(hard)} 个"
        f"（全在 baseline）、带兜底翻译缺口 {len(soft)} 个。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
