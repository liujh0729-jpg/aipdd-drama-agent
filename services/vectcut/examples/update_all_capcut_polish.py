import json
import os
import time

ASSETS = r"c:\Users\Hemanshi Makwana\OneDrive\Documents\new\assets"
BASE_DIR = r"C:\Users\Hemanshi Makwana\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft"
CINZEL_FONT = os.path.join(ASSETS, "fonts", "Cinzel-Bold.ttf").replace("\\", "/")

PROJECT_NAMES = ["Hemanshi_9_16", "Hemanshi_Final", "HemanshiName"]

text_configs = [
    # Scene 1: Introductory title (0:01.0 - 0:05.3) - Boosted to 8.0
    {
        "mat_id": "txt_mat_0",
        "text": "THE ESSENCE OF A NAME",
        "font_size": 8.0,
        "line_spacing": 0.20,
        "transform_y": 0.0,
        "styles": [
            {
                "fill": {"alpha": 1.0, "content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": [1.0, 0.843, 0.0]}}},
                "range": [0, 21],
                "size": 8.0,
                "bold": True,
                "shadows": [{"alpha": 0.95, "angle": -45.0, "content": {"solid": {"color": [0.0, 0.0, 0.0]}}, "diffuse": 0.06, "distance": 4.5}]
            }
        ]
    },
    # Scene 2: Upper-third clouds placement away from sun (0:06.4 - 0:11.8)
    # Line spacing 0.32 gives generous breathing room between Hema | Anshi and Pure Gold
    {
        "mat_id": "txt_mat_1",
        "text": "HEMA  |  ANSHI\nPure Gold  |  A Sacred Fragment",
        "font_size": 7.5,
        "line_spacing": 0.32,
        "transform_y": 0.48, # Upper third!
        "styles": [
            {
                "fill": {"alpha": 1.0, "content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": [1.0, 0.843, 0.0]}}},
                "range": [0, 14],
                "size": 8.8,
                "bold": True,
                "shadows": [{"alpha": 0.95, "angle": -45.0, "content": {"solid": {"color": [0.0, 0.0, 0.0]}}, "diffuse": 0.08, "distance": 5.0}]
            },
            {
                "fill": {"alpha": 1.0, "content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": [1.0, 0.96, 0.88]}}},
                "range": [15, 45],
                "size": 6.6, # Boosted from 4.8!
                "bold": False,
                "shadows": [{"alpha": 0.95, "angle": -45.0, "content": {"solid": {"color": [0.0, 0.0, 0.0]}}, "diffuse": 0.08, "distance": 4.0}]
            }
        ]
    },
    # Scene 3: Lower-third overlay on Hemanshi's photo (0:12.6 - 0:18.8)
    # Line spacing 0.28, lowered to -0.66 to sit cleanly over lower stairs
    {
        "mat_id": "txt_mat_2",
        "text": "HEMANSHI\nA Sacred Fragment of Gold",
        "font_size": 7.5,
        "line_spacing": 0.28,
        "transform_y": -0.66, # Lower third below subject!
        "styles": [
            {
                "fill": {"alpha": 1.0, "content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": [1.0, 0.843, 0.0]}}},
                "range": [0, 8],
                "size": 9.2,
                "bold": True,
                "shadows": [{"alpha": 0.95, "angle": -45.0, "content": {"solid": {"color": [0.0, 0.0, 0.0]}}, "diffuse": 0.06, "distance": 4.5}]
            },
            {
                "fill": {"alpha": 1.0, "content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": [1.0, 1.0, 1.0]}}},
                "range": [9, 34],
                "size": 6.6, # Boosted from 4.8!
                "bold": False,
                "shadows": [{"alpha": 0.95, "angle": -45.0, "content": {"solid": {"color": [0.0, 0.0, 0.0]}}, "diffuse": 0.06, "distance": 3.5}]
            }
        ]
    },
    # Scene 4: Scaled Outro Typography (0:19.4 - 0:24.8)
    # Line spacing 0.26, subtitle boosted to 6.8!
    {
        "mat_id": "txt_mat_3",
        "text": "HEMANSHI\nहेमांशी\nPrecious  -  Radiant  -  Pure",
        "font_size": 9.5,
        "line_spacing": 0.26,
        "transform_y": 0.0, # Center!
        "styles": [
            {
                "fill": {"alpha": 1.0, "content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": [1.0, 0.843, 0.0]}}},
                "range": [0, 8],
                "size": 11.5,
                "bold": True,
                "shadows": [{"alpha": 0.95, "angle": -45.0, "content": {"solid": {"color": [0.0, 0.0, 0.0]}}, "diffuse": 0.08, "distance": 6.0}]
            },
            {
                "fill": {"alpha": 1.0, "content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": [1.0, 1.0, 1.0]}}},
                "range": [9, 16],
                "size": 9.5,
                "bold": True,
                "shadows": [{"alpha": 0.95, "angle": -45.0, "content": {"solid": {"color": [0.0, 0.0, 0.0]}}, "diffuse": 0.08, "distance": 5.0}]
            },
            {
                "fill": {"alpha": 1.0, "content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": [1.0, 0.96, 0.88]}}},
                "range": [17, 45],
                "size": 6.8, # Boosted from 5.2!
                "bold": False,
                "shadows": [{"alpha": 0.95, "angle": -45.0, "content": {"solid": {"color": [0.0, 0.0, 0.0]}}, "diffuse": 0.06, "distance": 4.0}]
            }
        ]
    }
]

