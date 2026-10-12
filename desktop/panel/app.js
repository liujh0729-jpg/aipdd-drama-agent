// AIPDD DramaClaw 服务状态面板（无构建，走 Tauri v2 全局 API）。
// 与 Rust 侧事件/命令契约：
//   command get_status -> ServiceState[]        事件 svc://state  -> ServiceState
//   事件 log://line    -> {lines:[LogLine]} | LogLine            事件 boot://progress -> BootEvent
//   事件 model://progress -> DownloadProgress
//   command restart_service{id} / open_app_window / logs_dir_path / app_version
"use strict";

const T = window.__TAURI || {};
const invoke = T.core && T.core.invoke ? T.core.invoke : async () => { throw new Error("__TAURI.core 不可用"); };
const listen = T.event && T.event.listen ? T.event.listen : async () => () => {};

const STATUS_LABEL = {
  stopped: "停止", starting: "启动中", ready: "就绪",
  crashed: "异常", restarting: "重启中",
};

const servicesEl = document.getElementById("services");
const logEl = document.getElementById("log");
const logFilter = document.getElementById("logFilter");
const bootEl = document.getElementById("boot");
const bootMsg = document.getElementById("bootMsg");
const bootBar = document.getElementById("bootBar");
const bootTips = document.getElementById("bootTips");
const modelsEl = document.getElementById("models");
const openAppBtn = document.getElementById("openApp");

const cards = new Map();      // id -> {el, dot, meta, btn}
const lines = [];             // 缓冲的日志行 {service,stream,line,ts}
const MAX_LINES = 2000;
let booted = false;

// ---------- 服务卡片 ----------
function makeCard(s) {
  const el = document.createElement("div");
  el.className = "svc";
  el.innerHTML = `
    <span class="dot" data-s="stopped"></span>
    <div class="grow">
      <div class="name"></div>
      <div class="meta"></div>
    </div>
    <span class="badge"></span>
    <button class="restart">重启</button>`;
  el.querySelector(".name").textContent = s.display || s.id;
  const c = {
    el,
    dot: el.querySelector(".dot"),
    meta: el.querySelector(".meta"),
    badge: el.querySelector(".badge"),
    btn: el.querySelector(".restart"),
  };
  c.btn.addEventListener("click", () => restartService(s.id, c.btn));
  servicesEl.appendChild(el);
  cards.set(s.id, c);
  updateCard(s, c);
  return c;
}

function updateCard(s, c) {
  c = c || cards.get(s.id);
  if (!c) return;
  c.dot.setAttribute("data-s", s.status);
  const label = STATUS_LABEL[s.status] || s.status;
  const port = `:${s.port}`;
  const pid = s.pid ? ` pid ${s.pid}` : "";
  const rs = s.restarts ? ` · 重启 ${s.restarts} 次` : "";
  c.meta.textContent = `${port}${pid}${rs}`;
  c.badge.textContent = (s.optional ? "可选 · " : "") + label;
  if (s.last_error) c.meta.title = s.last_error;
}

async function refreshStatus() {
  try {
    const list = await invoke("get_status");
    for (const s of list) {
      if (!cards.has(s.id)) makeCard(s);
      else updateCard(s);
    }
  } catch (e) { /* 忽略轮询错误 */ }
}

async function restartService(id, btn) {
  if (btn) btn.disabled = true;
  try {
    await invoke("restart_service", { id });
  } catch (e) {
    appendLine({ service: id, stream: "system", line: `重启失败：${e}`, ts: Date.now() });
  } finally {
    if (btn) btn.disabled = false;
    refreshStatus();
  }
}

// ---------- 日志 ----------
function appendLine(l) {
  lines.push(l);
  if (lines.length > MAX_LINES) lines.splice(0, lines.length - MAX_LINES);
  renderLogLine(l);
}

function renderLogLine(l) {
  const f = logFilter.value;
  if (f !== "*" && l.service !== f) return;
  const span = document.createElement("span");
  if (l.stream === "stderr") span.className = "stderr";
  else if (l.stream === "system") span.className = "sys";
  const t = new Date(l.ts || Date.now()).toLocaleTimeString();
  span.textContent = `[${t}] ${l.service}/${l.stream}: ${l.line}\n`;
  logEl.appendChild(span);
  // 自动滚到底（仅在已接近底部时）。
  const nearBottom = logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 60;
  if (nearBottom) logEl.scrollTop = logEl.scrollHeight;
}

