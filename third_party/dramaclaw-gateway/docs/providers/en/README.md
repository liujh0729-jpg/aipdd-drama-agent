# Channel Support Matrix

This matrix helps contributors find provider and capability work. It separates
"registered in the adapter factory" from "verified against the DC-Media
contract." Registration means a code path exists; it does not prove that every
image, video, or reference-media combination works.

`GET /api/channel/types` is the runtime source of truth for channel-level
capabilities. This table records human verification and known gaps.

| Channel | Adapter entry | Current status | Suggested contribution |
|---|---|---|---|
| ComfyUI | `relay/channel/task/comfyui/` | DC-Media local video adaptation exists | Add reusable workflows, media-node coverage, and end-to-end examples |
| MiniMax / Hailuo | `relay/channel/task/hailuo/` | H3 and related video work exists; verify per model | Add model limits, frame-role, and reference-media contract tests |
| VolcEngine / DoubaoVideo | `relay/channel/task/doubao/` | DC-Media video conversion exists | Document model boundaries and provider error mapping |
| fal.ai | `relay/channel/fal/`, `relay/channel/task/fal/` | Sync media and async task adapters are registered | Add model fixtures and sanitized real-call evidence |
| Ali | `relay/channel/task/ali/` | Registered; full DC-Media coverage needs audit | Claim a protocol coverage audit and add missing-field tests |
| Kling | `relay/channel/task/kling/` | Registered; full DC-Media coverage needs audit | Claim a protocol coverage audit and add missing-field tests |
| Jimeng | `relay/channel/task/jimeng/` | Registered; full DC-Media coverage needs audit | Check last frame, multiple references, and explicit booleans |
| Vertex AI | `relay/channel/task/vertex/` | Registered; full DC-Media coverage needs audit | Check last frame, multiple references, and result mapping |
| Vidu | `relay/channel/task/vidu/` | Registered; full DC-Media coverage needs audit | Check last frame, multiple references, and video references |
| Gemini | `relay/channel/task/gemini/` | Registered; full DC-Media coverage needs audit | Check media roles and provider capability declarations |
| OpenAI / Sora | `relay/channel/task/sora/` | Registered; full DC-Media coverage needs audit | Check asynchronous status and result proxying |
| SunoAPI | `relay/channel/task/suno/` | Audio task adapter registered; not a DC-Media video adapter | Extend only after a public audio task contract is defined |

## Status Definitions

- **Registered**: `relay/relay_adaptor.go` can construct the adapter.
- **DC-Media adaptation exists**: code explicitly consumes DC-Media fields and
  has focused conversion tests.
- **Coverage needs audit**: inherited code may run, but all claimed scenarios
  have not been proven against the current contract.
- **Verified model**: requires official docs, conversion tests, and a real
  DramaClaw CE end-to-end result.

## Adding a Record

When adding or extending a channel, update this table in the same pull request
and add `docs/providers/<provider>.md` with:

- official documentation and verification date;
- upstream model IDs;
- supported scenarios, ratios, resolutions, durations, and media limits;
- explicitly unsupported DC-Media fields;
- create, query, cancel, and result-read behavior;
- sanitized end-to-end verification evidence.

中文矩阵：[`../README.md`](../README.md)。
