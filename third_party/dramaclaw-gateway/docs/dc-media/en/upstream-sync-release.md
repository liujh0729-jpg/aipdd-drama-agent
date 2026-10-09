# Upstream Synchronization and Release Guide

`dramaclaw-gateway` uses `dramaclaw/dramaclaw-gateway` as `origin` and
`QuantumNous/new-api` as its upstream. Synchronization should continue to reuse
New API channels and foundational capabilities while keeping the
`DC-Media-Protocol` contract stable.

## Synchronizing Upstream

1. Create a dedicated synchronization branch from the latest `dramaclaw-gateway` `main`.
2. Fetch a stable New API upstream revision. Do not mix new adapter work into the same commit.
3. After merging or rebasing, resolve conflicts in `relay/common`, `relay/channel/task`, task models, and video routes first.
4. Preserve the New API license, NOTICE, copyright notices, source attribution, and commit history.
5. Check newly introduced channel numbers for conflicts with `dramaclaw-gateway` types such as `ChannelTypeComfyUI`.
6. Run both the original New API tests and the focused DC protocol tests.

Recommended verification:

```bash
go test ./... -run '^$'
go test ./relay/common ./relay/channel/task/hailuo \
  ./relay/channel/task/doubao ./relay/channel/task/comfyui
cd relaykit && GOWORK=off go build ./...
cd ../web && bun install --frozen-lockfile && bun run typecheck && bun run build
```

Tests that listen on local ports may fail in a restricted sandbox. Run the full
`go test ./...` suite on a normal development machine or in CI. A sandbox
restriction is not evidence that the code passes.

## Conflict Review Priorities

- Confirm that `TaskSubmitReq` still retains DC fields and parses `duration="auto"`.
- Confirm that `ValidateBasicTaskRequest` still invokes DC normalization and validation.
- Confirm that image requests still normalize `width/height` into a compatible `size`.
- Confirm that public task IDs remain separate from upstream task IDs.
- Confirm that new task-state handling treats `CANCELLED` as a terminal state.
- Confirm that the ComfyUI channel type, settings DTO, frontend configuration entry, and adapter registration remain consistent.
- Confirm that upstream video adapters have not reintroduced silent media loss or role promotion.

## Release

1. Confirm that the worktree is clean and that the version and release notes match the actual commits.
2. List DC protocol changes, adapter changes, compatibility notes, and known limitations in the release notes.
3. Build the backend and frontend, then verify container startup, database initialization, channel creation, and token-based calls.
4. Run smoke tests for T2V, single-image I2V, and at least one complex reference-media request.
5. Verify public task ID creation, task querying, result proxying, and cancellation error semantics.
6. Create the version tag and release artifacts. Do not package local databases, channel credentials, workflow media, or result files.

An incompatible protocol change requires a protocol version bump. A provider's
internal field change that leaves the public request and response unchanged
requires only a `dramaclaw-gateway` release and does not require a synchronized
DramaClaw release.
