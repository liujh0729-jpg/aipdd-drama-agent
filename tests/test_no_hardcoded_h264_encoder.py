"""[aipdd fork delta] 静态守卫：H.264 编码器名只许出现在解析器与它的默认值里。

桌面装机版随包的 ffmpeg 是 LGPL 构建、没有 x264（实测 ``-c:v libx264`` 退出码 8），
所以老链路过去那些把 ``libx264`` 当字面量塞进 argv 的位置全部收口到
``src/novelvideo/video_encoder.py`` 的 ``video_encode_args()`` / ``pick_h264_encoder()``。
这条守卫钉的是「收口之后别再散回去」：任何一处新写 ``-c:v libx264`` 都能让装机版
重新报 ``Unknown encoder``，而这种回归没有任何单测会红——链路测试钉的是命令形状，
不是编码器名。

规则两条：

1. ``src/novelvideo`` 下出现 ``libx264`` 的文件只能是允许清单里那两个；
2. 允许清单不留死条目（棘轮）：登记过的位置若代码里已经消失，同样失败。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src" / "novelvideo"

# - video_encoder.py：阶梯与探测式回退，唯一产生 ``-c:v`` 的地方。
# - config.py：VIDEO_CODEC 的默认值，供 .env 覆盖，读它的人现在都先过探测。
ALLOWED = {"video_encoder.py", "config.py"}


def _hits() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for path in sorted(SRC_ROOT.rglob("*.py")):
        lines = [
            index
            for index, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
            )
            if "libx264" in line
        ]
        if lines:
            found[path.name] = lines
    return found


def test_only_the_resolver_names_x264() -> None:
    hits = _hits()
    unexpected = {name: lines for name, lines in hits.items() if name not in ALLOWED}
    assert not unexpected, (
        "这些地方又把 libx264 写死了，装机版（LGPL ffmpeg，无 x264）会报 Unknown encoder："
        + "；".join(f"{name}:{','.join(map(str, lines))}" for name, lines in unexpected.items())
        + "。请改用 video_encoder.video_encode_args()。"
    )


def test_the_allowlist_has_no_dead_entries() -> None:
    missing = ALLOWED - set(_hits())
    assert not missing, f"允许清单里有死条目：{sorted(missing)} 已不再出现 libx264，请从清单删掉"
