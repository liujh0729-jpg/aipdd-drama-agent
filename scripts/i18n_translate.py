#!/usr/bin/env python3
# SPDX-License-Identifier: Elastic-2.0
# [aipdd fork delta] 界面多语言的批量翻译管线（日/阿/法/西/俄等新增语种全量落地用）。
"""
从 `frontend/public/locales/zh/translation.json`（结构与原文的事实源）出发，为目标语种
生成/补全 `frontend/public/locales/<lang>/translation.json`：

- `--init <lang...>`：以 zh 结构深拷贝建立目标文件（所有叶子值先取 zh 原文）。文件即时
  结构完整、可加载；缺译字符串按 i18next `fallbackLng: zh` 的口径显示中文。幂等：已存在的
  文件只补齐 zh 有而目标缺的键，不覆盖已翻译的值。
- 翻译模式（默认）：对仍未翻译（值==zh 原文）的叶子，调用 OpenAI 兼容的 /chat/completions
  批量译成目标语言，保留 {{占位符}} / \\n / 复数后缀键，写回文件。带缓存，可反复续跑，只处理
  未译项，不动已译项。

端点凭据从环境变量或参数读，绝不写死、绝不入库：
  I18N_BASE_URL   例 https://api.aipdd.work/v1        （或 --base-url）
  I18N_API_KEY    st-...                              （或 --api-key，优先于命令行）
  I18N_MODEL      可译的文本模型                       （或 --model）

用法：
  python scripts/i18n_translate.py --init ja ar fr es ru
  I18N_BASE_URL=... I18N_API_KEY=... I18N_MODEL=... \\
    python scripts/i18n_translate.py --lang ja --batch 40
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request

LOCALE_DIR = os.path.join("frontend", "public", "locales")
SOURCE = "zh"
# i18next 复数后缀：这些键的原文不做整串翻译（含 count 语义），跳过即可。
PLACEHOLDER = re.compile(r"\{\{[^}]*\}\}")


def _read(lang: str) -> dict:
    with open(os.path.join(LOCALE_DIR, lang, "translation.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _write(lang: str, tree: dict) -> None:
    path = os.path.join(LOCALE_DIR, lang, "translation.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(tree, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _walk(node, prefix=""):
    """产出 (dotted_key, container, leaf_key) 三元组，逐个叶子字符串。"""
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str):
                yield f"{prefix}{k}", node, k
            else:
                yield from _walk(v, f"{prefix}{k}.")


def _clone_missing(dst: dict, src: dict) -> int:
    """把 src 里有、dst 里缺的键用 src 原文补进 dst；返回补入数。"""
    added = 0
    for key, container, leaf in list(_walk(src)):
        parts = key.split(".")
        cur = dst
        for p in parts[:-1]:
            nxt = cur.get(p)
            if not isinstance(nxt, dict):
                nxt = {}
                cur[p] = nxt
            cur = nxt
        if parts[-1] not in cur:
            cur[parts[-1]] = _get(src, parts)
            added += 1
    return added


def _get(tree: dict, parts: list[str]):
    cur = tree
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def cmd_init(langs: list[str]) -> int:
    src = _read(SOURCE)
    for lang in langs:
        path = os.path.join(LOCALE_DIR, lang, "translation.json")
        if os.path.exists(path):
            dst = _read(lang)
            added = _clone_missing(dst, src)
            _write(lang, dst)
            print(f"[init] {lang}: 已存在，补齐 {added} 个缺失键")
        else:
            dst = json.loads(json.dumps(src, ensure_ascii=False))
            _write(lang, dst)
            print(f"[init] {lang}: 依 zh 结构建立全量文件（值先取 zh，待翻译）")
    return 0


def _collect_untranslated(dst: dict, src: dict):
    items = []
    for key, _container, _leaf in _walk(src):
        parts = key.split(".")
        sv = _get(src, parts)
        dv = _get(dst, parts)
        if isinstance(sv, str) and sv and (dv is None or dv == sv):
            # 只译非占位符/纯符号的有意义串
            if PLACEHOLDER.sub("", sv).strip():
                items.append((key, sv))
    return items


def _cache_path(lang: str) -> str:
    d = os.path.join(LOCALE_DIR, ".i18n_mt_cache")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{lang}.json")


def _translate_batch(texts: list[str], lang: str, base: str, key: str, model: str) -> list[str]:
    joined = "\n".join(f"[{i}] {t}" for i, t in enumerate(texts))
    system = (
        f"你是 UI 本地化翻译引擎，把中文界面字符串翻译成语言代码 {lang} 对应的自然、地道、"
        "简洁的界面用语。严格只输出译文，逐行、与原行号一一对应，不输出解释、不加序号外的任何符号。"
        "必须原样保留所有 {{占位符}}、换行、HTML/Markdown 与专有名词（模型名、品牌名不译）。"
    )
    user = f"翻译以下 {len(texts)} 行（每行以 [行号] 开头，保持行号）：\n{joined}"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "stream": False,
    }
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.load(resp)
    content = data["choices"][0]["message"]["content"]
    out = {}
    for line in content.splitlines():
        m = re.match(r"\[(\d+)\]\s?(.*)$", line.strip())
        if m:
            out[int(m.group(1))] = m.group(2)
    return [out.get(i, texts[i]) for i in range(len(texts))]


def cmd_translate(lang: str, batch: int, base: str, key: str, model: str) -> int:
    if not (base and key and model):
        print("需要 I18N_BASE_URL / I18N_API_KEY / I18N_MODEL（或对应 --参数）才能翻译。", file=sys.stderr)
        return 2
    src = _read(SOURCE)
    dst = _read(lang)
    cache = {}
    cp = _cache_path(lang)
    if os.path.exists(cp):
        cache = json.load(open(cp, encoding="utf-8"))
    todo = _collect_untranslated(dst, src)
    print(f"[translate] {lang}: 待译 {len(todo)} 条（已缓存命中将跳过）")
    done = 0
    for i in range(0, len(todo), batch):
        chunk = todo[i : i + batch]
        results = []
        to_call = []
        for dk, sv in chunk:
            h = hashlib.sha256(f"{lang}|{sv}".encode()).hexdigest()[:16]
            if h in cache:
                results.append(cache[h])
            else:
                to_call.append((dk, sv, h))
                results.append(None)
        if to_call:
            try:
                tr = _translate_batch([sv for _, sv, _ in to_call], lang, base, key, model)
            except Exception as exc:  # noqa: BLE001
                print(f"[translate] 批次失败，停止（进度已落盘可续跑）：{exc}", file=sys.stderr)
                break
            for (dk, sv, h), text in zip(to_call, tr):
                cache[h] = text
                _set(dst, dk.split("."), text)
        for (dk, sv, _h), text in zip(
            [(a, b, None) for a, b in chunk],
            results,
        ):
            if text:
                _set(dst, dk.split("."), text)
        done += len(chunk)
        # 每批都落盘 + 落缓存，保证可续跑不丢
        _write(lang, dst)
        json.dump(cache, open(cp, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"  …{done}/{len(todo)}")
    return 0


def _set(tree: dict, parts: list[str], value: str) -> None:
    cur = tree
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", nargs="*", metavar="LANG", help="依 zh 建立/补齐目标语种文件")
    ap.add_argument("--lang", help="翻译目标语种（单个）")
    ap.add_argument("--batch", type=int, default=40)
    ap.add_argument("--base-url", default=os.environ.get("I18N_BASE_URL", ""))
    ap.add_argument("--api-key", default=os.environ.get("I18N_API_KEY", ""))
    ap.add_argument("--model", default=os.environ.get("I18N_MODEL", ""))
    args = ap.parse_args()

    if args.init is not None:
        langs = args.init or ["ja", "ar", "fr", "es", "ru"]
        return cmd_init(langs)
    if args.lang:
        return cmd_translate(args.lang, args.batch, args.base_url, args.api_key, args.model)
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
