# 临时脚本：替换 tools.voiceClone.modelHint 三语文案（去掉「默认引擎」引用）。
# SearchReplace 工具在该大文件上连续写入失败，故用字节级精确替换（其余内容不变）。
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "public" / "locales"

CASES = {
    "zh": (
        '"modelHint": "建议选择后台配置的语音模型（如 IndexTTS2 / ElevenLabs）；不确定就保持「默认引擎」。"',
        '"modelHint": "建议选择后台配置的语音模型（如 IndexTTS2 / ElevenLabs）；默认选中「设置 → 默认生成模型」中配置的音频模型。"',
    ),
    "en": (
        '"modelHint": "Recommended: a speech model configured in the backend (e.g. IndexTTS2 / ElevenLabs); keep \\"Default engine\\" if unsure."',
        '"modelHint": "Recommended: a speech model configured in the backend (e.g. IndexTTS2 / ElevenLabs); the audio model set in Settings → Default generation models is selected by default."',
    ),
    "vi": (
        '"modelHint": "Gợi ý: chọn mô hình giọng nói đã cấu hình ở backend (ví dụ IndexTTS2 / ElevenLabs); giữ «Công cụ mặc định» nếu chưa chắc."',
        '"modelHint": "Gợi ý: chọn mô hình giọng nói đã cấu hình ở backend (ví dụ IndexTTS2 / ElevenLabs); mặc định chọn mô hình âm thanh đặt trong Cài đặt → Model tạo mặc định."',
    ),
}

failed = False
for lang, (old, new) in CASES.items():
    path = BASE / lang / "translation.json"
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        print(f"[FAIL] {lang}: expected 1 match, found {count}")
        failed = True
        continue
    text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")
    # 校验替换后仍是合法 JSON 且值正确。
    data = json.loads(path.read_text(encoding="utf-8"))
    hint = data["tools"]["voiceClone"]["modelHint"]
    assert "默认引擎" not in hint and "Default engine" not in hint and "Công cụ mặc định" not in hint, lang
    print(f"[OK] {lang}: {hint[:60]}...")

sys.exit(1 if failed else 0)