# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""从飞书文档素材生成动效大片图册的海报、原片素材包，并把 sha256 写回清单。

图册的设计是「海报随包、原片按需」（见 `novelvideo/freezone/motion_media.py`）：
安装包只带 37 张静帧海报（几 MB），原片合计约 0.72 GB 由运维传到 OSS/CDN 或镜像
目录，用户点「播放原片」时才落到本机状态卷。这个脚本产出这三样东西：

1. 随包海报：`src/novelvideo/freezone/motion_posters/<case_id>/poster.webp`
   —— 用 ffmpeg 在开头一处抽帧，缩到长边 1280。
2. 可上传的原片包：`<pack-out>/<case_id>/<原文件名>` —— 上层目录名就是清单里的
   case_id，与 `MOTION_GALLERY_MIRROR` 的 `<mirror>/<case_id>/<file>` 取法对齐，
   整个目录拖上对象存储即可，不用改文件名。
3. 清单回填：每条案例的 `media.sha256` 按真实字节算好写回 `motion_templates.json`，
   运行时下载才有得校验 —— 镜像给错文件不会静默播成别人的片子。

原片来自飞书文档表格里的素材块。`--download` 会调 `lark-cli docs +media-download`
按 token 逐个拉（需要已登录的 lark-cli）；不想重复拉就把已下载的目录用 `--videos-dir`
传进来，脚本按 `<videos-dir>/<case_id>/<file>` 与 `<videos-dir>/<file>` 两种摆法找。

用法:

    .venv/Scripts/python.exe scripts/build_motion_pack.py --download
    .venv/Scripts/python.exe scripts/build_motion_pack.py \\
        --videos-dir ~/Downloads/motion --pack-out dist/motion_pack --ids N09,M43
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
# 随包 ffmpeg 是 LGPL 构建，不含 libx264：编码器必须走仓库统一的那条探测阶梯，
# 不能在这里写死 -c:v libx264。
from novelvideo.video_encoder import NoH264Encoder, pick_h264_encoder  # noqa: E402
DEFAULT_MANIFEST = REPO_ROOT / "src" / "novelvideo" / "freezone" / "motion_templates.json"
DEFAULT_POSTERS = REPO_ROOT / "src" / "novelvideo" / "freezone" / "motion_posters"
DEFAULT_PREVIEWS = REPO_ROOT / "src" / "novelvideo" / "freezone" / "motion_previews"
# 本机没装 ffmpeg 时用随包的桌面包二进制。
BUNDLED_FFMPEG = REPO_ROOT / "desktop" / "src-tauri" / "resources" / "bin" / "ffmpeg.exe"

POSTER_MAX_EDGE = 1280
POSTER_QUALITY = 78
POSTER_SAMPLE_SECONDS = 1.0
# 图册卡片悬停、详情首屏和工具面板都先放这段小样片：6 秒、480p、无声，37 条合计
# 十兆上下。原片仍然按需取，样片只回答「这片子动起来是什么样」。
PREVIEW_BASENAME = "preview.mp4"
PREVIEW_SECONDS = 6.0
PREVIEW_MAX_EDGE = 480
PREVIEW_CRF = 31
# 非 x264 编码器（h264_mf / libopenh264）不认 -crf，只能按码率。
PREVIEW_BITRATE = "600k"
PREVIEW_FPS = 24
# 开头一秒钟常常是片头淡入、纯黑或只有一颗点的空白构图（M08、M43 都是），拿它当
# 海报在图墙里看着像坏图。所以按片长取几个候选点，抽完比一下画面明暗离散度，
# 取最有内容的那一帧。
POSTER_CANDIDATE_FRACTIONS = (0.08, 0.25, 0.5, 0.8)


def _grab_frame(ffmpeg: str, video: Path, at: float, out: Path, max_edge: int) -> bool:
    """在 `at` 秒抽一帧到 out（PNG）。取不到画面回 False，不抛 —— 候选点本来就有多处。"""
    vf = f"scale={max_edge}:{max_edge}:force_original_aspect_ratio=decrease"
    try:
        subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
             "-ss", f"{at}", "-i", str(video), "-frames:v", "1", "-vf", vf, str(out)],
            check=True,
        )
    except subprocess.CalledProcessError:
        return False
    return out.is_file() and out.stat().st_size > 0


