# DC-Media Protocol: Unified Request Contract for Media Models

[简体中文](./dc-media-protocol.md) | English

> Status: Draft
>
> Scope: DramaClaw image, video, and audio model integration
>
> Protocol version: 1.2-draft

This document is the English counterpart of
[`dc-media-protocol.md`](./dc-media-protocol.md). Both documents define the same
public contract. A protocol change MUST update both language versions and the
contract tests in the same pull request.

The key words **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, and **MAY** in
this document describe protocol requirements.

## 1. Goals and Boundaries

DC-Media gives DramaClaw a provider-neutral image and asynchronous video
contract. `dramaclaw-gateway` validates and normalizes that contract before a
provider adapter creates an upstream request.

Provider field names, authentication headers, task status names, and model
quirks do not belong in the public request. The protocol does not include a
top-level `mode`; the gateway derives a provider call shape from media roles.

Responsibilities are divided as follows:

- **Model catalog:** declares model identity, supported modes, resolutions,
  ratios, media-count and duration limits, and optional model parameters.
- **DramaClaw:** validates user input, resolves the model catalog, creates a
  canonical request, and keeps quoted and executed parameters consistent.
- **Unified model gateway:** validates public fields, normalizes values, and
  converts the request into a provider representation.
- **Provider adapter:** maps fields and enforces provider limits. It MUST NOT
  change media roles or silently discard media.

This protocol does not define user-credit pricing, currency conversion,
provider purchase cost, secrets, authentication headers, deployment topology,
or undocumented provider parameters.

## 2. Design Principles

1. One semantic value has one public representation. Automatic ratio is always
   `auto` in new requests.
2. Provider differences stay in the gateway. DramaClaw does not switch between
   `auto`, `adaptive`, `-1`, or omission by provider.
3. Fields express media roles, and media combinations determine call shape. A
   reference image MUST NOT be promoted to a first frame.
4. The model catalog is authoritative for capabilities. Frontend restrictions
   do not replace backend validation.
5. Quotation and execution share one normalized model, resolution, duration,
   and media-count result.
6. New code emits canonical fields only. Legacy fields may be read only at an
   explicit compatibility boundary.
7. Audio extensions remain explicit. Speech synthesis keeps the OpenAI Speech
   base fields, while voice design uses a separate resource-creation endpoint
   and MUST NOT be represented as speech synthesis.

## 3. Endpoints and Base Structures

### 3.1 Image Endpoints

#### 3.1.1 Text-to-Image Endpoint

`POST /images/generations` generates images without reference media. Fixed
ratios send `width`, `height`, and the matching `metadata.ratio`; automatic
ratios send `metadata.ratio = "auto"` without dimensions.

#### 3.1.2 Image Editing and Reference-Image Endpoint

`POST /images/edits` handles image editing or generation with one or more
references. References use the top-level `image` array. Provider fields such as
`image_url` and `image_urls` are not part of the public request.

### 3.2 Video Endpoint

| Endpoint | Purpose |
|---|---|
| `POST /video/generations` | Submit an asynchronous video task |

### 3.3 Audio Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /audio/speech` | Speech synthesis, reference speech, or music generation |
| `POST /audio/voice-designs` | Create a reusable voice from a natural-language description |

The speech endpoint reuses the OpenAI Speech request shape and places explicit
DC-Media extensions in `metadata`. Voice design returns a JSON resource and
therefore has its own request DTO and response contract.

### 3.4 Image and Video Common Top-Level Fields

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

### 3.5 Speech Profile Top-Level Fields

| Field | Type | Required | Meaning |
|---|---|---:|---|
| `model` | string | yes | Gateway speech or music model name |
| `input` | string | yes | Text to synthesize or music description |
| `voice` | string | no | Catalog-supported voice name or ID |
| `response_format` | string | no | Requested audio format; defaults to `mp3` |
| `speed` | number | no | Speech speed when supported |
| `metadata` | object | no | Reference audio, emotion, or music extensions |

### 3.6 Voice Design Profile Top-Level Fields