function renderAllLog() {
  logEl.textContent = "";
  const f = logFilter.value;
  for (const l of lines) {
    if (f === "*" || l.service === f) renderLogLine(l);
  }
  logEl.scrollTop = logEl.scrollHeight;
}

// ---------- 启动进度 ----------
function onBoot(ev) {
  const { stage, message, progress } = ev;
  bootEl.classList.remove("hidden");
  if (stage === "error") {
    bootEl.classList.add("error");
    bootMsg.textContent = message || "启动失败";
    bootBar.style.width = "100%";
    bootTips.textContent = TROUBLESHOOT(message);
    return;
  }
  if (stage === "ready") {
    booted = true;
    bootBar.style.width = "100%";
    bootMsg.textContent = "全部服务已就绪";
    openAppBtn.disabled = false;
    setTimeout(() => bootEl.classList.add("hidden"), 900);
    return;
  }
  bootMsg.textContent = message || "启动中…";
  bootBar.style.width = `${Math.round((progress || 0) * 100)}%`;
}

function TROUBLESHOOT(msg) {
  const m = (msg || "").toString();
  if (m.includes("端口")) {
    return "端口被占用：请关闭占用 8080/8780/3000/9001 的程序（常见于仍在运行的 Docker/Colima 旧栈），或重启后重试。";
  }
  if (m.includes("内置可执行") || m.includes("Missing") || m.includes("损坏")) {
    return "内置二进制缺失或架构不匹配：确认下载的是本机对应架构（Apple 芯片 / Intel）的安装包，或重新安装。";
  }
  if (m.includes("权限")) {
    return "权限不足：确认应用对“文稿/应用数据”目录有读写权限；首次运行被隔离时按系统提示允许。";
  }
  if (m.includes("未") && (m.includes("就绪") || m.includes("超时"))) {
    return "服务未在超时内就绪：多为便携 Python 冷启动或依赖缺失。展开下方日志查看具体报错（api/gateway 红色为 stderr）。";
  }
  return "查看下方服务日志定位错误；仍无法解决可在“服务状态”窗口保留日志并反馈。";
}

// ---------- 模型下载进度 ----------
function onModel(p) {
  modelsEl.classList.remove("hidden");
  let row = modelsEl.querySelector(`[data-file="${p.file}"]`);
  if (!row) {
    row = document.createElement("div");
    row.className = "model svc";
    row.dataset.file = p.file;
    row.innerHTML = `<div class="grow"><div class="name"></div><div class="meta"></div></div>
                     <div style="width:140px"><div class="m-bar"><i></i></div></div>`;
    modelsEl.appendChild(row);
  }
  row.querySelector(".name").textContent = `模型：${p.file}`;
  const pct = p.total ? Math.floor((p.downloaded / p.total) * 100) : 0;
  row.querySelector(".m-bar > i").style.width = `${pct}%`;
  const mb = (n) => (n / 1048576).toFixed(1);
  row.querySelector(".meta").textContent = p.error
    ? p.error
    : (p.done ? "已完成" : `${p.total ? `${mb(p.downloaded)}/${mb(p.total)}MB (${pct}%)` : `${mb(p.downloaded)}MB`}`);
}

// ---------- 事件订阅 ----------
listen("svc://state", (e) => { const s = e.payload; if (s && s.id) updateCard(s); });
listen("log://line", (e) => {
  const p = e.payload;
  if (p && Array.isArray(p.lines)) p.lines.forEach(appendLine);
  else if (p && p.line) appendLine(p);
});
listen("boot://progress", (e) => e.payload && onBoot(e.payload));
listen("model://progress", (e) => e.payload && onModel(e.payload));

// ---------- 控件 ----------
document.getElementById("clearLog").addEventListener("click", () => { lines.length = 0; logEl.textContent = ""; });
logFilter.addEventListener("change", renderAllLog);
openAppBtn.addEventListener("click", () => invoke("open_app_window").catch(() => {}));
document.getElementById("copyLogs").addEventListener("click", async () => {
  try {
    const p = await invoke("logs_dir_path");
    await navigator.clipboard.writeText(p);
    document.getElementById("copyLogs").textContent = "已复制路径";
    setTimeout(() => (document.getElementById("copyLogs").textContent = "复制日志目录路径"), 1500);
  } catch (e) { /* 剪贴板可能受限 */ }
});

// ---------- 初始化 ----------
(async () => {
  try { document.getElementById("ver").textContent = "v" + (await invoke("app_version")); } catch (e) {}
  await refreshStatus();
  setInterval(refreshStatus, 2000); // 事件为主，轮询兜底
})();
