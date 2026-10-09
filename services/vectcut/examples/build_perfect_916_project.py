import json
import os
import time
import uuid
import shutil

ASSETS = r"c:\Users\Hemanshi Makwana\OneDrive\Documents\new\assets"
BASE_DIR = r"C:\Users\Hemanshi Makwana\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft"
DRAFT_DIR = r"C:\Dev\capcut\VectCutAPI\dfd_cat_1788448659_9eb33f97"

# Projects to update
PROJECT_NAMES = ["Hemanshi_9_16", "Hemanshi_Final", "HemanshiName"]

# Read source draft template from DRAFT_DIR
with open(os.path.join(DRAFT_DIR, "draft_info.json"), "r", encoding="utf-8") as f:
    d = json.load(f)

# 1. Canvas Config: STRICT 9:16 VERTICAL (1080x1920)
d["canvas_config"] = {
    "height": 1920,
    "ratio": "9:16",
    "width": 1080,
    "background": None
}
d["duration"] = 25000000
d["fps"] = 30.0

# 2. Update Video Materials to actual disk paths
clip_map = {
    0: os.path.join(ASSETS, "clip_1_hema.mp4"),
    1: os.path.join(ASSETS, "clip_2_anshi.mp4"),
    2: os.path.join(ASSETS, "clip_3_hemanshi.mp4"),
    3: os.path.join(ASSETS, "clip_4_resolution.mp4"),
}

for i, v in enumerate(d["materials"]["videos"]):
    actual_p = clip_map.get(i, "")
    v["path"] = actual_p
    v["media_path"] = actual_p
    v["material_name"] = os.path.basename(actual_p)
    v["width"] = 1080
    v["height"] = 1920
    v["crop_ratio"] = "9:16"

# 3. Update Audio Materials to actual disk paths
d["materials"]["audios"] = [
    {
        "app_id": 0, "category_id": "", "category_name": "local", "check_flag": 1,
        "copyright_limit_type": "none", "duration": 25000000, "effect_id": "", "formula_id": "",
        "id": "music_mat_id", "intensifies_path": "", "is_ai_clone_tone": False, "is_text_edit_overdub": False,
        "is_ugc": False, "local_material_id": "music_mat_id", "music_id": "music_mat_id",
        "name": "music_processed.mp3", "path": os.path.join(ASSETS, "music_processed.mp3"),
        "remote_url": os.path.join(ASSETS, "music_processed.mp3"), "query": "", "request_id": "",
        "resource_id": "", "search_id": "", "source_from": "", "source_platform": 0, "team_id": "",
        "text_id": "", "tone_category_id": "", "tone_category_name": "", "tone_effect_id": "",
        "tone_effect_name": "", "tone_platform": "", "tone_second_category_id": "", "tone_second_category_name": "",
        "tone_speaker": "", "tone_type": "", "type": "extract_music", "video_id": "", "wave_points": []
    },
    {
        "app_id": 0, "category_id": "", "category_name": "local", "check_flag": 1,
        "copyright_limit_type": "none", "duration": 23200000, "effect_id": "", "formula_id": "",
        "id": "vo_mat_id", "intensifies_path": "", "is_ai_clone_tone": False, "is_text_edit_overdub": False,
        "is_ugc": False, "local_material_id": "vo_mat_id", "music_id": "vo_mat_id",
        "name": "voiceover_hindi_processed.mp3", "path": os.path.join(ASSETS, "voiceover_hindi_processed.mp3"),
        "remote_url": os.path.join(ASSETS, "voiceover_hindi_processed.mp3"), "query": "", "request_id": "",
        "resource_id": "", "search_id": "", "source_from": "", "source_platform": 0, "team_id": "",
        "text_id": "", "tone_category_id": "", "tone_category_name": "", "tone_effect_id": "",
        "tone_effect_name": "", "tone_platform": "", "tone_second_category_id": "", "tone_second_category_name": "",
        "tone_speaker": "", "tone_type": "", "type": "extract_music", "video_id": "", "wave_points": []
    },
    {
        "app_id": 0, "category_id": "", "category_name": "local", "check_flag": 1,
        "copyright_limit_type": "none", "duration": 1690000, "effect_id": "", "formula_id": "",
        "id": "chime_mat_id", "intensifies_path": "", "is_ai_clone_tone": False, "is_text_edit_overdub": False,
        "is_ugc": False, "local_material_id": "chime_mat_id", "music_id": "chime_mat_id",
        "name": "sfx_chime.mp3", "path": os.path.join(ASSETS, "sfx_chime.mp3"),
        "remote_url": os.path.join(ASSETS, "sfx_chime.mp3"), "query": "", "request_id": "",
        "resource_id": "", "search_id": "", "source_from": "", "source_platform": 0, "team_id": "",
        "text_id": "", "tone_category_id": "", "tone_category_name": "", "tone_effect_id": "",
        "tone_effect_name": "", "tone_platform": "", "tone_second_category_id": "", "tone_second_category_name": "",
        "tone_speaker": "", "tone_type": "", "type": "extract_music", "video_id": "", "wave_points": []
    },
    {
        "app_id": 0, "category_id": "", "category_name": "local", "check_flag": 1,
        "copyright_limit_type": "none", "duration": 1100000, "effect_id": "", "formula_id": "",
        "id": "whoosh_mat_id", "intensifies_path": "", "is_ai_clone_tone": False, "is_text_edit_overdub": False,
        "is_ugc": False, "local_material_id": "whoosh_mat_id", "music_id": "whoosh_mat_id",
        "name": "sfx_whoosh.mp3", "path": os.path.join(ASSETS, "sfx_whoosh.mp3"),
        "remote_url": os.path.join(ASSETS, "sfx_whoosh.mp3"), "query": "", "request_id": "",
        "resource_id": "", "search_id": "", "source_from": "", "source_platform": 0, "team_id": "",
        "text_id": "", "tone_category_id": "", "tone_category_name": "", "tone_effect_id": "",
        "tone_effect_name": "", "tone_platform": "", "tone_second_category_id": "", "tone_second_category_name": "",
        "tone_speaker": "", "tone_type": "", "type": "extract_music", "video_id": "", "wave_points": []
    }
]

