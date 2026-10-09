# 上游同步与发布说明

`dramaclaw-gateway` 的 `origin` 是 `dramaclaw/dramaclaw-gateway`，上游是
`QuantumNous/new-api`。同步目标是持续复用 New API 的渠道和基础能力，同时保持
`DC-Media-Protocol` 契约稳定。

## 同步上游

1. 从最新 `dramaclaw-gateway` `main` 创建独立同步分支。
2. 获取 New API 上游稳定节点，不在同一提交中混入新适配器功能。
3. 合并或变基后优先解决 `relay/common`、`relay/channel/task`、任务模型和视频路由冲突。
4. 保留 New API 的许可证、NOTICE、版权、来源说明和提交历史。
5. 检查新增渠道编号，避免与 `ChannelTypeComfyUI` 等 `dramaclaw-gateway` 类型冲突。
6. 运行 New API 原测试和 DC 协议定向测试。

建议验证：

```bash
go test ./... -run '^$'
go test ./relay/common ./relay/channel/task/hailuo \
  ./relay/channel/task/doubao ./relay/channel/task/comfyui
cd relaykit && GOWORK=off go build ./...
cd ../web && bun install --frozen-lockfile && bun run typecheck && bun run build
```

需要监听本地端口的测试在受限沙箱中可能失败，应在正常开发机或 CI 中补跑完整
`go test ./...`，不能把沙箱限制当成代码通过。

## 冲突检查重点

- `TaskSubmitReq` 是否仍保留 DC 字段和 `duration="auto"` 解析。
- `ValidateBasicTaskRequest` 是否仍调用 DC 规范化与校验。
- 图片请求是否仍把 `width/height` 规范化为兼容 `size`。
- 任务公开 ID 与上游 ID 是否仍分离。
- 新任务状态是否把 `CANCELLED` 当作终态。
- ComfyUI 渠道类型、设置 DTO、前端配置入口和适配器注册是否仍一致。
- 上游视频适配器是否重新引入了素材静默丢弃或角色提升。

## 发布

1. 确认工作区干净，版本号和发布说明与实际提交一致。
2. 发布说明列出 DC 协议变化、适配器变化、兼容性和已知限制。
3. 构建后端和前端，验证容器启动、数据库初始化、渠道创建和令牌调用。
4. 使用 T2V、单图 I2V 和至少一种复杂素材请求执行冒烟测试。
5. 验证公开任务 ID 的创建、查询、结果代理和取消错误语义。
6. 创建版本标签和发行物；不要把本地数据库、渠道密钥、Workflow 素材或结果文件打包。

协议发生不兼容变化时必须升级协议版本。供应商内部字段变化但公共请求和响应不变时，
只发布 `dramaclaw-gateway`，不要求同步发布 DramaClaw。
