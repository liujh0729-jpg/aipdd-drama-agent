<!-- lang-switch -->
[English](../en/license.md) · **简体中文**

# 许可证

DramaClaw 社区版（CE）采用 [Elastic License 2.0（ELv2）](../../LICENSES/Elastic-2.0.txt)，**源码可得（source available）**。本文通俗解释你能做什么、不能做什么。

> **本 fork 是多许可的。** 应用源码是 Elastic-2.0，但 vendored 在 [`third_party/`](../../third_party/README.md) 下的第三方组件保留各自许可，以“ mere aggregation（单纯聚合）”的方式并列（每个路径在 [`REUSE.toml`](../../REUSE.toml) 中映射到其 SPDX 标识，并在 [`license-inventory.csv`](../../license-inventory.csv) 中列出）：
>
> - **`third_party/dramaclaw-gateway`** — **AGPL-3.0-only**。它作为独立服务经 HTTP 访问（单纯聚合）；若你修改它并通过网络提供服务，AGPL 的 copyleft / 源码公开义务适用于该组件。
> - **`third_party/DA-2`** — **Apache-2.0**。
> - **`third_party/ml-sharp`** — Apple 示例代码（**LicenseRef-Apple-Sample-Code**）加上 **Apple ML Research Model** 权重，后者为**非商业**许可（**LicenseRef-Apple-ML-Research-Model**）。模型权重**不得**用于商业目的；依赖它的 `world`（3DGS / SHARP）特性为可选、默认不启用。
>
> 你对整体分发的使用必须尊重每一部分**最严格**的适用许可。完整清单见 [`NOTICE`](../../NOTICE)，许可证原文在 [`LICENSES/`](../../LICENSES/)。

## 这算开源吗？ <!-- banned-word-allow -->

是 **source-available**，不是 OSI 定义的「开源」。源码公开、可读、可改、可自托管，但带一条商业限制（见下）。我们对外始终用 “source available”，不自称 “open source”。<!-- banned-word-allow -->

## 我可以免费做这些

- 本地 / 自托管运行，个人或公司内部使用
- 修改源码、二次开发
- 基于 DramaClaw 构建你自己的应用、为你的客户交付作品
- 通过 API / CLI 集成进你自己的产品

## 哪些受限（需商业授权）

ELv2 的核心限制只有一条：

- **不得把 DramaClaw 作为托管 / 管理服务提供给第三方**（即把 CE 直接做成 SaaS 对外转售）。

此外不得移除许可证 / 版权声明，不得规避许可证密钥机制（若有）。

如果你想做托管 / 多租户的对外商业服务，请走商业授权（见下方联系方式）。

## 企业版（EE）是什么关系

EE 是专有的商业版（多租户、团队协作、计费、托管运维等），不在本仓。CE 覆盖完整的个人创作能力。

## 我贡献的代码归谁

贡献遵循[贡献者协议](../../CONTRIBUTING.md#贡献者协议)：你的贡献可被用于商业用途（含官方云 / 托管），维护方可按需调整许可证。详见 CONTRIBUTING。

## 商业授权 / 合作

邮件联系 <business@dramaclaw.ai>。

> 本文为通俗说明，不构成法律意见；一切以 [LICENSE](../../LICENSES/Elastic-2.0.txt) 正文为准。