# 4. Perfectly Adjusted, Beautiful 9:16 Typography:
# In CapCut 9:16 vertical canvas:
# font_size: 4.5 - 5.5 is clean, legible, and fits neatly in the center!
text_configs = [
    {
        "mat_id": "txt_mat_0",
        "text": "Hema (हेम)\nGold • Radiance",
        "font_size": 5.0,
        "styles": [
            {
                "fill": {"alpha": 1.0, "content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": [1.0, 0.843, 0.0]}}},
                "range": [0, 10],
                "size": 5.5,
                "bold": True,
                "shadows": [{"alpha": 0.8, "angle": -45.0, "content": {"solid": {"color": [0.0, 0.0, 0.0]}}, "diffuse": 0.05, "distance": 3.0}]
            },
            {
                "fill": {"alpha": 1.0, "content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": [1.0, 0.96, 0.88]}}},
                "range": [11, 26],
                "size": 3.8,
                "bold": False,
                "shadows": [{"alpha": 0.8, "angle": -45.0, "content": {"solid": {"color": [0.0, 0.0, 0.0]}}, "diffuse": 0.05, "distance": 2.5}]
            }
        ]
    },
    {
        "mat_id": "txt_mat_1",
        "text": "Anshi (अंशी)\nA Sacred Fragment",
        "font_size": 5.0,
        "styles": [
            {
                "fill": {"alpha": 1.0, "content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": [1.0, 0.843, 0.0]}}},
                "range": [0, 12],
                "size": 5.5,
                "bold": True,
                "shadows": [{"alpha": 0.8, "angle": -45.0, "content": {"solid": {"color": [0.0, 0.0, 0.0]}}, "diffuse": 0.05, "distance": 3.0}]
            },
            {
                "fill": {"alpha": 1.0, "content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": [1.0, 0.96, 0.88]}}},
                "range": [13, 30],
                "size": 3.8,
                "bold": False,
                "shadows": [{"alpha": 0.8, "angle": -45.0, "content": {"solid": {"color": [0.0, 0.0, 0.0]}}, "diffuse": 0.05, "distance": 2.5}]
            }
        ]
    },
    {
        "mat_id": "txt_mat_2",
        "text": "HEMANSHI\nA Part of Gold",
        "font_size": 5.0,
        "styles": [
            {
                "fill": {"alpha": 1.0, "content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": [1.0, 0.843, 0.0]}}},
                "range": [0, 8],
                "size": 5.8,
                "bold": True,
                "shadows": [{"alpha": 0.9, "angle": -45.0, "content": {"solid": {"color": [0.0, 0.0, 0.0]}}, "diffuse": 0.05, "distance": 3.0}]
            },
            {
                "fill": {"alpha": 1.0, "content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": [1.0, 1.0, 1.0]}}},
                "range": [9, 23],
                "size": 3.8,
                "bold": False,
                "shadows": [{"alpha": 0.9, "angle": -45.0, "content": {"solid": {"color": [0.0, 0.0, 0.0]}}, "diffuse": 0.05, "distance": 2.5}]
            }
        ]
    },
    {
        "mat_id": "txt_mat_3",
        "text": "HEMANSHI\nहेमांशी\nPrecious • Radiant • Pure",
        "font_size": 5.0,
        "styles": [
            {
                "fill": {"alpha": 1.0, "content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": [1.0, 0.843, 0.0]}}},
                "range": [0, 8],
                "size": 5.8,
                "bold": True,
                "shadows": [{"alpha": 0.8, "angle": -45.0, "content": {"solid": {"color": [0.0, 0.0, 0.0]}}, "diffuse": 0.05, "distance": 3.0}]
            },
            {
                "fill": {"alpha": 1.0, "content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": [1.0, 1.0, 1.0]}}},
                "range": [9, 16],
                "size": 5.0,
                "bold": True,
                "shadows": [{"alpha": 0.8, "angle": -45.0, "content": {"solid": {"color": [0.0, 0.0, 0.0]}}, "diffuse": 0.05, "distance": 3.0}]
            },
            {
                "fill": {"alpha": 1.0, "content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": [1.0, 0.96, 0.88]}}},
                "range": [17, 43],
                "size": 3.6,
                "bold": False,
                "shadows": [{"alpha": 0.8, "angle": -45.0, "content": {"solid": {"color": [0.0, 0.0, 0.0]}}, "diffuse": 0.05, "distance": 2.5}]
            }
        ]
    }
]

