const API="/api";
let token=localStorage.getItem("bb_token");

function toast(msg,cls=""){
  const t=document.getElementById("toast");
  t.textContent=msg;t.className="toast show "+cls;
  setTimeout(()=>t.classList.remove("show"),2200);
}

async function api(path,opts={}){
  const h={"Content-Type":"application/json"};
  if(token) h.Authorization="Bearer "+token;
  const res=await fetch(API+path,Object.assign({headers:h,method:"GET"},opts));
  if(res.status===401){
    token=null;localStorage.removeItem("bb_token");
    showLogin();
    throw new Error("未登录或会话过期");
  }
  if(!res.ok){
    const body=await res.json().catch(()=>({}));
    throw new Error(body.detail||res.statusText);
  }
  return res.json();
}

function showLogin(){
  document.getElementById("loginPage").classList.remove("hidden");
  document.getElementById("dashboard").classList.add("hidden");
}

function showDashboard(){
  document.getElementById("loginPage").classList.add("hidden");
  document.getElementById("dashboard").classList.remove("hidden");
  switchView("dash");
}

// Login
document.getElementById("loginForm").addEventListener("submit",async(e)=>{
  e.preventDefault();
  const user=document.getElementById("lgUser").value.trim();
  const pass=document.getElementById("lgPass").value.trim();
  const hint=document.getElementById("lgHint");
  hint.textContent="登录中...";hint.className="hint";
  try{
    const data=await api("/login",{method:"POST",body:JSON.stringify({username:user,password:pass})});
    token=data.access_token;
    localStorage.setItem("bb_token",token);
    showDashboard();
  }catch(err){
    hint.textContent=err.message;hint.className="hint err";
  }
});

// Logout
document.getElementById("logoutBtn").addEventListener("click",()=>{
  token=null;localStorage.removeItem("bb_token");
  showLogin();
});

// Navigation
document.getElementById("nav").addEventListener("click",(e)=>{
  const a=e.target.closest("a[data-view]");
  if(a) switchView(a.dataset.view);
});

function switchView(name){
  document.querySelectorAll("#nav a").forEach(a=>a.classList.toggle("active",a.dataset.view===name));
  document.querySelectorAll(".view").forEach(v=>v.classList.toggle("active",v.id==="view-"+name));
  if(name==="dash"){
    document.getElementById("viewTitle").textContent="概览";
    document.getElementById("viewSub").textContent="账户与签到状态";
    loadStats();
  }else if(name==="accounts"){
    document.getElementById("viewTitle").textContent="账户列表";
    document.getElementById("viewSub").textContent="管理所有 BreezeBlue 账户";
    loadAccounts();
  }else if(name==="logs"){
    document.getElementById("viewTitle").textContent="签到日志";
    document.getElementById("viewSub").textContent="最近签到记录";
    loadLogs();
  }
}

// Stats
async function loadStats(){
  try{
    const stats=await api("/stats");
    const html=
      <div class="card"><div class="label"><span class="icon blue">♟</span>总账户数</div><div class="value"></div><div class="foot">已配置账户</div></div>
      <div class="card"><div class="label"><span class="icon green">★</span>总积分</div><div class="value"></div><div class="foot">所有账户积分合计</div></div>
      <div class="card"><div class="label"><span class="icon yellow">✓</span>今日签到</div><div class="value"></div><div class="foot">今日成功签到次数</div></div>;
    document.getElementById("statCards").innerHTML=html;
  }catch(err){toast(err.message,"err");}
}

// Accounts
async function loadAccounts(){
  try{
    const accounts=await api("/accounts");
    if(accounts.length===0){
      document.getElementById("accountTable").innerHTML="<tr><td colspan='6' style='text-align:center;color:var(--muted);padding:30px'>暂无账户</td></tr>";
      return;
    }
    let html="<thead><tr><th>ID</th><th>用户名</th><th>状态</th><th>积分</th><th>今日签到</th><th>操作</th></tr></thead><tbody>";
    for(const a of accounts){
      const statusTag=a.status==="active"?'<span class="tag success">正常</span>':'<span class="tag warning">异常</span>';
      const checkinTag=a.checked_in_today?'<span class="tag success">已签到</span>':'<span class="tag danger">未签到</span>';
      html+=<tr>
        <td></td>
        <td></td>
        <td></td>
        <td></td>
        <td></td>
        <td>
          <button class="btn ghost small" onclick="checkinAccount()">签到</button>
          <button class="btn danger small" onclick="deleteAccount()">删除</button>
        </td>
      </tr>;
    }
    html+="</tbody>";
    document.getElementById("accountTable").innerHTML=html;
  }catch(err){toast(err.message,"err");}
}

