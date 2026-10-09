"""Episode subtitle and archive export helpers shared by API and legacy UI."""

from __future__ import annotations

from pathlib import Path

from novelvideo.utils.async_ops import call_blocking
from novelvideo.utils.media_io import get_audio_duration_async


def format_srt_time(seconds: float) -> str:
    """Format seconds as SRT time: HH:MM:SS,mmm."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


async def build_srt_content(project_dir: Path, episode: int, beats: list[dict]) -> str:
    audio_dir = project_dir / "audio" / f"ep{episode:03d}"
    srt_lines: list[str] = []
    current_time = 0.0
    seq = 0

    for index, beat in enumerate(beats, 1):
        beat_num = beat.get("beat_number", index)
        narration = beat.get("narration_segment", "")
        if not narration:
            continue

        audio_path = audio_dir / f"beat_{beat_num:02d}.mp3"
        duration = 5.0
        if audio_path.exists():
            try:
                duration = await get_audio_duration_async(str(audio_path))
            except Exception:
                duration = 5.0

        start = current_time
        end = current_time + duration
        seq += 1

        srt_lines.append(f"{seq}")
        srt_lines.append(f"{format_srt_time(start)} --> {format_srt_time(end)}")
        srt_lines.append(narration)
        srt_lines.append("")

        current_time = end

    return "\n".join(srt_lines)


async def build_episode_srt_file(project_dir: Path, episode: int, beats: list[dict]) -> Path | None:
    """Generate and return the episode SRT file path."""
    if not beats:
        return None

    srt_content = await build_srt_content(project_dir, episode, beats)
    if not srt_content:
        return None

    srt_dir = project_dir / "videos" / "episodes"
    srt_dir.mkdir(parents=True, exist_ok=True)
    srt_path = srt_dir / f"ep{episode:03d}.srt"
    srt_path.write_text(srt_content, encoding="utf-8")
    return srt_path


async def build_episode_zip_file(
    project_dir: Path,
    project_name: str,
    episode: int,
    beats: list[dict],
) -> Path | None:
    """Generate and return the episode ZIP file path."""
    import zipfile

    from novelvideo.utils.path_resolver import PathResolver

    paths = PathResolver(str(project_dir), episode)
    files_to_pack: list[tuple[Path, str]] = []

    for beat in beats:
        beat_num = beat.get("beat_number", 0)
        if beat_num <= 0:
            continue
        audio_path = paths.audio(beat_num)
        if audio_path.exists():
            files_to_pack.append((audio_path, f"audio/{audio_path.name}"))
        video_path = paths.video(beat_num)
        if video_path.exists():
            files_to_pack.append((video_path, f"video/{video_path.name}"))

    final_path = paths.final_video()
    if final_path.exists():
        files_to_pack.append((final_path, final_path.name))

    srt_path = await build_episode_srt_file(project_dir, episode, beats)
    if srt_path and srt_path.exists():
        files_to_pack.append((srt_path, srt_path.name))

    if not files_to_pack:
        return None

    zip_dir = project_dir / "videos" / "episodes"
    zip_dir.mkdir(parents=True, exist_ok=True)
    zip_path = zip_dir / f"{project_name}_第{episode}集.zip"

    def _write_zip_file() -> None:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zip_file:
            for local_path, arc_name in files_to_pack:
                zip_file.write(local_path, arc_name)

    await call_blocking(_write_zip_file)
    return zip_path


__all__ = [
    "build_episode_srt_file",
    "build_episode_zip_file",
    "build_srt_content",
    "format_srt_time",
]
