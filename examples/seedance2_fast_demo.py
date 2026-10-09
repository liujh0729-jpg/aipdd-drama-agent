"""Run small Seedance 2.0 Fast experiments through the HuiMeng task API."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from novelvideo.generators.huimengi import (
    HuimengiTaskClient,
    extract_huimeng_result_duration,
    extract_huimeng_result_url,
    local_file_to_data_url,
)
from novelvideo.models import extract_char_identities_from_markers

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - python-dotenv is expected in this project.
    load_dotenv = None

MODEL_NAME = "seedance-2.0-fast"
DEFAULT_PROJECT_OUTPUT = Path("output/admin/exo_6ae59234")
DEFAULT_OUTPUT_ROOT = Path("output/_demos/seedance2_fast")


class ExperimentName(str, Enum):
    I2V_LEGACY_PROMPT = "i2v_legacy_prompt"
    I2V_SEEDANCE2_PROMPT = "i2v_seedance2_prompt"
    REFERENCE_CHARACTER_SCENE_SEEDANCE2_PROMPT = "reference_character_scene_seedance2_prompt"
    REFERENCE_SCENES_SEEDANCE2_PROMPT = "reference_scenes_seedance2_prompt"
    REFERENCE_IDENTITY_SCENE_STORYBOARD_ATOMS_PROMPT = (
        "reference_identity_scene_storyboard_atoms_prompt"
    )
    FLF_SEEDANCE2_PROMPT = "flf_seedance2_prompt"


DEFAULT_EXPERIMENTS = (
    ExperimentName.I2V_LEGACY_PROMPT,
    ExperimentName.I2V_SEEDANCE2_PROMPT,
    ExperimentName.REFERENCE_SCENES_SEEDANCE2_PROMPT,
    ExperimentName.FLF_SEEDANCE2_PROMPT,
)

CHARACTER_REFERENCE_EXPERIMENTS = {
    ExperimentName.REFERENCE_CHARACTER_SCENE_SEEDANCE2_PROMPT,
    ExperimentName.REFERENCE_IDENTITY_SCENE_STORYBOARD_ATOMS_PROMPT,
}
SCENE_REFERENCE_EXPERIMENTS = {
    ExperimentName.REFERENCE_CHARACTER_SCENE_SEEDANCE2_PROMPT,
    ExperimentName.REFERENCE_SCENES_SEEDANCE2_PROMPT,
    ExperimentName.REFERENCE_IDENTITY_SCENE_STORYBOARD_ATOMS_PROMPT,
}
STORYBOARD_REFERENCE_EXPERIMENTS = {
    ExperimentName.REFERENCE_IDENTITY_SCENE_STORYBOARD_ATOMS_PROMPT,
}
PURE_REFERENCE_EXPERIMENTS = {
    ExperimentName.REFERENCE_CHARACTER_SCENE_SEEDANCE2_PROMPT,
    ExperimentName.REFERENCE_SCENES_SEEDANCE2_PROMPT,
    ExperimentName.REFERENCE_IDENTITY_SCENE_STORYBOARD_ATOMS_PROMPT,
}
FLF_EXPERIMENTS = {
    ExperimentName.FLF_SEEDANCE2_PROMPT,
}


@dataclass(frozen=True)
class DemoAssets:
    project_output: Path
    script_path: Path
    first_frame_path: Path
    last_frame_path: Path
    character_reference_path: Path
    scene_reference_path: Path
    second_scene_reference_path: Path
    storyboard_reference_path: Path


@dataclass(frozen=True)
class Seedance2PromptAtom:
    atom_type: str
    label: str
    source_label: str | None = None
    target: str = ""
    preserve: tuple[str, ...] = ()
    ignore: tuple[str, ...] = ()
    content: tuple[str, ...] = ()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_env_file() -> None:
    if load_dotenv is not None:
        load_dotenv()


def load_beat(script_path: Path, beat_number: int) -> dict[str, Any]:
    script = _read_json(script_path)
    for beat in script.get("beats", []):
        if int(beat.get("beat_number") or -1) == beat_number:
            return dict(beat)
    raise ValueError(f"Beat {beat_number} not found in {script_path}")


def resolve_default_assets(project_output: Path, episode: int, beat: int) -> DemoAssets:
    episode_key = f"ep{episode:03d}"
    return DemoAssets(
        project_output=project_output,
        script_path=project_output / "scripts" / f"{episode_key}_script.json",
        first_frame_path=project_output / "frames" / episode_key / f"beat_{beat:02d}.png",
        last_frame_path=project_output / "frames" / episode_key / f"beat_{beat + 1:02d}.png",
        character_reference_path=(
            project_output / "assets" / "characters" / "陆辰" / "identities" / "书店老板时期.png"
        ),
        scene_reference_path=project_output / "assets" / "scenes" / "地下室" / "anchor.png",
        second_scene_reference_path=project_output
        / "assets"
        / "scenes"
        / "青鹿河·老码头"
        / "anchor.png",
        storyboard_reference_path=(
            project_output / "sketches" / episode_key / f"beat_{beat:02d}.png"
        ),
    )


def validate_asset_paths(paths: dict[str, Path | None]) -> None:
    missing = [
        f"{label}: {path}"
        for label, path in paths.items()
        if path is not None and not path.exists()
    ]
    if missing:
        raise FileNotFoundError("Missing required demo assets: " + "; ".join(missing))


def _text(value: Any) -> str:
    return str(value or "").strip()


def _identity_from_beat(beat: dict[str, Any]) -> tuple[str, str]:
    identities = extract_char_identities_from_markers(
        _text(beat.get("visual_description")),
        strict=False,
    )
    if not identities:
        return "主体", "身份造型"

    character, identity_id = next(iter(identities.items()))
    identity_name = identity_id.split("_", 1)[1] if "_" in identity_id else identity_id
    return character, identity_name


def build_seedance2_prompt_atoms(
    *,
    beat: dict[str, Any],
    include_character_reference: bool,
    include_scene_reference: bool,
    include_storyboard_reference: bool = False,
    include_second_scene_reference: bool = False,
    include_first_frame: bool = True,
) -> list[Seedance2PromptAtom]:
    atoms: list[Seedance2PromptAtom] = []
    if include_first_frame:
        atoms.append(
            Seedance2PromptAtom(
                atom_type="first_frame",
                label="起始画面",
                source_label="首帧",
                preserve=("起始构图", "人物当前位置", "最终视觉风格"),
            )
        )

    reference_index = 1
    if include_character_reference:
        character, identity_name = _identity_from_beat(beat)
        atoms.append(
            Seedance2PromptAtom(
                atom_type="identity",
                label=identity_name,
                source_label=f"图片{reference_index}",
                target=character,
                preserve=("脸型", "年龄感", "发型", "眼镜", "服装", "气质"),
                ignore=("背景", "姿势", "构图"),
            )
        )
        reference_index += 1

    if include_scene_reference:
        atoms.append(
            Seedance2PromptAtom(
                atom_type="scene",
                label="地下室空间",
                source_label=f"图片{reference_index}",
                preserve=("地下室空间", "旧书架", "潮湿砖墙", "冷暗光线"),
                ignore=("人物", "临时道具"),
            )
        )
        reference_index += 1

    if include_second_scene_reference:
        atoms.append(
            Seedance2PromptAtom(
                atom_type="scene",
                label="旧木结构补充",
                source_label=f"图片{reference_index}",
                preserve=("旧木结构", "石板路", "潮湿陈旧质感"),
                ignore=("人物", "主体场景替换"),
            )
        )
        reference_index += 1

    if include_storyboard_reference:
        atoms.append(
            Seedance2PromptAtom(
                atom_type="storyboard",
                label="分镜构图",
                source_label=f"图片{reference_index}",
                preserve=("构图", "人物站位", "姿势", "镜头角度", "画面重心"),
                ignore=("黑白线稿风格", "草图线条", "简化五官"),
            )
        )

    atoms.extend(
        [
            Seedance2PromptAtom(
                atom_type="motion",
                label="动作",
                content=(
                    _text(beat.get("video_prompt")),
                    "[0-1s] 保持起始构图，人物低头翻动旧书，动作克制。",
                    "[1-3s] 镜头缓慢向前推，书页轻微翻动，人物呼吸放轻，肩背紧绷。",
                ),
            ),
            Seedance2PromptAtom(
                atom_type="visual",
                label="画面",
                content=(
                    _text(beat.get("visual_description")),
                    _text(beat.get("scene_description")),
                    _text(beat.get("props_description")),
                ),
            ),
            Seedance2PromptAtom(
                atom_type="audio",
                label="声音",
                content=("地下室环境声", "雨声", "纸页摩擦声", "轻微呼吸声"),
                ignore=("旁白", "新增对白"),
            ),
            Seedance2PromptAtom(
                atom_type="constraints",
                label="约束",
                content=(
                    "不要生成分屏、拼贴、证件照或参考图展示墙。",
                    "不要新增无关人物，不要改变陆辰的年龄、服装主色或眼镜特征。",
                ),
            ),
        ]
    )
    return atoms


def _reference_line(atom: Seedance2PromptAtom) -> str | None:
    if atom.atom_type == "first_frame":
        return "- 首帧：作为起始构图和人物当前位置。"
    if atom.atom_type == "identity":
        preserved = "、".join(atom.preserve)
        ignored = "、".join(atom.ignore)
        return (
            f"- {atom.source_label}：身份原子，只用于锚定"
            f"{atom.target}“{atom.label}”的身份造型，包括{preserved}；"
            f"不要参考{ignored}。"
        )
    if atom.atom_type == "scene" and atom.label == "地下室空间":
        return (
            f"- {atom.source_label}：场景原子，只用于地下室空间、旧书架、潮湿砖墙和冷暗光线。"
        )
    if atom.atom_type == "scene":
        return (
            f"- {atom.source_label}：场景补充原子，只用于旧木结构、石板路和潮湿陈旧质感，"
            "不改变地下室主体场景。"
        )
    if atom.atom_type == "storyboard":
        return (
            f"- {atom.source_label}：分镜原子，只用于构图、人物站位、姿势、镜头角度和画面重心；"
            "不要继承黑白线稿风格、草图线条或简化五官。"
        )
    return None


def render_seedance2_prompt_from_atoms(
    *,
    atoms: list[Seedance2PromptAtom],
    beat: dict[str, Any],
    duration: int,
    ratio: str,
) -> str:
    video_prompt = _text(beat.get("video_prompt"))
    visual_description = _text(beat.get("visual_description"))
    scene_description = _text(beat.get("scene_description"))
    props_description = _text(beat.get("props_description"))

    reference_lines = [
        line for atom in atoms if (line := _reference_line(atom)) is not None
    ]

    return "\n".join(
        [
            f"生成 {duration} 秒 {ratio} 写实悬疑短剧镜头。",
            "",
            "参考素材原子约束：",
            *reference_lines,
            "不要生成分屏、拼贴、证件照或参考图展示墙。",
            "不要新增无关人物，不要改变陆辰的年龄、服装主色或眼镜特征。",
            "",
            "剧情与画面信息：",
            f"- 动作核心：{video_prompt}",
            f"- 画面描述：{visual_description}",
            f"- 场景：{scene_description}",
            f"- 道具：{props_description}",
            "",
            "镜头：",
            "[0-1s] 保持首帧构图，人物低头翻动旧书，动作克制。",
            "[1-3s] 镜头缓慢向前推，书页轻微翻动，人物呼吸放轻，肩背紧绷。",
            (
                f"[3-{duration}s] 人物手指停在关键物件或破旧封面上，"
                "光线微闪，定格在发现异常的瞬间。"
            ),
            "",
            "声音：",
            "地下室环境声、雨声、纸页摩擦声和轻微呼吸声，不要旁白，不要新增对白。",
            "",
            "风格：",
            "写实电影质感，低照度，冷灰绿色阴影，细节稳定，动作克制。",
        ]
    )


def build_seedance2_prompt(
    *,
    beat: dict[str, Any],
    duration: int,
    ratio: str,
    include_character_reference: bool,
    include_scene_reference: bool,
    include_second_scene_reference: bool = False,
    include_storyboard_reference: bool = False,
    include_first_frame: bool = True,
) -> str:
    atoms = build_seedance2_prompt_atoms(
        beat=beat,
        include_character_reference=include_character_reference,
        include_scene_reference=include_scene_reference,
        include_second_scene_reference=include_second_scene_reference,
        include_storyboard_reference=include_storyboard_reference,
        include_first_frame=include_first_frame,
    )
    return render_seedance2_prompt_from_atoms(
        atoms=atoms,
        beat=beat,
        duration=duration,
        ratio=ratio,
    )


def _legacy_prompt(beat: dict[str, Any]) -> str:
    prompt = _text(beat.get("video_prompt"))
    if prompt:
        return prompt
    fallback = _text(beat.get("visual_description"))
    if fallback:
        return fallback
    raise ValueError("Beat has no video_prompt or visual_description")


def _base_params(
    *,
    prompt: str,
    duration: int,
    resolution: str,
    ratio: str,
    generate_audio: bool,
) -> dict[str, Any]:
    return {
        "prompt": prompt,
        "duration": duration,
        "resolution": resolution,
        "ratio": ratio,
        "generate_audio": generate_audio,
        "return_last_frame": False,
    }


def build_experiment_request(
    *,
    experiment: ExperimentName,
    beat: dict[str, Any],
    first_frame_path: Path,
    last_frame_path: Path | None,
    character_reference_path: Path | None,
    scene_reference_path: Path | None,
    storyboard_reference_path: Path | None = None,
    duration: int,
    resolution: str,
    ratio: str,
    generate_audio: bool,
    second_scene_reference_path: Path | None = None,
    human_review: bool = False,
) -> dict[str, Any]:
    include_character_reference = experiment in CHARACTER_REFERENCE_EXPERIMENTS
    include_scene_reference = experiment in SCENE_REFERENCE_EXPERIMENTS
    include_second_scene_reference = experiment == ExperimentName.REFERENCE_SCENES_SEEDANCE2_PROMPT
    include_storyboard_reference = experiment in STORYBOARD_REFERENCE_EXPERIMENTS
    is_pure_reference = experiment in PURE_REFERENCE_EXPERIMENTS
    is_flf = experiment in FLF_EXPERIMENTS

    validate_asset_paths(
        {
            "first_frame": first_frame_path,
            "last_frame": last_frame_path if is_flf else None,
            "character_reference": (
                character_reference_path if include_character_reference else None
            ),
            "scene_reference": scene_reference_path if include_scene_reference else None,
            "second_scene_reference": (
                second_scene_reference_path if include_second_scene_reference else None
            ),
            "storyboard_reference": (
                storyboard_reference_path if include_storyboard_reference else None
            ),
        }
    )

    if experiment == ExperimentName.I2V_LEGACY_PROMPT:
        prompt = _legacy_prompt(beat)
    else:
        prompt = build_seedance2_prompt(
            beat=beat,
            duration=duration,
            ratio=ratio,
            include_character_reference=include_character_reference,
            include_scene_reference=include_scene_reference,
            include_second_scene_reference=include_second_scene_reference,
            include_storyboard_reference=include_storyboard_reference,
            include_first_frame=not is_pure_reference,
        )

    params = _base_params(
        prompt=prompt,
        duration=duration,
        resolution=resolution,
        ratio=ratio,
        generate_audio=generate_audio,
    )
    if human_review:
        params["human_review"] = True

    reference_images: list[str] = []
    if include_character_reference:
        reference_images.append(local_file_to_data_url(str(character_reference_path)))
    if include_scene_reference:
        reference_images.append(local_file_to_data_url(str(scene_reference_path)))
    if include_second_scene_reference:
        reference_images.append(local_file_to_data_url(str(second_scene_reference_path)))
    if include_storyboard_reference:
        reference_images.append(local_file_to_data_url(str(storyboard_reference_path)))

    if reference_images:
        if not is_pure_reference:
            raise ValueError(
                "reference_images can only be used by pure multimodal reference experiments"
            )
        if len(reference_images) > 9:
            raise ValueError("reference_images supports at most 9 images")
        params["reference_images"] = reference_images
    elif is_flf:
        params["first_frame_image"] = local_file_to_data_url(str(first_frame_path))
        params["last_frame_image"] = local_file_to_data_url(str(last_frame_path))
    elif not is_pure_reference:
        params["image_url"] = local_file_to_data_url(str(first_frame_path))

    return {"model": MODEL_NAME, "params": params}


def sanitize_request(
    request: dict[str, Any],
    *,
    source_paths: dict[str, Path] | None = None,
) -> dict[str, Any]:
    source_paths = source_paths or {}

    def replace_data_url(value: Any, key_path: str) -> Any:
        if isinstance(value, str) and value.startswith("data:"):
            source = source_paths.get(key_path)
            suffix = f": {source}" if source else ""
            return f"<data-url {key_path}{suffix}>"
        if isinstance(value, list):
            return [replace_data_url(item, f"{key_path}[{idx}]") for idx, item in enumerate(value)]
        if isinstance(value, dict):
            return {key: replace_data_url(child, key) for key, child in value.items()}
        return value

    return replace_data_url(request, "")


def source_path_map(
    *,
    experiment: ExperimentName,
    assets: DemoAssets,
) -> dict[str, Path]:
    mapping = {
        "image_url": assets.first_frame_path,
        "first_frame_image": assets.first_frame_path,
        "last_frame_image": assets.last_frame_path,
    }
    if experiment in {
        ExperimentName.REFERENCE_CHARACTER_SCENE_SEEDANCE2_PROMPT,
        ExperimentName.REFERENCE_IDENTITY_SCENE_STORYBOARD_ATOMS_PROMPT,
    }:
        mapping["reference_images[0]"] = assets.character_reference_path
        mapping["reference_images[1]"] = assets.scene_reference_path
        if experiment == ExperimentName.REFERENCE_IDENTITY_SCENE_STORYBOARD_ATOMS_PROMPT:
            mapping["reference_images[2]"] = assets.storyboard_reference_path
    elif experiment == ExperimentName.REFERENCE_SCENES_SEEDANCE2_PROMPT:
        mapping["reference_images[0]"] = assets.scene_reference_path
        mapping["reference_images[1]"] = assets.second_scene_reference_path
    return mapping


async def run_experiment(
    *,
    client: HuimengiTaskClient,
    experiment: ExperimentName,
    request: dict[str, Any],
    experiment_dir: Path,
    source_paths: dict[str, Path],
    poll_interval: float,
    max_polls: int,
    write_full_request: bool,
) -> dict[str, Any]:
    experiment_dir.mkdir(parents=True, exist_ok=True)
    prompt = request["params"]["prompt"]
    (experiment_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    _write_json(
        experiment_dir / "request.json",
        sanitize_request(request, source_paths=source_paths),
    )
    if write_full_request:
        _write_json(experiment_dir / "request_full.json", request)

    task_payload: dict[str, Any] = {}
    status = "failed"
    task_id = ""
    video_url = ""
    duration_seconds: float | None = None
    error = ""

    try:
        submitted = await client.submit_task(
            model=request["model"],
            params=request["params"],
        )
        task_id = str(submitted.get("task_id") or "")
        task_payload = await client.wait_for_completion(
            task_id,
            poll_interval=poll_interval,
            max_polls=max_polls,
            on_log=print,
        )
        result = task_payload.get("result") or {}
        video_url = extract_huimeng_result_url(result, "video_url", "video_urls")
        duration_seconds = extract_huimeng_result_duration(result)
        if not video_url:
            raise RuntimeError(f"No video_url in Huimeng result: {result}")
        await client.download_url(video_url, str(experiment_dir / "video.mp4"))
        status = "succeeded"
    except Exception as exc:
        error = str(exc)
        task_payload = task_payload or {"error": error, "task_id": task_id}

    _write_json(experiment_dir / "task.json", task_payload)
    readme = [
        f"# {experiment.value}",
        "",
        f"- model: `{request['model']}`",
        f"- status: `{status}`",
        f"- task_id: `{task_id}`",
        f"- video_url: `{video_url}`",
        f"- duration_seconds: `{duration_seconds}`",
        f"- error: `{error}`",
    ]
    (experiment_dir / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")
    return {
        "experiment": experiment.value,
        "status": status,
        "task_id": task_id,
        "video_url": video_url,
        "error": error,
        "path": str(experiment_dir),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Seedance 2.0 Fast HuiMeng demo.")
    parser.add_argument("--project-output", type=Path, default=DEFAULT_PROJECT_OUTPUT)
    parser.add_argument("--episode", type=int, default=1)
    parser.add_argument("--beat", type=int, default=1)
    parser.add_argument("--duration", type=int, default=5)
    parser.add_argument("--resolution", default="720p")
    parser.add_argument("--ratio", default="9:16")
    parser.add_argument("--no-audio", action="store_true")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--max-polls", type=int, default=120)
    parser.add_argument("--write-full-request", action="store_true")
    parser.add_argument(
        "--human-review",
        action="store_true",
        help="Enable HuiMeng real-person review / asset allow-list flow.",
    )
    parser.add_argument("--all", action="store_true")
    parser.add_argument(
        "--experiment",
        choices=[experiment.value for experiment in ExperimentName],
        default=ExperimentName.I2V_SEEDANCE2_PROMPT.value,
    )
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    load_env_file()
    if not os.environ.get("HUIMENGI_API_KEY"):
        raise RuntimeError("HUIMENGI_API_KEY must be set")

    assets = resolve_default_assets(args.project_output, args.episode, args.beat)
    experiments = DEFAULT_EXPERIMENTS if args.all else (ExperimentName(args.experiment),)
    wants_flf = any(experiment in FLF_EXPERIMENTS for experiment in experiments)
    wants_character_reference = any(
        experiment in CHARACTER_REFERENCE_EXPERIMENTS for experiment in experiments
    )
    wants_scene_reference = any(
        experiment in SCENE_REFERENCE_EXPERIMENTS for experiment in experiments
    )
    wants_second_scene_reference = any(
        experiment == ExperimentName.REFERENCE_SCENES_SEEDANCE2_PROMPT for experiment in experiments
    )
    wants_storyboard_reference = any(
        experiment in STORYBOARD_REFERENCE_EXPERIMENTS for experiment in experiments
    )
    validate_asset_paths(
        {
            "script": assets.script_path,
            "first_frame": assets.first_frame_path,
            "last_frame": assets.last_frame_path if wants_flf else None,
            "character_reference": (
                assets.character_reference_path if wants_character_reference else None
            ),
            "scene_reference": assets.scene_reference_path if wants_scene_reference else None,
            "second_scene_reference": (
                assets.second_scene_reference_path if wants_second_scene_reference else None
            ),
            "storyboard_reference": (
                assets.storyboard_reference_path if wants_storyboard_reference else None
            ),
        }
    )
    beat = load_beat(assets.script_path, args.beat)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = args.output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    client = HuimengiTaskClient()
    summaries = []
    for experiment in experiments:
        request = build_experiment_request(
            experiment=experiment,
            beat=beat,
            first_frame_path=assets.first_frame_path,
            last_frame_path=assets.last_frame_path,
            character_reference_path=assets.character_reference_path,
            scene_reference_path=assets.scene_reference_path,
            storyboard_reference_path=assets.storyboard_reference_path,
            duration=args.duration,
            resolution=args.resolution,
            ratio=args.ratio,
            generate_audio=not args.no_audio,
            second_scene_reference_path=assets.second_scene_reference_path,
            human_review=args.human_review,
        )
        summary = await run_experiment(
            client=client,
            experiment=experiment,
            request=request,
            experiment_dir=run_dir / experiment.value,
            source_paths=source_path_map(experiment=experiment, assets=assets),
            poll_interval=args.poll_interval,
            max_polls=args.max_polls,
            write_full_request=args.write_full_request,
        )
        summaries.append(summary)
        print(f"{experiment.value}: {summary['status']} -> {summary['path']}")

    _write_json(run_dir / "summary.json", summaries)
    print(f"Demo output: {run_dir}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
