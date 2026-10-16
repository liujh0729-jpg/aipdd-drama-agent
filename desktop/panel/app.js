// AIPDD DramaClaw 桌面壳的两块 UI，共用本文件（无构建，走 Tauri v2 全局 API）：
//   index.html   → PANEL_VIEW="splash"   启动卡：只有品牌 + 版本 + 百分比 + 一行阶段文字
//   console.html → PANEL_VIEW="console"  运行期诊断：服务卡片 + 日志 + 单服务重启
// 与 Rust 侧事件/命令契约：
//   事件 boot://progress -> BootEvent{stage,message,progress}   （两种视图都监听）
//   事件 svc://state     -> ServiceState      事件 log://line -> {lines:[LogLine]} | LogLine
//   事件 model://progress -> DownloadProgress
//   command app_version / quit_app / open_console_window        （splash）
//   command get_status / restart_service / open_app_window / logs_dir_path
//   command get_boot_state -> BootEvent|null / recent_logs{limit} -> LogLine[]   （补订阅前的事件历史；recent_logs 给 console）
"use strict";

const VIEW = window.PANEL_VIEW === "console" ? "console" : "splash";

// Tauri v2 的 withGlobalTauri 注入的全局名是 __TAURI__（前后各两个下划线）：
// crates/tauri/scripts/bundle.global.js 结尾就是 `window.__TAURI__ = __TAURI_IIFE__`。
// 早先按 v1 的习惯写 __TAURI 会拿到 undefined，invoke/listen 全部静默退化成桩。
const T = window.__TAURI__ || window.__TAURI || {};
const invoke = T.core && T.core.invoke ? T.core.invoke : async (c) => { throw new Error(`Tauri 桥不可用，无法调用 ${c}`); };
const listen = T.event && T.event.listen ? T.event.listen : async () => () => {};

function osLabel() {
  const ua = navigator.userAgent;
  if (/Windows/i.test(ua)) return "Windows";
  if (/Mac|iPhone|iPad/i.test(ua)) return "macOS";
  return "Linux";
}

// ============================================================ 启动卡（splash）
if (VIEW === "splash") {
  const card = document.getElementById("card");
  const stageEl = document.getElementById("stage");
  const pctEl = document.getElementById("pct");
  const barEl = document.getElementById("bar");
  const verEl = document.getElementById("ver");

  let lastPct = 0;         // 已显示到的比例（单调不回退）
  let target = 0;          // Rust 事件给出的真实阶段进度
  let finishing = false;   // 收到 ready：加速补满后交给 Rust 关窗
  let raf = 0, lastT = 0;

  // 节奏：只跟真实阶段进度（Rust 侧按各服务实测耗时分段、段内随探活轮次缓动）。
  // 前端只做两件事：数字限速连续爬（不一次性跳上去），以及收到就绪后把剩下的补到 100%。
  const CLIMB = 0.9;       // 每秒最多推进 90%，真实进度领先时数字仍在动
  const FINISH = 4.5;      // 收尾：约 0.2s 把剩下的补满

  function render() {
    const n = Math.round(lastPct * 100);
    barEl.style.width = `${n}%`;
    pctEl.textContent = `${n}%`;
  }

  function step(now) {
    const dt = Math.min(0.05, (now - lastT) / 1000);
    lastT = now;
    const goal = finishing ? 1 : target;
    if (lastPct < goal) lastPct = Math.min(goal, lastPct + dt * (finishing ? FINISH : CLIMB));
    render();
    if (finishing && lastPct >= 1) {
      raf = 0;
      card.dataset.s = "ready";
      stageEl.textContent = "全部服务已就绪，正在进入主界面…";
      // 100% 原地停留 0.35s 让用户看见「跑满」，再通知壳开主窗口并关掉本卡。
      setTimeout(() => { invoke("splash_finished").catch(() => {}); }, 350);
      return;
    }
    // 追平真实进度就停帧，等下一个事件再启动；没有事件就不假装在加载。
    raf = (finishing || lastPct < target) ? requestAnimationFrame(step) : 0;
  }

  function kick() {
    if (!raf) { lastT = performance.now(); raf = requestAnimationFrame(step); }
  }

  function setProgress(p) {
    const v = Math.min(1, Math.max(0, p || 0));
    if (v <= target) return;   // 单调不回退：可选服务失败被跳过后权重段会空转
    target = v;
    kick();
  }

  function onBoot(ev) {
    const { stage, message, progress } = ev;
    if (stage === "error") {
      card.dataset.s = "error";
      stageEl.textContent = message || "启动失败";
      return;
    }
    if (stage === "ready") {
      // 收到就绪不立刻换屏：先把百分比补到 100%，再由 step() 切 ready 态并通知壳开主窗口。
      finishing = true;
      setProgress(1);
      return;
    }
    stageEl.textContent = message || "正在启动本地服务…";
    setProgress(progress);
  }

  listen("boot://progress", (e) => e.payload && onBoot(e.payload));

  document.getElementById("quit").addEventListener("click", () => {
    invoke("quit_app").catch(() => {});
  });
  document.getElementById("detail").addEventListener("click", () => {
    invoke("open_console_window").catch(() => {});
  });

  (async () => {
    try { verEl.textContent = `Version ${await invoke("app_version")} · ${osLabel()}`; }
    catch (e) { verEl.textContent = `Version · ${osLabel()}`; }
    // 补历史：事件订阅之前已经发过的阶段（秒级完成的启动尤其容易错过）靠这条命令追平。
    try {
      const b = await invoke("get_boot_state");
      if (b && b.stage) onBoot(b);
    } catch (e) { /* 拿不到就只等事件 */ }
  })();
}

// ======================================================= 诊断窗口（服务 + 日志）
if (VIEW === "console") {
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

  const cards = new Map();      // id -> {el, dot, meta, badge, btn}
  const lines = [];             // 缓冲的日志行 {service,stream,line,ts}
  const MAX_LINES = 2000;

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

  // ---------- 启动进度（本窗口只在启动失败时才有内容可看） ----------
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
      return "服务未在超时内就绪：多为便携 Python 冷启动或依赖缺失。看下方日志的具体报错（api/gateway 红色为 stderr）。";
    }
    return "查看下方服务日志定位错误；仍无法解决请保留日志后反馈。";
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
  listen("svc://state", (e) => {
    const s = e.payload;
    if (!s || !s.id) return;
    if (cards.has(s.id)) updateCard(s);
    else makeCard(s); // 首轮轮询还没回来的话，事件自己也能把卡片建出来
  });
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
  // 本窗口通常在启动失败之后才被打开，那一刻的实时事件已经过去了：
  // 先按命令把「最后一条启动事件 + 日志尾巴」补回来，再挂监听收增量。
  async function bootstrap() {
    try { document.getElementById("ver").textContent = "v" + (await invoke("app_version")); } catch (e) {}
    try {
      const b = await invoke("get_boot_state");
      if (b && b.stage) onBoot(b);
    } catch (e) { /* 老版本壳没这个命令 */ }
    try {
      const list = await invoke("recent_logs", { limit: MAX_LINES });
      if (Array.isArray(list)) list.forEach(appendLine);
    } catch (e) { /* 同上 */ }
    await refreshStatus();
    setInterval(refreshStatus, 2000); // 事件为主，轮询兜底
  }
  bootstrap();
}