| Field | Type | Required | Meaning |
|---|---|---:|---|
| `model` | string | yes | Gateway voice-design model name |
| `target_model` | string | yes | Speech model that will consume the created voice |
| `preferred_name` | string | yes | Stable requested voice name or prefix |
| `voice_prompt` | string | yes | Chinese or English description of voice traits |
| `preview_text` | string | yes | Text spoken by the preview audio |
| `language` | string | no | Preview-text language; allowed values come from the model catalog |
| `sample_rate` | integer | no | Preview-audio sample rate |
| `response_format` | string | no | Preview format; defaults to `wav` |

Voice design does not use the Speech Profile's `input`, `voice`, `speed`, or
`metadata.audio_url` fields.

## 4. Value Normalization

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

## 5. Geometry Contract

### 5.1 Fixed Ratio

For a fixed ratio, preserve ratio semantics and send dimensions when they are
known:

```json
{
  "width": 2048,
  "height": 1152,
  "metadata": {
    "ratio": "16:9",
    "resolution": "2k"
  }
}
```

- `metadata.ratio` is the primary frame semantic.
- `metadata.resolution` is a quality or billing tier, not an aspect ratio.
- `width`, `height`, `ratio`, and `resolution` SHOULD come from the same size
  mapping.
- `width` and `height` MUST appear together and be positive integers.
- A fixed ratio MUST remain present when dimensions cannot be calculated.
- The gateway SHOULD tolerate reasonable codec-alignment rounding.
- New requests MUST NOT also send deprecated `size` or top-level
  `aspect_ratio` fields.

### 5.2 Automatic Ratio

Automatic ratio has one representation:

```json
{
  "metadata": {
    "ratio": "auto",
    "resolution": "2k"
  }
}
```

An automatic-ratio request MUST omit `width`, `height`, and fixed `size`. The
gateway derives the provider ratio from input media and provider rules.

## 6. Image Requests

### 6.1 Text-to-Image

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

### 6.2 Image Editing and Multi-Image Reference

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

### 6.3 Optional Image Parameters

Optional public image fields include `quality`, `output_format`,
`input_fidelity`, and top-level `watermark`. Omit options the user did not select
instead of guessing provider defaults. With `ratio=auto`, do not derive fixed
dimensions from the first reference image.

## 7. Video Generation Modes

Video uses `POST /video/generations`.

### 7.1 Mode-to-Field Mapping

The model catalog uses these canonical business-mode names:

| Mode | Meaning |
|---|---|
| `text_to_video` | Generate video from text only |
| `image_to_video` | Use one image as content reference without locking the first frame |
| `first_frame` | Use one image as the strict first frame |
| `first_last_frame` | Use a first frame, last frame, or both |
| `image_reference` | Use one or more images as style, identity, or content references |
| `all_reference` | Use multimodal image, video, audio, file, or web references |
| `video_edit` | Edit a source video |
| `video_extend` | Continue a source video with newly generated content |

Modes are internal DramaClaw business semantics and are not transmitted as a
top-level `mode` field. Models that must distinguish multimodal reference,
video editing, and video extension use `metadata.omni_reference_task_type` as
a stable task subtype. Modes map to public fields as follows:

| Mode | Top-level `image` | `last_frame_image` | `reference_images` | `reference_videos` | `reference_audios` | `reference_file/link` | Ratio | Duration |
|---|---|---|---|---|---|---|---|---|
| `text_to_video` | omitted | omitted | omitted | omitted | omitted | omitted | fixed or catalog value | fixed |
| `image_to_video` | omitted | omitted | exactly 1 | omitted | omitted | omitted | fixed or catalog value | fixed |
| `first_frame` | first frame | omitted | omitted | omitted | omitted | omitted | `auto` | fixed |
| `first_last_frame` | optional first frame | optional last frame | omitted | omitted | omitted | omitted | `auto` | fixed |
| `image_reference` | omitted | omitted | 1 or more | omitted | omitted | omitted | fixed or catalog value | fixed |
| `all_reference` | omitted | omitted | optional | optional | optional | optional, mutually exclusive | fixed or catalog value | fixed |
| `video_edit` | omitted | omitted | optional | source and allowed references | optional | omitted | `auto` | `auto` |
| `video_extend` | omitted | omitted | omitted | exactly 1 source video | omitted | omitted | `auto` | fixed |

