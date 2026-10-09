# 渠道支持矩阵

本表帮助社区寻找可贡献的渠道和能力。它区分“已在适配器工厂注册”和“已针对 DC-Media
完成契约验证”。注册只代表存在代码路径，不代表所有图片、视频或参考素材组合均可用。

运行中的 `GET /api/channel/types` 是渠道级能力的权威来源。本表记录人工验证状态和缺口。

| 渠道 | 适配器入口 | 当前状态 | 建议贡献方向 |
|---|---|---|---|
| ComfyUI | `relay/channel/task/comfyui/` | 已进行 DC-Media 本地视频适配 | 补充更多可复用 Workflow、素材节点和端到端样例 |
| MiniMax / Hailuo | `relay/channel/task/hailuo/` | 已适配 H3 等视频任务，需按模型持续验证 | 补模型限制、首尾帧及参考素材契约测试 |
| VolcEngine / DoubaoVideo | `relay/channel/task/doubao/` | 已有 DC-Media 视频转换 | 按官方模型补齐能力边界和错误映射 |
| fal.ai | `relay/channel/fal/`、`relay/channel/task/fal/` | 已注册同步媒体和异步任务 | 增加模型级 fixture 和真实调用证据 |
| Ali | `relay/channel/task/ali/` | 已注册，DC-Media 完整覆盖待审计 | 认领协议覆盖审计并补缺失字段测试 |
| Kling | `relay/channel/task/kling/` | 已注册，DC-Media 完整覆盖待审计 | 认领协议覆盖审计并补缺失字段测试 |
| Jimeng | `relay/channel/task/jimeng/` | 已注册，DC-Media 完整覆盖待审计 | 检查尾帧、多参考图及显式布尔值 |
| Vertex AI | `relay/channel/task/vertex/` | 已注册，DC-Media 完整覆盖待审计 | 检查尾帧、多参考图和结果映射 |
| Vidu | `relay/channel/task/vidu/` | 已注册，DC-Media 完整覆盖待审计 | 检查尾帧、多参考图及视频参考 |
| Gemini | `relay/channel/task/gemini/` | 已注册，DC-Media 完整覆盖待审计 | 检查媒体角色及供应商能力声明 |
| OpenAI / Sora | `relay/channel/task/sora/` | 已注册，DC-Media 完整覆盖待审计 | 检查异步任务状态和结果代理 |
| SunoAPI | `relay/channel/task/suno/` | 已注册音频任务，非 DC-Media 视频适配 | 在明确公共音频任务契约后再扩展 |

## 状态定义

- **已注册**：`relay/relay_adaptor.go` 可以创建对应适配器。
- **已进行 DC-Media 适配**：代码显式消费 DC-Media 字段，并存在相关转换测试。
- **完整覆盖待审计**：继承的适配器可运行，但尚未证明所有声明场景均符合当前协议。
- **已验证模型**：必须有官方文档、转换测试和 DramaClaw CE 真实端到端证据。

## 新增记录

新增或扩展渠道时，请在同一 PR 更新本表，并在 `docs/providers/<provider>.md` 记录：

- 官方文档和验证日期；
- 上游模型 ID；
- 支持场景、比例、分辨率、时长和素材上限；
- 明确不支持的 DC-Media 字段；
- 创建、查询、取消和结果读取情况；
- 脱敏后的端到端验证结果。

English matrix: [`./en/README.md`](./en/README.md).
