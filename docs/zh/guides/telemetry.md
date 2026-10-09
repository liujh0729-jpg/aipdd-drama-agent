<!-- lang-switch -->
[English](../../en/guides/telemetry.md) · **简体中文**

# 遥测说明 / FAQ

> **简而言之:DramaClaw CE 默认不收集、不上报任何使用数据。** 没有埋点 SDK、没有 phone-home、没有匿名统计。下面说明唯一可选的"可观测追踪",以及它默认关闭。

## 我们不做什么

- ❌ 不内置任何分析/埋点 SDK(无 PostHog / Mixpanel / GA 之类)。
- ❌ 不上报使用量、项目内容、模型调用记录。
- ❌ 默认不做崩溃上报。
- ✅ 还**主动关掉依赖自带的遥测**:运行时强制 `COGNEE_TELEMETRY_ENABLED=false`,阻止 Cognee 知识图谱库自行上报。

你的原稿、密钥、产出都留在本机(本地文件系统 + SQLite)。唯一的对外网络是**你自己配置的模型网关**调用——目标和密钥都由你掌控。

## 唯一可选项:可观测追踪(默认关闭)

为方便排查与性能分析,CE 集成了 PydanticAI 的可选追踪(基于 Logfire / OpenTelemetry),**默认完全不启用**。只有你显式设置下列任一环境变量才会开:

| 环境变量 | 作用 |
|---|---|
| `NOVELVIDEO_ENABLE_LOGFIRE=1` | 打开 PydanticAI 追踪埋点 |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=<你的端点>` | 把 trace 导出到**你自己的** OTLP/Jaeger 收集器 |
| `LOGFIRE_TOKEN=<token>` | **唯一**会把数据发往 Logfire SaaS 的开关;不设则不发 |

行为细节:

- 三个都不设 → 追踪不初始化,什么都不发生。
- 设了 `OTEL_*` 但没设 `LOGFIRE_TOKEN` → trace 只进**你自己的**收集器,**不**发往 Logfire SaaS。
- 没装 `logfire` 包 → 直接跳过,不报错。
- 只有显式设置 `LOGFIRE_TOKEN` 才会把追踪数据发到外部 SaaS——这是你主动选择的结果。

> 想要本地可观测又不外发:设 `NOVELVIDEO_ENABLE_LOGFIRE=1` + `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 指向自建 collector,不要设 `LOGFIRE_TOKEN`。

## 相关

- [配置模型供应商](../getting-started/configuring-models.md)(你掌控唯一的对外调用)
- [环境变量参考](../reference/environment-variables.md) ｜ [自托管手册](self-hosting.md)
