# DC-Media Protocol v1: Concise English Implementation Reference

This document is a concise implementation reference for contributors. The full
English specification is
[`dc-media-protocol.en.md`](../../../dc-media-protocol.en.md), paired with the
Chinese [`dc-media-protocol.md`](../../../dc-media-protocol.md). When this guide
and the full specification disagree, the full specification and its contract
tests take precedence.

## Goals and Boundaries

DC-Media gives DramaClaw provider-neutral image, asynchronous video, speech,
and voice-design contracts. `dramaclaw-gateway` validates and normalizes those
contracts before a provider adapter creates an upstream request.

Provider field names, authentication headers, task status names, and model
quirks do not belong in the public request. The protocol does not include a
top-level `mode`; the gateway derives a provider call shape from media roles.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /images/generations` | Text-to-image without reference images |
| `POST /images/edits` | Image editing or generation with one or more reference images |
| `POST /video/generations` | Submit an asynchronous video task |
| `POST /audio/speech` | Synthesize speech or generate audio |
| `POST /audio/voice-designs` | Create a reusable voice from a text description |

Voice design is not speech synthesis. It uses a dedicated DTO and returns a
JSON `audio.voice` resource containing a reusable `voice`, `target_model`, and
preview audio. The gateway must preserve ownership between the voice and the
provider credential that created it so later speech requests reach a credential
that can access the voice.

## Common Fields

| Field | Type | Meaning |
|---|---|---|
| `model` | string | Gateway model name resolved from the DramaClaw model catalog |
| `prompt` | string | Final user or business-layer prompt |
| `image` | string or string[] | Image references for edits; video first frame for video requests |
| `duration` | positive integer or `"auto"` | Video output duration |
| `width`, `height` | integer pair | Fixed output dimensions; both or neither must be present |
| `n` | integer | Number of outputs, currently normally `1` |
| `response_format` | string | Usually `b64_json` for images and `url` for video |
| `metadata` | object | Ratio, resolution, reference media, and optional capabilities |

Normalize named values before the request reaches an adapter:

```text
adaptive -> auto
4K       -> 4k
2K       -> 2k
1K       -> 1k
1080P    -> 1080p
720P     -> 720p
480P     -> 480p
```

Explicit pixel sizes such as `1920x1080` remain unchanged. New requests must not
use `adaptive` or `-1` to represent automatic values.

## Image Requests

Text-to-image uses `/images/generations` and contains no image input:

```json
{
  "model": "example-image-model",
  "prompt": "cinematic street at night",
  "n": 1,
  "response_format": "b64_json",
  "width": 2048,
  "height": 1152,
  "metadata": {
    "ratio": "16:9",
    "resolution": "2k"
  }
}
```

Image editing and multi-image reference generation use `/images/edits`. All
references use the top-level `image` array; provider fields such as `image_url`
or `image_urls` are not part of DC-Media:

```json
{
  "model": "example-image-model",
  "prompt": "keep the identity and change the scene",
  "image": [
    "https://example.invalid/person.png",
    "https://example.invalid/style.png"
  ],
  "n": 1,
  "response_format": "b64_json",
  "metadata": {
    "ratio": "auto",
    "resolution": "2k"
  }
}
```

Optional public image fields include `quality`, `output_format`,
`input_fidelity`, and top-level `watermark`. Omit options the user did not select
instead of guessing provider defaults. With `ratio=auto`, do not derive fixed
dimensions from the first reference image.

## Video Media Roles

Video uses `POST /video/generations`.

| Public field | Role |
|---|---|
| top-level `image` | strict first frame |
| `metadata.last_frame_image` | strict last frame |
| `metadata.reference_images` | content, identity, or style references |
| `metadata.reference_videos` | reference video or video-edit source |
| `metadata.reference_audios` | reference audio |
| `metadata.reference_file` | one reference document or file URL |
| `metadata.reference_link` | one public web page URL |

The first reference image must never be promoted to a first frame. Top-level
`image` and `reference_images` are mutually exclusive. A last frame cannot be
combined with reference image, video, audio, file, or link fields. A top-level
first frame cannot be combined with a reference file or link. `reference_file`
and `reference_link` are mutually exclusive.

DramaClaw model modes map to public fields, but mode names are not transmitted
as a top-level `mode`. Omni-reference models use
`metadata.omni_reference_task_type` to distinguish `reference`, `edit`, and
`extend` subtypes. The gateway validates mutual exclusion and derives a generic
media call shape in this order:

1. `duration="auto"`, `metadata.ratio="auto"`, and at least one reference video:
   video edit;
2. a last frame, optionally with a first frame: first/last-frame video;
3. a top-level image: first-frame video;
4. reference video, audio, file, or link: multimodal reference;
5. more than one reference image: image reference;
6. exactly one reference image: image-to-video;
7. no media input: text-to-video.

The derived shape chooses a provider endpoint, workflow, or payload. It does not
recover the original DramaClaw UI mode. If the provider does not support the
shape, reject the request instead of dropping media or degrading modes.
Video extension remains multimodal reference at the generic media-shape layer.
A supporting adapter distinguishes it using the explicit `extend` subtype; it
must not infer extension from a reference video and fixed duration alone.

### First Frame

```json
{
  "model": "example-video-model",
  "prompt": "the subject turns toward the camera",
  "image": "https://example.invalid/first.png",
  "duration": 5,
  "metadata": {
    "ratio": "auto",
    "resolution": "720p"
  }
}
```

### First and Last Frame

```json
{
  "model": "example-video-model",
  "prompt": "transition naturally from day to night",
  "image": "https://example.invalid/first.png",
  "duration": 5,
  "metadata": {
    "last_frame_image": "https://example.invalid/last.png",
    "ratio": "auto",
    "resolution": "720p"
  }
}
```

This shape uses `ratio=auto` and sends no fixed width or height.

### Multimodal Reference

```json
{
  "model": "example-video-model",
  "prompt": "create a new shot from the references",
  "duration": 8,
  "metadata": {
    "ratio": "16:9",
    "resolution": "720p",
    "reference_images": ["https://example.invalid/character.png"],
    "reference_videos": ["https://example.invalid/motion.mp4"],
    "reference_audios": ["https://example.invalid/voice.mp3"],
    "omni_reference_task_type": "reference"
  }
}
```

A fixed-duration request with reference video is multimodal reference, not video
editing.

### Reference File or Link

```json
{
  "model": "example-video-model",
  "prompt": "create a product introduction from the document",
  "duration": 8,
  "metadata": {
    "ratio": "16:9",
    "resolution": "720p",
    "reference_file": "https://example.invalid/product.pdf"
  }
}
```

`reference_file` and `reference_link` are single URL strings and cannot be used
together. They may accompany reference images, videos, or audio, but cannot be
mixed with first- or last-frame inputs. Either field selects the
fixed-duration `all_reference` call shape and cannot be combined with
`duration="auto"`. DC-Media does not embed raw file bytes in a video request;
clients must provide a provider-accessible URL.

### Video Edit

```json
{
  "model": "example-video-model",
  "prompt": "keep the motion and replace the background",
  "duration": "auto",
  "metadata": {
    "ratio": "auto",
    "resolution": "720p",
    "reference_videos": ["https://example.invalid/source.mp4"],
    "reference_images": ["https://example.invalid/background.png"],
    "omni_reference_task_type": "edit"
  }
}
```

Video edit requires automatic duration, automatic ratio, and a source video. It
must not include fixed dimensions or a fixed ratio.

### Video Extension

```json
{
  "model": "example-video-model",
  "prompt": "continue naturally for five seconds",
  "duration": 5,
  "metadata": {
    "ratio": "auto",
    "resolution": "720p",
    "reference_videos": ["https://example.invalid/source.mp4"],
    "omni_reference_task_type": "extend"
  }
}
```

Video extension requires exactly one source video, automatic ratio, no fixed
dimensions, and a positive integer extension duration.

## Optional Video Metadata

Public optional video fields live in `metadata`:

| Field | Type | Meaning |
|---|---|---|
| `generate_audio` | boolean | Generate synchronized audio |
| `human_review` | boolean | Enable provider human review or allowlisting flow |
| `watermark` | boolean | Add a result watermark |
| `output_format` | string | Output container such as `mp4` or `mov` |
| `return_last_frame` | boolean | Return the generated last frame as an image result |
| `scene_optimize` | string | Catalog-declared scene optimization option |
| `audio_setting` | string | Audio handling policy for video editing |
| `omni_reference_task_type` | string | Omni-reference subtype: `reference`, `edit`, or `extend` |

Preserve explicit `false` and `0` values. Unsupported options must be omitted
only when the public contract defines them as optional and no user value was
selected; otherwise return a parameter error.

## Responses

Image responses use an OpenAI-compatible `data` array. Multiple outputs remain
multiple array items. For `response_format=b64_json`, return `b64_json` rather
than an empty URL.

Video submission returns a gateway public task ID:

```json
{
  "id": "task_01H...",
  "task_id": "task_01H...",
  "status": "queued",
  "model": "example-video-model"
}
```

`id` and `task_id` are identical. Never expose the provider task ID.

Task queries return a result array:

```json
{
  "id": "task_01H...",
  "task_id": "task_01H...",
  "status": "succeeded",
  "progress": 100,
  "results": [
    {
      "type": "video",
      "url": "https://example.invalid/result.mp4",
      "format": "mp4"
    }
  ]
}
```

Public task states are `queued`, `running`, `succeeded`, `failed`, `cancelled`,
and `expired`. Provider states must be mapped inside the adapter.

Stable errors use this shape:

```json
{
  "error": {
    "code": "unsupported_media_combination",
    "message": "the channel does not support multiple reference images",
    "retryable": false,
    "upstream_request_id": "provider-request-id"
  }
}
```

Errors must not contain API keys, authorization headers, or full sensitive
request bodies. Report cancellation success only after the provider confirms the
specific task was cancelled. Otherwise return `task_cancellation_unsupported`.

## Model Catalog and Adapter Limits

The DramaClaw model catalog declares user-facing modes, ratios, resolutions,
duration bounds, and reference-media counts. The gateway adapter still enforces
provider limits. An omitted catalog limit does not mean the provider is
unlimited.

Models that expose file or web references declare `referenceFileMax`,
`referenceLinkMax`, and optional lowercase `referenceFileTypes`. The current
protocol allows at most one file or one link, and a positive maximum enables the
corresponding client input.

Model labels are display text. Stable catalog IDs and gateway model names drive
selection and execution. Adapters should use the configured upstream model
mapping rather than a mutable display label.

## Compatibility Rules

- Additive optional fields are compatible when old adapters safely ignore an
  absent value.
- Changing field meaning, media roles, inference priority, or response shape is
  breaking and requires a protocol version update.
- Provider-internal changes that preserve the public contract require only an
  adapter release.
- Historical New API media task shapes are not a compatibility target for
  DC-Media endpoints in this repository.

## Minimum Contract Tests

An implementation must cover:

- text image generation and multi-image editing;
- fixed and automatic ratios;
- text video, one reference image, strict first frame, and first/last frame;
- multiple reference images and video/audio/file/link references when claimed;
- automatic-duration video editing;
- mutual exclusion and unsupported combinations;
- preservation of explicit booleans and zero values;
- public/upstream task ID separation;
- public status, result array, error, and cancellation mapping.
