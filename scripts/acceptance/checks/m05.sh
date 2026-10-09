# M05 场景/草图/首帧 · curl 冒烟（被 run.sh source；可用 $MODE/$PORT/check/log）
# 对应 docs/oss-split/modules/M05 §1.5 的 M05 端点归属清单与 §4 API/落盘检查。

M05_TMP="acceptance-logs/m05-${MODE}-$$"
M05_COOKIE_FILE="${M05_TMP}-cookie.txt"
M05_COOKIE_JAR="${M05_TMP}-cookies.jar"
M05_CREATE_JSON="${M05_TMP}-create.json"
M05_PROJECT_FILE="${M05_TMP}-project-id.txt"
M05_PROJECT_DIR_FILE="${M05_TMP}-project-dir.txt"
M05_PROJECT_NAME="m05_accept_${MODE}_$$"
M05_OPENAPI_JSON="${M05_TMP}-openapi.json"
: > "$M05_COOKIE_JAR"

check "OpenAPI 暴露 M05 §1.5 实测 60 个 method/path 操作" \
  bash -c "curl -sS 'localhost:$PORT/openapi.json' -o '$M05_OPENAPI_JSON' && uv run python - <<'PY'
import json

spec = json.load(open('$M05_OPENAPI_JSON', encoding='utf-8'))
paths = spec['paths']
expected = {
    ('GET', '/api/v1/projects/{project}/scenes'),
    ('GET', '/api/v1/projects/{project}/scenes/plate-preview'),
    ('GET', '/api/v1/projects/{project}/scenes/{name}/pano/manifest'),
    ('PATCH', '/api/v1/projects/{project}/scenes/{name}/pano/correction'),
    ('GET', '/api/v1/projects/{project}/scenes/{name}/director-stage/manifest'),
    ('POST', '/api/v1/projects/{project}/scenes/{name}/director-stage/world'),
    ('POST', '/api/v1/projects/{project}/scenes/{name}/director-stage/world/clear'),
    ('POST', '/api/v1/projects/{project}/scenes'),
    ('PATCH', '/api/v1/projects/{project}/scenes/{name}'),
    ('POST', '/api/v1/projects/{project}/scenes/{name}/delete'),
    ('POST', '/api/v1/projects/{project}/scenes/build'),
    ('POST', '/api/v1/projects/{project}/scenes/{name}/master/upload'),
    ('POST', '/api/v1/projects/{project}/scenes/{name}/master/delete'),
    ('POST', '/api/v1/projects/{project}/scenes/{name}/master/generate-async'),
    ('POST', '/api/v1/projects/{project}/scenes/{name}/reverse/generate-async'),
    ('POST', '/api/v1/projects/{project}/scenes/{name}/pano/upload'),
    ('POST', '/api/v1/projects/{project}/scenes/{name}/pano/delete'),
    ('POST', '/api/v1/projects/{project}/scenes/{name}/custom/upload'),
    ('POST', '/api/v1/projects/{project}/scenes/{name}/custom/delete'),
    ('POST', '/api/v1/projects/{project}/scenes/{name}/3gs/master-ply/generate-async'),
    ('POST', '/api/v1/projects/{project}/scenes/{name}/3gs/reverse-ply/generate-async'),
    ('POST', '/api/v1/projects/{project}/scenes/{name}/3gs/pano-ply/generate-async'),
    ('POST', '/api/v1/projects/{project}/scenes/{name}/pano/generate-async'),
    ('POST', '/api/v1/projects/{project}/episodes/{episode_num}/scenes/plan'),
    ('GET', '/api/v1/projects/{project}/sketch-settings'),
    ('PATCH', '/api/v1/projects/{project}/sketch-settings'),
    ('GET', '/api/v1/projects/{project}/episodes/{episode_num}/sketch-regen-queue'),
    ('PUT', '/api/v1/projects/{project}/episodes/{episode_num}/sketch-regen-queue'),
    ('GET', '/api/v1/projects/{project}/episodes/{episode_num}/sketch-image-usage'),
    ('GET', '/api/v1/projects/{project}/episodes/{episode_num}/image-generation-guard'),
    ('POST', '/api/v1/projects/{project}/episodes/{episode_num}/image-generation-guard/verify-password'),
    ('POST', '/api/v1/projects/{project}/episodes/{episode_num}/sketches/generate'),
    ('POST', '/api/v1/projects/{project}/episodes/{episode_num}/render/plan'),
    ('POST', '/api/v1/projects/{project}/episodes/{episode_num}/render/execute'),
    ('POST', '/api/v1/projects/{project}/episodes/{episode_num}/beats/regenerate'),
    ('POST', '/api/v1/projects/{project}/episodes/{episode_num}/sketches/regenerate'),
    ('GET', '/api/v1/projects/{project}/episodes/{episode_num}/beats/{beat_num}/pano-background/manifest'),
    ('GET', '/api/v1/projects/{project}/episodes/{episode_num}/beats/{beat_num}/sketch-candidates'),
    ('GET', '/api/v1/projects/{project}/director-stage/palette'),
    ('GET', '/api/v1/projects/{project}/episodes/{episode_num}/beats/{beat_num}/director-stage/manifest'),
    ('GET', '/api/v1/projects/{project}/episodes/{episode_num}/beats/{beat_num}/director-stage/overlay'),
    ('POST', '/api/v1/projects/{project}/episodes/{episode_num}/beats/{beat_num}/director-stage/overlay'),
    ('POST', '/api/v1/projects/{project}/episodes/{episode_num}/beats/{beat_num}/director-stage/control-frame'),
    ('GET', '/api/v1/projects/{project}/episodes/{episode_num}/beats/{beat_num}/background-anchors'),
    ('PATCH', '/api/v1/projects/{project}/episodes/{episode_num}/beats/{beat_num}/background-anchor'),
    ('POST', '/api/v1/projects/{project}/episodes/{episode_num}/beats/{beat_num}/background-anchor/crop'),
    ('POST', '/api/v1/projects/{project}/episodes/{episode_num}/beats/{beat_num}/background-anchor/upload'),
    ('GET', '/api/v1/projects/{project}/episodes/{episode_num}/beats/{beat_num}/director-control-frame'),
    ('POST', '/api/v1/projects/{project}/episodes/{episode_num}/beats/{beat_num}/director-control-to-sketch'),
    ('GET', '/api/v1/projects/{project}/episodes/{episode_num}/beats/{beat_num}/sketch/pose-editor'),
    ('POST', '/api/v1/projects/{project}/episodes/{episode_num}/beats/{beat_num}/sketch/pose-editor'),
    ('POST', '/api/v1/projects/{project}/episodes/{episode_num}/beats/{beat_num}/sketch/crop'),
    ('POST', '/api/v1/projects/{project}/episodes/{episode_num}/sketches/generate-missing-manual'),
    ('GET', '/api/v1/projects/{project}/episodes/{episode_num}/grids'),
    ('POST', '/api/v1/projects/{project}/episodes/{episode_num}/grids/rebuild-pool'),
    ('POST', '/api/v1/projects/{project}/episodes/{episode_num}/beats/{beat_num}/pool-select'),
    ('POST', '/api/v1/projects/{project}/episodes/{episode_num}/beats/{beat_num}/sketch/upload'),
    ('POST', '/api/v1/projects/{project}/episodes/{episode_num}/beats/{beat_num}/render/upload'),
    ('POST', '/api/v1/projects/{project}/episodes/{episode_num}/grids/{grid_index}/sketch-preview'),
    ('POST', '/api/v1/projects/{project}/episodes/{episode_num}/verify/sketch-edit-execute/start'),
}
actual = {
    (method.upper(), path)
    for path, methods in paths.items()
    for method in methods
    if method.lower() in {'get', 'post', 'patch', 'put', 'delete'}
}
missing = sorted(expected - actual)
assert len(expected) == 60, len(expected)
assert not missing, missing
print('M05 operations OK', len(expected))
PY"

