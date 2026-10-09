<!-- lang-switch -->
**English** · [简体中文](../../zh/guides/telemetry.md)

# Telemetry Notes / FAQ

> **In short: DramaClaw CE collects and reports no usage data by default.** No instrumentation SDK, no phone-home, no anonymous statistics. Below we describe the only optional "observability tracing," which is also off by default.

## What we don't do

- ❌ No built-in analytics/instrumentation SDK (no PostHog / Mixpanel / GA and the like).
- ❌ No reporting of usage, project content, or model-call records.
- ❌ No crash reporting by default.
- ✅ We also **proactively disable telemetry shipped with dependencies**: at runtime we force `COGNEE_TELEMETRY_ENABLED=false` to prevent the Cognee knowledge-graph library from reporting on its own.

Your drafts, keys, and outputs all stay on your machine (local filesystem + SQLite). The only outbound network traffic is calls to **the model gateway you configure yourself**—both the target and the key are under your control.

## The one optional feature: observability tracing (off by default)

For easier troubleshooting and performance analysis, CE integrates PydanticAI's optional tracing (built on Logfire / OpenTelemetry), which is **completely disabled by default**. It only turns on if you explicitly set one of the following environment variables:

| Environment variable | Effect |
|---|---|
| `NOVELVIDEO_ENABLE_LOGFIRE=1` | Enables PydanticAI tracing instrumentation |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=<your endpoint>` | Exports traces to **your own** OTLP/Jaeger collector |
| `LOGFIRE_TOKEN=<token>` | The **only** switch that sends data to the Logfire SaaS; if unset, nothing is sent |

Behavior details:

- None of the three set → tracing is not initialized; nothing happens.
- `OTEL_*` set but `LOGFIRE_TOKEN` not set → traces go only to **your own** collector, **not** to the Logfire SaaS.
- The `logfire` package not installed → it is skipped silently, with no error.
- Only when `LOGFIRE_TOKEN` is explicitly set does tracing data get sent to an external SaaS—and that is a choice you actively make.

> Want local observability without sending anything out: set `NOVELVIDEO_ENABLE_LOGFIRE=1` + `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` pointing at a self-hosted collector, and do not set `LOGFIRE_TOKEN`.

## Related

- [Configuring model providers](../getting-started/configuring-models.md) (you control the only outbound calls)
- [Environment variable reference](../reference/environment-variables.md) ｜ [Self-hosting handbook](self-hosting.md)