def _luma_spread(path: Path) -> float:
    """帧的明暗离散度（灰度标准差）。黑场和一片空白的构图都接近 0，有内容的画面高。"""
    with Image.open(path) as image:
        gray = image.convert("L").resize((64, 36))
        hist = gray.histogram()
        total = sum(hist) or 1
        mean = sum(index * count for index, count in enumerate(hist)) / total
        variance = sum(count * (index - mean) ** 2 for index, count in enumerate(hist)) / total
        return variance ** 0.5


def poster_candidates(start: float, duration: float | None) -> list[float]:
    points = [start]
    if duration and duration > 0:
        points.extend(round(duration * fraction, 2) for fraction in POSTER_CANDIDATE_FRACTIONS)
    # 去重并保证不早于给定起点，避免 1 秒的短片被排到片长之外。
    unique: list[float] = []
    for point in points:
        if point >= 0 and point not in unique:
            unique.append(point)
    return unique


def extract_poster(
    ffmpeg: str,
    video: Path,
    out: Path,
    start: float,
    max_edge: int,
    quality: int,
    duration: float | None,
) -> int:
    """抽一帧转 webp，回海报字节数。

    多个候选点各抽一帧，留明暗离散度最高的那张 —— 海报要在图墙里一眼认出这支
    片子，黑场（M08 开头）、只有一颗点的空白构图（M43 开头）都做不到。全抽不到才报错。
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.stem}.raw.png")
    keep = out.with_name(f"{out.stem}.best.png")
    best: float | None = None
    try:
        for at in poster_candidates(start, duration):
            if not _grab_frame(ffmpeg, video, at, tmp, max_edge):
                continue
            spread = _luma_spread(tmp)
            if best is None or spread > best:
                # 这一帧当选：把它挪到 keep，之前那张作废。留文件而不是留分数，
                # 否则最后一个候选点落选时 keep 上什么都没有，收尾就取不到图。
                best = spread
                os.replace(tmp, keep)
            tmp.unlink(missing_ok=True)
        if best is None:
            raise SystemExit(
                f"抽帧失败：{video} 在 {poster_candidates(start, duration)} 都取不到画面"
            )
        with Image.open(keep) as image:
            image.convert("RGB").save(out, "WEBP", quality=quality, method=6)
    finally:
        tmp.unlink(missing_ok=True)
        keep.unlink(missing_ok=True)
    return out.stat().st_size


def preview_start(duration: float | None, seconds: float) -> float:
    """样片起点：跳过片头空白（取片长 8%），又不让样片越过片尾。"""
    if not duration or duration <= 0:
        return 0.0
    head = min(max(duration * 0.08, 0.4), max(duration - seconds - 0.2, 0.0))
    return round(head, 3)


def extract_preview(
    ffmpeg: str,
    video: Path,
    out: Path,
    duration: float | None,
    seconds: float,
    max_edge: int,
    crf: int,
) -> int:
    """截一段无声小样片（H.264/MP4）回字节数。

    图册卡片悬停和工具面板要的是「动起来大概什么样」，不是给原片做备份：480p、
    24fps、去掉音轨，37 条加起来仍是个位数到十兆级，能跟着安装包走。
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.stem}.part{out.suffix}")
    start = preview_start(duration, seconds)
    vf = (
        f"fps={PREVIEW_FPS},"
        f"scale={max_edge}:{max_edge}:force_original_aspect_ratio=decrease:force_divisible_by=2"
    )
    try:
        encoder, rateless = pick_h264_encoder(executable=ffmpeg)
    except NoH264Encoder as exc:
        raise SystemExit(str(exc)) from exc
    encode_args = ["-c:v", encoder]
    if rateless:
        encode_args += ["-preset", "veryfast", "-crf", str(crf)]
    else:
        encode_args += ["-b:v", PREVIEW_BITRATE]
    try:
        subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
             "-ss", f"{start}", "-i", str(video), "-t", f"{seconds}",
             "-an", "-vf", vf, *encode_args,
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(tmp)],
            check=True,
        )
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
    return out.stat().st_size


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_ffmpeg(explicit: Path | None) -> str:
    if explicit:
        return str(explicit)
    found = shutil.which("ffmpeg")
    if found:
        return found
    if BUNDLED_FFMPEG.is_file():
        return str(BUNDLED_FFMPEG)
    raise SystemExit("找不到 ffmpeg：用 --ffmpeg 指定，或装一个到 PATH")


