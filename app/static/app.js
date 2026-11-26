const API = "/api";
let token = localStorage.getItem("bb_token");

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, function (c) {
    return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c];
  });
}

function toast(msg, cls) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast show " + (cls || "");
  setTimeout(function () { t.classList.remove("show"); }, 2600);
}

async function api(path, opts) {
  opts = opts || {};
  const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  if (token) headers.Authorization = "Bearer " + token;
  const res = await fetch(API + path, Object.assign({ method: "GET" }, opts, { headers }));
  if (res.status === 401) {
    token = null;
    localStorage.removeItem("bb_token");
    showLogin();
    throw new Error("未登录或会话过期");
  }
  const text = await res.text();
  let body = {};
  try { body = text ? JSON.parse(text) : {}; } catch (e) { body = { detail: text }; }
  if (!res.ok) throw new Error(body.detail || res.statusText || "请求失败");
  return body;
}

function showLogin() {
  document.getElementById("loginPage").classList.remove("hidden");
  document.getElementById("dashboard").classList.add("hidden");
}

function showDashboard() {
  document.getElementById("loginPage").classList.add("hidden");
  document.getElementById("dashboard").classList.remove("hidden");
  switchView(document.querySelector("#nav a.active")?.dataset?.view || "dash");
}

document.getElementById("togglePass").addEventListener("click", function () {
  const input = document.getElementById("lgPass");
  const btn = document.getElementById("togglePass");
  if (input.type === "password") {
    input.type = "text";
    btn.textContent = "🙈";
    btn.title = "隐藏密码";
  } else {
    input.type = "password";
    btn.textContent = "👁";
    btn.title = "显示密码";
  }
});

document.getElementById("loginForm").addEventListener("submit", async function (e) {
  e.preventDefault();
  const hint = document.getElementById("lgHint");
  hint.textContent = "登录中...";
  hint.className = "hint";
  try {
    const data = await api("/login", {
      method: "POST",
      body: JSON.stringify({ username: document.getElementById("lgUser").value.trim(), password: document.getElementById("lgPass").value }),
    });
    token = data.token;
    localStorage.setItem("bb_token", token);
    showDashboard();
  } catch (err) {
    hint.textContent = err.message;
    hint.className = "hint err";
  }
});

document.getElementById("logoutBtn").addEventListener("click", function () {
  token = null;
  localStorage.removeItem("bb_token");
  showLogin();
});

document.getElementById("nav").addEventListener("click", function (e) {
  const a = e.target.closest("a[data-view]");
  if (a) switchView(a.dataset.view);
});

function switchView(name) {
  document.querySelectorAll("#nav a").forEach(function (a) { a.classList.toggle("active", a.dataset.view === name); });
  document.querySelectorAll(".view").forEach(function (v) { v.classList.toggle("active", v.id === "view-" + name); });
  const titles = {
    dash: ["概览", "账户与签到状态"],
    accounts: ["账户列表", "账户余额、签到状态、Key 与操作"],
    relay: ["API 中转", "使用账户 Key 通过本平台调用 BreezeBlue"],
    logs: ["签到日志", "最近签到记录"],
  };
  document.getElementById("viewTitle").textContent = titles[name]?.[0] || "概览";
  document.getElementById("viewSub").textContent = titles[name]?.[1] || "";
  if (name === "dash") loadStats();
  if (name === "accounts") loadAccounts();
  if (name === "logs") loadLogs();
  if (name === "relay") loadRelayDefaults();
}

async function loadStats() {
  try {
    const stats = await api("/stats");
    document.getElementById("statCards").innerHTML =
      `<div class="card"><div class="label"><span class="icon blue">♟</span>总账户数</div><div class="value">${Number(stats.account_count || 0)}</div><div class="foot">已配置账户</div></div>` +
      `<div class="card"><div class="label"><span class="icon green">★</span>总积分</div><div class="value">${Number(stats.total_credits || 0).toLocaleString()}</div><div class="foot">所有账户当前积分合计</div></div>` +
      `<div class="card"><div class="label"><span class="icon yellow">✓</span>今日签到</div><div class="value">${Number(stats.today_checkins || 0)}</div><div class="foot">今日成功签到次数</div></div>`;
  } catch (err) {
    toast(err.message, "err");
  }
}

function statusTag(status) {
  const map = { connected: "正常", partial: "部分可用", failed: "异常", pending_login: "待登录", unchecked: "未验证" };
  const cls = status === "connected" ? "success" : status === "failed" ? "danger" : "warning";
  return `<span class="tag ${cls}">${esc(map[status] || status || "未验证")}</span>`;
}