d["materials"]["texts"] = []
for cfg in text_configs:
    t_obj = {
        "id": cfg["mat_id"],
        "name": "",
        "type": "text",
        "content": json.dumps({"text": cfg["text"], "styles": cfg["styles"]}, ensure_ascii=False),
        "font_size": cfg["font_size"],
        "text_size": 15,
        "alignment": 1,
        "line_spacing": 0.04,
        "has_shadow": True,
        "shadow_color": "#000000",
        "shadow_alpha": 0.8,
        "shadow_distance": 3.0,
        "initial_scale": 0.0,
        "typesetting": 0,
        "line_feed": 1,
        "use_effect_default_color": True,
        "layer_weight": 0,
        "letter_spacing": 0.0,
        "font_path": "C:/Users/Hemanshi Makwana/AppData/Local/CapCut/Apps/9.4.0.4008/Resources/Font/SystemFont/en.ttf",
        "font_title": "none"
    }
    d["materials"]["texts"].append(t_obj)

# 5. Clean Tracks Structure
# Video Track
v_track = {
    "attribute": 0, "flag": 0, "id": "v_track_main", "is_default_name": True, "name": "video_main", "type": "video",
    "segments": [
        {
            "id": "v_seg_0", "material_id": d["materials"]["videos"][0]["id"],
            "target_timerange": {"start": 0, "duration": 6000000},
            "source_timerange": {"start": 0, "duration": 6000000},
            "speed": 1.0, "volume": 0.0, "render_index": 0, "visible": True,
            "clip": {"scale": {"x": 1.0, "y": 1.0}, "transform": {"x": 0.0, "y": 0.0}, "rotation": 0.0, "flip": {"vertical": False, "horizontal": False}, "alpha": 1.0},
            "uniform_scale": {"on": True, "value": 1.0},
            "extra_material_refs": []
        },
        {
            "id": "v_seg_1", "material_id": d["materials"]["videos"][1]["id"],
            "target_timerange": {"start": 6000000, "duration": 6000000},
            "source_timerange": {"start": 0, "duration": 6000000},
            "speed": 1.0, "volume": 0.0, "render_index": 0, "visible": True,
            "clip": {"scale": {"x": 1.0, "y": 1.0}, "transform": {"x": 0.0, "y": 0.0}, "rotation": 0.0, "flip": {"vertical": False, "horizontal": False}, "alpha": 1.0},
            "uniform_scale": {"on": True, "value": 1.0},
            "extra_material_refs": []
        },
        {
            "id": "v_seg_2", "material_id": d["materials"]["videos"][2]["id"],
            "target_timerange": {"start": 12000000, "duration": 7000000},
            "source_timerange": {"start": 0, "duration": 7000000},
            "speed": 1.0, "volume": 0.0, "render_index": 0, "visible": True,
            "clip": {"scale": {"x": 1.0, "y": 1.0}, "transform": {"x": 0.0, "y": 0.0}, "rotation": 0.0, "flip": {"vertical": False, "horizontal": False}, "alpha": 1.0},
            "uniform_scale": {"on": True, "value": 1.0},
            "extra_material_refs": []
        },
        {
            "id": "v_seg_3", "material_id": d["materials"]["videos"][3]["id"],
            "target_timerange": {"start": 19000000, "duration": 6000000},
            "source_timerange": {"start": 0, "duration": 6000000},
            "speed": 1.0, "volume": 0.0, "render_index": 0, "visible": True,
            "clip": {"scale": {"x": 1.0, "y": 1.0}, "transform": {"x": 0.0, "y": 0.0}, "rotation": 0.0, "flip": {"vertical": False, "horizontal": False}, "alpha": 1.0},
            "uniform_scale": {"on": True, "value": 1.0},
            "extra_material_refs": []
        }
    ]
}