if [ "$MODE" = ce ]; then
  check "CE OpenAPI 暴露 scene director-stage world/clear" \
    bash -c "curl -sS 'localhost:$PORT/openapi.json' -o '$M05_OPENAPI_JSON' && uv run python - <<'PY'
import json
paths = json.load(open('$M05_OPENAPI_JSON', encoding='utf-8'))['paths']
assert '/api/v1/projects/{project}/scenes/{name}/director-stage/world' in paths
assert '/api/v1/projects/{project}/scenes/{name}/director-stage/world/clear' in paths
print('CE world routes present')
PY"
fi

if [ "$MODE" = ee ]; then
  check "EE 登录取得 st_session" \
    bash -c "curl -sS -c '$M05_COOKIE_JAR' -D '$M05_COOKIE_FILE.headers' -o '$M05_COOKIE_FILE.body' -X POST 'localhost:$PORT/api/v1/auth/login' -H 'Content-Type: application/json' -d '{\"username\":\"admin\",\"password\":\"admin123\"}' >/dev/null && grep -q 'st_session' '$M05_COOKIE_JAR'"
fi

check "setup 创建临时项目" \
  bash -c "code=\$(curl -sS -o '$M05_CREATE_JSON' -w '%{http_code}' -X POST 'localhost:$PORT/api/v1/projects' -b '$M05_COOKIE_JAR' -H 'Content-Type: application/json' -d '{\"name\":\"$M05_PROJECT_NAME\"}'); test \"\$code\" = 200 && uv run python -c 'import json,sys; print(json.load(open(sys.argv[1]))[\"data\"][\"id\"])' '$M05_CREATE_JSON' > '$M05_PROJECT_FILE'"