Reference fields in this table live under `metadata`. Exactly one reference
image normalizes to image-to-video. Multiple images without video or audio
normalize to image reference. Any reference video, audio, file, or link with
fixed duration normalizes to all reference. A provider that cannot support the
inferred shape MUST return an explicit unsupported error instead of dropping
extra media.

Omni-reference task subtypes use this fixed mapping:

| DramaClaw mode | `metadata.omni_reference_task_type` |
|---|---|
| `all_reference` | `reference` |
| `video_edit` | `edit` |
| `video_extend` | `extend` |

This value is not kept inside a provider request's nested `metadata` object.
An adapter that supports it converts the value to the provider-required
location. Doubao/VolcEngine sends it as a top-level upstream request field.

### 7.2 Gateway Call-Shape Inference

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

DramaClaw model modes map to public fields, but mode names are not transmitted.
The gateway validates mutual exclusion and derives a generic media call shape
in this order:

1. `duration="auto"`, `metadata.ratio="auto"`, and at least one reference video:
   video edit;
2. a last frame, optionally with a first frame: first/last-frame video;
3. a top-level image: first-frame video;
4. reference video, audio, file, or link: multimodal reference;
5. more than one reference image: image reference;
6. exactly one reference image: image-to-video;
7. no media input: text-to-video.

The derived shape chooses a provider endpoint, workflow, or payload. It does not
recover the original DramaClaw UI mode. Automatic-duration video editing cannot
include a reference file or link. If the provider does not support the shape,
reject the request instead of dropping media or degrading modes.
Video extension remains multimodal reference at the generic media-shape layer.
A supporting adapter MUST distinguish and convert it using the explicit
`omni_reference_task_type="extend"` value. It MUST NOT infer extension from a
reference video and fixed duration alone.

### 7.3 First Frame

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

### 7.4 First and Last Frame

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

### 7.5 Image Reference and All Reference

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
editing. Models that support omni-reference task subtypes also send
`omni_reference_task_type="reference"`.

### 7.6 Reference File or Link

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
mixed with first- or last-frame inputs. Either field selects the fixed-duration
`all_reference` call shape and cannot be combined with `duration="auto"`.
DC-Media does not embed raw file bytes in a video request; clients must provide
a provider-accessible URL.

### 7.7 Video Edit

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

### 7.8 Video Extension

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
dimensions, and a positive integer `duration` describing the generated
extension. `metadata.omni_reference_task_type="extend"` is required so a fixed
duration source-video request is not interpreted as ordinary multimodal
reference.

## 8. Video Duration

### 8.1 Fixed Duration

A fixed duration is a positive integer number of seconds:

```json
{
  "duration": 5
}
```

It MUST satisfy catalog `minDuration` and `maxDuration` limits and MUST NOT be
combined with `seconds`.

### 8.2 Automatic Duration

Automatic duration has one representation:

```json
{
  "duration": "auto"
}
```

Automatic duration is currently reserved for video editing. New requests MUST
NOT use `-1`, `"-1"`, or an empty value. The gateway MAY translate `"auto"`
into an omitted field, `-1`, or another provider representation.

## 9. Optional Video Parameters

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
`omni_reference_task_type` is only used by catalog-declared omni-reference
models and must agree with the actual media, ratio, and duration shape.

## 10. DC-Media Audio Profiles

### 10.1 Speech Profile

`POST /v1/audio/speech` uses the OpenAI Speech fields `model`, `input`,
`voice`, `response_format`, and `speed`. Reference speech, emotion controls,
and music options are explicit `metadata` extensions. Adapters MUST reject
unsupported extensions instead of silently falling back to basic TTS.

The canonical extension fields are:

| `metadata` field | Type | Meaning |
|---|---|---|
| `audio_url` | string | HTTP(S) audio URL or audio Data URL used as a voice reference |
| `should_use_prompt_for_emotion` | boolean | Enable text-directed emotion control |
| `emotion_prompt` | string | Emotion, tone, or performance description |
| `music_length_ms` | integer | Requested music duration from 3,000 through 600,000 ms |
| `force_instrumental` | boolean | Request instrumental output |
| `respect_sections_durations` | boolean | Preserve section durations from the prompt |
| `output_format` | string | Provider-independent encoding option declared by the catalog |

