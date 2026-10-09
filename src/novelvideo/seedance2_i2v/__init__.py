"""Seedance 2.0 image-to-video integration helpers."""

from novelvideo.seedance2_i2v.assets import (
    Seedance2ResolvedAsset,
    build_seedance2_project_assets,
    selected_reference_paths,
)
from novelvideo.seedance2_i2v.models import Seedance2I2VMode, Seedance2VideoConfig
from novelvideo.seedance2_i2v.prompt import (
    build_seedance2_asset_manifest,
    build_seedance2_prompt_draft,
    compute_seedance2_prompt_inputs_hash,
    generate_seedance2_prompt,
)
from novelvideo.seedance2_i2v.request import build_seedance2_huimeng_params
from novelvideo.seedance2_i2v.voice_audio_records import (
    Seedance2VoiceAudioRecord,
    Seedance2VoiceAudioState,
    classify_seedance2_voice_audio,
    get_seedance2_voice_audio_record,
    seedance2_narration_scope,
    seedance2_voice_scope,
    upsert_seedance2_voice_audio_record,
)

__all__ = [
    "Seedance2I2VMode",
    "Seedance2VideoConfig",
    "Seedance2ResolvedAsset",
    "Seedance2VoiceAudioRecord",
    "Seedance2VoiceAudioState",
    "build_seedance2_huimeng_params",
    "build_seedance2_project_assets",
    "build_seedance2_asset_manifest",
    "build_seedance2_prompt_draft",
    "classify_seedance2_voice_audio",
    "compute_seedance2_prompt_inputs_hash",
    "generate_seedance2_prompt",
    "get_seedance2_voice_audio_record",
    "seedance2_narration_scope",
    "seedance2_voice_scope",
    "selected_reference_paths",
    "upsert_seedance2_voice_audio_record",
]