async function loadAccounts() {
  try {
    const accounts = await api("/accounts");
    if (!accounts.length) {
      document.getElementById("accountTable").innerHTML = "<tr><td colspan='7' style='text-align:center;color:var(--muted);padding:30px'>暂无账户</td></tr>";
      return;
    }
    let html = "<thead><tr><th>ID</th><th>账户</th><th>状态</th><th>积分</th><th>今日签到</th><th>API Key</th><th>操作</th></tr></thead><tbody>";
    for (const a of accounts) {
      const checkin = a.checked_in_today ? '<span class="tag success">已签到</span>' : '<span class="tag danger">未签到</span>';
      const key = a.has_api_key ? `<span title="${esc(a.api_key_masked)}">${esc(a.api_key_masked || "已创建")}</span>` : "<span style='color:var(--muted)'>未创建</span>";
      html += `<tr><td>${a.id}</td><td><div>${esc(a.email)}</div><small style='color:var(--muted)'>${esc(a.label || "")}</small></td><td>${statusTag(a.status)}</td><td>${Number(a.credits || 0).toLocaleString()}</td><td>${checkin}</td><td>${key}</td><td style='white-space:nowrap'>
        <button class='btn ghost small' onclick='loginAccount(${a.id})'>登录</button>
        <button class='btn ghost small' onclick='checkinAccount(${a.id})'>签到</button>
        <button class='btn ghost small' onclick='syncAccount(${a.id})'>同步</button>
        <button class='btn ghost small' onclick='createKey(${a.id})'>Key</button>
        <button class='btn ghost small' onclick='useRelay(${a.id})'>中转</button>
        <button class='btn danger small' onclick='deleteAccount(${a.id})'>删</button>
      </td></tr>`;
    }
    html += "</tbody>";
    document.getElementById("accountTable").innerHTML = html;
  } catch (err) {
    toast(err.message, "err");
  }
}

async function loginAccount(id) {
  try {
    const res = await api(`/accounts/${id}/login`, { method: "POST", body: "{}" });
    toast("账户登录成功", "ok");
    if (res.item) renderAccountRow(res.item);
    loadAccounts();
  } catch (err) {
    toast(err.message, "err");
  }
}

async function checkinAccount(id) {
  try {
    const result = await api(`/accounts/${id}/checkin`, { method: "POST", body: "{}" });
    const earned = Number(result.credits_earned || 0);
    toast(result.already_claimed ? "今日已签到，无需重复领取" : `签到成功，当前积分 ${Number(result.credits || 0).toLocaleString()}`, result.already_claimed ? "" : "ok");
    if (earned) toast(`获得 ${earned} 积分`, "ok");
    loadAccounts();
  } catch (err) {
    toast(err.message, "err");
  }
}

async function syncAccount(id) {
  try {
    const res = await api(`/accounts/${id}/sync`, { method: "POST", body: "{}" });
    toast(`同步成功，当前积分 ${Number(res.item?.credits || 0).toLocaleString()}`, "ok");
    loadAccounts();
  } catch (err) {
    toast(err.message, "err");
  }
}

async function deleteAccount(id) {
  if (!confirm("确定删除这个账户？")) return;
  try {
    await api(`/accounts/${id}`, { method: "DELETE" });
    toast("账户已删除", "ok");
    loadAccounts();
  } catch (err) {
    toast(err.message, "err");
  }
}

async function createKey(id) {
  try {
    const res = await api(`/accounts/${id}/keys`, { method: "POST", body: JSON.stringify({ name: "BreezeBlue Manager" }) });
    showKeyModal(res.api_key || "", res.item || {});
    toast(res.created ? "API Key 已创建并保存" : "已存在 API Key", "ok");
    loadAccounts();
  } catch (err) {
    toast(err.message, "err");
  }
}

function showKeyModal(fullKey, item) {
  const modal = document.getElementById("modal");
  modal.classList.remove("hidden");
  document.getElementById("modalBox").innerHTML =
    "<h3>API Key</h3>" +
    `<p>账户：${esc(item.email || "")}</p>` +
    (fullKey
      ? `<label><span>一次性密钥</span><input id='fullKeyInput' value="${esc(fullKey)}" readonly></label><div class='actions'><button class='btn ghost' onclick='closeModal()'>关闭</button> <button class='btn primary' onclick='copyFullKey()'>复制</button></div>`
      : `<p>密钥已保存，平台显示：${esc(item.api_key_masked || "已创建")}</p><p style='color:var(--muted)'>BreezeBlue 只在创建时返回一次性密钥，历史密钥无法再次读取。</p><div class='actions'><button class='btn primary' onclick='closeModal()'>关闭</button></div>`);
}

async function copyFullKey() {
  const input = document.getElementById("fullKeyInput");
  try {
    await navigator.clipboard.writeText(input.value);
    toast("已复制", "ok");
  } catch (e) {
    input.select();
    document.execCommand("copy");
    toast("已复制", "ok");
  }
}

