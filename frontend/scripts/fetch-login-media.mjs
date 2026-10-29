#!/usr/bin/env node
// 拉取登录页/更新弹窗的大体量成片，并按 frontend/login-media.sha256 校验。
//
// 封面、客服二维码、更新提示短片都随 Git 提交；9 个 20–190 MB 的展示成片 Git
// 装不下，放在**本仓库**的 media-v1 预发布（prerelease，永远不会被
// releases/latest 选中）里，构建前拉下来。绝不从上游下载站取，改不到我们的产物。
//
//   node scripts/fetch-login-media.mjs           # 补齐缺失文件
//   node scripts/fetch-login-media.mjs --check   # 只校验，不联网
import { createHash } from "node:crypto";
import { createReadStream, createWriteStream } from "node:fs";
import { mkdir, readFile, rename, rm, stat } from "node:fs/promises";
import { dirname, join } from "node:path";
import { pipeline } from "node:stream/promises";
import { fileURLToPath } from "node:url";

const FRONTEND = dirname(dirname(fileURLToPath(import.meta.url)));
const MEDIA_DIR = join(FRONTEND, "public", "login-media");
const MANIFEST = join(FRONTEND, "login-media.sha256");
const MEDIA_TAG = "media-v1";
const MEDIA_BASE = `https://github.com/liujh0729-jpg/aipdd-drama-agent/releases/download/${MEDIA_TAG}`;

const checkOnly = process.argv.includes("--check");

function sha256(file) {
  return new Promise((resolve, reject) => {
    const hash = createHash("sha256");
    createReadStream(file)
      .on("data", (chunk) => hash.update(chunk))
      .on("error", reject)
      .on("end", () => resolve(hash.digest("hex")));
  });
}

function readManifest(text) {
  const entries = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const sep = line.indexOf("  ");
    const digest = sep > 0 ? line.slice(0, sep) : "";
    const rel = sep > 0 ? line.slice(sep + 2) : "";
    if (!/^[0-9a-f]{64}$/.test(digest) || !rel) {
      throw new Error(`清单格式不对：${JSON.stringify(raw)}`);
    }
    entries.push({ digest, rel });
  }
  return entries;
}

async function exists(file) {
  try {
    await stat(file);
    return true;
  } catch {
    return false;
  }
}

async function download(url, dest) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}：${url}`);
  const part = `${dest}.part`;
  await mkdir(dirname(dest), { recursive: true });
  await pipeline(res.body, createWriteStream(part));
  await rename(part, dest);
}

async function main() {
  const entries = readManifest(await readFile(MANIFEST, "utf8"));
  const missing = [];
  const broken = [];

  for (const { digest, rel } of entries) {
    const dest = join(MEDIA_DIR, rel);
    if (!(await exists(dest))) missing.push(rel);
    else if ((await sha256(dest)) !== digest) broken.push(rel);
  }

  if (broken.length) {
    console.error("[login-media] 校验失败（文件内容与清单不一致）：");
    for (const rel of broken) console.error(`  ${rel}`);
    return 1;
  }

  if (checkOnly) {
    if (missing.length) {
      console.error(`[login-media] 缺少 ${missing.length} 个文件；运行 node scripts/fetch-login-media.mjs 拉取`);
      return 1;
    }
    console.log(`[login-media] 全部 ${entries.length} 个文件校验通过`);
    return 0;
  }

  for (const rel of missing) {
    // 资产名就是文件名（清单里的 basename 互不重复），Release 里是平铺的一层。
    const asset = rel.split("/").pop();
    const url = `${MEDIA_BASE}/${encodeURIComponent(asset)}`;
    console.log(`[login-media] 下载 ${rel}`);
    try {
      await download(url, join(MEDIA_DIR, rel));
    } catch (error) {
      await rm(join(MEDIA_DIR, `${rel}.part`), { force: true });
      throw error;
    }
  }

  for (const { digest, rel } of entries) {
    if ((await sha256(join(MEDIA_DIR, rel))) !== digest) {
      console.error(`[login-media] 下载后校验失败：${rel}`);
      return 1;
    }
  }
  console.log(`[login-media] 就绪：${entries.length} 个文件全部校验通过`);
  return 0;
}

main().then(
  (code) => process.exit(code),
  (error) => {
    // 只认本项目 Release（media-v1）：取不到就失败，不回退上游 DramaClaw 的下载站——
    // 那条回退会让"素材由本项目决定"这条约束失效。
    console.error(`[login-media] ${error.message}`);
    process.exit(1);
  },
);
