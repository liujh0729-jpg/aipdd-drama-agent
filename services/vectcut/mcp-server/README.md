# CapCut MCP Server

A professional Model Context Protocol (MCP) server for **CapCut Pro** video editing automation. This server enables AI assistants and applications to create and edit videos programmatically through CapCut's powerful editing capabilities.

## 🎬 Features

- **Complete Video Editing Suite**: Create drafts, add videos, audio, text, images, effects, and more
- **Professional Tools**: 11 specialized tools for video production workflows
- **Type-Safe**: Built with TypeScript for reliability and excellent IDE support
- **Flexible Transport**: Supports both stdio (local) and HTTP (remote) connections
- **Input Validation**: Comprehensive Zod schemas with helpful error messages
- **Dual Output Formats**: JSON for machines, Markdown for humans

## 📋 Prerequisites

Before using this MCP server, you need to have the **VectCutAPI** (CapCut API server) running:

1. **Install VectCutAPI**:
   ```bash
   git clone https://github.com/sun-guannan/VectCutAPI.git
   cd VectCutAPI
   pip install -r requirements.txt
   ```

2. **Start the API Server**:
   ```bash
   python capcut_server.py
   ```
   The server will start on `http://localhost:9001` by default.

## 🚀 Installation

### Option 1: Install from npm (once published)
```bash
npm install -g capcut-mcp-server
```

### Option 2: Build from Source
```bash
# Clone this repository
git clone <your-repo-url>
cd capcut-mcp-server

# Install dependencies
npm install

# Build the project
npm run build

# Test the server
npm start
```

## 🔧 Configuration

### For Claude Desktop

Add to your Claude Desktop config file:

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "capcut": {
      "command": "node",
      "args": ["/absolute/path/to/capcut-mcp-server/dist/index.js"],
      "env": {
        "CAPCUT_API_URL": "http://localhost:9001"
      }
    }
  }
}
```

### For Other MCP Clients

The server supports two transport modes:

#### Stdio Mode (Default - Local Integration)
```bash
node dist/index.js
```

#### HTTP Mode (Remote Access)
```bash
TRANSPORT=http PORT=3000 node dist/index.js
```

## 🛠️ Available Tools

### 1. `capcut_create_draft`
Create a new video editing project with custom dimensions and frame rate.

**Example**:
```typescript
{
  "width": 1920,
  "height": 1080,
  "fps": 30
}
```

### 2. `capcut_add_video`
Add video clips with transitions, speed control, and volume adjustments.

**Example**:
```typescript
{
  "draft_id": "abc123",
  "video_url": "https://example.com/video.mp4",
  "start": 0,
  "end": 10,
  "volume": 0.8,
  "transition": "fade_in",
  "speed": 1.0
}
```

### 3. `capcut_add_audio`
Add background music or sound effects with fade in/out.

**Example**:
```typescript
{
  "draft_id": "abc123",
  "audio_url": "https://example.com/music.mp3",
  "start": 0,
  "end": 30,
  "volume": 0.5,
  "fade_in": 2,
  "fade_out": 2
}
```

### 4. `capcut_add_text`
Add styled text overlays with animations, shadows, and backgrounds.

**Example**:
```typescript
{
  "draft_id": "abc123",
  "text": "Welcome!",
  "start": 0,
  "end": 3,
  "font_size": 72,
  "font_color": "#FFFFFF",
  "background_color": "#000000",
  "shadow_enabled": true,
  "animation": "fade_in"
}
```

### 5. `capcut_add_image`
Add image overlays with positioning, scaling, and rotation.

**Example**:
```typescript
{
  "draft_id": "abc123",
  "image_url": "https://example.com/logo.png",
  "start": 0,
  "end": 5,
  "position_x": 0.9,
  "position_y": 0.1,
  "scale": 0.3
}
```

### 6. `capcut_add_subtitle`
Import subtitles from SRT format with custom styling.

**Example**:
```typescript
{
  "draft_id": "abc123",
  "srt_content": "1\n00:00:01,000 --> 00:00:03,000\nWelcome to my video",
  "font_size": 36,
  "font_color": "#FFFFFF",
  "background_enabled": true
}
```

### 7. `capcut_add_keyframe`
Create smooth animations using keyframe interpolation.

**Example**:
```typescript
{
  "draft_id": "abc123",
  "track_name": "main",
  "property_types": ["scale_x", "scale_y"],
  "times": [0, 2, 4],
  "values": ["1.0", "1.5", "1.0"]
}
```

### 8. `capcut_add_effect`
Apply visual effects like blur, brightness, saturation, etc.

**Example**:
```typescript
{
  "draft_id": "abc123",
  "effect_name": "blur",
  "start": 0,
  "end": 2,
  "intensity": 0.7
}
```

### 9. `capcut_add_sticker`
Add decorative stickers or emojis.

**Example**:
```typescript
{
  "draft_id": "abc123",
  "sticker_url": "https://example.com/emoji.png",
  "start": 1,
  "end": 5,
  "position_x": 0.9,
  "position_y": 0.1,
  "scale": 0.2
}
```

### 10. `capcut_save_draft`
Save the draft to import into CapCut application.

**Example**:
```typescript
{
  "draft_id": "abc123"
}
```

### 11. `capcut_get_duration`
Get duration and metadata of media files.

**Example**:
```typescript
{
  "url": "https://example.com/video.mp4"
}
```

## 📖 Usage Examples

### Complete Video Creation Workflow

```typescript
// 1. Create a new draft
const draft = await capcut_create_draft({
  width: 1920,
  height: 1080,
  fps: 30
});

