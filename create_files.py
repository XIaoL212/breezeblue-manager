 import pathlib
 
 STATIC = pathlib.Path("F:/Codex Xiangm/amd-notebook/breezeblue-manager/app/static")
 
 # index.html
 index_html = """<!DOCTYPE html>
 <html lang="zh-CN">
 <head>
 <meta charset="UTF-8">
 <meta name="viewport" content="width=device-width, initial-scale=1.0">
 <title>BreezeBlue 账户管理</title>
 <link rel="stylesheet" href="app.css">
 </head>
 <body>
 <div id="login" class="login-wrap">
   <div class="login-card">
     <div class="brand"><span class="logo">B</span><div><h1>BreezeBlue 账户管理</h1><p>批量登录与自动签到</p></div></div>
     <form id="loginForm">
       <label>用户名<input id="lgUser" value="XIaoL" autocomplete="username"></label>
       <label>密码<input id="lgPass" type="password" value="breezeblue-admin" autocomplete="current-password"></label>
       <button class="btn primary" type="submit">登录</button>
       <p class="hint" id="lgHint"></p>
     </form>
   </div>
 </div>
 
 <div id="app" class="layout hidden">
   <aside class="sidebar">
     <div class="brand small"><span class="logo">B</span><div><strong>BreezeBlue</strong><p>账户管理</p></div></div>
     <nav id="nav">
       <a data-view="dash" class="active"><i>▤</i>概览</a>
       <a data-view="accounts"><i>♟</i>账户列表</a>
       <a data-view="logs"><i>≡</i>签到日志</a>
     </nav>
     <div class="side-foot">
       <button class="btn ghost small" id="logoutBtn">退出登录</button>
     </div>
   </aside>
 
   <main class="main">
     <header class="topbar">
       <div><h2 id="viewTitle">概览</h2><p id="viewSub">账户与签到状态</p></div>
       <div class="top-actions">
         <button class="btn ghost small" id="refreshBtn">刷新</button>
         <span class="clock" id="clock"></span>
       </div>
     </header>
 
     <section id="view-dash" class="view active">
       <div class="cards" id="statCards"></div>
       <div class="panel">
         <div class="panel-head"><h3>快速操作</h3></div>
         <div class="actions">
           <button class="btn primary" id="checkinAllBtn">全部签到</button>
           <button class="btn ghost" id="addAccountBtn">添加账户</button>
         </div>
       </div>
     </section>
 
     <section id="view-accounts" class="view">
       <div class="panel">
         <div class="panel-head"><h3>账户列表</h3><button class="btn primary small" id="addAccountBtn2">+ 添加账户</button></div>
         <div class="table-wrap"><table id="accountTable"></table></div>
       </div>
     </section>
 
     <section id="view-logs" class="view">
       <div class="panel">
         <div class="panel-head"><h3>签到日志</h3><button class="btn ghost small" id="refreshLogsBtn">刷新</button></div>
         <div class="table-wrap"><table id="logTable"></table></div>
       </div>
     </section>
   </main>
 </div>
 
 <div id="modal" class="modal-wrap hidden"><div class="modal" id="modalBox"></div></div>
 <div id="toast" class="toast hidden"></div>
 <script src="app.js"></script>
 </body>
 </html>"""
 
 (STATIC / "index.html").write_text(index_html, encoding="utf-8")
 print("Created index.html")