def update_projects():
    for p_name in PROJECT_NAMES:
        p_dir = os.path.join(BASE_DIR, p_name)
        tl_id = "50CFAF2A-71FD-4ada-BB0A-83495337B6D7"
        tl_dir = os.path.join(p_dir, "Timelines", tl_id)
        
        main_draft_path = os.path.join(tl_dir, "draft_content.json")
        if not os.path.exists(main_draft_path):
            main_draft_path = os.path.join(p_dir, "draft_content.json")
            
        with open(main_draft_path, "r", encoding="utf-8") as f:
            d = json.load(f)
            
        # Update text materials
        d["materials"]["texts"] = []
        for cfg in text_configs:
            t_obj = {
                "id": cfg["mat_id"],
                "name": "",
                "type": "text",
                "content": json.dumps({"text": cfg["text"], "styles": cfg["styles"]}, ensure_ascii=False),
                "font_size": cfg["font_size"],
                "text_size": 20,
                "alignment": 1,
                "line_spacing": 0.05,
                "has_shadow": True,
                "shadow_color": "#000000",
                "shadow_alpha": 0.9,
                "shadow_distance": 4.0,
                "initial_scale": 0.0,
                "typesetting": 0,
                "line_feed": 1,
                "use_effect_default_color": True,
                "layer_weight": 0,
                "letter_spacing": 0.0,
                "font_path": CINZEL_FONT,
                "font_title": "none"
            }
            d["materials"]["texts"].append(t_obj)
            
        # Update text track segment transforms
        for trk in d["tracks"]:
            if trk.get("type") == "text":
                for i, seg in enumerate(trk.get("segments", [])):
                    cfg = text_configs[i]
                    seg["clip"]["transform"] = {"x": 0.0, "y": cfg["transform_y"]}
                    seg["uniform_scale"] = {"on": True, "value": 1.0}
                    
        # Update video materials to point to new clip 1 and clip 3
        clip_map = {
            0: os.path.join(ASSETS, "clip_1_hema.mp4"),
            1: os.path.join(ASSETS, "clip_2_anshi.mp4"),
            2: os.path.join(ASSETS, "clip_3_hemanshi.mp4"),
            3: os.path.join(ASSETS, "clip_4_resolution.mp4"),
        }
        for i, v in enumerate(d["materials"]["videos"]):
            if i in clip_map:
                v["path"] = clip_map[i]
                v["media_path"] = clip_map[i]
                v["material_name"] = os.path.basename(clip_map[i])
                
        content_str = json.dumps(d, ensure_ascii=False, separators=(',', ':'))
        
        # Write to all draft files
        target_files = [
            os.path.join(p_dir, "draft_content.json"),
            os.path.join(p_dir, "draft_content.json.bak"),
            os.path.join(p_dir, "template-2.tmp"),
            os.path.join(tl_dir, "draft_content.json"),
            os.path.join(tl_dir, "draft_content.json.bak"),
            os.path.join(tl_dir, "template-2.tmp"),
            os.path.join(tl_dir, "template.tmp")
        ]
        for tf in target_files:
            if os.path.exists(os.path.dirname(tf)):
                with open(tf, "w", encoding="utf-8") as f:
                    f.write(content_str)
                    
        print(f"Updated CapCut project: {p_name}")

if __name__ == "__main__":
    update_projects()
    print("CapCut project updates complete!")