// 2. Add background video
await capcut_add_video({
  draft_id: draft.draft_id,
  video_url: "https://example.com/background.mp4",
  start: 0,
  end: 10,
  volume: 0.6
});

// 3. Add title text
await capcut_add_text({
  draft_id: draft.draft_id,
  text: "Amazing Video",
  start: 1,
  end: 4,
  font_size: 72,
  animation: "fade_in"
});

// 4. Add background music
await capcut_add_audio({
  draft_id: draft.draft_id,
  audio_url: "https://example.com/music.mp3",
  start: 0,
  end: 10,
  volume: 0.5
});

// 5. Add zoom animation
await capcut_add_keyframe({
  draft_id: draft.draft_id,
  track_name: "main",
  property_types: ["scale_x", "scale_y"],
  times: [0, 5, 10],
  values: ["1.0", "1.2", "1.0"]
});

// 6. Save the draft
const result = await capcut_save_draft({
  draft_id: draft.draft_id
});

console.log(`Draft saved to: ${result.draft_url}`);
```

## 🎯 Use Cases

- **AI-Powered Video Generation**: Let AI assistants create complete video projects
- **Batch Video Production**: Automate creation of multiple videos from templates
- **Social Media Content**: Generate TikTok, Reels, and YouTube Shorts automatically
- **Educational Content**: Create tutorial videos with synchronized text and audio
- **Marketing Automation**: Generate promotional videos at scale

## 🔍 Troubleshooting

### Server Not Responding
- Ensure VectCutAPI server is running on port 9001
- Check `CAPCUT_API_URL` environment variable is correct
- Verify network connectivity to the API server

### Build Errors
```bash
# Clean and rebuild
rm -rf dist node_modules package-lock.json
npm install
npm run build
```

### Media Files Not Found
- Ensure all media URLs are accessible
- Use direct links to files (avoid redirects)
- Check file format is supported

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 📄 License

MIT License - feel free to use this in your projects!

## 🙏 Acknowledgments

- Built on top of [VectCutAPI](https://github.com/sun-guannan/VectCutAPI) by sun-guannan
- Uses the [Model Context Protocol](https://modelcontextprotocol.io/) specification
- Powered by [Anthropic's MCP SDK](https://github.com/modelcontextprotocol/typescript-sdk)

## 📞 Support

For issues related to:
- **This MCP Server**: Open an issue in this repository
- **VectCutAPI**: Visit https://github.com/sun-guannan/VectCutAPI
- **CapCut Application**: Contact CapCut support

---

**Note**: This is an unofficial MCP server for CapCut Pro. It requires the VectCutAPI backend to function. CapCut is a trademark of Bytedance Ltd.