check "setup 种子写入 M05 场景/角色/episode/beat/文件" \
  bash -c "PROJECT_ID=\$(cat '$M05_PROJECT_FILE'); PROJECT_ID=\"\$PROJECT_ID\" uv run python - <<'PY' > '$M05_PROJECT_DIR_FILE'
import asyncio
import json
import os
from pathlib import Path

from PIL import Image

from novelvideo.director_world import stage_manifest
from novelvideo.models import CharacterIdentity, NovelCharacter, NovelEpisode, NovelScene, NovelVisualBeat
from novelvideo.ports import get_project_registry
from novelvideo.ports import registry
from novelvideo.sqlite_store import SQLiteStore


def png(path: Path, size=(2, 2), color=(30, 60, 90)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', size, color=color).save(path, format='PNG')


async def main():
    registry.ensure_bootstrap()
    record = await get_project_registry().get_project(os.environ['PROJECT_ID'])
    if record is None:
        raise SystemExit('project not found')
    store = SQLiteStore(
        f'{record.owner_username}/{record.name}',
        output_dir=record.output_dir,
        state_dir=record.state_dir,
    )
    project_dir = Path(record.output_dir)
    try:
        await store.initialize()
        identity = CharacterIdentity(
            identity_id='林昭_青年',
            character_name='林昭',
            identity_name='青年',
            appearance_details='青衣青年',
            face_prompt='clear eyes',
        )
        character = NovelCharacter(
            name='林昭',
            role='主角',
            is_main=True,
            face_prompt='clear eyes',
            description='少年侠客',
        )
        character.identities = [identity]
        await store.add_character(character)
        await store.add_scene(NovelScene(name='中庭', scene_type='exterior', environment_prompt='青石中庭'))
        await store.add_scene(
            NovelScene(
                name='中庭_雨夜',
                scene_type='exterior',
                base_scene_id='中庭',
                variant_id='雨夜',
            )
        )
        await store.add_episodes([
            NovelEpisode(number=1, title='第一集', raw_content='雨夜', beat_source_text='雨夜')
        ])
        await store.add_visual_beats([
            NovelVisualBeat(
                episode_number=1,
                beat_number=1,
                shot_order=10,
                narration='雨落中庭。',
                visual_description='{{林昭_青年}}站在中庭。',
                scene_ref_json=json.dumps({'scene_id': '中庭'}, ensure_ascii=False),
                detected_identities_json=json.dumps(['林昭_青年'], ensure_ascii=False),
            ),
            NovelVisualBeat(
                episode_number=1,
                beat_number=2,
                shot_order=20,
                narration='补拍雨声。',
                visual_description='{{林昭_青年}}回头。',
                scene_ref_json=json.dumps({'scene_id': '中庭'}, ensure_ascii=False),
                detected_identities_json=json.dumps(['林昭_青年'], ensure_ascii=False),
                is_manual_shot=True,
            ),
        ])
        await store.set_sketch_colors(1, {'林昭_青年': '#3366FF'})
    finally:
        await store.close()

    png(project_dir / 'assets' / 'characters' / '林昭' / 'identities' / '青年.png')
    png(project_dir / 'assets' / 'scenes' / '中庭' / 'master.png')
    png(project_dir / 'assets' / 'scenes' / '中庭' / 'reverse_master.png')
    world_dir = stage_manifest.stage_dir(project_dir, '中庭')
    png(world_dir / 'pano_360.png', size=(4, 2))
    for name in ('master_sharp.ply', 'reverse_sharp.ply', 'pano_depth.ply'):
        (world_dir / name).write_bytes(b'ply')
    stage_manifest.update_manifest(
        project_dir,
        '中庭',
        source='uploaded_360',
        pano_path='pano_360.png',
        ply_path='master_sharp.ply',
        master_ply_path='master_sharp.ply',
        reverse_ply_path='reverse_sharp.ply',
        pano_ply_path='pano_depth.ply',
    )
    labels_dir = project_dir / 'verify_reports' / 'ep001'
    labels_dir.mkdir(parents=True, exist_ok=True)
    row = {
        'project_dir': str(project_dir),
        'episode_num': 1,
        'beat_number': 1,
        'execution_mode': 'polish',
        'sketch_path': 'sketches/ep001/beat_01.png',
        'beat': {'beat_number': 1},
        'sketch_colors': [],
        'result': {
            'decision': 'revise',
            'main_problem': 'composition_weak',
            'reasoning': '需要强化构图。',
            'edit_instruction': 'Tighten the courtyard composition.',
            'confidence': 0.8,
        },
    }
    (labels_dir / 'labels.jsonl').write_text(json.dumps(row, ensure_ascii=False) + '\\n', encoding='utf-8')
    fixture_dir = project_dir / '_acceptance_fixtures'
    png(fixture_dir / 'tiny.png')
    png(fixture_dir / 'render.png', color=(90, 30, 60))
    png(fixture_dir / 'pano.png', size=(4, 2))
    (fixture_dir / 'scene.sog').write_bytes(b'sog data')
    print(project_dir)


asyncio.run(main())
PY"

check "M05 任务链路、读写往返与落盘断言" \
  bash -c "set -e; PROJECT_ID=\$(cat '$M05_PROJECT_FILE'); BASE='localhost:$PORT/api/v1/projects/'\"\$PROJECT_ID\"; FIXTURE_DIR=\$(tail -n 1 '$M05_PROJECT_DIR_FILE')/_acceptance_fixtures; IMG=\"\$FIXTURE_DIR/tiny.png\"; RENDER_IMG=\"\$FIXTURE_DIR/render.png\"; PANO=\"\$FIXTURE_DIR/pano.png\"; PLY=\"\$FIXTURE_DIR/scene.sog\";
wait_lane_slot() {
  lane_name=\"\$1\"
  for i in \$(seq 1 90); do
    curl -sS \"\$BASE/tasks/limits\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-task-limits.json'
    if LANE=\"\$lane_name\" uv run python - <<'PY'
import json
import os
payload = json.load(open('$M05_TMP-task-limits.json', encoding='utf-8'))
lane = payload.get('data', {}).get(os.environ['LANE'], {})
active = int(lane.get('active') or 0)
limit = lane.get('limit')
limit = 3 if limit is None else int(limit)
raise SystemExit(0 if active < limit else 1)
PY
    then
      return 0
    fi
    sleep 1
  done
  cat '$M05_TMP-task-limits.json'
  return 1
}
wait_default_slot() { wait_lane_slot default; }
wait_world_slot() { wait_lane_slot world; }
curl -sS \"\$BASE/scenes\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-scenes-list.json';
curl -sS \"\$BASE/scenes/plate-preview?scene_id=%E4%B8%AD%E5%BA%AD\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-plate-preview.json';
curl -sS \"\$BASE/scenes/%E4%B8%AD%E5%BA%AD/pano/manifest\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-pano-manifest.json';
curl -sS -X PATCH \"\$BASE/scenes/%E4%B8%AD%E5%BA%AD/pano/correction\" -b '$M05_COOKIE_JAR' -H 'Content-Type: application/json' -d '{\"front_yaw_deg\":12,\"sphere_correction_deg\":{\"yaw\":1,\"pitch\":0,\"roll\":0}}' -o '$M05_TMP-pano-correction.json';
curl -sS \"\$BASE/scenes/%E4%B8%AD%E5%BA%AD/director-stage/manifest\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-stage-manifest.json';
curl -sS -X POST \"\$BASE/scenes\" -b '$M05_COOKIE_JAR' -H 'Content-Type: application/json' -d '{\"name\":\"廊下\",\"environment_prompt\":\"木质长廊\"}' -o '$M05_TMP-scene-create.json';
curl -sS -X PATCH \"\$BASE/scenes/%E5%BB%8A%E4%B8%8B\" -b '$M05_COOKIE_JAR' -H 'Content-Type: application/json' -d '{\"notes\":\"updated\"}' -o '$M05_TMP-scene-patch.json';
curl -sS -X POST \"\$BASE/scenes/%E5%BB%8A%E4%B8%8B/delete\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-scene-delete.json';
wait_default_slot; curl -sS -X POST \"\$BASE/scenes/build\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-scenes-build.json';
curl -sS -X POST \"\$BASE/scenes/%E4%B8%AD%E5%BA%AD/master/upload\" -b '$M05_COOKIE_JAR' -F \"file=@\$IMG;type=image/png\" -o '$M05_TMP-master-upload.json';
wait_default_slot; curl -sS -X POST \"\$BASE/scenes/%E4%B8%AD%E5%BA%AD/master/generate-async\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-master-gen.json';
wait_default_slot; curl -sS -X POST \"\$BASE/scenes/%E4%B8%AD%E5%BA%AD/reverse/generate-async\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-reverse-gen.json';
curl -sS -X POST \"\$BASE/scenes/%E4%B8%AD%E5%BA%AD/pano/upload\" -b '$M05_COOKIE_JAR' -F \"file=@\$PANO;type=image/png\" -o '$M05_TMP-pano-upload.json';
curl -sS -X POST \"\$BASE/scenes/%E4%B8%AD%E5%BA%AD/custom/upload\" -b '$M05_COOKIE_JAR' -F \"file=@\$PLY;type=application/octet-stream\" -o '$M05_TMP-custom-upload.json';
wait_world_slot; curl -sS -X POST \"\$BASE/scenes/%E4%B8%AD%E5%BA%AD/3gs/master-ply/generate-async\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-3gs-master.json';
wait_world_slot; curl -sS -X POST \"\$BASE/scenes/%E4%B8%AD%E5%BA%AD/3gs/reverse-ply/generate-async\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-3gs-reverse.json';
wait_world_slot; curl -sS -X POST \"\$BASE/scenes/%E4%B8%AD%E5%BA%AD/3gs/pano-ply/generate-async\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-3gs-pano.json';
wait_world_slot; curl -sS -X POST \"\$BASE/scenes/%E4%B8%AD%E5%BA%AD/pano/generate-async\" -b '$M05_COOKIE_JAR' -H 'Content-Type: application/json' -d '{\"source\":\"text\"}' -o '$M05_TMP-pano-gen.json';
curl -sS -X POST \"\$BASE/scenes/%E4%B8%AD%E5%BA%AD/master/delete\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-master-delete.json';
curl -sS -X POST \"\$BASE/scenes/%E4%B8%AD%E5%BA%AD/master/upload\" -b '$M05_COOKIE_JAR' -F \"file=@\$IMG;type=image/png\" -o '$M05_TMP-master-reupload.json';
wait_default_slot; curl -sS -X POST \"\$BASE/episodes/1/scenes/plan\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-scenes-plan.json';
curl -sS \"\$BASE/sketch-settings\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-sketch-settings-get.json';
curl -sS -X PATCH \"\$BASE/sketch-settings\" -b '$M05_COOKIE_JAR' -H 'Content-Type: application/json' -d '{}' -o '$M05_TMP-sketch-settings-patch.json';
curl -sS -X PUT \"\$BASE/episodes/1/sketch-regen-queue\" -b '$M05_COOKIE_JAR' -H 'Content-Type: application/json' -d '{\"items\":[{\"id\":\"q1\",\"modeKey\":\"1x1_2-3\",\"modeLabel\":\"1x1\",\"beatNumbers\":[1],\"sceneIds\":[\"中庭\"],\"createdAt\":\"2026-06-17T00:00:00Z\"}]}' -o '$M05_TMP-queue-put.json';
curl -sS \"\$BASE/episodes/1/sketch-regen-queue\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-queue-get.json';
curl -sS \"\$BASE/episodes/1/sketch-image-usage\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-sketch-usage.json';
curl -sS \"\$BASE/episodes/1/image-generation-guard?task_type=sketch_grid&scope=grid_0\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-image-guard.json';
curl -sS -X POST \"\$BASE/episodes/1/image-generation-guard/verify-password\" -b '$M05_COOKIE_JAR' -H 'Content-Type: application/json' -d '{\"password\":\"\"}' -o '$M05_TMP-image-guard-password.json';
wait_default_slot; curl -sS -X POST \"\$BASE/episodes/1/sketches/generate\" -b '$M05_COOKIE_JAR' -H 'Content-Type: application/json' -d '{\"grid_index\":0}' -o '$M05_TMP-sketch-generate.json';
wait_default_slot; curl -sS -X POST \"\$BASE/episodes/1/sketches/regenerate\" -b '$M05_COOKIE_JAR' -H 'Content-Type: application/json' -d '{\"beat_indices\":[1]}' -o '$M05_TMP-sketch-regenerate.json';
wait_default_slot; curl -sS -X POST \"\$BASE/episodes/1/sketches/generate-missing-manual\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-sketch-manual.json';
wait_default_slot; curl -sS -X POST \"\$BASE/episodes/1/verify/sketch-edit-execute/start\" -b '$M05_COOKIE_JAR' -H 'Content-Type: application/json' -d '{}' -o '$M05_TMP-sketch-edit.json';
curl -sS -X POST \"\$BASE/episodes/1/render/plan\" -b '$M05_COOKIE_JAR' -H 'Content-Type: application/json' -d '{\"beat_indices\":[1],\"strategy\":\"naive\",\"aspect_mode\":\"9:16\"}' -o '$M05_TMP-render-plan.json';
uv run python - <<'PY'
import json
plan = json.load(open('$M05_TMP-render-plan.json', encoding='utf-8'))['data']
body = {
    'beat_indices': [1],
    'strategy': 'naive',
    'aspect_mode': '9:16',
    'plan': plan['plan'],
    'plan_hash': plan['plan_hash'],
    'input_fingerprint': plan['input_fingerprint'],
}
open('$M05_TMP-render-execute-body.json', 'w', encoding='utf-8').write(json.dumps(body, ensure_ascii=False))
PY
wait_default_slot; curl -sS -X POST \"\$BASE/episodes/1/render/execute\" -b '$M05_COOKIE_JAR' -H 'Content-Type: application/json' --data-binary '@$M05_TMP-render-execute-body.json' -o '$M05_TMP-render-execute.json';
curl -sS \"\$BASE/episodes/1/beats/1/pano-background/manifest\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-beat-pano.json';
curl -sS \"\$BASE/director-stage/palette\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-palette.json';
curl -sS \"\$BASE/episodes/1/beats/1/director-stage/manifest\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-beat-stage.json';
curl -sS \"\$BASE/episodes/1/beats/1/director-stage/overlay\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-overlay-get.json';
curl -sS -X POST \"\$BASE/episodes/1/beats/1/director-stage/overlay\" -b '$M05_COOKIE_JAR' -H 'Content-Type: application/json' -d '{\"actors\":[],\"props\":[],\"stagings\":[]}' -o '$M05_TMP-overlay-post.json';
uv run python - <<'PY'
import base64, json
data = 'data:image/png;base64,' + base64.b64encode(open('$M05_TMP', 'rb').read() if False else b'').decode()
png = 'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFElEQVR4nGNkYGBoYGBgYGBgAAACAAEAhiL91AAAAABJRU5ErkJggg=='
body = {'images': {'combined': 'data:image/png;base64,' + png, 'env_only': 'data:image/png;base64,' + png}, 'frame_meta': {'camera': 'wide'}}
open('$M05_TMP-control-body.json', 'w', encoding='utf-8').write(json.dumps(body))
PY
curl -sS -X POST \"\$BASE/episodes/1/beats/1/director-stage/control-frame\" -b '$M05_COOKIE_JAR' -H 'Content-Type: application/json' --data-binary '@$M05_TMP-control-body.json' -o '$M05_TMP-control-post.json';
curl -sS \"\$BASE/episodes/1/beats/1/director-control-frame\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-control-get.json';
wait_default_slot; curl -sS -X POST \"\$BASE/episodes/1/beats/1/director-control-to-sketch\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-control-to-sketch.json';
curl -sS \"\$BASE/episodes/1/beats/1/background-anchors\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-bg-get.json';
curl -sS -X POST \"\$BASE/episodes/1/beats/1/background-anchor/upload\" -b '$M05_COOKIE_JAR' -F \"file=@\$IMG;type=image/png\" -o '$M05_TMP-bg-upload.json';
curl -sS -X PATCH \"\$BASE/episodes/1/beats/1/background-anchor\" -b '$M05_COOKIE_JAR' -H 'Content-Type: application/json' -d '{\"anchor_id\":\"selected_background\"}' -o '$M05_TMP-bg-patch.json';
curl -sS -X POST \"\$BASE/episodes/1/beats/1/background-anchor/crop\" -b '$M05_COOKIE_JAR' -H 'Content-Type: application/json' -d '{\"anchor_id\":\"reverse\",\"x\":0,\"y\":0,\"width\":1,\"height\":1}' -o '$M05_TMP-bg-crop.json';
curl -sS \"\$BASE/episodes/1/grids\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-grids-get.json';
curl -sS -X POST \"\$BASE/episodes/1/grids/rebuild-pool\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-grids-rebuild.json';
curl -sS -X POST \"\$BASE/episodes/1/beats/1/sketch/upload\" -b '$M05_COOKIE_JAR' -F \"file=@\$IMG;type=image/png\" -o '$M05_TMP-sketch-upload.json';
curl -sS -X POST \"\$BASE/episodes/1/beats/1/render/upload\" -b '$M05_COOKIE_JAR' -F \"file=@\$RENDER_IMG;type=image/png\" -o '$M05_TMP-render-upload.json';
uv run python - <<'PY'
import json
sketch = json.load(open('$M05_TMP-sketch-upload.json', encoding='utf-8'))['data']['pool_id']
render = json.load(open('$M05_TMP-render-upload.json', encoding='utf-8'))['data']['pool_id']
open('$M05_TMP-pool-sketch.json', 'w', encoding='utf-8').write(json.dumps({'pool_id': sketch, 'force': True}))
open('$M05_TMP-pool-render.json', 'w', encoding='utf-8').write(json.dumps({'pool_id': render}))
PY
curl -sS -X POST \"\$BASE/episodes/1/beats/1/pool-select\" -b '$M05_COOKIE_JAR' -H 'Content-Type: application/json' --data-binary '@$M05_TMP-pool-sketch.json' -o '$M05_TMP-pool-select-sketch.json';
curl -sS -X POST \"\$BASE/episodes/1/beats/1/pool-select\" -b '$M05_COOKIE_JAR' -H 'Content-Type: application/json' --data-binary '@$M05_TMP-pool-render.json' -o '$M05_TMP-pool-select-render.json';
curl -sS \"\$BASE/episodes/1/beats/1/sketch/pose-editor\" -b '$M05_COOKIE_JAR' -o '$M05_TMP-pose-get.json';
curl -sS -X POST \"\$BASE/episodes/1/beats/1/sketch/pose-editor\" -b '$M05_COOKIE_JAR' -H 'Content-Type: application/json' -d '{\"strokes\":[]}' -o '$M05_TMP-pose-post.json';
curl -sS -X POST \"\$BASE/episodes/1/beats/1/sketch/crop\" -b '$M05_COOKIE_JAR' -H 'Content-Type: application/json' -d '{\"x\":0,\"y\":0,\"width\":1,\"height\":1}' -o '$M05_TMP-sketch-crop.json';
curl -sS -X POST \"\$BASE/episodes/1/grids/0/sketch-preview\" -b '$M05_COOKIE_JAR' -H 'Content-Type: application/json' -d '{\"rows\":1,\"cols\":1,\"beat_numbers\":[1]}' -o '$M05_TMP-sketch-preview.json';
MODE='$MODE' PROJECT_DIR_FILE='$M05_PROJECT_DIR_FILE' uv run python - <<'PY'
import json
import os
from pathlib import Path

prefix = '$M05_TMP'
project_dir = Path([
    line.strip()
    for line in open(os.environ['PROJECT_DIR_FILE'], encoding='utf-8')
    if line.strip()
][-1])
expected_backend = 'inline' if os.environ['MODE'] == 'ce' else 'celery'

def body(name):
    return json.load(open(f'{prefix}-{name}.json', encoding='utf-8'))

def ok(name):
    payload = body(name)
    assert payload.get('ok') is True, (name, payload)
    return payload

def task(name, task_type):
    payload = ok(name)
    assert payload['task_type'] == task_type, (name, payload)
    assert payload['task_id'] and payload['task_key'] and 'queue' in payload, (name, payload)
    # guard: "backend"] == ("inline" if os.environ["MODE"] == "ce" else "celery")
    assert payload[\"backend\"] == (\"inline\" if os.environ[\"MODE\"] == \"ce\" else \"celery\"), (name, payload)
    return payload

for name in [
    'scenes-list', 'plate-preview', 'pano-manifest', 'pano-correction', 'stage-manifest',
    'scene-create', 'scene-patch', 'scene-delete', 'master-upload', 'master-delete',
    'master-reupload', 'pano-upload',
    'custom-upload', 'sketch-settings-get', 'sketch-settings-patch', 'queue-put',
    'queue-get', 'sketch-usage', 'image-guard', 'image-guard-password', 'render-plan',
    'render-execute', 'beat-pano', 'palette', 'beat-stage', 'overlay-get', 'overlay-post',
    'control-post', 'control-get', 'control-to-sketch', 'bg-get', 'bg-upload',
    'bg-patch', 'bg-crop', 'sketch-upload', 'render-upload', 'grids-get',
    'grids-rebuild', 'pool-select-sketch', 'pool-select-render', 'pose-get',
    'pose-post', 'sketch-crop', 'sketch-preview',
]:
    ok(name)

task('scenes-build', 'build_scenes')
task('master-gen', 'scene_reference_asset')
task('reverse-gen', 'scene_reference_asset')
task('3gs-master', 'stage_asset')
task('3gs-reverse', 'stage_asset')
task('3gs-pano', 'stage_asset')
task('pano-gen', 'stage_asset')
task('scenes-plan', 'episode_scene_planner')
task('sketch-generate', 'sketch_generation')
task('sketch-regenerate', 'sketch_regen')
task('sketch-edit', 'sketch_edit_execute')
manual = ok('sketch-manual')
assert manual['task_type'] == 'sketch_regen' and manual['data']['dispatched'] >= 1, manual
plan = ok('render-plan')['data']
assert {'plan', 'plan_hash', 'input_fingerprint'} <= set(plan), plan
execute = ok('render-execute')['data']
assert execute['task_type'] == 'render_plan' and execute['task_ids'], execute
assert 'selected_regen' in json.dumps(execute, ensure_ascii=False) or execute['task_ids']
assert (project_dir / 'sketches' / 'ep001' / 'beat_01.png').exists()
assert (project_dir / 'frames' / 'ep001' / 'beat_01.png').exists()
assert (project_dir / 'director_control_frames' / 'ep001' / 'beat_01' / 'selected_background.png').exists()
assert (project_dir / 'director_control_frames' / 'ep001' / 'beat_01' / 'combined.png').exists()
assert (project_dir / 'assets' / 'scenes' / '中庭' / 'master.png').exists()
print('M05 chain and disk assertions OK', expected_backend)
PY"
