# DC 媒体适配器开发指南

本文说明如何在 `dramaclaw-gateway` 中为 `DC-Media-Protocol v1` 增加供应商适配。
协议定义以仓库根目录的 `dc-media-protocol.md` 为准。

新渠道可以先生成安全骨架：

```bash
make new-adapter PROVIDER=example TYPE=64 MODE=task CAPABILITIES=video
```

生成器不会修改共享注册表，生成后的适配器在完成供应商转换前只会返回未实现错误。

## 开始之前

1. 确认 New API 现有适配器是否已经支持目标接口。
2. 只在公共字段无法正确转换时新增或扩展独立适配器。
3. 不根据模型名称猜测 DramaClaw 的业务模式。调用形态必须来自素材结构。
4. 不迁移商业虾驿的计费表达式、调用审计、结果归档或运营功能。

视频请求在 `relay/common.ValidateDCMediaTaskRequest` 中完成规范化、互斥校验和形态
推断。适配器必须继续保留素材角色：顶层 `image` 是首帧，
`metadata.reference_images` 始终是参考图，不能把第一张参考图提升为首帧。
`metadata.reference_file` 与 `metadata.reference_link` 是互斥的单值 URL，属于全能参考形态；
不支持该能力的适配器必须明确拒绝，不能忽略后继续提交。

独立音频生成继续使用 OpenAI 兼容的 `/v1/audio/speech` 和 `dto.AudioRequest`。
`relay/common.NormalizeDCMediaAudioRequest` 负责应用 DC-Media Audio Profile，识别基础
TTS、参考音频合成和音乐生成。不得为 Speech Profile 新增平行路由或请求 DTO。

音色设计使用 `/v1/audio/voice-designs` 和独立 DTO。它产生的是可复用音色资源和预览音频，不是 Speech Profile 的合成音频。适配器必须解析非空 `voice`，保留音色与创建凭证的归属关系，并在调用审计中省略大体积预览音频 Base64。若网关使用签名句柄封装上游音色 ID 和凭证归属，签名密钥必须在重启后保持稳定，并在所有实例间一致；密钥轮换需要兼容旧密钥，否则已有音色句柄会失效。

## 目录与注册

异步媒体适配器放在 `relay/channel/task/<provider>/`。一个完整适配器通常需要：

- 实现 `relay/channel.TaskAdaptor`；
- 在 `relay/relay_adaptor.go` 注册渠道类型；
- 在 `constant/channel.go` 声明渠道编号、名称和默认地址；
- 为渠道增加协议转换测试；
- 若供应商支持安全的按任务取消，实现可选的 `channel.TaskCanceller`。

`TaskAdaptor` 的职责分为验证、请求构造、提交响应解析、任务查询和查询响应解析。
公共任务 ID 与供应商任务 ID 必须分开保存，客户端只能看到 `dramaclaw-gateway` 生成的
`task_*` ID。

同步音频适配继续实现 `channel.Adaptor.ConvertAudioRequest` 和 `DoResponse`。公共层只
解析和校验 DC-Media `metadata`；供应商端点、鉴权、请求 DTO 和响应字段仍保留在
`relay/channel/<provider>/` 中。适配器必须明确拒绝不支持的音频请求形态，不能降级为
基础 TTS 后静默忽略参考音频或音乐参数。

## 请求转换

适配器应从 `relaycommon.GetTaskRequest(c)` 取得已经规范化的请求，并按以下顺序处理：

1. 调用 `ValidateDCMediaTaskRequest` 获取调用形态。
2. 校验供应商自己的素材数量、时长、比例和分辨率限制。
3. 将每类素材映射到供应商要求的字段或角色。
4. 保留显式的 `false`、`0` 和空白以外的有效值；可选标量使用指针类型。
5. 在请求上游前拒绝不支持的组合，不得删除素材后继续提交。

稳定的本地错误应使用 `TaskErrorWrapperLocal` 返回。常用错误码包括：

- `unsupported_media_combination`
- `invalid_media_request`
- `invalid_dimensions`
- `invalid_auto_duration`
- `task_cancellation_unsupported`

供应商错误可以保留经过处理的 request ID 和可读信息，但不能返回 API Key、鉴权头或
完整的敏感请求体。

## 异步任务

创建接口成功后，`DoResponse` 返回供应商任务 ID，由任务模型写入
`PrivateData.UpstreamTaskID`。`FetchTask` 只使用该上游 ID 查询，`ParseTaskResult`
统一映射为 `QUEUED`、`IN_PROGRESS`、`SUCCESS` 或 `FAILURE`。

结果 URL 写入任务私有数据。需要鉴权或位于局域网的结果应通过
`/v1/videos/{task_id}/content` 代理，不应把渠道密钥放进 URL。

取消只有在供应商确认指定任务已取消后才能成功。全局中断接口不能实现按任务取消。
不支持取消时返回 `task_cancellation_unsupported`，不能只修改本地状态。

## 测试要求

每个适配器至少覆盖：

- 文生视频和单图请求；
- 首帧与参考图角色不混用；
- 支持的多图、视频、音频、文件和网页组合；
- 参考文件与网页链接互斥，且不能与首帧/尾帧形态混用；
- 不支持组合在发上游前失败；
- 公共任务 ID 不泄漏供应商任务 ID；
- 状态、结果 URL 和错误映射；
- 若支持取消，覆盖排队、运行中、已结束和重复取消。

音频适配器还应覆盖：

- OpenAI 基础 TTS 字段转换；
- 支持的参考音频、情感或音乐 `metadata`；
- 显式 `false` 和输出格式不会丢失；
- 模型与音频请求形态不匹配时在调用上游前失败；
- 二进制、URL 或 Base64 上游结果被转换为规范响应。

常用验证命令：

```bash
go test ./relay/common ./relay/channel/task/<provider> ./relay/channel/<provider>
cd relaykit && GOWORK=off go build ./...
cd web && bun run typecheck && bun run build
```