Speech responses are complete audio bytes with an accurate `Content-Type`, or
a canonical JSON URL/Base64 response when the gateway does not download the
provider result.

### 10.2 Voice Design Profile

Voice design is a synchronous JSON resource-creation endpoint:

```http
POST /v1/audio/voice-designs
Content-Type: application/json
```

```json
{
  "model": "qwen-voice-design",
  "target_model": "qwen3-tts-vd-2026-01-26",
  "preferred_name": "custom_voice",
  "voice_prompt": "A lively young female voice with a naturally rising tone.",
  "preview_text": "Welcome to our live stream.",
  "language": "en",
  "sample_rate": 24000,
  "response_format": "wav"
}
```

A successful response uses this contract:

```json
{
  "id": "voice_xxx",
  "object": "audio.voice",
  "model": "qwen-voice-design",
  "target_model": "qwen3-tts-vd-2026-01-26",
  "voice": "voice_xxx",
  "preview_audio": {
    "data": "UklGRg...",
    "format": "wav"
  },
  "request_id": "provider-request-id"
}
```

Rules:

- `model`, `target_model`, `preferred_name`, `voice_prompt`, and
  `preview_text` MUST be non-empty strings.
- The catalog MUST allow `target_model` for the selected design model.
- `language` MUST match `preview_text`; omission uses the model catalog's default.
- Voice descriptions MUST describe traits and MUST NOT request imitation of an
  identifiable real person. Catalog and adapter limits apply to both texts.
- A voice can be scoped to the provider account or credential that created it.
  The gateway MUST retain that ownership and MUST NOT route later speech calls
  to a credential that cannot access the voice.
- Clients store and submit only the gateway-returned `voice`; provider account,
  channel, and internal resource identifiers remain gateway concerns.
- Audit logs SHOULD omit or summarize large preview-audio Base64 payloads.
- A failed creation MUST NOT return a usable `voice` or settle a successful
  per-creation charge.

### 10.3 Audio Adapter Responsibilities

Adapters explicitly map every supported Speech or Voice Design field, validate
provider limits before dispatch, preserve explicit zero and false values, and
return stable sanitized errors. Voice Design adapters also parse a non-empty
`voice`, preserve credential ownership, and normalize preview audio.

## 11. Responses and Task Status

### 11.1 Image Response

Image responses use an OpenAI-compatible `data` array. Multiple outputs remain
multiple array items. For `response_format=b64_json`, return `b64_json` rather
than an empty URL.

### 11.2 Video Task Submission Response

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

### 11.3 Video Task Query Response

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

### 11.4 Task Status

Public task states are `queued`, `running`, `succeeded`, `failed`, `cancelled`,
and `expired`. Provider states must be mapped inside the adapter.

| Status | Meaning |
|---|---|
| `queued` | Created and waiting for provider processing |
| `running` | Processing |
| `succeeded` | Successful and results are available |
| `failed` | Failed and will not continue |
| `cancelled` | Cancelled |
| `expired` | Task or temporary results expired |

Provider values such as `processing`, `submitted`, or `SUCCESS` MUST NOT become
new public statuses.

### 11.5 Error Response

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
request bodies.

- `code` is stable and MUST NOT be replaced with provider error prose.
- `message` MAY contain sanitized provider details for diagnosis.
- `retryable` states whether retrying the same parameters may succeed.
- `upstream_request_id` SHOULD be returned when the provider supplies one.

### 11.6 Task Cancellation

Report cancellation success only after the provider confirms the specific task
was cancelled. Otherwise return `task_cancellation_unsupported`.

## 12. Model Catalog Contract

The DramaClaw model catalog declares user-facing modes, ratios, resolutions,
duration bounds, and reference-media counts. The gateway adapter still enforces
provider limits. An omitted catalog limit does not mean the provider is
unlimited.