async function checkinAccount(id){
  try{
    const result=await api(/accounts//checkin);
    toast(签到成功！获得  积分,"ok");
    loadAccounts();
  }catch(err){toast(err.message,"err");}
}

async function deleteAccount(id){
  if(!confirm("确定删除这个账户？")) return;
  try{
    await api(/accounts/,{method:"DELETE"});
    toast("账户已删除","ok");
    loadAccounts();
  }catch(err){toast(err.message,"err");}
}

// Add Account
document.getElementById("addAccountBtn").addEventListener("click",showAddAccountModal);
document.getElementById("addAccountBtn2").addEventListener("click",showAddAccountModal);

async function showAddAccountModal(){
  const modal=document.getElementById("modal");
  modal.classList.remove("hidden");
  modal.querySelector("#modalBox").innerHTML=
    <h3>添加账户</h3>
    <form id="addAccountForm">
      <label><span>用户名</span><input name="username" required></label>
      <label><span>密码</span><input name="password" type="password" required></label>
      <label><span>Cookie</span><input name="cookie"></label>
      <div class="actions">
        <button class="btn ghost" type="button" onclick="closeModal()">取消</button>
        <button class="btn primary" type="submit">添加</button>
      </div>
    </form>;
  document.getElementById("addAccountForm").addEventListener("submit",async(e)=>{
    e.preventDefault();
    const form=e.target;
    const data={
      username:form.username.value.trim(),
      password:form.password.value,
      cookie:form.cookie.value.trim()
    };
    try{
      await api("/accounts",{method:"POST",body:JSON.stringify(data)});
      toast("账户添加成功","ok");
      closeModal();
      loadAccounts();
    }catch(err){toast(err.message,"err");}
  });
}

function closeModal(){
  document.getElementById("modal").classList.add("hidden");
}

// Logs
async function loadLogs(){
  try{
    const logs=await api("/logs");
    if(logs.length===0){
      document.getElementById("logTable").innerHTML="<tr><td colspan='5' style='text-align:center;color:var(--muted);padding:30px'>暂无签到日志</td></tr>";
      return;
    }
    let html="<thead><tr><th>时间</th><th>用户名</th><th>状态</th><th>积分变化</th><th>消息</th></tr></thead><tbody>";
    for(const l of logs){
      const statusTag=l.success?'<span class="tag success">成功</span>':'<span class="tag danger">失败</span>';
      html+=<tr>
        <td></td>
        <td></td>
        <td></td>
        <td>+</td>
        <td></td>
      </tr>;
    }
    html+="</tbody>";
    document.getElementById("logTable").innerHTML=html;
  }catch(err){toast(err.message,"err");}
}

// Checkin All
document.getElementById("checkinAllBtn").addEventListener("click",async()=>{
  try{
    const result=await api("/checkin/all");
    toast(全部签到完成：成功 ，失败 ,"ok");
    loadStats();
    loadAccounts();
  }catch(err){toast(err.message,"err");}
});

// Refresh
document.getElementById("refreshBtn").addEventListener("click",()=>{
  const activeView=document.querySelector("#nav a.active");
  if(activeView) switchView(activeView.dataset.view);
});

document.getElementById("refreshLogsBtn").addEventListener("click",loadLogs);

// Clock
function updateClock(){
  const now=new Date();
  document.getElementById("clock").textContent=now.toLocaleTimeString("zh-CN",{hour:"2-digit",minute:"2-digit",second:"2-digit"});
}
setInterval(updateClock,1000);
updateClock();

// Initial state
if(token){
  showDashboard();
}else{
  showLogin();
}
