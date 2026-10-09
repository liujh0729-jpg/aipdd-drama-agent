# Contributing to dramaclaw-gateway

[简体中文](./CONTRIBUTING.zh_CN.md) | English

Thank you for helping DramaClaw reach more model providers. This guide defines
the contribution contract for provider channels and media models.

## Before You Start

1. Search existing issues and pull requests in this repository.
2. Open a **Provider adapter request** issue before implementing a new channel.
3. Use official provider documentation. Do not implement an API from screenshots
   or assumptions alone.
4. State which image or video scenarios are supported and which are rejected.
5. Never include a real API key, private media URL, generated file, database, or
   unredacted provider response.

Small fixes to an existing adapter may go directly to a pull request when their
scope and expected behavior are already clear.

## Architecture Contract

The request path is:

```text
DramaClaw -> DC-Media -> common normalization -> provider adapter -> provider
```

The layers have different owners:

| Layer | Responsibility |
|---|---|
| DramaClaw model catalog | User-visible modes, ratios, resolutions, durations, and media limits |
| DC-Media common layer | Stable fields, normalization, media-role inference, and mutual exclusion |
| Provider adapter | Authentication, provider endpoint, provider payload, limits, polling, and errors |
| Channel metadata | Stable provider ID and protocol-level capabilities such as image or video |

Provider-specific fields must not be added to the public contract merely to make
one adapter easier to implement. Do not infer a workflow mode from a model name.
Do not silently omit an unsupported field or media item.

The canonical English protocol is
[`dc-media-protocol.en.md`](./dc-media-protocol.en.md). The corresponding
Chinese specification is [`dc-media-protocol.md`](./dc-media-protocol.md).

## Choose the Adapter Type

Use `relay/channel/<provider>/` for synchronous endpoints such as image
generation or editing. Implement the relevant methods on `channel.Adaptor` and
reuse common normalization before creating the provider payload.

Use `relay/channel/task/<provider>/` for asynchronous media jobs. Implement
`channel.TaskAdaptor` for validation, request construction, submission, polling,
and result mapping. Implement `channel.TaskCanceller` only when the provider can
cancel one specific upstream task safely.

Start from the [adapter development guide](./docs/dc-media/en/adapter-development.md)
and [example adapter](./docs/dc-media/en/example-adapter.md).

## Generate a Scaffold

Use the repository generator instead of copying an unrelated provider:

```bash
make new-adapter PROVIDER=example TYPE=64 MODE=task CAPABILITIES=video
make new-adapter PROVIDER=example_image TYPE=65 MODE=sync \
  NAME="Example Image" CAPABILITIES=image
```

Arguments:

- `PROVIDER`: stable lowercase machine ID; letters, digits, and underscores only;
- `TYPE`: proposed positive channel type ID, rejected when explicitly assigned;
- `MODE`: `task` for asynchronous jobs or `sync` for synchronous relays;
- `NAME`: optional display name used in the generated provider document;
- `CAPABILITIES`: optional comma-separated protocol capabilities. It defaults to
  `video` for `task` and `image` for `sync`.

The generated adapter compiles but every upstream operation returns a
not-implemented error. The generator does not modify shared registries. Follow
the generated bilingual provider documents before registering the adapter.

## Registration Checklist

A new provider normally touches:

- `constant/channel.go`: stable channel type ID, display name, and default URL;
- `common/api_type.go` and `constant/api_type.go`: synchronous API type mapping,
  when a synchronous adapter is required;
- `relay/relay_adaptor.go`: synchronous or task adapter factory registration;
- `relay/channel/<provider>/` or `relay/channel/task/<provider>/`: conversion;
- `relay/channel_types.go`: no provider list should be added here; metadata must
  be discovered from the registered adapter;
- `web/src/features/channels/`: channel configuration entry when the generic
  editor cannot represent the provider;
- `docs/providers/`: supported scenarios, limits, and known gaps.