```json
{
  "catalog_id": "stable-catalog-id",
  "media_type": "video",
  "label": "Example Video Model",
  "gateway_model": "example-video-model",
  "enabled": true,
  "config": {
    "supportedModes": [
      "text_to_video",
      "first_frame",
      "first_last_frame"
    ],
    "resolutionOptions": ["480p", "720p"],
    "ratioOptions": ["16:9", "9:16", "1:1", "auto"],
    "minDuration": 4,
    "maxDuration": 15,
    "referenceImageMax": 2,
    "referenceVideoMax": 0,
    "referenceAudioMax": 0,
    "referenceFileMax": 1,
    "referenceLinkMax": 1,
    "referenceFileTypes": ["pdf", "docx", "xlsx", "pptx", "txt", "md"],
    "supportsGenerateAudio": true,
    "humanReview": true
  }
}
```

Model labels are display text. Stable catalog IDs and gateway model names drive
selection and execution. Adapters should use the configured upstream model
mapping rather than a mutable display label.

- `catalog_id` is immutable across label and gateway-model changes.
- `gateway_model` is the model name sent to the unified gateway.
- `supportedModes` uses the canonical names defined above.
- Disabled models do not appear in new-task options, and the backend MUST reject
  direct calls.
- The frontend provides early feedback; the backend revalidates all modes,
  tiers, and media limits.

### 12.1 Media Limits

The catalog MAY declare:

- `referenceImageMax`
- `referenceVideoMax`
- `referenceAudioMax`
- `referenceFileMax`
- `referenceLinkMax`
- `referenceFileTypes`
- `referenceAudioMinSeconds`
- `referenceAudioMaxSeconds`
- `referenceAudioTotalMinSeconds`
- `referenceAudioTotalMaxSeconds`
- `referenceVideoMinSeconds`
- `referenceVideoMaxSeconds`
- `referenceVideoTotalMinSeconds`
- `referenceVideoTotalMaxSeconds`

Per-item and total limits are validated separately. `referenceFileMax` and
`referenceLinkMax` currently have a protocol maximum of one; a positive value
enables the corresponding client input and zero means unsupported.
`referenceFileTypes` contains lowercase extensions without a leading dot. An
omitted limit means DramaClaw adds no catalog restriction; it does not mean the
provider has no limit.

### 12.2 Declarative Model Parameters

Additional options MUST map to safe public request paths:

```json
{
  "request": {
    "endpoint": "video/generations",
    "parameters": [
      {
        "key": "output_format",
        "label": "Output format",
        "control": "select",
        "requestPath": "metadata.output_format",
        "options": ["mp4", "mov"],
        "default": "mp4",
        "required": false,
        "modes": ["text_to_video", "video_edit"]
      }
    ]
  }
}
```

Declarative configuration MUST NOT override `model`, `prompt`, authentication
headers, API keys, or gateway addresses. The backend validates control type,
range, options, and mode restrictions. Optional unselected values are omitted.

### 12.3 Audio Model Catalog

Audio catalog entries use `media_type = "audio"`. Speech and music models use
`audio/speech`; voice-design models use `audio/voice-designs`. A voice-design
entry declares allowed `target_model` values, prompt limits, preview limits,
sample rates, and response formats. Clients cannot bypass those declarations
by supplying an arbitrary upstream target model.

## 13. Quotation and Execution Consistency

This contract does not define prices, but it requires consistent billing input:

- Quotation, pre-consumption, and execution use the same `catalog_id`.
- The gateway model is resolved from that catalog item; the client does not
  submit a separate billing model.
- Resolution and fixed duration use the normalized values actually executed.
- Product billing derives a quantity for `duration = "auto"` from input media
  and MUST NOT treat the string `auto` as a number.
- A video-input tier applies only when this request actually contains
  `reference_videos`, not when the model merely permits them.
- Batch quantity comes from actual generation units and is not replaced with
  character count or duration merely because the billing unit is `call`.

User-credit accounting and provider-cost accounting MAY use different
settlement bases, but that difference must be an explicit product decision.

Voice design is billed per successfully created voice. The quantity for one
request is `1`; a response without a usable `voice` MUST NOT settle a successful
creation charge.

