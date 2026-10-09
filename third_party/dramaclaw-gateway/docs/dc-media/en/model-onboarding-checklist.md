# New Media Model Onboarding Checklist

## Model Catalog

- [ ] DramaClaw uses a stable `gateway_model`, and channel mappings do not change the client-facing model name.
- [ ] Supported generation modes, aspect ratios, resolutions, and minimum and maximum durations are declared.
- [ ] Limits for image, video, audio, file, and web-link references and allowed file types are declared.
- [ ] DramaClaw sends only `DC-Media-Protocol` fields and no provider-specific parameters.

## Protocol and Conversion

- [ ] A request without media inputs is handled as text-to-video.
- [ ] The top-level `image` maps only to the first frame.
- [ ] A single item in `reference_images` remains a reference image and is not promoted to the first frame.
- [ ] Multiple reference images are neither truncated nor ignored.
- [ ] First/last frames are not mixed with reference media.
- [ ] Video or audio references select the all-reference request shape.
- [ ] A reference file or web link selects all-reference, and file and link are never sent together.
- [ ] `duration=auto`, `ratio=auto`, and a reference video are handled as video editing.
- [ ] `duration=auto` video editing does not include a reference file or web link.
- [ ] Explicit `false`, `0`, `watermark`, and `generate_audio` values are not lost through `omitempty`.
- [ ] Unsupported combinations return a stable error before the upstream request.

## Audio Profiles

- [ ] Speech synthesis keeps `/v1/audio/speech` and `dto.AudioRequest`; no parallel route is added for the Speech Profile.
- [ ] Voice design uses `/v1/audio/voice-designs` and a dedicated request DTO instead of pretending to be speech synthesis.
- [ ] Base fields keep OpenAI Speech API semantics; extensions exist only in DC-Media `metadata`.
- [ ] Basic TTS, reference speech, and music are classified and validated consistently by the shared profile.
- [ ] Provider fields remain inside the channel adapter and do not enter the DramaClaw request contract.
- [ ] Models explicitly reject unsupported reference-audio, emotion, or music capabilities.
- [ ] Audio responses use binary, canonical URL, or canonical Base64 forms.
- [ ] Voice-design responses preserve `voice`, `target_model`, preview audio, and the upstream `request_id`.
- [ ] Voice-design models declare preview language, name, text-length, sample-rate, and format limits.
- [ ] The gateway preserves ownership between a custom voice and the credential that created it.
- [ ] Voice creation is settled as one successful call only after the provider returns a usable `voice`.

## Asynchronous Tasks

- [ ] The creation response exposes only the `dramaclaw-gateway` public task ID.
- [ ] The upstream task ID is stored only in private task data.
- [ ] Query states are mapped to stable public states.
- [ ] Successful results use array semantics, including single-result responses.
- [ ] Results that require authentication or private-network access are read through a controlled proxy.
- [ ] Local state is updated for cancellation only after provider confirmation.
- [ ] Concurrent updates between cancellation, success, failure, and timeout use CAS to avoid duplicate refunds.

## Testing and Verification

- [ ] Protocol happy-path, boundary, compatibility, and invalid-combination tests pass.
- [ ] Adapter conversion tests assert fields and media roles, not only request success.
- [ ] `go test ./relay/common ./relay/channel/task/<provider>` passes.
- [ ] `go test ./... -run '^$'` completes a repository-wide compilation check.
- [ ] `cd relaykit && GOWORK=off go build ./...` passes.
- [ ] `cd web && bun run typecheck && bun run build` passes.
- [ ] Task creation, querying, and result retrieval are verified end to end with a real provider account.
- [ ] The same model is verified end to end through the DramaClaw CE XiaHua node.

## Release Information

- [ ] Document the new channel type, default Base URL, and required configuration.
- [ ] Document provider model names, capability limits, and unsupported cases.
- [ ] State whether a database migration is required; adding a channel adapter usually requires no migration.
- [ ] No API keys, local paths from exported workflows, or generated media files are included.
