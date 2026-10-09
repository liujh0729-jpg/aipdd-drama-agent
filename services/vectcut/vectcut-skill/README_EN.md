# VectCutAPI Skill for Claude Code

<div align="center">

**English** | **[中文](README.md)**

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Claude Code](https://img.shields.io/badge/Claude_Code-Skill-purple.svg)](https://claude.com/claude-code)
[![VectCutAPI](https://img.shields.io/badge/VectCutAPI-1.5k%2B%20Stars-orange.svg)](https://github.com/sun-guannan/VectCutAPI)

Empowering AI with professional video editing capabilities through VectCutAPI

[Quick Start](#quick-start) • [Features](#features) • [Examples](#usage-examples) • [API Docs](#api-documentation)

</div>

---

## Overview

**VectCutAPI Skill** is a professional video editing skill for [Claude Code](https://claude.com/claude-code), powered by the robust [VectCutAPI](https://github.com/sun-guannan/VectCutAPI) project.

With this skill, Claude AI can directly utilize VectCutAPI's full capabilities:
- Automated video draft creation
- Video, audio, and image material management
- Text, subtitle, and effects addition
- Transition and keyframe animation application
- Batch video processing
- AI-driven video generation workflows

### Key Benefits

- **Seamless Integration** - Auto-detected and loaded by Claude Code
- **Complete Coverage** - Supports all 35+ HTTP endpoints and 11 MCP tools of VectCutAPI
- **Python Client** - Elegant Python API wrapper included
- **Rich Examples** - 8+ workflow example codes included
- **Dual Protocol** - Supports both HTTP REST and MCP protocols

---

## Acknowledgments

This project is a wrapper and extension of the following open-source projects:

### Core Dependencies

| Project | Author | License | Description |
|---------|--------|---------|-------------|
| [VectCutAPI](https://github.com/sun-guannan/VectCutAPI) | [@sun-guannan](https://github.com/sun-guannan) | Apache 2.0 | Powerful cloud video editing API providing programmatic control over CapCut/JianYing |
| [Claude Code](https://claude.com/claude-code) | Anthropic | - | Official Anthropic CLI tool supporting custom skill extensions |
| [skill-creator](https://github.com/anthropics/claude-code-skills) | Anthropic | - | Claude Code skill creation guide and tools |

### Special Thanks

- **@sun-guannan** - For creating the excellent VectCutAPI project that bridges the gap between AI-generated content and professional video editing
- **Anthropic** - For providing Claude Code and the skill system, enabling AI to seamlessly integrate with professional tools

### Disclaimer

This wrapper project exists as a companion skill to VectCutAPI, aiming to provide convenient integration for Claude Code users. The core video editing functionality entirely depends on the original VectCutAPI project.

---

## Features

### Supported Video Editing Capabilities

| Module | Description |
|--------|-------------|
| **Draft Management** | Create, save, query CapCut/JianYing draft files |
| **Video Processing** | Multi-format video import, editing, transitions, effects, masks |
| **Audio Editing** | Audio tracks, volume control, audio effects |
| **Image Processing** | Image import, animations, masks, filters |
| **Text Editing** | Multi-style text, shadows, backgrounds, animations |
| **Subtitle System** | SRT subtitle import, styling, time sync |
| **Effects Engine** | Visual effects, filters, transition animations |
| **Sticker System** | Sticker materials, position control, animations |
| **Keyframes** | Property animations, timeline control, easing |
| **Media Analysis** | Video duration retrieval, format detection |

### Built-in Resources

- **SKILL.md** - Complete skill usage guide
- **Python Client** - `vectcut_client.py` providing elegant API wrapper
- **API Reference** - Detailed interface documentation and parameter descriptions
- **Workflow Examples** - 8+ common video production scenarios with complete code

---

## Quick Start

### Requirements

- Python 3.10+
- Claude Code (Anthropic's official CLI)
- JianYing or CapCut International
- FFmpeg (optional)

### 1. Install VectCutAPI

```bash
# Clone VectCutAPI project
git clone https://github.com/sun-guannan/VectCutAPI.git
cd VectCutAPI

# Install dependencies
pip install -r requirements.txt      # HTTP API basic dependencies
pip install -r requirements-mcp.txt  # MCP protocol support (optional)

# Configure
cp config.json.example config.json
# Edit config.json as needed

# Start service
python capcut_server.py  # HTTP API server (default port: 9001)
```

### 2. Install Skill

```bash
# Clone this project
git clone https://github.com/HUNSETO1413/vectcut-skill.git
cd vectcut-skill

# Copy skill files to Claude Code skills directory
# Windows:
copy skill\* %USERPROFILE%\.claude\skills\public\vectcut-api\ /E

# Linux/macOS:
cp -r skill/* ~/.claude/skills/public/vectcut-api/
```

### 3. Verify Installation

In Claude Code, enter:

```
I need to create a 1080x1920 video draft
```

Claude should automatically load the vectcut-api skill and invoke relevant functions.

---

## Usage Examples

### Basic Video Production

```python
from skill.scripts.vectcut_client import VectCutClient

# Create client
client = VectCutClient("http://localhost:9001")

# Create draft
draft = client.create_draft(width=1080, height=1920)

# Add background video
client.add_video(
    draft.draft_id,
    "https://example.com/background.mp4",
    volume=0.6
)

# Add title text
client.add_text(
    draft.draft_id,
    "Welcome to VectCutAPI",
    start=0,
    end=5,
    font_size=56,
    font_color="#FFD700",
    shadow_enabled=True
)

# Save draft
result = client.save_draft(draft.draft_id)
print(f"Draft saved: {result.draft_url}")
```

### AI Text-to-Video Workflow

```python
import requests

BASE_URL = "http://localhost:9001"

# 1. Create draft
draft = requests.post(f"{BASE_URL}/create_draft", json={
    "width": 1080,
    "height": 1920
}).json()
draft_id = draft["output"]["draft_id"]

# 2. Add background video
requests.post(f"{BASE_URL}/add_video", json={
    "draft_id": draft_id,
    "video_url": "https://example.com/bg.mp4",
    "volume": 0.4
})

# 3. Add segmented text
segments = ["First text", "Second text", "Third text"]
colors = ["#FF6B6B", "#4ECDC4", "#45B7D1"]

for i, (segment, color) in enumerate(zip(segments, colors)):
    requests.post(f"{BASE_URL}/add_text", json={
        "draft_id": draft_id,
        "text": segment,
        "start": i * 4,
        "end": (i + 1) * 4,
        "font_size": 48,
        "font_color": color,
        "shadow_enabled": True
    })

# 4. Save draft
result = requests.post(f"{BASE_URL}/save_draft", json={
    "draft_id": draft_id
}).json()

print(f"Video generated: {result['output']['draft_url']}")
```

For more examples, see [workflows.md](skill/references/workflows.md).

---

## API Documentation

### Core APIs

| API | Function |
|-----|----------|
| `create_draft()` | Create video draft |
| `save_draft()` | Save draft and generate URL |
| `add_video()` | Add video track |
| `add_audio()` | Add audio track |
| `add_image()` | Add image material |
| `add_text()` | Add text element |
| `add_subtitle()` | Add SRT subtitle |
| `add_effect()` | Add video effect |
| `add_sticker()` | Add sticker |
| `add_video_keyframe()` | Add keyframe animation |

For complete API documentation, see [api_reference.md](skill/references/api_reference.md).

---

## Workflow Examples

The project includes complete workflow examples for:

1. **Basic Video Production** - Vertical short video production
2. **AI Text-to-Video** - Convert text content to video
3. **Video Mashup** - Multi-segment video splicing with transitions
4. **Video with Subtitles** - SRT subtitle import
5. **Keyframe Animation** - Image animation showcase
6. **Product Introduction Video** - Professional product showcase
7. **Split Screen Effect** - Left-right split screen comparison
8. **Image Slideshow** - Image slideshow

See [workflows.md](skill/references/workflows.md) for details.

---

## Related Projects

- [VectCutAPI](https://github.com/sun-guannan/VectCutAPI) - Core video editing API
- [pyJianYingDraft](https://github.com/sun-guannan/pyJianYingDraft) - JianYing draft Python library
- [Claude Code Skills](https://github.com/anthropics/claude-code-skills) - Official skill collection

---

## License

This project is licensed under the [MIT License](LICENSE).

**Note**: The VectCutAPI core library wrapped by this project is licensed under Apache 2.0.

---

## Contributing

Contributions are welcome! Please follow this workflow:

1. Fork this project
2. Create feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to branch (`git push origin feature/AmazingFeature`)
5. Submit Pull Request

### Contribution Areas

- New workflow examples
- Python client optimization
- Documentation improvement
- Bug fixes

---

## Contact

### Author Information

**Project Author**: HUNSETO1413

- **Project Home**: [GitHub Repository](https://github.com/HUNSETO1413/vectcut-skill)
- **Issue Tracker**: [Issues](https://github.com/HUNSETO1413/vectcut-skill/issues)
- **Original Project**: [sun-guannan/VectCutAPI](https://github.com/sun-guannan/VectCutAPI)

### WeChat Contact

Scan the QR code to add the author on WeChat for technical discussions:

<div align="center">

![Author WeChat](Mark微信.png)

**WeChat ID**: `399187854`

</div>

---

## Changelog

### v1.0.0 (2025-01-25)

Initial release

- ✅ Complete VectCutAPI Skill wrapper
- ✅ Python client library
- ✅ 8+ workflow examples
- ✅ Complete API reference documentation
- ✅ Support for all VectCutAPI features

---

## Star History

If this project helps you, please give it a Star ⭐️

Also welcome to star the original project [VectCutAPI](https://github.com/sun-guannan/VectCutAPI) 🌟

---

<div align="center">

**Made with ❤️ by HUNSETO1413**

Based on [VectCutAPI](https://github.com/sun-guannan/VectCutAPI) by [@sun-guannan](https://github.com/sun-guannan)

WeChat: **399187854**

</div>