## 14. Validation and Errors

Errors that can be determined locally MUST be rejected before task creation or
provider invocation, including:

- a missing or disabled model;
- an unsupported selected mode or inferred call shape;
- an unconfigured resolution, ratio, or declarative parameter;
- one missing dimension or dimensions that clearly conflict with the ratio;
- automatic ratio combined with fixed dimensions;
- media count or per-item/total duration over the limit;
- a missing first frame, key frame, or source video required by the mode;
- a reference image placed in the first-frame field;
- `catalog_id` inconsistent with the execution model.

Errors use stable codes and readable messages. Business error classification
MUST NOT depend on parsing provider prose.

Voice design requests are rejected locally when required fields are absent, the
target model is not catalog-approved, text limits are exceeded, or the sample
rate or response format is unsupported.

## 15. Compatibility and Deprecation

These are compatibility inputs and MUST NOT be emitted by new code:

| Legacy representation | Canonical representation |
|---|---|
| `adaptive` | `auto` |
| `duration: -1` | `duration: "auto"` |
| `seconds` | `duration` |
| `size` | `width` + `height` |
| top-level `aspect_ratio` | `metadata.ratio` |
| `image_url` / `first_frame_image` | top-level `image` |
| `end_image_url` | `metadata.last_frame_image` |
| `image_urls` | image top-level `image` or video `metadata.reference_images` |
| `video_urls` | `metadata.reference_videos` |
| `audio_urls` | `metadata.reference_audios` |

- Compatibility conversion exists only in explicit boundary functions.
- A normalized request MUST NOT contain duplicate legacy and canonical fields.
- Legacy backend names MAY map to `newapi_*`, but new provider-direct backends
  SHOULD NOT be added.
- Compatibility input is removed only after historical projects, queued
  messages, and external API callers no longer depend on it.
- Additive optional fields are compatible when old adapters safely ignore an
  absent value.
- Changing field meaning, media roles, inference priority, or response shape is
  breaking and requires a protocol version update.
- Historical New API media task shapes are not a compatibility target for
  DC-Media endpoints in this repository.

## 16. New Model Onboarding Checklist

At minimum, a new image or video model requires:

1. Create a stable `catalog_id` and configure the media type and
   `gateway_model`.
2. Declare every supported mode without inferring capability from model names.
3. Configure resolutions, ratios, durations, reference file types, and media limits.
4. Expose model-specific options only through declarative parameters.
5. Implement provider adaptation in the gateway, not as a provider branch in
   DramaClaw.
6. Verify fixed ratios preserve ratio, resolution, and matching dimensions.
7. Verify automatic ratios omit dimensions and automatic duration emits only
   `duration: "auto"`.
8. Verify first frame, last frame, image/video/audio references, reference files,
   and web links use the correct fields.
9. Align frontend feedback, backend validation, and gateway constraints.
10. Verify quotation and execution use the same model and normalized values.
11. Add request-contract coverage for every supported mode and rejection tests
    for invalid combinations.
12. State migration, configuration, provider, and billing impact in the PR.

## 17. Minimum Contract Tests

An implementation must cover:

- text image generation, single-image editing, and multi-image reference;
- fixed and automatic image ratios;
- text video, one reference image, strict first frame, first/last frame, image
  reference, all reference, and video edit;
- call-shape inference for one image, multiple images, video/audio/file/link
  references, and automatic-duration video edit;
- normalization from `adaptive` to `auto` and resolution-case normalization;
- fixed-ratio/dimension consistency and automatic-ratio mutual exclusion;
- fixed and automatic durations;
- explicit `true`, explicit `false`, and omission for optional booleans;
- catalog mode rejection and media count/duration boundaries;
- mutual exclusion for reference files and links and their exclusion from
  first-frame, last-frame, and automatic-duration video-edit shapes;
- legacy input producing canonical output only;
- quotation parameters consistent with execution parameters;
- public/upstream task ID separation;
- canonical public statuses, result arrays, stable errors, and cancellation.

Any addition, removal, or semantic change to a public field MUST update both
language versions of this document and the contract tests before provider
adapters are changed.
