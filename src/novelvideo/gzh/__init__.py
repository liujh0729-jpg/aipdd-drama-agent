# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 公众号「文章编写与排版」后端程序化 pipeline。

方法论移植自公开资料与平台规则，组件 HTML 为本仓库**自研重写**（不 vendor 第三方
原文件，规避无 license 风险）。链路：写作风格 prompt → 网关文本模型出 Markdown
（``write``）→ 程序化拼公众号合规 HTML（``layout``）→ ``validate`` 强门禁（ERROR=0）
→ 可选 ``publish``（官方 API 建草稿/直发，凭证由用户配置，未配置不阻塞排版）。
"""