def locate_video(videos_dir: Path, case: dict) -> Path | None:
    file_name = str(case["media"].get("file") or "")
    if not file_name:
        return None
    for candidate in (videos_dir / case["id"] / file_name, videos_dir / file_name):
        if candidate.is_file():
            return candidate
    return None


def download_video(case: dict, target: Path, lark_cli: str) -> None:
    token = str(case["media"].get("token") or "")
    if not token:
        raise SystemExit(f"案例 {case['id']} 没有素材 token，无法下载")
    # Windows 上 npm 装的是 lark-cli.cmd，CreateProcess 认不到裸名，必须先按 PATHEXT 解析。
    exe = shutil.which(lark_cli) or lark_cli
    target.parent.mkdir(parents=True, exist_ok=True)
    # lark-cli 只肯往 cwd 内写（`--output` 必须是目录内的相对路径），所以把 cwd 定到目标目录。
    subprocess.run(
        [exe, "docs", "+media-download", "--token", token, "--output", f"./{target.name}"],
        cwd=str(target.parent),
        check=True,
    )


def copy_into_pack(source: Path, target: Path) -> bool:
    """把原片复制进可上传目录；已在位且字节一致就不重抄。回是否新复制。"""
    if target.is_file() and target.stat().st_size == source.stat().st_size:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    try:
        shutil.copyfile(source, tmp)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return True


