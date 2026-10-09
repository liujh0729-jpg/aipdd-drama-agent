# DC-Media 开发文档

本目录面向为 `dramaclaw-gateway` 开发渠道和模型适配的贡献者。公共请求和响应的规范以
仓库根目录 [`dc-media-protocol.md`](../../dc-media-protocol.md) 为准，本目录解释如何将
规范落实到代码和测试。

## 阅读顺序

1. [`dc-media-protocol.md`](../../dc-media-protocol.md)：公共协议、素材角色、调用形态、
   状态和错误。
2. [`adapter-development.md`](./adapter-development.md)：适配器类型、注册位置和转换规则。
3. [`example-adapter.md`](./example-adapter.md)：最小异步视频适配器示例。
4. [`model-onboarding-checklist.md`](./model-onboarding-checklist.md)：单个模型接入与验证清单。
5. [`../providers/README.md`](../providers/README.md)：当前渠道注册状态和待贡献方向。
6. [`upstream-sync-release.md`](./upstream-sync-release.md)：同步 New API 上游及发布要求。

## 贡献入口

- 新增供应商前，使用仓库的“渠道适配申请”Issue 模板。
- 开发和测试要求见 [`CONTRIBUTING.zh_CN.md`](../../CONTRIBUTING.zh_CN.md)。
- 协议缺少公共能力时先创建协议 Issue，不要直接在单个适配器中扩展公共字段。
- 已有适配器缺少某个模型时，提交模型文档、能力限制和脱敏请求示例即可认领。

## 责任边界

- DramaClaw 模型目录决定用户界面显示哪些模式和限制。
- DC-Media 公共层负责稳定字段、标准化和素材角色。
- 渠道适配器负责供应商协议、鉴权、限制和响应映射。
- `/api/channel/types` 返回代码中实际注册的渠道级能力，是运行时渠道列表的权威来源。
- `docs/providers/` 记录人工验证情况和已知缺口，不替代运行时元数据。

English documentation: [`./en/README.md`](./en/README.md).