# Text Track
txt_track = {
    "attribute": 0, "flag": 0, "id": "t_track_main", "is_default_name": True, "name": "text_main", "type": "text",
    "segments": [
        {
            "id": "t_seg_0", "material_id": "txt_mat_0",
            "target_timerange": {"start": 500000, "duration": 5300000},
            "source_timerange": None, "speed": 1.0, "volume": 1.0, "render_index": 15000, "visible": True,
            "clip": {"scale": {"x": 1.0, "y": 1.0}, "transform": {"x": 0.0, "y": 0.0}, "rotation": 0.0, "flip": {"vertical": False, "horizontal": False}, "alpha": 1.0},
            "uniform_scale": {"on": True, "value": 1.0}, "extra_material_refs": []
        },
        {
            "id": "t_seg_1", "material_id": "txt_mat_1",
            "target_timerange": {"start": 6400000, "duration": 5400000},
            "source_timerange": None, "speed": 1.0, "volume": 1.0, "render_index": 15001, "visible": True,
            "clip": {"scale": {"x": 1.0, "y": 1.0}, "transform": {"x": 0.0, "y": 0.0}, "rotation": 0.0, "flip": {"vertical": False, "horizontal": False}, "alpha": 1.0},
            "uniform_scale": {"on": True, "value": 1.0}, "extra_material_refs": []
        },
        {
            "id": "t_seg_2", "material_id": "txt_mat_2",
            "target_timerange": {"start": 12300000, "duration": 6500000},
            "source_timerange": None, "speed": 1.0, "volume": 1.0, "render_index": 15002, "visible": True,
            "clip": {"scale": {"x": 1.0, "y": 1.0}, "transform": {"x": 0.0, "y": -0.60}, "rotation": 0.0, "flip": {"vertical": False, "horizontal": False}, "alpha": 1.0},
            "uniform_scale": {"on": True, "value": 1.0}, "extra_material_refs": []
        },
        {
            "id": "t_seg_3", "material_id": "txt_mat_3",
            "target_timerange": {"start": 19400000, "duration": 4600000},
            "source_timerange": None, "speed": 1.0, "volume": 1.0, "render_index": 15003, "visible": True,
            "clip": {"scale": {"x": 1.0, "y": 1.0}, "transform": {"x": 0.0, "y": 0.0}, "rotation": 0.0, "flip": {"vertical": False, "horizontal": False}, "alpha": 1.0},
            "uniform_scale": {"on": True, "value": 1.0}, "extra_material_refs": []
        }
    ]
}

