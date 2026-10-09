# 为 dramaclaw-gateway 贡献代码

简体中文 | [English](./CONTRIBUTING.md)

感谢你帮助 DramaClaw 接入更多模型供应商。本文定义渠道和媒体模型贡献的工程契约。

## 开始之前

1. 搜索本仓库已有 Issue 和 Pull Request。
2. 新增渠道前先创建“渠道适配申请”Issue。
3. 必须依据供应商官方文档开发，不能只凭截图或推测实现接口。
4. 明确声明支持和不支持的图片、视频场景。
5. 禁止提交真实 API Key、私有媒体 URL、生成文件、数据库或未脱敏的供应商响应。

已有适配器的小范围修复，如果行为和边界已经明确，可以直接提交 PR。

## 架构契约

请求链路为：

```text
DramaClaw -> DC-Media -> 公共层标准化 -> 渠道适配器 -> 供应商
```

各层责任不同：

| 层级 | 责任 |
|---|---|
| DramaClaw 模型目录 | 用户可见模式、比例、分辨率、时长和素材上限 |
| DC-Media 公共层 | 稳定字段、值规范化、素材角色推断和互斥校验 |
| 渠道适配器 | 鉴权、供应商接口、请求结构、限制、轮询和错误转换 |
| 渠道元数据 | 稳定 provider ID 及 image、video 等协议级能力 |

不能为了简化单个供应商适配而把供应商专属字段加入公共协议，不能通过模型名称推断工作流
模式，也不能静默删除不支持的字段或素材。

协议以 [`dc-media-protocol.md`](./dc-media-protocol.md) 为准。

## 选择适配器类型

图片生成、图片编辑等同步接口放在 `relay/channel/<provider>/`，实现
`channel.Adaptor` 对应方法，并在构造供应商请求前复用公共标准化。

异步媒体任务放在 `relay/channel/task/<provider>/`，实现 `channel.TaskAdaptor` 的校验、
请求构造、任务提交、轮询和结果转换。只有供应商能够安全取消单个指定任务时，才能实现
`channel.TaskCanceller`。

开发前阅读[适配器开发指南](./docs/dc-media/adapter-development.md)和
[示例适配器](./docs/dc-media/example-adapter.md)。

## 生成适配器骨架

不要直接复制一个行为不相关的渠道，可以使用仓库生成器：

```bash
make new-adapter PROVIDER=example TYPE=64 MODE=task CAPABILITIES=video
make new-adapter PROVIDER=example_image TYPE=65 MODE=sync \
  NAME="Example Image" CAPABILITIES=image
```

参数说明：

- `PROVIDER`：稳定的小写机器标识，只能包含字母、数字和下划线；
- `TYPE`：拟使用的正整数渠道类型编号，已显式分配的编号会被拒绝；
- `MODE`：异步任务使用 `task`，同步转发使用 `sync`；
- `NAME`：可选的显示名称，用于生成供应商文档；
- `CAPABILITIES`：可选的逗号分隔协议能力；`task` 默认 `video`，`sync` 默认
  `image`。

生成结果可以编译，但所有上游操作默认返回未实现错误。生成器不会修改共享注册表；完成
生成的中英文供应商文档检查项之后，才能注册适配器。

## 注册检查清单

新增渠道通常涉及：

- `constant/channel.go`：稳定渠道类型编号、显示名称和默认 URL；
- `common/api_type.go`、`constant/api_type.go`：需要同步适配器时注册 API Type；
- `relay/relay_adaptor.go`：同步或异步适配器工厂；
- `relay/channel/<provider>/` 或 `relay/channel/task/<provider>/`：协议转换；
- `relay/channel_types.go`：不能增加第二份供应商列表，元数据必须从实际注册适配器发现；
- `web/src/features/channels/`：通用编辑器无法表达配置时增加前端入口；
- `docs/providers/`：记录支持场景、限制和缺口。

渠道类型编号和机器 `provider` ID 是公共兼容契约，不能复用或重排现有编号。Provider ID
应为稳定的小写标识，不能来自可修改的显示名称。

