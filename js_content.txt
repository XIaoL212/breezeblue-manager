 let token = localStorage.getItem("bb_token");
 let currentView = "dash";
 
 function api(path, options = {}) {
     let headers = { "Content-Type": "application/json" };
     if (token) headers["Authorization"] = "Bearer " + token;
     return fetch(path, { ...options, headers })
         .then(r => {
             if (r.status === 401) { logout(); throw new Error("登录已过期"); }
             if (!r.ok) return r.json().then(e => { throw new Error(e.detail || "请求失败"); });
             return r.json();
         });
 }
 
 function show(el) { document.getElementById(el).classList.remove("hidden"); }
 function hide(el) { document.getElementById(el).classList.add("hidden"); }
 function toast(msg) {
     let t = document.getElementById("toast");
     t.textContent = msg;
     show("toast");
     setTimeout(() => hide("toast"), 3000);
 }
 
 document.getElementById("loginForm").addEventListener("submit", async (e) => {
     e.preventDefault();
     let username = document.getElementById("lgUser").value;
     let password = document.getElementById("lgPass").value;
     try {
         let r = await api("/api/login", { method: "POST", body: JSON.stringify({ username, password }) });
         token = r.token;
         localStorage.setItem("bb_token", token);
         hide("login");
         show("app");
         refreshAll();
     } catch (err) {
         document.getElementById("lgHint").textContent = err.message;
     }
 });
 
 document.getElementById("logoutBtn").addEventListener("click", logout);
 document.getElementById("refreshBtn").addEventListener("click", refreshAll);
 document.getElementById("checkinAllBtn").addEventListener("click", doCheckinAll);
 document.getElementById("addAccountBtn").addEventListener("click", openAccountModal);
 document.getElementById("addAccountBtn2").addEventListener("click", openAccountModal);
 document.getElementById("refreshLogsBtn").addEventListener("click", loadLogs);
 
 document.getElementById("nav").addEventListener("click", (e) => {
     let link = e.target.closest("a[data-view]");
     if (!link) return;
     switchView(link.dataset.view);
 });
 
 function switchView(view) {
     currentView = view;
     document.querySelectorAll("#nav a").forEach(a => a.classList.remove("active"));
     document.querySelector("#nav a[data-view=\"" + view + "\"]").classList.add("active");
     document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
     show("view-" + view);
     let titles = { dash: "概览", accounts: "账户列表", logs: "签到日志" };
     document.getElementById("viewTitle").textContent = titles[view];
     refreshAll();
 }
 
 async function refreshAll() {
     if (currentView === "dash") loadStats();
     if (currentView === "accounts") loadAccounts();
     if (currentView === "logs") loadLogs();
 }
 
 async function loadStats() {
     try {
         let s = await api("/api/stats");
         document.getElementById("statCards").innerHTML = "" +
             '<div class="card"><h4>账户总数</h4><div class="value">' + s.total_accounts + '</div></div>' +
             '<div class="card"><h4>总积分</h4><div class="value">' + Math.round(s.total_credits) + '</div></div>' +
             '<div class="card"><h4>今日签到</h4><div class="value">' + s.today_checkins + '</div></div>';
     } catch (err) { toast(err.message); }
 }
 
 async function loadAccounts() {
     try {
         let accounts = await api("/api/accounts");
         let html = "<tr><th>邮箱</th><th>积分</th><th>连续签到</th><th>上次签到</th><th>操作</th></tr>";
         if (accounts.length === 0) html += "<tr><td colspan=5 style=\"text-align:center;color:#888;padding:20px\">暂无账户</td></tr>";
         accounts.forEach(a => {
             html += "<tr>" +
                 "<td>" + a.email + "</td>" +
                 "<td>" + Math.round(a.credits) + "</td>" +
                 "<td>" + a.checkin_streak + "</td>" +
                 "<td>" + (a.last_checkin || "-") + "</td>" +
                 "<td><button class=\"btn small ghost\" onclick=\"checkinOne(" + a.id + ',\'' + a.email + "')\">签到</button> <button class=\"btn small ghost\" onclick=\"deleteAccount(" + a.id + ")\">删除</button></td>" +
                 "</tr>";
         });
         document.getElementById("accountTable").innerHTML = html;
     } catch (err) { toast(err.message); }
 }
 
 async function loadLogs() {
     try {
         let logs = await api("/api/logs?limit=50");
         let html = "<tr><th>时间</th><th>账户</th><th>状态</th><th>获得积分</th><th>累计</th><th>连续</th></tr>";
         if (logs.length === 0) html += "<tr><td colspan=6 style=\"text-align:center;color:#888;padding:20px\">暂无日志</td></tr>";
         logs.forEach(l => {
             html += "<tr>" +
                 "<td>" + l.ts + "</td>" +
                 "<td>" + l.email + "</td>" +
                 "<td><span style=\"color:" + (l.status === "success" ? "green" : "red") + "\">" + l.status + "</span></td>" +
                 "<td>" + l.credits_earned + "</td>" +
                 "<td>" + l.new_total + "</td>" +
                 "<td>" + l.streak + "</td>" +
                 "</tr>";
         });
         document.getElementById("logTable").innerHTML = html;
     } catch (err) { toast(err.message); }
 }
 
 async function checkinOne(id, email) {
     try {
         toast("正在签到: " + email);
         await api("/api/accounts/" + id + "/checkin", { method: "POST" });
         toast("签到成功: " + email);
         loadAccounts();
     } catch (err) {
         toast("签到失败: " + err.message);
     }
 }
 
 async function doCheckinAll() {
     try {
         toast("正在批量签到...");
         let r = await api("/api/checkin-all", { method: "POST" });
         let success = r.results.filter(x => x.status === "success").length;
         let fail = r.results.length - success;
         toast("完成: " + success + " 成功, " + fail + " 失败");
         loadStats();
         loadAccounts();
     } catch (err) {
         toast("批量签到失败: " + err.message);
     }
 }
 
 function openAccountModal() {
     let box = document.getElementById("modalBox");
     box.innerHTML = "" +
         "<h3>添加账户</h3>" +
         "<form id=\"accountForm\">" +
         "<label><span>邮箱</span><input id=\"accEmail\" type=\"email\" required></label>" +
         "<label><span>密码</span><input id=\"accPass\" type=\"password\" required></label>" +
         "<label><span>备注</span><input id=\"accNote\" type=\"text\"></label>" +
         "<div class=\"actions\"><button class=\"btn primary\" type=\"submit\">添加</button><button class=\"btn ghost\" type=\"button\" onclick=\"closeModal()\">取消</button></div>" +
         "</form>";
     show("modal");
     document.getElementById("accountForm").addEventListener("submit", addAccount);
 }
 
 async function addAccount(e) {
     e.preventDefault();
     let email = document.getElementById("accEmail").value;
     let password = document.getElementById("accPass").value;
     let note = document.getElementById("accNote").value;
     try {
         await api("/api/accounts", { method: "POST", body: JSON.stringify({ email, password, note }) });
         toast("账户已添加");
         closeModal();
         loadAccounts();
     } catch (err) {
         toast(err.message);
     }
 }
 
 function closeModal() { hide("modal"); }
 
 async function deleteAccount(id) {
     if (!confirm("确定删除此账户？")) return;
     try {
         await api("/api/accounts/" + id, { method: "DELETE" });
         toast("已删除");
         loadAccounts();
     } catch (err) {
         toast(err.message);
     }
 }
 
 function logout() {
     token = null;
     localStorage.removeItem("bb_token");
     hide("app");
     show("login");
 }
 
 setInterval(() => {
     let now = new Date();
     document.getElementById("clock").textContent = now.toLocaleTimeString("zh-CN");
 }, 1000);
 
 if (token) {
     hide("login");
     show("app");
     refreshAll();
 }