# Music Track
music_track = {
    "attribute": 0, "flag": 0, "id": "a_track_music", "is_default_name": True, "name": "audio_main", "type": "audio",
    "segments": [{
        "id": "a_seg_music", "material_id": "music_mat_id",
        "target_timerange": {"start": 0, "duration": 25000000},
        "source_timerange": {"start": 0, "duration": 25000000},
        "speed": 1.0, "volume": 0.65, "render_index": 0, "visible": True,
        "clip": None, "extra_material_refs": []
    }]
}

# Voiceover Track
vo_track = {
    "attribute": 0, "flag": 0, "id": "a_track_vo", "is_default_name": True, "name": "audio_voiceover", "type": "audio",
    "segments": [{
        "id": "a_seg_vo", "material_id": "vo_mat_id",
        "target_timerange": {"start": 0, "duration": 23200000},
        "source_timerange": {"start": 0, "duration": 23200000},
        "speed": 1.0, "volume": 1.0, "render_index": 0, "visible": True,
        "clip": None, "extra_material_refs": []
    }]
}

# SFX Chime Track
chime_track = {
    "attribute": 0, "flag": 0, "id": "a_track_chime", "is_default_name": True, "name": "audio_sfx_chime", "type": "audio",
    "segments": [{
        "id": "a_seg_chime", "material_id": "chime_mat_id",
        "target_timerange": {"start": 400000, "duration": 1690000},
        "source_timerange": {"start": 0, "duration": 1690000},
        "speed": 1.0, "volume": 0.5, "render_index": 0, "visible": True,
        "clip": None, "extra_material_refs": []
    }]
}

# SFX Whoosh Track
whoosh_track = {
    "attribute": 0, "flag": 0, "id": "a_track_whoosh", "is_default_name": True, "name": "audio_sfx_whoosh", "type": "audio",
    "segments": [{
        "id": "a_seg_whoosh", "material_id": "whoosh_mat_id",
        "target_timerange": {"start": 5800000, "duration": 1100000},
        "source_timerange": {"start": 0, "duration": 1100000},
        "speed": 1.0, "volume": 0.45, "render_index": 0, "visible": True,
        "clip": None, "extra_material_refs": []
    }]
}

d["tracks"] = [v_track, music_track, txt_track, vo_track, chime_track, whoosh_track]