无法可靠推断的协议能力应通过 `CapabilityMetadataProvider` 显式声明。管理员配置的模型名
不能替代适配器能力声明。

## 转换规则

每个媒体适配器必须：

1. 消费经过标准化的 DC-Media 请求；
2. 保持顶层 `image` 为首帧，参考素材继续使用参考角色；
3. 校验供应商自己的素材数量、格式、时长和尺寸限制；
4. 需要时使用指针字段，保留显式 `false` 和 `0`；
5. 不支持的组合必须在请求供应商前失败；
6. 将供应商错误转换为稳定公共错误，且不能包含凭据；
7. 上游任务 ID 保持私有，客户端只能看到网关公开任务 ID；
8. 私有或需要鉴权的结果必须代理，不能把渠道密钥放进结果 URL。

禁止把第一张参考图提升为首帧、截断多图请求，或在忽略不支持素材后仍返回成功。

## 模型接入

渠道适配器实现的是供应商协议。除非上游接口固定模型，否则不应硬编码管理员使用的单个
模型别名。

每个完成验证的供应商模型需要记录：

- 上游模型 ID 及验证时使用的网关模型映射；
- 支持的输入场景；
- 比例、分辨率、时长和素材数量限制；
- 不支持的 DC-Media 字段或组合；
- 是否支持取消和私有结果代理；
- 验证日期及供应商文档版本。

使用[模型接入检查清单](./docs/dc-media/model-onboarding-checklist.md)逐项检查。

## 测试

测试必须断言真实的供应商请求和公共响应契约，不能只断言函数没有报错。

最低覆盖范围：

- 每个声明场景至少一个成功请求；
- 首帧和参考图角色不混用；
- 素材数量和时长边界；
- 不支持的组合在发出 HTTP 请求前失败；
- 供应商成功、失败、损坏响应和 request ID 转换；
- 异步任务排队、运行、成功和失败状态；
- 公开任务 ID 不泄漏上游任务 ID；
- 实现取消时覆盖取消行为；
- 错误及结果 URL 不包含凭据。

至少运行：

```bash
gofmt -w <changed-go-files>
go test ./relay/common ./relay/channel/task/<provider> ./relay/channel/<provider>
go test ./... -run '^$'
cd relaykit && GOWORK=off go build ./...
cd ../web && bun install --frozen-lockfile
bun run typecheck
bun run build
```

标记供应商已验证前，需要使用真实供应商账号完成端到端请求，再从 DramaClaw CE 媒体节点
调用同一模型，并在 PR 中提供脱敏后的命令和证据。

## Pull Request

渠道开发必须与上游同步及无关重构分开提交。使用仓库 PR 模板并关联渠道 Issue。

满足以下条件才达到可合入状态：

- 渠道注册和元数据完整；
- 声明支持的 DC-Media 场景有转换测试；
- 不支持场景显式失败；
- 公共任务 ID 与供应商任务 ID 分离；
- 状态、错误、结果和取消行为有测试；
- 已记录供应商文档和能力限制；
- 后端及受影响前端检查通过；
- 不包含密钥或本地运行资源。

当支持范围明确、不支持路径能够安全失败且支持矩阵记录了剩余缺口时，维护者可以合入部分
供应商能力。

## 公开仓库中的 CI 密钥

本仓库是公开 fork，持有用于发布 `claymorelab/dramaclaw-gateway` 镜像的 registry 凭证
（`DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN`）。为避免凭证暴露给 PR 代码：

- 只有由 `v*-dramaclaw.*` tag 的 `push` 或 `workflow_dispatch` 触发的工作流可以引用这些 secrets；
- `pull_request_target` 触发的工作流绝不能 checkout PR head，也不能把 secrets 传给运行 PR 代码的步骤；
- `pull_request_target` 触发的工作流其 `permissions:` 必须全部为只读；
- 第三方 action 一律钉完整 commit SHA。

## 许可证

贡献代码按仓库 GNU AGPLv3 许可证及适用 NOTICE 接收。请保留已有版权和许可证头。提交
Pull Request 表示你确认有权按照这些条款提供代码。
