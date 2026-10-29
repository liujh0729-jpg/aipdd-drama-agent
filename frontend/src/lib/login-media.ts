// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * 登录页 / 更新说明里出现的所有媒体，一律从**同源**的 `public/login-media` 取。
 *
 * 这些文件原先是放在上游 DramaClaw 的 CDN（nfg-web-assets.cdnfg.com/dramaclaw）
 * 上的：那边换一张封面、撤一条片子、甚至整个站点下线，都会直接换掉我们登录页的
 * 内容，而我们这边一行代码都没动过。改成同源后，页面上出现的每一帧都由本项目的
 * 发布决定；打包脚本按 `login-media.sha256` 校验素材完整性后随包分发。
 *
 * 路径与上游目录结构一一对应（`luban/luban-ep01.mp4` 等），迁移时不需要改数据。
 */
export const LOGIN_MEDIA_BASE = "/login-media";

export function loginMedia(path: string): string {
  return encodeURI(`${LOGIN_MEDIA_BASE}/${path}`);
}