Channel type IDs and machine provider IDs are public compatibility contracts.
Do not reuse or renumber an existing type. Provider IDs must be stable lowercase
identifiers and must not derive from a mutable display name.

Declare protocol capabilities through `CapabilityMetadataProvider` when they
cannot be inferred reliably. Model names configured by an administrator are not
a substitute for adapter capability declarations.

## Conversion Rules

Every media adapter must:

1. consume the normalized DC-Media request;
2. preserve `image` as first frame and keep reference media in their reference
   roles;
3. validate provider-specific counts, formats, durations, and dimensions;
4. preserve explicit `false` and `0` values by using pointer fields where needed;
5. reject unsupported combinations before contacting the provider;
6. map provider errors to stable public errors without credentials;
7. keep upstream task IDs private and expose only gateway public task IDs;
8. proxy private or authenticated result assets instead of leaking channel keys.

An adapter must not turn the first reference image into a first frame, truncate a
multi-image request, or report success after ignoring unsupported media.

## Model Onboarding

The channel adapter implements a provider protocol. It should not hard-code a
single administrator model alias unless the provider endpoint requires a fixed
model.

For every tested provider model, document:

- upstream model ID and the gateway model mapping used for verification;
- supported input scenarios;
- ratio, resolution, duration, and media-count limits;
- unsupported DC-Media fields or combinations;
- whether cancellation and private result proxying work;
- the date and provider documentation revision used for verification.

Use the [model onboarding checklist](./docs/dc-media/en/model-onboarding-checklist.md).

## Testing

Tests must assert the actual provider request and public response contract, not
only that a function returned no error.

Minimum adapter coverage:

- one successful request for every claimed scenario;
- first-frame and reference-image roles remain distinct;
- boundary media counts and durations;
- unsupported combinations fail before an HTTP request;
- provider success, failure, malformed response, and request ID mapping;
- asynchronous queued, running, succeeded, and failed states;
- public task ID does not expose the upstream task ID;
- cancellation behavior when implemented;
- no credential appears in an error or result URL.

Run at least:

```bash
gofmt -w <changed-go-files>
go test ./relay/common ./relay/channel/task/<provider> ./relay/channel/<provider>
go test ./... -run '^$'
cd relaykit && GOWORK=off go build ./...
cd ../web && bun install --frozen-lockfile
bun run typecheck
bun run build
```

Before marking a provider as verified, run an end-to-end request with a real
provider account and then run the same model from a DramaClaw CE media node.
Record commands and sanitized evidence in the pull request.

## Pull Requests

Keep provider work separate from upstream synchronization and unrelated
refactors. Use the repository pull request template and link the provider issue.

A provider adapter is ready to merge when:

- registration and channel metadata are complete;
- claimed DC-Media scenarios have conversion tests;
- unsupported scenarios fail explicitly;
- provider and public task IDs are separated;
- status, error, result, and cancellation behavior are covered;
- provider documentation and limitations are recorded;
- backend and affected frontend checks pass;
- no secret or local runtime asset is included.

Maintainers may merge partial provider support when its scope is explicit, the
unsupported paths fail safely, and the support matrix marks the remaining gaps.

## CI Secrets on a Public Fork

This repository is a public fork and holds registry credentials
(`DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN`) for publishing
`claymorelab/dramaclaw-gateway`. To keep them out of reach of pull request code:

- only workflows triggered by `push` on a `v*-dramaclaw.*` tag or by
  `workflow_dispatch` may reference those secrets;
- a workflow triggered by `pull_request_target` must never check out the pull
  request head and must never pass secrets to steps that run pull request code;
- a workflow triggered by `pull_request_target` must also keep its `permissions:` read-only;
- keep third-party actions pinned to a full commit SHA.

## Licensing

Contributions are accepted under the repository's GNU AGPLv3 license and
applicable notices. Preserve existing copyright and license headers. By opening a
pull request, you confirm that you have the right to submit the contribution
under those terms.
