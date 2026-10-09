# DC-Media Development Documentation

This directory is for contributors building provider and model adapters for
`dramaclaw-gateway`. The canonical English public request and response contract
is [`dc-media-protocol.en.md`](../../../dc-media-protocol.en.md), paired with the
Chinese [`dc-media-protocol.md`](../../../dc-media-protocol.md). These documents
explain how to implement and test that contract.

## Reading Order

1. [`dc-media-protocol.en.md`](../../../dc-media-protocol.en.md): complete
   English protocol specification.
2. [`protocol.md`](./protocol.md): concise English implementation reference.
3. [`adapter-development.md`](./adapter-development.md): adapter types,
   registration, and conversion rules.
4. [`example-adapter.md`](./example-adapter.md): minimal asynchronous video
   adapter example.
5. [`model-onboarding-checklist.md`](./model-onboarding-checklist.md): model
   implementation and verification checklist.
6. [`../../providers/en/README.md`](../../providers/en/README.md): current
   registration status and contribution opportunities.
7. [`upstream-sync-release.md`](./upstream-sync-release.md): New API upstream
   synchronization and release requirements.

## Contribution Entry Points

- Use the **Provider adapter request** issue template before adding a provider.
- Follow [`CONTRIBUTING.md`](../../../CONTRIBUTING.md) for implementation and
  testing requirements.
- If the public protocol lacks a required capability, open a protocol issue
  before adding a provider-specific public field.
- To add a model to an existing provider, provide official model documentation,
  limits, and sanitized request examples when claiming the work.

## Ownership Boundaries

- The DramaClaw model catalog controls user-visible modes and limits.
- The DC-Media common layer owns stable fields, normalization, and media roles.
- Provider adapters own provider protocol, authentication, limits, and response
  mapping.
- `/api/channel/types` is the runtime source of truth for registered
  channel-level capabilities.
- `docs/providers/` records human verification and known gaps; it does not
  replace runtime metadata.

中文文档：[`../README.md`](../README.md)。
