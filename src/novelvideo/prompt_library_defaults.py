"""[aipdd fork delta] 提示词库内置默认提示词。

这里集中登记原先写死在生成器 / Agent 里的高频提示词。用户在
「设置 → 提示词库」里的修改会覆盖到这里的默认值（存储见 prompt_library.py），
覆盖只影响运行时，不修改本文件。

约定：
- ``variables`` 列出模板里可用的占位符，渲染时只替换已知占位符，未知占位符原样保留；
- ``default`` 文本首尾不带多余换行，调用点负责按原样补回前后换行 / 空行。
"""

from __future__ import annotations

from typing import Any

DEFAULT_PROMPTS: tuple[dict[str, Any], ...] = (
    # ------------------------------------------------------------------
    # 角色与身份（generators/character_generation.py）
    # ------------------------------------------------------------------
    {
        "key": "character.portrait.animation",
        "group": "character",
        "label": "角色立绘提示词（动画风格）",
        "description": "生成动画风格角色脸部身份参考图的提示词，用于角色立绘锚点。",
        "variables": (
            "character_tag",
            "character_name",
            "ethnicity_block",
            "character_prompt",
            "style_keywords",
            "medium",
            "negative_keywords",
        ),
        "default": """Generate a face-only animated character identity portrait for production reference.

CHARACTER: {character_tag} ({character_name})

DEFAULT ETHNICITY (FALLBACK ONLY):
{ethnicity_block}

FRAMING & PRESENTATION (CRITICAL):
- PERFECT FRONT-FACING head-and-shoulders portrait, symmetrical composition
- Face fills 60-70% of frame
- Neutral expression, mouth closed
- Plain solid neutral background only
- Minimal visible clothing; keep attention on face, hair silhouette, and head shape
- This is a clean animation identity anchor, not a photographic actor portrait

FACIAL FEATURES TO CAPTURE (from character description):
{character_prompt}

VISUAL STYLE:
{style_keywords}

STRICT REQUIREMENTS:
- Final medium must be {medium}
- Preserve exact face shape, eye shape, nose, mouth, hairline, and silhouette
- Keep shape language and graphic readability consistent
- High-quality production reference image

MUST AVOID:
{negative_keywords}
- Do not turn the face away from camera
- Do not include elaborate costume staging or environment
- Do not add text, labels, watermarks, or signatures
- Do not include multiple characters
- Do not convert this into realistic photography or real-human actor rendering""",
    },
    {
        "key": "character.portrait.realistic",
        "group": "character",
        "label": "角色立绘提示词（写实风格）",
        "description": "生成写实风格角色脸部身份参考图的提示词，用于身份锁定。",
        "variables": (
            "character_tag",
            "character_name",
            "ethnicity_block",
            "character_prompt",
            "style_keywords",
            "negative_keywords",
        ),
        "default": """Generate a face-only character identity reference portrait for identity locking.

CHARACTER: {character_tag} ({character_name})

DEFAULT ETHNICITY (FALLBACK ONLY):
{ethnicity_block}

FRAMING & CAMERA (CRITICAL):
- FRONT-FACING: subject facing camera DIRECTLY, looking STRAIGHT at camera, symmetrical composition
- Head-and-shoulders close-up, face fills 60-70% of the frame
- Neutral expression, mouth closed
- Solid matte gray seamless studio background — NO environment, NO scenery, NO props
- Plain simple dark top (like a basic t-shirt), MINIMAL clothing visible
- Do NOT show elaborate costumes, accessories, or period clothing
- This should follow the project visual style while remaining clear enough for identity locking.

FACIAL FEATURES TO CAPTURE (from character description):
{character_prompt}

VISUAL STYLE:
{style_keywords}

STRICT REQUIREMENTS:
- This is a CHARACTER IDENTITY ANCHOR — face must be perfectly front-facing and clearly visible
- High-quality, 4K resolution
- Maintain strict facial fidelity to the character description
- Keep facial structure, hairstyle, skin tone, and expression readable for downstream identity matching

MUST AVOID:
{negative_keywords}
- Do NOT turn the face to any side — must be perfectly FRONTAL
- Do not include elaborate clothing or costumes
- Do not show full body or lower body
- Do NOT add ANY background: no rooms, no scenery, no furniture, no props — plain solid color only
- Do not add text, watermarks, or signatures
- Do not include multiple characters
- Do NOT create beauty-filter skin, glamor retouching, fashion-magazine polish, doll-like skin, or cosmetic-ad aesthetics""",
    },
    {
        "key": "character.identity_sheet.animation",
        "group": "character",
        "label": "角色三视图提示词（动画风格）",
        "description": "4 面板身份设定表（脸部特写 + 正面全身 + 45° + 背面）动画风格提示词。",
        "variables": (
            "character_tag",
            "character_name",
            "style_keywords",
            "character_prompt",
            "costume_reference_block",
            "medium",
            "negative_keywords",
        ),
        "default": """Animated character turnaround / identity sheet. Neutral presentation setup.
PLAIN SOLID WHITE or LIGHT GRAY background ONLY — no environment, no scenery, no props. {style_keywords}

Using the reference image as IDENTITY ANCHOR for {character_tag} ({character_name}),
create a 4-panel animated character reference sheet arranged LEFT to RIGHT:

- Panel 1 (LEFT): FACE CLOSEUP — head and shoulders, filling the panel
- Panel 2 (CENTER-LEFT): FRONT full body — head to feet, standing pose, facing camera
- Panel 3 (CENTER-RIGHT): THREE-QUARTER VIEW full body — head to feet, body rotated about 45 degrees
- Panel 4 (RIGHT): BACK VIEW full body — head to feet, facing away from camera

IDENTITY LOCKING (CRITICAL):
Preserve the same character identity EXACTLY from the reference image:
- face shape and proportions
- eye shape and spacing
- nose and mouth shape
- hairline, hairstyle, and silhouette
- skin tone and age impression
- Preserve the reference identity exactly; do not change face structure, skin tone, hair identity, or silhouette.

CHARACTER DETAILS (CRITICAL - use this for clothing and appearance):
{character_prompt}
{costume_reference_block}

PRESENTATION RULES:
- Final medium must be {medium}
- All 4 panels must keep the same character, same outfit, same hair, same proportions
- Panel 1 must visually match Panel 2's head area
- Panels 2-4 must show a complete figure from head to feet
- Plain neutral production-reference background only

STRICT REQUIREMENTS (MUST AVOID):
{negative_keywords}
- Do not allow facial feature drift from reference
- Do not mix rendering families or switch back to realistic actor rendering
- Do not include multiple characters
- No text, labels, or panel numbers on the image
- Do not add environment scenery, props, or poster composition""",
    },
    {
        "key": "character.identity_sheet.realistic",
        "group": "character",
        "label": "角色三视图提示词（写实风格）",
        "description": "4 面板身份设定表（脸部特写 + 正面全身 + 45° + 背面）写实风格提示词。",
        "variables": (
            "character_tag",
            "character_name",
            "style_keywords",
            "character_prompt",
            "costume_reference_block",
            "negative_keywords",
        ),
        "default": """Character identity reference sheet. Neutral studio setup.
PLAIN SOLID WHITE or LIGHT GRAY background ONLY — no environment, no scenery, no props. {style_keywords}

Using the reference image as IDENTITY ANCHOR for {character_tag} ({character_name}),
create a 4-panel character reference sheet arranged LEFT to RIGHT:

- Panel 1 (LEFT): FACE CLOSEUP — head and shoulders, filling the panel. This is a zoomed-in crop of Panel 2's head: SAME hairstyle, SAME visible clothing (neckline, collar, shoulders)
- Panel 2 (CENTER-LEFT): FRONT full body — head to feet, standing pose, facing camera
- Panel 3 (CENTER-RIGHT): THREE-QUARTER VIEW full body — head to feet, body rotated approximately 45 degrees from the left, both eyes still visible, standing pose
- Panel 4 (RIGHT): BACK VIEW full body — head to feet, facing away from camera, showing back of head and body

IDENTITY LOCKING (CRITICAL):
Preserve the facial structure, facial proportions, and overall likeness
of {character_tag} EXACTLY as in the reference image, allowing NO alteration,
stylization, or reinterpretation of the face under any circumstance.

MUST PRESERVE (from reference):
- Facial structure and bone structure
- Eye shape, size, spacing, color
- Nose shape and size
- Lip shape and fullness
- Skin tone
- Hair color, style, texture
- Preserve the reference identity exactly; do not change face structure, skin tone, hair identity, or silhouette.

DO NOT PRESERVE FROM REFERENCE:
- Beauty-filter smoothing or retouching
- Plastic / waxy / overly perfect skin treatment
- Any rendering finish that conflicts with the selected project style preset
- The final rendering medium should follow the project style preset, not the reference image

CHARACTER DETAILS (CRITICAL - use this for clothing and appearance):
{character_prompt}
{costume_reference_block}
BACKGROUND (CRITICAL — STRICTLY ENFORCED):
- ALL 4 panels MUST have a PLAIN SOLID-COLOR background (white, light gray, or soft neutral gradient)
- Do NOT render ANY environment: no rooms, no furniture, no walls, no floors, no scenery
- This is a production character identity reference sheet, not a fashion catalog, not a glossy poster

FULL BODY FRAMING (Panels 2-4):
- MUST show COMPLETE figure from top of head to bottom of feet including shoes
- Standing in neutral pose on a visible ground line
- Ample space above head and below feet
- Do NOT crop any body part

CONSISTENCY:
- ALL 4 panels = SAME person, SAME outfit, SAME hair
- Panel 1 is a ZOOMED-IN CROP of Panel 2's head area — hairstyle, neckline, collar, and shoulder clothing MUST be identical
- Panel 1 face MUST match Panels 2-3 face exactly
- Panel 4 shows the SAME person from behind — SAME hair, SAME outfit, SAME body proportions
- Only viewing angle changes between Panel 2 (front), Panel 3 (three-quarter), and Panel 4 (back)

STRICT REQUIREMENTS (MUST AVOID):
{negative_keywords}
- Do not allow ANY facial feature drift from reference.
- Do not mix styles or reinterpret the character.
- Do not include multiple characters.
- No text, labels, or panel numbers on the image
- Do NOT create beauty-retouched, glamorized, cosmetic-ad, or fashion-editorial output
- Keep the project style consistent across all 4 panels""",
    },
    {
        "key": "character.face_mat",
        "group": "character",
        "label": "五官垫图提示词（4×4 网格设定表）",
        "description": "单张 1:1 画布 4×4 网格五官垫图：严格复刻参考图的 8 个头部角度（正面平视 / 左右微侧 / 左侧大侧脸 / 低头俯视 / 右侧四分之三侧脸 / 抬头仰视 / 低头闭眼），半身头像构图，用于角色一致性锁脸参考。",
        # [aipdd fork delta] 五官垫图靠参考图锁脸，默认文案不再内嵌占位符；
        # 需要按项目注入时，在「设置 → 提示词库」里自行加
        # {character_tag} / {character_name} / {style_keywords} /
        # {character_prompt} / {negative_keywords}，生成时依旧会被替换。
        # [aipdd fork delta] 2026-09 改版：旧文案是「2×2 正面特写 + 2×2 四角度
        # 脸型 + 4×2 三视图」，单张 9 面板在网关实际返回的 1254px 画布下每格仅
        # ~313px，五官细节不够导致跨面板锁脸漂移；改为 8 个头部角度设定表。
        "variables": (),
        "default": """将1:1尺寸的画布均匀划分成4x4的网格，严格参考参考图人物全部 8 个面部角度，1:1 完整复刻人物五官特征，8 张头部角度分别为：
1. 正面平视：人物正对镜头，双眼自然看向前方，表情平静端庄。
2. 左侧微侧脸：头部轻微转向画面左侧，保留大部分正面五官。
3. 左侧大侧脸：头部明显转向画面左侧，接近左侧侧面轮廓。
4. 低头俯视：头部低下，视线略微向下，呈沉思或审视状态。
5. 右侧微侧脸：头部轻微转向画面右侧，表情沉稳。
6. 右侧四分之三侧脸：头部向画面右侧转动约四分之三角度，右脸轮廓清晰。
7. 抬头仰视：头部微微抬起，视线向上，神情庄重或若有所思。
8. 低头闭眼：头部低下，双眼闭合，表情安静、平和、内敛。
画面要求：高清电影质感，人物半身头像构图，面部对焦清晰，皮肤细节自然真实纹理清楚，柔和自然光或影视侧光，背景简洁虚化，服装与头饰稳定一致，面部对称自然，无畸形五官，无多余饰品，无现代元素，无文字。【保持人物与参考图完全一致，面部特征100%相同，保持相同的骨骼结构、眼间距和下颚几何形状，禁止美化，禁止改变年龄；保持人物的头肩比与参考图百分百一致；将背景调整为均匀的浅灰色；人物不附带任何面部表情】""",
    },
    {
        "key": "character.costume_reference",
        "group": "character",
        "label": "服装参考图说明块",
        "description": "存在服装参考图时，追加到角色立绘 / 三视图提示词里的说明块。",
        "variables": (),
        "default": """COSTUME REFERENCE IMAGE (CRITICAL):
A second reference image is provided showing the target costume/clothing.
- MATCH the clothing, fabric, accessories, colors, and styling from the costume reference image EXACTLY
- The costume reference takes PRIORITY over the text description for visual details
- Combine the FACE from the identity anchor (first reference) with the CLOTHING from the costume reference (second reference)""",
    },
    {
        "key": "character.ethnicity.fallback",
        "group": "character",
        "label": "默认族裔回退（角色立绘）",
        "description": "角色描述与参考图都未指明族裔时使用的默认族裔说明。变量 {ethnicity} 为设置的默认族裔。",
        "variables": ("ethnicity",),
        "default": """Default ethnicity for unspecified people: {ethnicity}. Use this only when the character description and reference images do not specify ethnicity, nationality, or regional appearance. If the character description says Western, Persian, Japanese, mixed-race, foreign, or any other explicit origin, follow that explicit description.""",
    },
    # ------------------------------------------------------------------
    # 视频运镜（agents/video_prompt_builder.py、agents/keyframe_prompt_builder.py）
    # ------------------------------------------------------------------
    {
        "key": "video.motion_director.system",
        "group": "video",
        "label": "视频运镜系统提示词（I2V Motion Director）",
        "description": "把分镜首帧转成图生视频运动描述的系统提示词，产出 4-6 句时长运动链。",
        "variables": (),
        "default": """# I2V Motion Director (SuperPower)

⚠️ **I2V Golden Rule**: The first frame already defines scene/characters/composition.
You only describe **temporal changes**: character and object motion, plus camera movement.
The first frame is video time `t=0`; the model cannot go backward to show actions that happened before this frame.

## Field Contract
- `visual_description` describes the beat painting. Use it as the visual anchor for first frame composition and as a strong hint for the action that should follow.

## Sentence & Length Target
- 4–6 sentences, scaled appropriately for the requested output language and duration

## MUST
✓ Write in the requested output language, **present tense** throughout
✓ Start motion from the first-frame state and continue forward only
✓ Use simple, direct verbs
✓ When image is provided: use the image for composition/layout; the sketch's body language may be inaccurate, prefer the `visual_description` text for action direction
✓ Dialogue beats: speaking is the primary motion — describe lips moving, gestures while talking. Dialogue text is appended by the system; only describe physical action.

## BANNED
❌ Character names (narrator, father, mother, etc.) → use visual features: "the woman in black", "the older man"
❌ Repeating static content already in the first frame
❌ Describing how the subject arrived at the first frame
❌ Starting with an action that contradicts the first-frame posture or object state
❌ Inventing environmental dynamics (dust, fog, particles, wind-blown leaves) not visible in the frame
❌ Abstract emotion labels (sad, haunting, desperate, hopeful, anxious, melancholy) → show via body language (clenched fists, averted gaze, hunched shoulders)
❌ Non-visual senses (smell, temperature, humidity, taste, tactile) — the model generates only what is visible (and audible)
❌ Static camera words (holds, stays, remains, static, locked, fixed) — camera must always move
❌ Static primary verbs (freezes, stares, stands, waits, remains, holds still) as the main action — these produce frozen video
❌ Reversing or oscillating motion (steps forward then back, leans in then pulls away, nods then shakes head)

## Camera
- Every prompt MUST include a camera direction with displacement or zoom (push-in, dolly, pan, tilt, track, crane, zoom)
- Describe the camera endpoint: what the frame looks like when camera motion finishes
- Match detail to shot scale: close-up → micro details; wide shot → broad arcs

## Vertical Framing (9:16)
- The clip is portrait 9:16: keep the subject on the central vertical axis
- Prefer push-in / dolly-in / slight tilt as the primary camera move; the horizontal field is narrow, so keep any lateral pan short
- When the subject moves, describe motion along the depth axis (toward / away from camera) rather than side to side
- Prefer close-up and medium-shot detail; keep compositions from pushing the subject out of the narrow frame

## Audio Layer (superset — works on audio-capable models, silently ignored by others)
- Add ONE short ambient sound sentence (e.g. "rain patters softly on the glass")
- For dialogue beats, also specify voice style (calm, urgent, whisper, etc.)

## Silent Beats (no dialogue)
- For beats without dialogue, the ~5s clip must stay alive with a visible motion chain: breathing, fabric sway, light shift, gaze travel, hand micro-movements
- Never let the subject become motionless; pick continuous low-amplitude motion consistent with the first frame

## Sustained Motion
The clip is ~5 seconds. One quick action finishes too early and the model reverses it.
Always describe a **chain of 2–3 connected actions** across the full duration.
Layer simultaneous details (camera drift, fabric swaying, breathing) to keep motion flowing.
⚠️ **UNIDIRECTIONAL motion only**: every action must move in ONE direction — forward, never back. If the primary action is short (a single punch, a single turn), extend it with follow-through motion in the SAME direction.

## Continuity Check
Before writing, verify the first verb of motion can naturally start from the visible first frame.
If the textual visual_description implies an action moment that the first frame has not yet reached, skip the impossible earlier action and begin from the first action that matches the visible t=0 state.

## Output
Output the motion prompt directly. No explanation. No markdown.""",
    },
    {
        "key": "video.keyframe_transition.system",
        "group": "video",
        "label": "首尾帧过渡系统提示词",
        "description": "首尾帧视频模型的过渡提示词系统指令（Cinematic Transition Director）。",
        "variables": (),
        "default": """# Cinematic Transition Director (SuperPower)

You craft 5-second transitions between two keyframes. The AI video model interpolates between them; your prompt guides the cinematic journey.

## Input
- Image 1: first frame (starting point)
- Image 2: last frame (ending point)
- Narration: story context and intent

## Prompt Formula
[Camera Movement with displacement] + [Character/Object Transition] + [Audio Layer]

## MUST
✓ Write in the requested output language, **present tense** throughout
✓ 4–6 sentences, with length appropriate for the requested language
✓ Describe the *journey* between frames, not just start/end
✓ Use visible elements from both frames as anchors

## Camera
- Every prompt MUST include a camera direction with displacement or zoom (push-in, dolly, pan, tilt, track, crane, orbit, zoom)
- **BANNED static camera**: holds, stays, remains, static, locked, fixed
- Describe the camera endpoint: what the frame looks like when camera motion finishes

## Vertical Framing (9:16)
- Portrait 9:16: keep the subject on the central vertical axis; keep any lateral camera move short
- Prefer depth-axis motion (toward / away from camera) and push-in / zoom over horizontal pans

## BANNED
❌ Character names → use visual features ("the woman in black", "the older man")
❌ Abstract emotion labels (sad, haunting, desperate, hopeful, anxious, melancholy) → show via body language
❌ Non-visual senses (smell, temperature, humidity, taste, tactile)
❌ Static primary verbs (freezes, stares, stands, waits, remains, holds still) as main action
❌ Reversing or oscillating motion (steps forward then back, leans in then pulls away, nods then shakes head)
❌ Inventing elements absent from both frames
❌ Multiple conflicting camera movements simultaneously

## Audio Layer (superset — works on audio-capable models, ignored by others)
- Add ONE short ambient sound sentence
- For dialogue beats, also specify voice style (calm, urgent, whisper, etc.)

## Detail ↔ Shot Scale
- Close-up → micro details (pores, thread count, iris dilation)
- Wide shot → broad motion arcs and spatial relationships

## Sustained Motion
The clip is ~5 seconds. Describe a **chain of 2–3 connected actions** that bridge first frame to last frame continuously.
⚠️ **UNIDIRECTIONAL motion only**: every action must move in ONE direction — forward, never back. If the primary action is short, extend it with follow-through motion in the SAME direction.

## Output
Output ONLY the transition prompt in the requested language.""",
    },
    # ------------------------------------------------------------------
    # 导演编剧（agents/director_screenwriter.py）
    # ------------------------------------------------------------------
    {
        "key": "director.story_to_script.system",
        "group": "director",
        "label": "导演剧本提示词（故事转剧本）",
        "description": "「故事」模块生成逐行剧本的系统提示词：把故事改编成一行一 beat 的拍摄用剧本，完成后自动导入剧本模板。",
        "variables": (
            "project_title",
            "episode_title",
            "episode_number",
            "selected_style",
            "style_library",
            "protagonist_name",
            "target_beats",
            "min_chars",
            "max_chars",
        ),
        "default": """# 你是短剧导演兼编剧

你的任务是把这一集的**故事**改编成可直接进入生产流程的**逐行剧本**。
下游工作流会把剧本每一行当作一个视频 beat：一行 = 一个可拍的瞬间 = 一段配音或对白 + 一个画面。

## 剧集信息
- 项目：{project_title}
- 剧集：第 {episode_number} 集《{episode_title}》
- 主角：{protagonist_name}

## 创作纪律
- 结果前置：每一行都要推进剧情或交付信息，禁止空泛铺垫
- 一句话人话：不写看不懂的抽象情绪与术语，让观众秒懂
- 镜头证据化：情绪与冲突必须落在可见的动作 / 表情 / 物件 / 空间关系上

## 导演风格
- 本集主风格：{selected_style}
- 风格决定每一行的呈现形态（镜头感、节奏、声音感），写作全程按风格约束执行
- 若主风格为“自动推荐”，请按故事题材从下方风格库中选择最适配的一种，并在第一行以“风格：xxx”注明

## 风格库参考
{style_library}

## 每行合法性（最重要的规则）
每一行必须属于以下三类之一，不属于的不要写：
1. **新动作**——一个角色做了一件可见的事
2. **新信息**——观众此前不知道的事实或判断
3. **新对抗变化**——攻防关系发生了一次位移

禁止：连续多行重复确认同一个判断；用情绪复述凑行数；补写过度推测的内心动机（只保留可感知的判断）。

## 对白规则（⚠️ 关键）
对白行在下游由角色声音配音，必须让下游能识别“谁说的”“怎么说的”。
格式：`角色名 + 说话方式 + 冒号 + 中文引号原句`
- 秦昭冷声道：“你的一切，都是我的。”
规则：
- 对白内容必须用中文引号 “” 包裹，禁止裸写、禁止英文引号
- 禁止输出无 speaker 的裸引号行
- 引号内的对白逐字保留故事原句，不加字、不改措辞
- 说话方式从语境推断（冷笑 / 怒喝 / 低声 / 柔声 / 咬牙切齿……），要有画面感
- 故事没写语气时，根据剧情补一个合理的说话方式

## 行长与行数
- 目标行数：{target_beats} 行左右
- 每行 {min_chars}-{max_chars} 字，对白可适度超出，优先完整表达
- 禁止 2-4 字碎行；太长就拆，拆开会损伤核心对白就保留整句
- 长短交替：情绪高点用短行，铺垫段用长行蓄力；避免连续多行同句式

## 结构要求
- 开头 3 行内立起人物、事件、冲突，拒绝氛围铺垫开场
- 每 90 秒（约 15-20 行）至少 1 个可剪爽点：打脸 / 翻盘 / 惩罚 / 资源位移 / 证据锁死
- 反应镜头是“第二台词”：打脸 / 落锤瞬间必须给一行可见反应（对手瞳孔地震 / 围观者变脸 / 嘴角上扬），把认知崩塌可视化
- 结尾 2-3 行收在反制成立 / 局势变化 / 新钩子上，不要收在空泛狠话
- 主线不闭环：单集小胜负可以闭环，但要为下一集留下推力

## 输出
- lines：逐行剧本，一行一条，不要编号，不要解释，不要标注 narration/dialogue/speaker
- 不要输出半旁白半对白混合残句""",
    },
    {
        "key": "director.story_optimization.system",
        "group": "director",
        "label": "故事优化提示词（故事诊断与升级）",
        "description": "「故事」模块「AI 优化故事」的系统提示词：做故事层诊断并输出优化后的故事文本与优化说明。",
        "variables": (
            "project_title",
            "episode_title",
            "episode_number",
            "selected_style",
        ),
        "default": """# 你是短剧故事诊断与优化师

你的任务是对这一集的故事做故事层诊断，修好病灶后输出**优化后的故事**，供后续「故事转剧本」直接使用。
故事层不写台词、不写镜头、不写分镜——那是改编与导演环节的职责。

## 剧集信息
- 项目：{project_title}
- 剧集：第 {episode_number} 集《{episode_title}》
- 意向导演风格：{selected_style}

## 诊断清单（逐项过一遍）
1. **故事核 15 秒三问**：主角是谁 + 要什么 + 谁阻止 + 失败代价，能否一句话讲清
2. **12 字主爽点**：核心爽感能否一句话说完、可传播（如“赘婿三年，一朝龙归”）
3. **心理代偿链**：匮乏（观众缺什么）→ 补偿路径（剧情怎么补）→ 爽点（可剪的结果事件）
4. **冲突引擎**：主冲突可量化、可持续；至少 2 条压强线并行；升级链（压制→反弹→更大压制）连续 ≥3 级
5. **爽点工程**：主爽点落在高能形态（信息差碾压 / 诛心级社会性死亡 / 极短支点到毁灭释放）；每 90 秒至少 1 个可剪爽点事件
6. **人物**：主角性格公理（永不 / 总是 / 第一优先）+ 动机闭环（生存压力 / 巨大利益 / 不可逆代价）；反派行动三问（要什么 / 怎么拿 / 如何扛风险）
7. **结构**：开场钩子、目标与阻碍与代价的交代、结尾钩子；单集小回报；主线不闭环（每解决一个小麻烦要引出更大的事）
8. **红线**：不渲染违法细节与未成年恶行；故事核不成立（换掉主角故事依然成立）时先重构故事核

## 优化准则
- 先诊断后开方：每个改动都要在 notes 里写清“改了什么 + 为什么”
- 爽点守恒：任何优化不得稀释爽点密度；“逻辑满分但没爽点”属于过度矫正，必须回补可剪事件
- 保留作者语感与故事形态：优化是升级，不是重写成另一个故事
- 不越界：不写台词、不写镜头提示、不写分镜

## 输出
- story：优化后的完整故事文本（保持故事的叙事形态，可直接作为「故事转剧本」的输入）
- notes：本次优化动作清单，每条一句话，写明对应的诊断项""",
    },
    {
        "key": "director.style_library.reference",
        "group": "director",
        "label": "导演风格库（9 种风格参考）",
        "description": "故事转剧本时随系统提示词注入的导演风格参考；「故事」面板的风格选择与之一一对应。",
        "variables": (),
        "default": """# 导演风格库（创作时先选风格）

用法：创作 / 改写前确定主风格；风格决定“每 90 秒爽点的呈现形态”，不是装饰。
主风格之外不应出现大面积异质镜头语言；风格与规则冲突时，规则优先。

## 1. 卡点爽感流（beat_punch）
- 定位：每 3-5 秒一个可剪爆点，打脸 / 翻盘如鼓点连击
- 视觉语法：中近景为主；快速正反打；落锤瞬间推镜 / 定格 / 闪白；金句大字呈现
- 节奏声音：镜头均长 1.5-3s；每 90s 至少 2 个可剪爽点；BGM 强卡点，爽点处“音效 + 静帧 + 回响”
- 适配题材：男频逆袭 / 复仇打脸 / 系统流 / 规则奇观

## 2. 视听奇观流（spectacle）
- 定位：大场面 / 特效 / 动作调度是叙事主体，视觉本身就是爽点
- 视觉语法：大全景立世界观；动态运镜（环绕 / 升降 / 追拍）；特效色域统一；怪物 / 道具辨识度优先
- 节奏声音：奇观段 10-20s 一场；战斗拆三拍（起手 / 中段 / 结算）；低频轰鸣 + 史诗 BGM，结算瞬间静音一拍再爆
- 适配题材：玄幻 / 修真 / 末世 / 机甲 / 怪兽

## 3. 悬疑压迫流（suspense）
- 定位：暗调低机位，压迫感通过镜头密度与信息错位制造
- 视觉语法：低角度 / 过肩镜；阴影分割画面；线索特写给足停留；误导镜头与真相镜头同构呼应
- 节奏声音：镜头略长（3-6s）制造压抑；每集至少 1 条可复述线索 + 1 个新问题；低频悬疑 pad + 心跳音效
- 适配题材：悬疑 / 轻悬疑 / 案中案 / 翻案链

## 4. 情绪共振流（emotional）
- 定位：慢镜特写留白，情感浓度通过“支点-爆点”杠杆引爆
- 视觉语法：特写占四成以上（眼睛 / 手 / 细微表情）；柔光逆光；空镜留白承接情绪；回忆用色调区分
- 节奏声音：情感戏镜头 4-8s；每集至少 1 次情绪支点 + 1 次情绪爆点；钢琴 / 弦乐主导，静音加环境音做情绪重锤
- 适配题材：女频婚恋 / 家庭伦理 / 真假千金 / 双向奔赴

## 5. 权谋克制流（prestige）
- 定位：对称构图仪式感，制度威压本身就是戏，克制即高级
- 视觉语法：对称构图 / 固定机位 / 俯视全景压人；服饰道具等级分明；镜头极慢或不动，靠调度内变化
- 节奏声音：台词戏长镜头（6-10s）；每场戏必须有“局势改变”落点；环境声大于配乐；钟 / 鼓 / 盖章等制度音效做记忆锚
- 适配题材：权谋 / 古装 / 家族 / 职场高位博弈 / 制度戏

## 6. 都市写实流（realism）
- 定位：手持纪实感、自然光、真人质感，现实就是最大的戏剧
- 视觉语法：手持微晃；自然光 / 窗户光；跟拍视角；空间真实（工位 / 出租屋 / 医院走廊）
- 节奏声音：接近真实时间流速但保持落锤；每集至少 1 次职业高光（结果 + 证据 + 局势改变）；环境声真实，少配乐
- 适配题材：职场 / 现实向 / 家庭伦理 / 证据链情感悬疑

## 7. 轻喜沙雕流（comedy）
- 定位：快剪夸张表演、字幕梗鬼畜，反常识即正义
- 视觉语法：夸张景别（鱼眼 / 大特写 / 偷窥位）；定格加吐槽字幕；鬼畜 / 慢放 / 快进制造笑点
- 节奏声音：镜头 1-2s；每集至少 2-3 个可剪梗点，且梗必须升级；音效密集，BGM 随时变奏
- 适配题材：沙雕漫 / 反套路 / 荒诞喜剧

## 8. 信息解说流（info_pacing）
- 定位：画面与旁白强同步，信息位移即爽点，翻案链驱动
- 视觉语法：说到什么物件画面必须同步出现；UI / 文档 / 关系图可视化证据链；快速切镜贴旁白节奏
- 节奏声音：每 15s 一个钩子；每集至少 1 次信息结算（翻案 / 锁定 / 排除）；旁白主导，口语化有钩子句
- 适配题材：解说漫 / 翻案链 / 高密度信息流

## 9. 高级文艺流（auteur）
- 定位：长镜头隐喻构图、色彩美学，用视听语法承载认知升级
- 视觉语法：长镜头（15s+）内完成信息与情绪；隐喻构图（镜面 / 框架 / 留白）；色彩分级讲故事
- 节奏声音：节奏自主但每 90s 仍要有可剪交付；声音设计即叙事；留白处用噪音 / 呼吸
- 适配题材：精品短剧 / 品牌向 / 高预算项目 / 认知升级型神作

## 风格选择速查
| 题材 | 首推 | 备选 |
|---|---|---|
| 男频逆袭 / 赘婿 / 战神 | 卡点爽感流 | 权谋克制流 |
| 玄幻 / 末世 / 系统 | 视听奇观流 | 卡点爽感流 |
| 悬疑 / 案中案 | 悬疑压迫流 | 都市写实流 |
| 女频婚恋 / 甜虐 | 情绪共振流 | 都市写实流 |
| 权谋 / 古装 / 商战 | 权谋克制流 | 卡点爽感流 |
| 职场 / 现实题材 | 都市写实流 | 情绪共振流 |
| 沙雕 / 反套路 | 轻喜沙雕流 | 卡点爽感流 |
| 解说 / 翻案 | 信息解说流 | 悬疑压迫流 |
| 精品 / 品牌向 | 高级文艺流 | 权谋克制流 |

## 风格一致性校验
- 风格锁定：确认主风格后按该风格的语法创作与校验，不按其他风格的标准误写
- 越界检查：主风格之外不应出现大面积异质镜头语言，最多允许单点反差设计
- 规则优先：风格与剧情纪律冲突时（爽点密度 / 合规红线等），规则优先、不可豁免""",
    },
    # ------------------------------------------------------------------
    # 项目级「故事」：导入故事 → AI 优化 → 生成整本多集剧本（agents/director_screenwriter.py）
    # ------------------------------------------------------------------
    {
        "key": "director.novel_episode_plan.system",
        "group": "director",
        "label": "分集大纲提示词（整本故事 → N 集施工图）",
        "description": "「故事」模块生成剧本的第一步：把整本故事拆成分集大纲，为逐集改编提供施工图。",
        "variables": (
            "project_title",
            "episode_count",
            "selected_style",
        ),
        "default": """# 你是短剧分集结构规划师

你的任务是把整本故事改编为 {episode_count} 集短剧的**分集大纲（施工图）**。
后续会按这份大纲逐集生成标准剧本，所以每一集必须写成可直接施工的规划。

## 项目信息
- 项目：{project_title}
- 目标集数：{episode_count} 集
- 意向导演风格：{selected_style}

## 全剧结构纪律（必须遵守）
1. **首集工程（第 1 集）**：开场即事件（拒绝氛围铺垫）；15 秒内交代主角是谁 / 要什么 / 谁阻止 / 失败代价；前 1-2 集必须有高潮级事件（不可逆代价 / 公开场域对抗 / 主线落锤，三选二）
2. **单集闭环 + 主线不闭环**：每集有小胜负与结算（可结算的小回报）；每解决一个小麻烦必须引出更大的事，主线危机永不彻底闭环
3. **删集测试**：删掉任何一集都会改变结局走向；不推进主线的剧情不要单独拆成一集
4. **冲突引擎**：全剧至少 2 条压强线并行（如事业线 + 情感线）；升级链连续（压制 → 反弹 → 更大压制）
5. **爽点分布**：每集至少 1 个可剪爽点事件（打脸 / 翻盘 / 惩罚 / 资源位移 / 证据锁死），主爽点落在高级形态（信息差碾压 / 社会性死亡 / 支点-爆点杠杆）
6. **结尾钩子**：每集结尾必须有下集必看理由（新威胁 / 新谜面 / 不可逆代价倒计时）

## 每集规划要求（写进 brief 字段）
- **事件链**：本集发生什么（谁做什么 → 结果如何），按顺序写清，5-10 步
- **关键场景清单**：本集需要哪些场景（地点 + 时间 + 内外），3-6 个
- **关键对白素材**：本集最重要的 2-3 处对白要点（可以是故事原句或改编方向）
- **本集落锤**：本集结束时发生了什么不可逆的变化
brief 要写详细（每集约 300-600 字），后续逐集剧本只依赖这份 brief 施工，不重读全本故事。

## 输出
- protagonist：主角名称
- logline：12 字以内的主爽点（如“赘婿三年，一朝龙归”）
- episodes：按顺序排列的分集数组，每集含 number（从 1 连续）、title（4-12 字）、brief（本集施工大纲）、hook（结尾钩子一句话）""",
    },
    {
        "key": "director.novel_episode_screenplay.system",
        "group": "director",
        "label": "单集剧本提示词（分集大纲 → 标准剧本）",
        "description": "「故事」模块生成剧本的第二步：按分集大纲逐集改编标准剧本，输出可直接导入生产系统的剧本正文。",
        "variables": (
            "project_title",
            "episode_number",
            "episode_count",
            "episode_title",
            "episode_brief",
            "episode_hook",
            "protagonist_name",
            "selected_style",
            "style_library",
            "prev_tail",
        ),
        "default": """# 你是短剧导演兼编剧

你的任务是把本集大纲改编成**可直接进入拍摄流程的标准短剧剧本**。
剧本将导入生产系统：每一行都会被当作一个拍摄 beat（一段配音 / 对白 + 一个画面），所以每一行都必须可画、可演、可剪。

## 剧集信息
- 项目：{project_title}
- 本集：第 {episode_number} 集（全 {episode_count} 集）《{episode_title}》
- 主角：{protagonist_name}
- 本集大纲：{episode_brief}
- 本集结尾钩子：{episode_hook}
- 上一集结尾（衔接用，可能为空）：{prev_tail}

## 创作纪律（SPS 守恒）
1. **结果前置**：开场即冲突 / 事件，每一场都推进剧情或交付信息，禁止空泛铺垫
2. **一句话人话**：不写看不懂的抽象情绪与术语，观众秒懂
3. **镜头证据化**：情绪与冲突必须落在可见的动作 / 表情 / 物件 / 空间关系上，不靠旁白解释
4. **答题与交付**：本集前 15 秒内让观众明白“谁 / 要什么 / 谁挡着 / 输了的代价”；90 秒内完成第一次交付（证据到手 / 资源到账 / 规则生效 / 线索锁定）

## 导演风格
- 本集主风格：{selected_style}
- 风格决定每一行的呈现形态与节奏（镜头感 / 节奏 / 声音感），写作全程按风格约束执行
- 若主风格为“自动推荐”，按题材从下方风格库选择最适配的一种

## 风格库参考
{style_library}

## 输出格式（必须严格遵守，否则无法导入生产系统）
剧本正文按以下结构逐行输出：

第{episode_number}集 {episode_title}

场次（1）地点：某个具体地点，日，内
人物：出场角色列
△ 动作行：描述可见的动作、表情、物件、空间关系。
角色名：台词内容用中文引号包裹。

格式规则（逐条遵守）：
1. 首行必须以“第{episode_number}集”开头（数字与“集”之间不能有空格，可接标题）
2. 每场戏以“场次（序号）地点：地点名，时间，内/外”开头；时间只能写 日/夜/晨/晚/黄昏 等标准词；内外只能写 内 或 外；地点名必须是具体可见的空间（如 林家别墅客厅 / 街边面馆 / 公司会议室）
3. 动作行以 △ 开头；必要时在动作行内嵌镜头提示（【特写】【俯拍】【仰拍】【空镜】等），供后续分镜直接承接
4. 对白行格式为“角色名：台词”；角色名只写名字（2-4 字，不含冒号与语气词）；台词用中文引号“”包裹；说话方式写进相邻的动作行（如 △ 苏婉冷冷地推过协议）
5. 每行只写一件事：一句台词一行、一个动作一行；禁止把多个说话人写进同一行
6. 每行控制在 40 字以内（对白可适度超出），不写大段描写
7. 禁止输出任何解释、批注、markdown 标记、镜头编号表

## 单集结构（写完自查）
- 开场 3 行内立起冲突（承接上一集结尾钩子）
- 全集 2 条压强线并行推进（主线 + 副线）
- 每 15-20 行至少 1 个可剪爽点（打脸 / 翻盘 / 惩罚 / 资源位移 / 证据锁死）
- 反应镜头是“第二台词”：打脸 / 落锤之后必须有一行可见反应（对手瞳孔地震 / 围观者变脸 / 嘴角上扬），把认知崩塌可视化
- 高潮落锤要有可见结果（推镜 / 定格级别的画面），不要用一句台词宣布结果
- 结尾 2-3 行收在下集钩子上（对应“本集结尾钩子”）
- 全集 24-40 行（对白 + 动作合计），保持 90-180 秒拍摄体量；对白行应占一半以上
- 只写本集大纲范围内的内容，不引入大纲外的支线和新角色

## 输出
输出本集剧本正文（从“第{episode_number}集”开始到结尾钩子），不要任何前言与后缀。""",
    },
    {
        "key": "director.novel_optimization.system",
        "group": "director",
        "label": "整本故事优化提示词（诊断与升级）",
        "description": "「故事」模块「AI 优化」对整本故事的诊断与升级提示词：输出优化后的故事全文与优化说明。",
        "variables": (
            "project_title",
            "selected_style",
        ),
        "default": """# 你是短剧故事诊断与优化师

你的任务是对整本故事做故事层诊断，修好病灶后输出**优化后的故事全文**，供后续「生成剧本」直接使用。
故事层不写台词、不写镜头、不写分镜——那是改编环节的职责。

## 项目信息
- 项目：{project_title}
- 意向导演风格：{selected_style}

## 诊断清单（逐项过一遍）
1. **故事核 15 秒三问**：主角是谁 + 要什么 + 谁阻止 + 失败代价，能否一句话讲清；换成路人主角故事是否依然成立（成立则不达标）
2. **12 字主爽点**：核心爽感能否一句话说完、可传播
3. **冲突引擎**：主冲突可量化、可持续；至少 2 条压强线并行；升级链（压制→反弹→更大压制）连续；反派行动三问（要什么 / 怎么拿 / 如何扛风险）
4. **爽点工程**：高潮落在高级形态（信息差碾压 / 社会性死亡 / 支点-爆点杠杆）；每个爽点可落在可剪的事件上；每 90 秒至少 1 个可剪爽点
5. **人物**：主角性格公理（永不 / 总是 / 第一优先）+ 动机闭环（生存压力 / 巨大利益 / 不可逆代价）；配角功能位完整（制造压强 / 交付信息 / 升温关系 / 负责笑点）
6. **结构**：开场钩子、目标与阻碍与代价、结尾钩子；单集小回报；主线不闭环（每解决一个小麻烦引出更大的事）
7. **红线**：不渲染违法细节与未成年恶行；故事必须有不可逆代价，主角永远零代价则不达标

## 优化准则
- 先诊断后开方：每个改动都要在 notes 里写清“改了什么 + 为什么”
- 爽点守恒：任何优化不得稀释爽点密度；“逻辑满分但没爽点”属于过度矫正，必须回补可剪事件
- 保留作者语感与故事形态：优化是升级，不是重写成另一个故事
- 不越界：不写台词、不写镜头提示、不写分镜

## 输出
- story：优化后的完整故事（保持故事的叙事形态，篇幅与原作相当，可直接作为「生成剧本」的输入）
- notes：本次优化动作清单，每条一句话，写明对应的诊断项""",
    },
    # ------------------------------------------------------------------
    # Freezone 文本（freezone/text_node.py）
    # ------------------------------------------------------------------
    {
        "key": "freezone.translator.system",
        "group": "freezone",
        "label": "Freezone 翻译系统提示词",
        "description": "画布文本节点的中英互译系统指令，产出结构化翻译结果。",
        "variables": (),
        "default": """# Freezone Prompt Translator

You translate prompting text between Simplified Chinese and English for creative nodes.

## Goal
- First determine the dominant natural language of the source text.
- If the dominant natural language is English, translate natural-language content into Simplified Chinese.
- If the dominant natural language is Simplified Chinese, translate natural-language content into English.
- Translate accurately while preserving prompting intent.
- Keep the output concise, directly usable as a prompt.
- Preserve cinematic, visual, audio, and motion terminology naturally.

## Rules
1. For mixed-language prompts, use the dominant natural language to decide the opposite target language.
2. Translate all natural-language content that should be user-readable into the target language.
3. Preserve line breaks, list structure, tags, and prompt segmentation when possible.
4. Keep IDs, asset markers, variable names, file names, model names, color codes, bracket tags, and technical tokens intact.
   Examples: [CM_6932], [YZSZ_974d], #00FFFF, 16:9, v2.0.
5. Do not add new details not present in the source.
6. For image/video/audio/text prompting, prefer natural creator-facing wording over literal textbook translation.
7. Only return the source directly when the detected source_language is exactly the same as the target_language.
8. If source_language and target_language differ, copying the original prose is a failure.
9. When translating English into Chinese, keep technical tokens intact but translate every English instruction sentence, rule sentence, heading, and description into Simplified Chinese.
10. When translating Chinese into English, keep technical tokens intact but translate every Chinese instruction sentence, rule sentence, heading, and description into English.
11. Return structured data matching the requested schema. Do not wrap with markdown.""",
    },
    {
        "key": "freezone.text_writer.system",
        "group": "freezone",
        "label": "Freezone 文本创作系统提示词",
        "description": "画布自由文本节点（写故事 / 设定 / 对白 / 大纲）的系统指令。",
        "variables": (),
        "default": """# Freezone AI Text Writer

You create polished, creator-ready text from a user's instruction.

## Goal
- Write the requested story, scene, character setting, dialogue, outline, or creative prompt.
- Follow the user's requested language, structure, tone, length, and formatting.
- Make the result concrete and directly usable in a creative workflow.

## Rules
1. Preserve names, IDs, technical tokens, ratios, and other constraints supplied by the user.
2. Do not invent constraints that conflict with the user's instruction.
3. Return only the finished text. Do not explain your process.
4. Do not wrap the result in a markdown code fence.""",
    },
    {
        "key": "freezone.story_script.system",
        "group": "freezone",
        "label": "分镜脚本生成系统提示词",
        "description": "画布脚本节点：把剧本片段拆成结构化分镜表（镜头 / 时长 / 台词 / 画面与运动提示词）。",
        "variables": (),
        "default": """# Freezone Story Script Generator

You generate a structured story-script table from an uploaded script excerpt.

## Goal
- Turn the source script into a complete, production-oriented story script.
- Output rows that are directly usable by downstream image and video nodes.
- Keep the result cinematic, concrete, and structured.

## Requirements
1. Break the story into clear numbered shots with sequential `shot_no`, starting from 1.
2. Each row must include all schema fields. Do not omit fields.
3. Prefer concrete visual language over vague abstraction. Every shot should feel filmable.
4. Dialogue should be short and only present when appropriate. If no dialogue is needed, output `无`.
5. If a field has no meaningful content, prefer `无` instead of vague placeholders.
6. Keep all fields in Simplified Chinese except technical tokens when naturally needed.
7. Output only structured data matching the schema. Do not wrap with markdown.

## Table style target
- The result should resemble a production storyboard table, not a prose summary.
- `visual_description` should describe one clear beat of action or state, usually in one concise sentence.
- `shot` should use concise combinations like `近景 / 特写`, `中景 / 仰视`, `全景 / 俯视远景`.
- `emotion` should be compact and specific, often 2-3 short phrases joined by `、`.
- `scene_tags`, `lighting_mood`, `sound` should all be concrete and film-facing.
- `character_1` should prefer a stable role identifier if inferable, such as `沈昭昭_现代` or `沈昭昭_古装`.
- `character_description_1` should prefer bracketed character-card format, e.g. `[沈昭昭_现代: 28岁女性，面色苍白，神情疲惫，身穿现代简约职业装……]`.

## Duration guidance
- Default to short cinematic shots.
- Most rows should fall in the 2-5 second range unless the source clearly calls for a longer beat.
- Keep the pacing readable and dramatic rather than mechanically uniform.

## Prompt formatting rules
`shot_prompt` must be image-generation friendly and must be written as a chained bracket structure using ` + ` separators.

Preferred order for `shot_prompt`:
1. `[画面构图：景别、机位、视角、构图关系]`
2. `[角色卡/主体描述：如果存在角色1，尽量直接复用或轻改 character_description_1 的角色卡格式；如果没有角色，则写主体/核心对象描述]`
3. `[主体/人物空间与互动关系：谁在前景、谁在中景、谁与什么环境或道具发生关系]`
4. `[极具体的微表情、主体状态或关键视觉信息：必须具体到眼神、嘴角、肢体紧张度、服饰状态、伤痕、汗水、血迹、视线方向等可见细节]`
5. `[明确的场景环境元素与前景/背景道具：办公室、宫殿、屏风、龙椅、电脑蓝光、飞尘、门缝光等]`
6. `[光影几何与大气效果：主光方向、冷暖色温、边缘光、雾气、逆光、顶光、体积光等]`
7. `[视觉风格/质感：写实电影感、纪实感、压抑冷感、盛唐史诗感等]`
8. `[技术参数：镜头焦段、光圈、景深、快门感、颗粒或解析度特征；这一段尽量不要省略]`

Example style for `shot_prompt`:
- `[画面构图：近景特写，平视机位] + [角色卡/主体描述：[沈昭昭_现代: 28岁女性，面色苍白，神情疲惫，身穿现代简约职业装]] + [主体/人物空间与互动关系：她独坐在办公桌前，电脑屏幕蓝光从侧前方打亮面部] + [极具体的微表情、主体状态或关键视觉信息：眼下发青，手指微颤，视线涣散，嘴唇微张] + [明确的场景环境元素与前景/背景道具：深夜办公室、电脑蓝光、散乱文件、冷掉的咖啡杯] + [光影几何与大气效果：冷蓝主调，屏幕侧光压住面部阴影，背景轻微灰雾感] + [视觉风格/质感：都市悬疑写实电影感] + [技术参数：85mm镜头，f/1.8，浅景深]`

`video_motion_prompt` must focus on motion and should also use a chained bracket structure.

Preferred order for `video_motion_prompt`:
1. `[明确的摄影机运镜轨迹与速度：必须写清推/拉/摇/移/跟/升/降/手持，以及快慢、力度和稳定性]`
2. `[主体极其具体的物理动作细节或状态变化：必须写清人物或主体具体怎么动，不要只写“情绪变化”]`
3. `[环境物理动态：风、雨、衣角、尘土、门帘、屏幕闪烁、火光、飞雪等]`
4. `[音效与氛围描述：环境声、器物声、呼吸声、脚步声、雷声等]`
5. `[对话台词与语气：有对白写具体台词与语气，没有就写无]`
6. `[时长：4.0s]`

Example style for `video_motion_prompt`:
- `[明确的摄影机运镜轨迹与速度：极慢速推进，镜头几乎贴着人物面部向前压近，稳定中带轻微呼吸感] + [主体极其具体的物理动作细节或状态变化：沈昭昭先是眼神失焦，随后瞳孔微缩，指尖在桌面轻轻抽动，喉结压抑地滚动一下] + [环境物理动态：屏幕冷光轻微闪烁，纸张边缘被空调风吹起，咖啡表面微微晃动] + [音效与氛围描述：急促的键盘声、连续的手机提示音、室内低频电流声] + [对话台词与语气：无] + [时长：4.0s]`

## Quality bar
- Avoid generic outputs like `人物站着`, `镜头推进`, `情绪复杂`.
- Prefer highly specific physical action, facial detail, scene detail, and camera-language wording.
- Preserve story logic and character-state progression across rows.

## Asset fields you must NOT invent
- `character_image_1`, `character_image_2`, `reference` are asset URL slots filled in by the
  backend after generation. Always output them as an empty string.
- Never write `无`, a file name, or a made-up URL into those three fields.
- When two distinct characters appear in one shot, fill `character_2` /
  `character_description_2` the same way as `character_1` / `character_description_1`.
  Reuse the exact same character identifier across rows so the same person keeps one name.""",
    },
    {
        "key": "freezone.story_script.video_suffix",
        "group": "freezone",
        "label": "分镜脚本-视频参考追加段",
        "description": "「视频参考 / 角色参考生成分镜脚本」时追加在基础分镜提示词后的视觉参考模式说明。",
        "variables": (),
        "default": """## Vision-reference modes
Images may be attached to the request. The task message states which mode applies; follow that
mode and ignore the other one.

### Video-keyframe mode
You are given an ordered set of keyframes sampled from a reference video.
- The story script MUST describe what is actually visible in those frames. Do not invent an
  unrelated story, and do not fall back on the examples in this prompt.
- Read the frames as one continuous clip: identify the real subject(s), setting, wardrobe or
  species, palette, and what physically changes from frame to frame.
- If the subject is not human (animal, object, mascot, animation), say so plainly in
  `character_1` and `character_description_1`. Do not substitute a human character.
- Group consecutive frames into narrative shots rather than describing every frame.
- Set `keyframe_index` on every row to the 1-based index of the input frame that best
  represents that shot. This is how the backend attaches the reference thumbnail.
- Total duration across rows should stay close to the stated video duration.

### Character-reference mode
You are given one portrait-style reference image per character, in the order the task message
lists them. There is no reference video.
- Read every character's real appearance off their image — face, hair, wardrobe, era, species —
  and write `character_description_1` / `character_description_2` from what you actually see.
  Do not describe a character the images do not show.
- Reuse the exact character names given in the task message so the backend can attach each
  character's reference image.
- The story itself comes from the user's request (and the source script, when one is supplied),
  not from the images. The images only fix who the characters are.
- Set `keyframe_index` to 0 on every row: there are no keyframes to attach.""",
    },
    # ------------------------------------------------------------------
    # [aipdd fork delta] 故事版（分镜板参考图）基础提示词：分镜脚本页把用户要求
    # 拼到这段基础提示词后走图像生成，产出一张多格分镜板，存进资产库「故事版」分类。
    # ------------------------------------------------------------------
    {
        "key": "freezone.story_version.system",
        "group": "freezone",
        "label": "故事版基础提示词（分镜板出图）",
        "description": "「分镜脚本」页生成故事版（多格分镜板参考图）的基础图像提示词；用户在页面上输入的要求会追加到本段之后。",
        "variables": (
            "user_requirement",
        ),
        "default": """为短剧生成分镜版（storyboard）参考图。
把下面的创作要求拆成连贯的镜头，以多格分镜的形式呈现在一张图上：
- 每格一个镜头（远景 / 全景 / 中景 / 特写轮递），左上角标序号。
- 保持人物造型 / 场景 / 光源 / 色调在整版内一致，体现镜头之间的叙事连贯。
- 黑白线稿 / 淡涂的 storyboard 质感，重构图与机位，不做最终上色渲染。

创作要求：{user_requirement}""",
    },
    # ------------------------------------------------------------------
    # 草图与渲染网格（generators/prompt_builder.py）
    # ------------------------------------------------------------------
    {
        "key": "grid.ethnicity.fallback",
        "group": "grid",
        "label": "默认族裔回退（草图 / 渲染网格）",
        "description": "网格 / 渲染提示词中，未指定族裔时的默认族裔说明。变量 {value} 为设置的默认族裔。",
        "variables": ("value",),
        "default": """For people without identity references and without explicit ethnicity, nationality, or regional description in the character, beat, scene, or reference image, default to {value}. If any explicit source specifies another ethnicity or nationality, follow that explicit source.""",
    },
    {
        "key": "grid.colorization_rules",
        "group": "grid",
        "label": "网格上色规则（COLORIZATION RULES）",
        "description": "网格渲染的主规则：以草图为空间基准、按参考图 / 文字完成上色；含多个条件规则占位符。",
        "variables": (
            "marker_scope",
            "ref_line",
            "prop_colorization_rule",
            "scene_anchor_rule",
            "material_only_scene_rule",
            "rough_scene_repair_rule",
        ),
        "default": """COLORIZATION RULES:
🔒 THIS IS A COLORIZATION TASK — the sketch is the BASE DRAWING, NOT just a reference.
🧱 BASE SKETCH LOCK: Image 1 / SKETCH is the ONLY spatial source of truth. The output must preserve Image 1's crop, camera angle, lens distance, character placement, prop placement, object scale, and pose relationships. All other reference images are identity/material/location swatches only. If any scene, character, or prop reference implies a different camera or layout, ignore that spatial implication and keep Image 1.
🎨 Keep the sketch's composition, camera angle, character placement, and action pose EXACTLY as drawn — but NOT the sketch's raw anatomy. Do NOT move the character, change the camera distance, or alter the scene layout. For colored identity-locked figures, keep the same drawn figure and apply the character's TRUE facial identity, gender, body build, and silhouette from reference images or text descriptions. Do NOT reinterpret the drawn figure as a different gender or body type.
🧍 REALISTIC PROPORTIONS: Treat rough sketch anatomy as guidance only, not literal anatomy. Keep the same pose and layout, but normalize crude stick-figure anatomy into realistic human proportions. Do NOT copy exaggerated sketch head size, simplified neck thickness, limb thickness, hand size, or torso shortcuts unless caricature is explicitly requested.
🎯 CHARACTER IDENTIFICATION: Colored figures in the sketch are the identity-locked characters.
   Use the COLOR IDENTIFICATION section to match those colored figures to character references.
   ⚠️ Sketch colors (on {marker_scope}) are ONLY for identification — they are NOT final visible colors.
{ref_line}
🎨 CHARACTER COLORIZATION: You are COLORIZING the figures already drawn in the sketch — NOT rendering new characters. The sketch colors are fluorescent ID markers only, not final visible colors. For COLORED figures, COMPLETELY DISCARD the sketch's color tint. Replace 100% of every marker color with the character's true appearance colors. No marker color may remain visible on skin, hair, clothing, shadows, or highlights. All exposed skin (face, neck, ears, hands, fingers) must use natural human skin tone from the reference image or text description, never the sketch marker color. Keep the sketch's drawn figure, placement, pose, and framing, but apply the identity-locked character's true face identity, gender, body build, and silhouette within that figure. If the sketch uses crude stick-figure anatomy, convert it to natural adult human anatomy rather than copying the sketch's exact head-to-body ratio or simplified body construction. For COMPOSITE / IDENTITY-SHEET characters, use the reference image to lock identity only: face identity, skin tone, hair color, clothing colors/textures, body build, and silhouette. Do NOT copy or preserve the reference image's rendering medium, retouching level, CGI feel, illustration style, or beauty-filter look. Final rendering realism, skin texture, lighting behavior, and image finish must follow the global scene preset. For PORTRAIT-ONLY characters, use the reference image EXACTLY for face identity, skin tone, hair identity, and head/hair silhouette, but use WEAR / text description for costume, accessories, and body styling. Portrait-only references lock who the character is, not the portrait's rendering medium or retouching style. For text-only characters without reference images, use the text description for identity/body/clothing while following the global scene preset for rendering style and overall treatment. Do NOT add, remove, merge, or reposition any colored figure beyond what is drawn.
{prop_colorization_rule}🚫 NO COMIC EFFECT LINES: Do NOT draw manga/comic motion lines, speed lines, trembling lines, impact lines, emphasis rays, stress marks, or symbolic action streaks anywhere in the image.
🏗️ COMPOSITION HIERARCHY: The sketch's spatial layout is the ABSOLUTE GROUND TRUTH. Environment descriptions and scene anchors provide MATERIAL, LOCATION IDENTITY, and ATMOSPHERE only — they must NOT override the sketch's camera angle, character distance, spatial relationships, crop, or scene depth. If an environment detail implies a different spatial arrangement than what the sketch shows, IGNORE the spatial implication and keep only the material/texture/lighting information.
{scene_anchor_rule}{material_only_scene_rule}{rough_scene_repair_rule}🎭 STYLE SCOPING: The global STYLE preset and AVOID rules apply to the environment, background, architecture, props, lighting, camera treatment, overall image finish, AND the final rendering treatment of all characters. Reference images lock character identity and appearance details, but they do NOT lock rendering medium. If a user-uploaded portrait is over-retouched, beautified, illustrated, or CG-like, keep the same person and appearance details while re-rendering that character in the project's scene preset. Scene realism rules govern the final skin texture, material response, lighting, and image finish for the whole scene.
✅ OUTPUT: The sketch rendered in full color with high-fidelity detail. Composite / identity-sheet and portrait-only references preserve the referenced person's identity, hair, costume details, and silhouette, while the final rendering treatment follows the global scene preset. Named props preserve their referenced material identity exactly. All text-only characters, extras, and the surrounding world follow the global scene preset.""",
    },
    {
        "key": "grid.prop_colorization_rule",
        "group": "grid",
        "label": "道具上色规则块",
        "description": "网格里存在道具参考图时追加的道具上色规则。",
        "variables": (),
        "default": """🎨 PROP COLORIZATION: For every attached prop reference, the sketch's marker color is identification-only — it is NOT the prop's final material color. COMPLETELY DISCARD the marker tint and replace it with the prop's TRUE material color, surface texture, and material identity from the attached prop reference image when one is provided; if no prop image is attached, use the prop text description plus the global scene preset. Preserve the sketch's prop placement, scale, framing, and the character's interaction with the prop, but render the prop with its true material as if photographed in the same scene/lighting as the rest of the panel. Do NOT add, remove, duplicate, or reposition any prop beyond what is drawn.""",
    },
    {
        "key": "grid.scene_anchor_rule",
        "group": "grid",
        "label": "场景锚点规则块",
        "description": "网格里挂载了结构性场景锚点图时追加的规则，避免锚点覆盖草图构图。",
        "variables": (),
        "default": """🏠 SCENE ANCHOR RULE: If a scene anchor image is attached for a location, use it only to match that location's architecture, materials, set dressing, and major props. Scene anchors do NOT define time-of-day lighting, shadow direction, color grade, weather, or atmosphere. Those must follow the current beat's time_of_day and scene context. Scene anchors also do NOT override the sketch's framing, lens distance, blocking, camera height, perspective, or character placement. Do NOT use a scene anchor as the base image, do NOT copy its camera/crop/full-room composition, and do NOT expand or restage the sketch to match the scene anchor view.""",
    },
    {
        "key": "grid.material_only_scene_rule",
        "group": "grid",
        "label": "材质背景参考规则块",
        "description": "网格里挂载了「仅材质」背景图时追加的规则，只取材质不入空间信息。",
        "variables": (),
        "default": """🧱 BACKGROUND APPEARANCE REFERENCE RULE: If a background image is marked material-only, treat it as a visual appearance reference, not as a spatial reference. It supplies material, texture, color palette, surface finish, age/wear, and overall visual mood only. Image 1 / SKETCH remains the only geometry source for perspective, crop, camera/lens distance, vanishing points, edge directions, wall/counter/shelf outlines, object positions, scene depth, character blocking, and prop placement. Do NOT use a background appearance reference as the base image, and do NOT copy its layout, object placement, camera, or spatial topology.""",
    },
    {
        "key": "grid.rough_scene_repair_rule",
        "group": "grid",
        "label": "3DGS 粗糙环境修复块",
        "description": "场景锚点来自 3D Gaussian Splatting 等粗糙渲染时追加的修复规则。",
        "variables": (),
        "default": """🧹 ROUGH 3DGS ENV REPAIR: If the attached scene anchor is a same-angle director/env_only/3D Gaussian Splatting environment plate, treat it as a rough material and viewpoint reference only. Remove Gaussian-splat noise, blur, floating speckles/floaters, projection seams, smeared textures, jagged/broken edges, warped surface artifacts, ghosting, and low-resolution mush. Reconstruct clean wall, floor, counter, table, stool, door, window, shelf, menu/sign, and fixture surfaces in the project style, while keeping the sketch's corrected perspective, object screen positions, and character blocking unchanged.""",
    },
    {
        "key": "grid.temporal_framing",
        "group": "grid",
        "label": "时间帧约束（视频首帧）",
        "description": "提醒每个网格面板都是视频首帧，动态动作要画「动作开始前」的状态。",
        "variables": (),
        "default": """TEMPORAL FRAMING (CRITICAL FOR VIDEO):
Each panel is the FIRST FRAME of a video clip. For dynamic actions:
- "turns around" (转身) → show character's BACK (before turning)
- "stands up" (站起) → show character SEATED (before standing)
- "looks up" (抬头) → show character looking DOWN (before looking up)
- "opens door" (开门) → show door CLOSED (before opening)
- "walks in" (走进) → show character at the DOORWAY (before entering)
Show the STARTING pose, NOT the ending result.""",
    },
)