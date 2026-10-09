import hashlib
from pathlib import Path


def test_voice_audio_record_upsert_and_lookup(tmp_path):
    from novelvideo.seedance2_i2v.voice_audio_records import (
        get_seedance2_voice_audio_record,
        upsert_seedance2_voice_audio_record,
    )

    db_path = tmp_path / "state" / "data.db"
    audio_path = tmp_path / "project" / "audio" / "ep001" / "beat_03.mp3"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"audio")

    upsert_seedance2_voice_audio_record(
        db_path=db_path,
        episode_number=1,
        beat_number=3,
        speaker="谢铮_幼年时期",
        audio_path=audio_path,
        voice_sha256="abc123",
        mode="missing_only",
        provider="newapi",
        model="index-tts-2",
        status="completed",
        error="",
    )

    record = get_seedance2_voice_audio_record(
        db_path=db_path,
        episode_number=1,
        beat_number=3,
        speaker="谢铮_幼年时期",
    )

    assert record is not None
    assert record.voice_sha256 == "abc123"
    assert record.mode == "missing_only"
    assert record.status == "completed"


def test_classify_seedance2_voice_audio_states(tmp_path):
    from novelvideo.seedance2_i2v.voice_audio_records import (
        classify_seedance2_voice_audio,
        upsert_seedance2_voice_audio_record,
    )

    db_path = tmp_path / "state" / "data.db"
    project_dir = tmp_path / "project"
    missing_path = project_dir / "audio" / "ep001" / "beat_01.mp3"
    unknown_path = project_dir / "audio" / "ep001" / "beat_02.mp3"
    stale_path = project_dir / "audio" / "ep001" / "beat_03.mp3"
    current_path = project_dir / "audio" / "ep001" / "beat_04.mp3"
    for path in [unknown_path, stale_path, current_path]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")

    upsert_seedance2_voice_audio_record(
        db_path=db_path,
        episode_number=1,
        beat_number=3,
        speaker="谢铮_幼年时期",
        audio_path=stale_path,
        voice_sha256="old",
        mode="redo_all",
        provider="newapi",
        model="index-tts-2",
        status="completed",
        error="",
    )
    upsert_seedance2_voice_audio_record(
        db_path=db_path,
        episode_number=1,
        beat_number=4,
        speaker="谢铮_幼年时期",
        audio_path=current_path,
        voice_sha256="new",
        mode="redo_all",
        provider="newapi",
        model="index-tts-2",
        status="completed",
        error="",
    )

    assert classify_seedance2_voice_audio(
        db_path=db_path,
        episode_number=1,
        beat_number=1,
        speaker="谢铮_幼年时期",
        audio_path=missing_path,
        current_voice_sha256="new",
    ).state == "missing"
    assert classify_seedance2_voice_audio(
        db_path=db_path,
        episode_number=1,
        beat_number=2,
        speaker="谢铮_幼年时期",
        audio_path=unknown_path,
        current_voice_sha256="new",
    ).state == "unknown"
    assert classify_seedance2_voice_audio(
        db_path=db_path,
        episode_number=1,
        beat_number=3,
        speaker="谢铮_幼年时期",
        audio_path=stale_path,
        current_voice_sha256="new",
    ).state == "stale"
    assert classify_seedance2_voice_audio(
        db_path=db_path,
        episode_number=1,
        beat_number=4,
        speaker="谢铮_幼年时期",
        audio_path=current_path,
        current_voice_sha256="new",
    ).state == "current"


def test_classify_seedance2_voice_audio_marks_text_hash_changes_stale(tmp_path):
    from novelvideo.seedance2_i2v.voice_audio_records import (
        classify_seedance2_voice_audio,
        upsert_seedance2_voice_audio_record,
    )

    db_path = tmp_path / "state" / "data.db"
    audio_path = tmp_path / "project" / "audio" / "ep001" / "beat_05.mp3"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"audio")
    old_text_hash = hashlib.sha256("旧台词".encode("utf-8")).hexdigest()
    new_text_hash = hashlib.sha256("新台词".encode("utf-8")).hexdigest()

    upsert_seedance2_voice_audio_record(
        db_path=db_path,
        episode_number=1,
        beat_number=5,
        speaker="谢铮_幼年时期",
        audio_path=audio_path,
        voice_sha256="voice",
        text_sha256=old_text_hash,
        mode="sync_changed",
        provider="newapi",
        model="index-tts-2",
        status="completed",
        error="",
    )

    assert classify_seedance2_voice_audio(
        db_path=db_path,
        episode_number=1,
        beat_number=5,
        speaker="谢铮_幼年时期",
        audio_path=audio_path,
        current_voice_sha256="voice",
        current_text_sha256=old_text_hash,
    ).state == "current"
    assert classify_seedance2_voice_audio(
        db_path=db_path,
        episode_number=1,
        beat_number=5,
        speaker="谢铮_幼年时期",
        audio_path=audio_path,
        current_voice_sha256="voice",
        current_text_sha256=new_text_hash,
    ).state == "stale"


def test_audio_scope_attempt_count_tracks_task_starts(tmp_path):
    from novelvideo.audio_request_usage import (
        count_audio_scope_attempts,
        record_audio_generation_attempt,
        update_audio_generation_attempt,
    )

    project_output_dir = tmp_path / "output" / "admin" / "demo"

    assert count_audio_scope_attempts(
        project_output_dir=project_output_dir,
        task_type="seedance2_voice_audio",
        scope="ep001:谢铮_幼年时期",
        episode=1,
    ) == 0

    record_audio_generation_attempt(
        project_output_dir=project_output_dir,
        request_id="attempt-1",
        provider="newapi",
        model_name="index-tts-2",
        task_type="seedance2_voice_audio",
        scope="ep001:谢铮_幼年时期",
        episode=1,
        speaker="谢铮_幼年时期",
    )
    update_audio_generation_attempt(
        project_output_dir=project_output_dir,
        request_id="attempt-1",
        status="completed",
    )

    assert count_audio_scope_attempts(
        project_output_dir=project_output_dir,
        task_type="seedance2_voice_audio",
        scope="ep001:谢铮_幼年时期",
        episode=1,
    ) == 1