def load_manifest(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("cases"), list):
        raise SystemExit(f"清单结构不对（要 cases 数组）：{path}")
    return raw


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--videos-dir", type=Path, help="已下载原片的目录")
    parser.add_argument("--download", action="store_true", help="缺失时用 lark-cli 按 token 拉")
    parser.add_argument("--lark-cli", default="lark-cli", help="lark-cli 可执行文件，默认取 PATH")
    parser.add_argument("--ffmpeg", type=Path, help="ffmpeg 可执行文件，默认 PATH 再退包内")
    parser.add_argument("--posters-out", type=Path, default=DEFAULT_POSTERS)
    parser.add_argument(
        "--previews-out",
        type=Path,
        help="同时生成图册悬停/详情用的小样片（默认不生成）；建议指到 motion_previews",
    )
    parser.add_argument("--pack-out", type=Path, help="把原片复制成可上传的目录包")
    parser.add_argument("--ids", help="只处理这些案例 id，逗号分隔")
    parser.add_argument("--ss", type=float, default=POSTER_SAMPLE_SECONDS, help="抽帧时间点（秒）")
    parser.add_argument("--max-edge", type=int, default=POSTER_MAX_EDGE)
    parser.add_argument("--quality", type=int, default=POSTER_QUALITY)
    parser.add_argument(
        "--preview-seconds", type=float, default=PREVIEW_SECONDS, help="样片时长（秒）"
    )
    parser.add_argument("--preview-max-edge", type=int, default=PREVIEW_MAX_EDGE)
    parser.add_argument("--preview-crf", type=int, default=PREVIEW_CRF)
    parser.add_argument(
        "--skip-existing-posters",
        action="store_true",
        help="已有海报的案例不再抽帧（重跑时省时，但仍会算 sha256）",
    )
    parser.add_argument(
        "--skip-existing-previews",
        action="store_true",
        help="已有样片的案例不再重截",
    )
    args = parser.parse_args()

    manifest_path = args.manifest
    manifest = load_manifest(manifest_path)
    cases = manifest["cases"]
    if args.ids:
        wanted = [item.strip() for item in args.ids.split(",") if item.strip()]
        known = {str(case.get("id") or "") for case in cases}
        unknown = [item for item in wanted if item not in known]
        if unknown:
            raise SystemExit(f"清单里没有这些 id: {unknown}")
        cases = [case for case in cases if str(case.get("id")) in wanted]

    ffmpeg = resolve_ffmpeg(args.ffmpeg)
    videos_dir = args.videos_dir.expanduser() if args.videos_dir else None
    if videos_dir is None and not args.download:
        raise SystemExit("要给原片：--videos-dir 指目录，或 --download 从飞书拉")
    posters_dir = args.posters_out.expanduser()
    previews_dir = args.previews_out.expanduser() if args.previews_out else None
    pack_dir = args.pack_out.expanduser() if args.pack_out else None
    download_root = videos_dir or (REPO_ROOT / "dist" / "motion_videos")

    missing: list[str] = []
    digest_by_id: dict[str, str] = {}
    posters_written = 0
    poster_bytes = 0
    previews_written = 0
    preview_bytes = 0
    for index, case in enumerate(cases, start=1):
        case_id = str(case["id"])
        file_name = str(case["media"].get("file") or "")
        print(f"[{index}/{len(cases)}] {case_id} {case.get('label')}", flush=True)

        source = locate_video(videos_dir, case) if videos_dir else None
        if source is None and args.download:
            target = download_root / case_id / file_name
            if not target.is_file():
                try:
                    download_video(case, target, args.lark_cli)
                except subprocess.CalledProcessError as exc:
                    # 逐条下 37 个文件，中间任何一次网络/授权抖动都不该让前面下好的白下。
                    print(f"        下载失败（{exc.returncode}），继续下一条", flush=True)
                    missing.append(case_id)
                    continue
            source = target
        if source is None or not source.is_file():
            missing.append(case_id)
            print("        没有原片，跳过", flush=True)
            continue

        size = source.stat().st_size
        expected = int(case["media"].get("bytes") or 0)
        if expected and size != expected:
            print(f"        注意：字节数与文档记录不符（文档 {expected}，实得 {size}）", flush=True)
        digest_by_id[case_id] = sha256_file(source)

        poster = posters_dir / case_id / "poster.webp"
        if poster.is_file() and args.skip_existing_posters:
            print("        海报已存在，跳过抽帧", flush=True)
        else:
            written = extract_poster(
                ffmpeg,
                source,
                poster,
                args.ss,
                args.max_edge,
                args.quality,
                case.get("duration_seconds"),
            )
            posters_written += 1
            poster_bytes += written
            print(f"        海报 {written // 1024} KB → {poster}", flush=True)

        if previews_dir is not None:
            preview = previews_dir / case_id / PREVIEW_BASENAME
            if preview.is_file() and args.skip_existing_previews:
                print("        样片已存在，跳过截取", flush=True)
            else:
                written = extract_preview(
                    ffmpeg,
                    source,
                    preview,
                    case.get("duration_seconds"),
                    args.preview_seconds,
                    args.preview_max_edge,
                    args.preview_crf,
                )
                previews_written += 1
                preview_bytes += written
                print(f"        样片 {written // 1024} KB → {preview}", flush=True)

        if pack_dir is not None and file_name:
            copied = copy_into_pack(source, pack_dir / case_id / file_name)
            print(f"        素材包{'已复制' if copied else '已在位'} {file_name}", flush=True)

    changed = 0
    for case in manifest["cases"]:
        digest = digest_by_id.get(str(case.get("id")))
        if digest and str(case["media"].get("sha256") or "") != digest:
            case["media"]["sha256"] = digest
            changed += 1
    if changed:
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    print(f"完成：抽帧 {posters_written} 张（共 {poster_bytes // 1024} KB），清单回填 {changed} 条 sha256")
    if previews_dir is not None:
        print(f"样片：截取 {previews_written} 条（共 {preview_bytes // 1024} KB）")
    if missing:
        print(f"缺原片：{missing}")
        print("补法：--download 从飞书拉，或把已下载的目录用 --videos-dir 传进来。")


if __name__ == "__main__":
    main()