function showAddAccountModal() {
  const modal = document.getElementById("modal");
  modal.classList.remove("hidden");
  document.getElementById("modalBox").innerHTML =
    "<h3>添加账户</h3>" +
    "<form id='addAccountForm'>" +
    "<label><span>备注</span><input name='label' placeholder='可选'></label>" +
    "<label><span>邮箱</span><input name='email' required></label>" +
    "<label><span>密码</span><input name='password' type='password' required></label>" +
    "<label><span>自动签到</span><input name='auto_checkin' type='checkbox' checked></label>" +
    "<div class='actions'><button class='btn ghost' type='button' onclick='closeModal()'>取消</button> <button class='btn primary' type='submit'>添加</button></div>" +
    "</form>";
  document.getElementById("addAccountForm").addEventListener("submit", async function (e) {
    e.preventDefault();
    const form = e.target;
    try {
      await api("/accounts", {
        method: "POST",
        body: JSON.stringify({
          label: form.label.value.trim(),
          email: form.email.value.trim(),
          password: form.password.value,
          auto_checkin: form.auto_checkin.checked,
        }),
      });
      toast("账户添加成功，正在后台登录、创建 Key 并签到", "ok");
      closeModal();
      switchView("accounts");
      loadAccounts();
    } catch (err) {
      toast(err.message, "err");
    }
  });
}

function closeModal() {
  document.getElementById("modal").classList.add("hidden");
}

async function loadLogs() {
  try {
    const logs = await api("/logs");
    if (!logs.length) {
      document.getElementById("logTable").innerHTML = "<tr><td colspan='5' style='text-align:center;color:var(--muted);padding:30px'>暂无签到日志</td></tr>";
      return;
    }
    let html = "<thead><tr><th>时间</th><th>账户</th><th>状态</th><th>积分</th><th>消息</th></tr></thead><tbody>";
    for (const l of logs) {
      const tag = l.success ? '<span class="tag success">成功</span>' : '<span class="tag danger">失败</span>';
      html += `<tr><td>${esc(l.ts || "")}</td><td>${esc(l.account_email || "")}</td><td>${tag}</td><td>${Number(l.credits_earned || 0).toLocaleString()}</td><td>${esc(l.message || "")}</td></tr>`;
    }
    html += "</tbody>";
    document.getElementById("logTable").innerHTML = html;
  } catch (err) {
    toast(err.message, "err");
  }
}

function loadRelayDefaults() {
  const active = document.getElementById("relayAccount");
  if (!active.value || active.value === "1") {
    api("/accounts").then((accounts) => {
      if (accounts[0]) active.value = String(accounts[0].id);
    }).catch(() => {});
  }
}

function useRelay(id) {
  switchView("relay");
  document.getElementById("relayAccount").value = String(id);
}

async function sendRelay() {
  const path = document.getElementById("relayPath").value.trim();
  const account = document.getElementById("relayAccount").value.trim();
  const method = document.getElementById("relayMethod").value;
  const bodyText = document.getElementById("relayBody").value;
  try {
    const url = new URL(path, location.origin);
    url.searchParams.set("account_id", account);
    const hasBody = ["POST", "PUT", "PATCH"].includes(method);
    const res = await fetch(url.pathname + url.search, {
      method,
      headers: { Authorization: "Bearer " + token, ...(hasBody ? { "Content-Type": "application/json" } : {}) },
      body: hasBody ? bodyText : undefined,
    });
    const text = await res.text();
    let pretty = text;
    try { pretty = JSON.stringify(JSON.parse(text), null, 2); } catch (e) {}
    document.getElementById("relayResponse").value = `${res.status} ${res.statusText}\n\n${pretty}`;
    if (!res.ok) toast("中转请求返回错误", "err");
  } catch (err) {
    toast(err.message, "err");
  }
}

document.getElementById("checkinAllBtn").addEventListener("click", async function () {
  try {
    const res = await api("/checkin/all", { method: "POST", body: "{}" });
    toast(`全部签到完成：成功 ${res.success}，失败 ${res.failed}`, res.failed ? "" : "ok");
    loadStats();
    loadAccounts();
  } catch (err) {
    toast(err.message, "err");
  }
});

document.getElementById("refreshBtn").addEventListener("click", function () {
  const active = document.querySelector("#nav a.active");
  if (active) switchView(active.dataset.view);
});

document.getElementById("refreshLogsBtn").addEventListener("click", loadLogs);
document.getElementById("addAccountBtn").addEventListener("click", showAddAccountModal);
document.getElementById("addAccountBtn2").addEventListener("click", showAddAccountModal);
document.getElementById("sendRelayBtn").addEventListener("click", sendRelay);

function updateClock() {
  document.getElementById("clock").textContent = new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
setInterval(updateClock, 1000);
updateClock();

if (token) showDashboard();
else showLogin();
