// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 全局「模型广场」：未进入项目（仪表盘 / 登录页 icon）时的落点。
// 与项目内 /projects/$project/models 复用同一页——模型广场本就是全局只读目录
// （GET /model-gateway/model-plaza），不依赖任何项目上下文，项目内路由只是壳。
import { createLazyFileRoute } from "@tanstack/react-router";

import { ModelPlazaPage } from "@/features/models/ModelPlazaPage";

export const Route = createLazyFileRoute("/_app/models")({
  component: ModelPlazaPage,
});