const API="/api";
let token=localStorage.getItem("bb_token");

function toast(msg,cls){
  if(!cls) cls="";
  const t=document.getElementById("toast");
  t.textContent=msg;
  t.className="toast show "+cls;
  setTimeout(function(){t.classList.remove("show");},2200);
}

async function api(path,opts){
  if(!opts) opts={};
  const h={"Content-Type":"application/json"};
  if(token) h.Authorization="Bearer "+token;
  const defaultOpts={headers:h,method:"GET"};
  Object.assign(defaultOpts,opts);
  const res=await fetch(API+path,defaultOpts);
  if(res.status===401){
    token=null;
    localStorage.removeItem("bb_token");
    showLogin();
    throw new Error("未登录或会话过期");
  }
  if(!res.ok){
    try{
      const body=await res.json();
      throw new Error(body.detail||res.statusText);
    }catch(e){
      throw new Error(res.statusText);
    }
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

// Password visibility toggle
document.getElementById("togglePass").addEventListener("click",function(){
  const input=document.getElementById("lgPass");
  const btn=document.getElementById("togglePass");
  if(input.type==="password"){
    input.type="text";
    btn.textContent="\\uD83D\\uDDE8";
    btn.title="\\u9690\\u85CF\\u5BC6\\u7801";
  }else{
    input.type="password";
    btn.textContent="\\uD83D\\uDC41";
    btn.title="\\u663E\\u793A\\u5BC6\\u7801";
  }
});

// Login
document.getElementById("loginForm").addEventListener("submit",function(e){
  e.preventDefault();
  const user=document.getElementById("lgUser").value.trim();
  const pass=document.getElementById("lgPass").value.trim();
  const hint=document.getElementById("lgHint");
  hint.textContent="\\u767B\\u5F55\\u4E2D...";
  hint.className="hint";
  api("/login",{method:"POST",body:JSON.stringify({username:user,password:pass})})
    .then(function(data){
      token=data.token;
      localStorage.setItem("bb_token",token);
      showDashboard();
    })
    .catch(function(err){
      hint.textContent=err.message;
      hint.className="hint err";
    });
});

// Logout
document.getElementById("logoutBtn").addEventListener("click",function(){
  token=null;
  localStorage.removeItem("bb_token");
  showLogin();
});

// Navigation
document.getElementById("nav").addEventListener("click",function(e){
  const a=e.target.closest("a[data-view]");
  if(a) switchView(a.dataset.view);
});

function switchView(name){
  document.querySelectorAll("#nav a").forEach(function(a){
    a.classList.toggle("active",a.dataset.view===name);
  });
  document.querySelectorAll(".view").forEach(function(v){
    v.classList.toggle("active",v.id==="view-"+name);
  });
  if(name==="dash"){
    document.getElementById("viewTitle").textContent="\\u6982\\u89C8";
    document.getElementById("viewSub").textContent="\\u8D26\\u6237\\u4E0E\\u7B7E\\u5230\\u72B6\\u6001";
    loadStats();
  }else if(name==="accounts"){
    document.getElementById("viewTitle").textContent="\\u8D26\\u6237\\u5217\\u8868";
    document.getElementById("viewSub").textContent="\\u7BA1\\u7406\\u6240\\u6709 BreezeBlue \\u8D26\\u6237";
    loadAccounts();
  }else if(name==="logs"){
    document.getElementById("viewTitle").textContent="\\u7B7E\\u5230\\u65E5\\u5FD7";
    document.getElementById("viewSub").textContent="\\u6700\\u8FD1\\u7B7E\\u5230\\u8BB0\\u5F55";
    loadLogs();
  }
}

// Stats
async function loadStats(){
  try{
    const stats=await api("/stats");
    const html=""+
      '<div class="card"><div class="label"><span class="icon blue">\\u265F</span>\\u603B\\u8D26\\u6237\\u6570</div><div class="value">'+stats.account_count+'</div><div class="foot">\\u5DF2\\u914D\\u7F6E\\u8D26\\u6237</div></div>'+
      '<div class="card"><div class="label"><span class="icon green">\\u2605</span>\\u603B\\u79EF\\u5206</div><div class="value">'+stats.total_credits+'</div><div class="foot">\\u6240\\u6709\\u8D26\\u6237\\u79EF\\u5206\\u5408\\u8BA1</div></div>'+
      '<div class="card"><div class="label"><span class="icon yellow">\\u2713</span>\\u4ECA\\u65E5\\u7B7E\\u5230</div><div class="value">'+stats.today_checkins+'</div><div class="foot">\\u4ECA\\u65E5\\u6210\\u529F\\u7B7E\\u5230\\u6B21\\u6570</div></div>';
    document.getElementById("statCards").innerHTML=html;
  }catch(err){
    toast(err.message,"err");
  }
}

// Accounts
async function loadAccounts(){
  try{
    const accounts=await api("/accounts");
    if(accounts.length===0){
      document.getElementById("accountTable").innerHTML="<tr><td colspan='6' style='text-align:center;color:var(--muted);padding:30px'>\\u6682\\u65E0\\u8D26\\u6237</td></tr>";
      return;
    }
    let html="<thead><tr><th>ID</th><th>\\u7528\\u6237\\u540D</th><th>\\u72B6\\u6001</th><th>\\u79EF\\u5206</th><th>\\u4ECA\\u65E5\\u7B7E\\u5230</th><th>\\u64CD\\u4F5C</th></tr></thead><tbody>";
    for(const a of accounts){
      const statusTag=a.status==="active"?'<span class="tag success">\\u6B63\\u5E38</span>':'<span class="tag warning">\\u5F02\\u5E38</span>';
      const checkinTag=a.checked_in_today?'<span class="tag success">\\u5DF2\\u7B7E\\u5230</span>':'<span class="tag danger">\\u672A\\u7B7E\\u5230</span>';
      html+="<tr><td>"+a.id+"</td><td>"+a.email+"</td><td>"+statusTag+"</td><td>"+a.credits+"</td><td>"+checkinTag+"</td><td><button class=\"btn ghost small\" onclick=\"checkinAccount("+a.id+")\">\\u7B7E\\u5230</button> <button class=\"btn danger small\" onclick=\"deleteAccount("+a.id+")\">\\u5220\\u9664</button></td></tr>";
    }
    html+="</tbody>";
    document.getElementById("accountTable").innerHTML=html;
  }catch(err){
    toast(err.message,"err");
  }
}

async function checkinAccount(id){
  try{
    const result=await api("/accounts/"+id+"/checkin",{method:"POST",body:"{}"});
    toast("\\u7B7E\\u5230\\u6210\\u529F\\uff01\\u83B7\\u5F97 "+result.credits_earned+" \\u79EF\\u5206","ok");
    loadAccounts();
  }catch(err){
    toast(err.message,"err");
  }
}

async function deleteAccount(id){
  if(!confirm("\\u786E\\u5B9A\\u5220\\u9664\\u8FD9\\u4E2A\\u8D26\\u6237\\uff1F")) return;
  try{
    await api("/accounts/"+id,{method:"DELETE"});
    toast("\\u8D26\\u6237\\u5DF2\\u5220\\u9664","ok");
    loadAccounts();
  }catch(err){
    toast(err.message,"err");
  }
}

// Add Account
document.getElementById("addAccountBtn").addEventListener("click",showAddAccountModal);
document.getElementById("addAccountBtn2").addEventListener("click",showAddAccountModal);

async function showAddAccountModal(){
  const modal=document.getElementById("modal");
  modal.classList.remove("hidden");
  modal.querySelector("#modalBox").innerHTML=""+
    "<h3>\\u6DFB\\u52A0\\u8D26\\u6237</h3>"+
    "<form id='addAccountForm'>"+
    "<label><span>\\u7528\\u6237\\u540D</span><input name='username' required></label>"+
    "<label><span>\\u5BC6\\u7801</span><input name='password' type='password' required></label>"+
    "<label><span>Cookie</span><input name='cookie'></label>"+
    "<div class='actions'><button class='btn ghost' type='button' onclick='closeModal()'>\\u53D6\\u6D88</button> <button class='btn primary' type='submit'>\\u6DFB\\u52A0</button></div>"+
    "</form>";
  document.getElementById("addAccountForm").addEventListener("submit",function(e){
    e.preventDefault();
    const form=e.target;
    const data={
      username:form.username.value.trim(),
      password:form.password.value,
      cookie:form.cookie.value.trim()
    };
    api("/accounts",{method:"POST",body:JSON.stringify(data)})
      .then(function(){
        toast("\\u8D26\\u6237\\u6DFB\\u52A0\\u6210\\u529F","ok");
        closeModal();
        loadAccounts();
      })
      .catch(function(err){
        toast(err.message,"err");
      });
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
      document.getElementById("logTable").innerHTML="<tr><td colspan='5' style='text-align:center;color:var(--muted);padding:30px'>\\u6682\\u65E0\\u7B7E\\u5230\\u65E5\\u5FD7</td></tr>";
      return;
    }
    let html="<thead><tr><th>\\u65F6\\u95F4</th><th>\\u7528\\u6237\\u540D</th><th>\\u72B6\\u6001</th><th>\\u79EF\\u5206\\u53D8\\u5316</th><th>\\u6D88\\u606F</th></tr></thead><tbody>";
    for(const l of logs){
      const statusTag=l.success?'<span class="tag success">\\u6210\\u529F</span>':'<span class="tag danger">\\u5931\\u8D25</span>';
      html+="<tr><td>"+l.ts+"</td><td>"+l.account_email+"</td><td>"+statusTag+"</td><td>+"+l.credits_earned+"</td><td>"+l.message+"</td></tr>";
    }
    html+="</tbody>";
    document.getElementById("logTable").innerHTML=html;
  }catch(err){
    toast(err.message,"err");
  }
}

// Checkin All
document.getElementById("checkinAllBtn").addEventListener("click",async function(){
  try{
    const result=await api("/checkin/all",{method:"POST",body:"{}"});
    toast("\\u5168\\u90E8\\u7B7E\\u5230\\u5B8C\\u6210\\uff1a\\u6210\\u529F "+result.success+"\\uff0c\\u5931\\u8D25 "+result.failed,"ok");
    loadStats();
    loadAccounts();
  }catch(err){
    toast(err.message,"err");
  }
});

// Refresh
document.getElementById("refreshBtn").addEventListener("click",function(){
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