# Deploy to all target directories
for p_name in PROJECT_NAMES:
    p_dir = os.path.join(BASE_DIR, p_name)
    tl_id = "50CFAF2A-71FD-4ada-BB0A-83495337B6D7"
    tl_dir = os.path.join(p_dir, "Timelines", tl_id)
    os.makedirs(tl_dir, exist_ok=True)
    
    d["id"] = tl_id
    content_json = json.dumps(d, ensure_ascii=False, separators=(',', ':'))
    
    # Write files
    for fname in ["draft_content.json", "draft_content.json.bak", "template-2.tmp"]:
        with open(os.path.join(p_dir, fname), "w", encoding="utf-8") as f:
            f.write(content_json)
            
    for fname in ["draft_content.json", "draft_content.json.bak", "template-2.tmp", "template.tmp"]:
        with open(os.path.join(tl_dir, fname), "w", encoding="utf-8") as f:
            f.write(content_json)
            
    # Timelines project.json
    now_us = int(time.time() * 1000000)
    tl_proj = {
        "config": {"color_space": -1, "hdr_vivid": False, "mixed_track_mode_on": False, "render_index_track_mode_on": False, "use_float_render": False},
        "create_time": now_us,
        "id": tl_id,
        "main_timeline_id": tl_id,
        "timelines": [{"create_time": now_us, "id": tl_id, "is_marked_delete": False, "name": "Timeline 01", "update_time": now_us}],
        "update_time": now_us,
        "version": 0
    }
    with open(os.path.join(p_dir, "Timelines", "project.json"), "w", encoding="utf-8") as f:
        json.dump(tl_proj, f, ensure_ascii=False)
        
    # timeline_layout.json
    layout = {
        "dockItems": [{"dockIndex": 0, "ratio": 1, "timelineIds": [tl_id], "timelineNames": ["Timeline 01"]}],
        "layoutOrientation": 1
    }
    with open(os.path.join(p_dir, "timeline_layout.json"), "w", encoding="utf-8") as f:
        json.dump(layout, f, ensure_ascii=False)
        
    # draft_meta_info.json
    meta_path = os.path.join(p_dir, "draft_meta_info.json")
    meta = {}
    if os.path.exists(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    meta["draft_name"] = p_name
    meta["draft_root_path"] = BASE_DIR
    meta["tm_duration"] = 25000000
    meta["tm_draft_modified"] = now_us
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
        
    print(f"Deployed 9:16 vertical project: {p_name}")

# Update root_meta_info.json so CapCut sees all 3 projects with 25s duration
root_meta_path = os.path.join(BASE_DIR, "root_meta_info.json")
if os.path.exists(root_meta_path):
    with open(root_meta_path, "r", encoding="utf-8") as f:
        rm = json.load(f)
    draft_names_in_rm = [x.get("draft_name") for x in rm.get("all_draft_store", [])]
    for p_name in PROJECT_NAMES:
        if p_name not in draft_names_in_rm:
            entry = {
                "cloud_draft_cover": False, "cloud_draft_sync": False, "draft_cloud_last_action_download": False,
                "draft_cloud_purchase_info": "", "draft_cloud_template_id": "", "draft_cloud_tutorial_info": "",
                "draft_cloud_videocut_purchase_info": "",
                "draft_cover": os.path.join(BASE_DIR, p_name, "draft_cover.jpg").replace("/", "\\"),
                "draft_fold_path": os.path.join(BASE_DIR, p_name).replace("\\", "/"),
                "draft_id": str(uuid.uuid4()).upper(),
                "draft_is_ai_shorts": False, "draft_is_cloud_temp_draft": False, "draft_is_infinite_canvas_draft": False,
                "draft_is_invisible": False, "draft_is_pippit_draft": False, "draft_is_web_article_video": False,
                "draft_json_file": os.path.join(BASE_DIR, p_name, "draft_content.json"),
                "draft_name": p_name, "draft_new_version": "",
                "draft_root_path": BASE_DIR, "draft_timeline_materials_size": 70000, "draft_type": "",
                "draft_web_article_video_enter_from": "", "pippit_avatar_url": "", "pippit_extra_info": "",
                "pippit_id": "", "pippit_user_name": "", "streaming_edit_draft_ready": True,
                "tm_draft_cloud_completed": "", "tm_draft_cloud_entry_id": -1, "tm_draft_cloud_modified": 0,
                "tm_draft_cloud_parent_entry_id": -1, "tm_draft_cloud_space_id": -1, "tm_draft_cloud_user_id": -1,
                "tm_draft_create": now_us, "tm_draft_modified": now_us, "tm_draft_removed": 0, "tm_duration": 25000000
            }
            rm["all_draft_store"].insert(0, entry)
        else:
            for item in rm["all_draft_store"]:
                if item.get("draft_name") == p_name:
                    item["tm_duration"] = 25000000
                    item["tm_draft_modified"] = now_us
    with open(root_meta_path, "w", encoding="utf-8") as f:
        json.dump(rm, f, ensure_ascii=False)
    print("Updated root_meta_info.json")

print("All projects 9:16 conversion and font adjustments complete!")
