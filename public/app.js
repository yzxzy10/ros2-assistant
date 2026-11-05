/* ROS2 学习助手 前端逻辑 */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var chat = $("chat"), input = $("input"), sendBtn = $("send"), optBar = $("options-bar");

  var sid = localStorage.getItem("ros2tutor_sid");
  if (!sid || !/^[A-Za-z0-9_-]{6,64}$/.test(sid)) {
    sid = "s" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
    localStorage.setItem("ros2tutor_sid", sid);
  }
  // ?sid=xxx 可直接打开某段会话（分享项目、排障时用）
  try {
    var _qs = new URLSearchParams(location.search).get("sid");
    if (_qs && /^[A-Za-z0-9_-]{6,64}$/.test(_qs) && _qs !== sid) {
      sid = _qs;
      localStorage.setItem("ros2tutor_sid", sid);
    }
  } catch (e) {}

  // 学生身份：由服务端签发（HttpOnly Cookie 里的签名 uid），前端只做展示缓存。
  // 请求里带的 userId 服务端一律忽略，所以改 localStorage 冒充不了别人。
  var uid = localStorage.getItem("ros2tutor_uid") || "";
  var identityReady = null;

  function ensureIdentity(force) {
    if (identityReady && !force) return identityReady;
    identityReady = fetch("/api/identity", { credentials: "same-origin" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (d) {
        uid = d.user_id;
        try { localStorage.setItem("ros2tutor_uid", uid); } catch (e) {}
        return d;
      });
    return identityReady;
  }

  // 访问令牌：服务端开启校验后需要；?token=xxx 可一次性带入并自动记住
  var TOKEN_KEY = "ros2tutor_token";
  var token = localStorage.getItem(TOKEN_KEY) || "";
  try {
    var _qt = new URLSearchParams(location.search).get("token");
    if (_qt) {
      token = _qt;
      localStorage.setItem(TOKEN_KEY, _qt);
      var _u = new URL(location.href);       // 从地址栏抹掉，避免被复制外传
      _u.searchParams.delete("token");
      history.replaceState(null, "", _u.pathname + (_u.search || "") + _u.hash);
    }
  } catch (e) {}

  // 统一请求入口：带上身份 Cookie 与令牌，401/403 给出可读错误
  function apiFetch(url, opt, retried) {
    opt = opt || {};
    opt.credentials = "same-origin";
    opt.headers = opt.headers || {};
    if (token) opt.headers["X-Auth-Token"] = token;
    return fetch(url, opt).then(function (r) {
      if (r.status === 401 || r.status === 403) {
        return r.json().catch(function () { return {}; }).then(function (d) {
          if (d.need_identity && !retried) {      // 身份过期：重取一次再试
            return ensureIdentity(true).then(function () {
              return apiFetch(url, opt, true);
            });
          }
          var err = new Error(d.message || ("HTTP " + r.status));
          err.status = r.status;
          err.needToken = !!d.need_token;
          err.needIdentity = !!d.need_identity;
          throw err;
        });
      }
      return r;
    });
  }

  function showGate(msg) {
    var g = $("token-gate");
    if (!g) return;
    g.classList.remove("hidden");
    var e = $("token-err");
    if (e) e.textContent = msg || "";
    var inp = $("token-input");
    if (inp) { inp.value = token || ""; setTimeout(function () { inp.focus(); }, 60); }
  }

  function hideGate() {
    var g = $("token-gate");
    if (g) g.classList.add("hidden");
  }

  function handleAuthError(e) {
    if (e && e.needToken) { showGate(e.message); return true; }
    // 403 但服务端没说要令牌：是「这段会话不属于当前身份」（常见于重新部署后身份被重置），
    // 输令牌解决不了，直接丢掉旧会话号并重载，用户无感换到新会话。
    if (e && e.status === 403) {
      try { localStorage.removeItem("ros2tutor_sid"); } catch (_) {}
      location.reload();
      return true;
    }
    return false;
  }

  // CDN 挂了（断网答辩、内网演示）也不能让整个界面崩掉：
  // marked 缺失时退化为「转义 + 保留换行」，功能降级但不白屏。
  if (window.marked) marked.setOptions({
    breaks: true,
    highlight: function (code, lang) {
      try {
        if (window.hljs) {
          if (lang && hljs.getLanguage(lang)) return hljs.highlight(code, { language: lang }).value;
          return hljs.highlightAuto(code).value;
        }
      } catch (e) {}
      return code;
    }
  });

  function md(text) {
    text = text || "";
    if (!window.marked) {
      return String(text).replace(/[&<>]/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c];
      }).replace(/\n/g, "<br>");
    }
    return marked.parse(text);
  }

  // 代码块增强：给每个 <pre> 加「语言标签 + 复制按钮」工具条
  function markCopied(btn) {
    btn.textContent = "已复制"; btn.classList.add("ok");
    setTimeout(function () { btn.textContent = "复制"; btn.classList.remove("ok"); }, 1600);
  }
  function fallbackCopy(txt, btn) {
    var ta = document.createElement("textarea");
    ta.value = txt; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); markCopied(btn); } catch (e) {}
    document.body.removeChild(ta);
  }
  function enhanceCodeBlocks(root) {
    if (!root || !root.querySelectorAll) return;
    root.querySelectorAll("pre").forEach(function (pre) {
      if (pre.parentNode && pre.parentNode.className === "code-block") return;
      var lang = "";
      var codeEl = pre.querySelector("code");
      if (codeEl) {
        var m = (codeEl.className || "").match(/language-([\w+-]+)/);
        if (m) lang = m[1];
      }
      var wrap = document.createElement("div");
      wrap.className = "code-block";
      var bar = document.createElement("div");
      bar.className = "code-toolbar";
      var label = document.createElement("span");
      label.className = "lang";
      label.textContent = lang || "code";
      var btn = document.createElement("button");
      btn.type = "button"; btn.className = "copy-btn"; btn.textContent = "复制";
      btn.onclick = function () {
        var txt = pre.textContent || "";
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(txt).then(function () { markCopied(btn); })
            .catch(function () { fallbackCopy(txt, btn); });
        } else { fallbackCopy(txt, btn); }
      };
      bar.appendChild(label); bar.appendChild(btn);
      pre.parentNode.insertBefore(wrap, pre);
      wrap.appendChild(bar); wrap.appendChild(pre);
    });
  }

  function scrollBottom() { chat.scrollTop = chat.scrollHeight; }

  function addMsg(role, html, stages) {
    var wrap = document.createElement("div");
    wrap.className = "msg " + role;
    var avatar = document.createElement("div");
    avatar.className = "avatar";
    if (role === "assistant") {
      var img = document.createElement("img");
      img.src = "/static/logo.jpg?v=18";
      img.alt = "AI";
      avatar.appendChild(img);
    } else {
      avatar.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-3.5 3.6-6 8-6s8 2.5 8 6"/></svg>';
    }
    var bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.innerHTML = html;
    enhanceCodeBlocks(bubble);
    if (stages && stages.length) {
      var bar = document.createElement("div");
      bar.className = "stage-bar";
      stages.forEach(function (s) {
        var el = document.createElement("span");
        el.className = "stage";
        el.textContent = "⚙ " + s;
        bar.appendChild(el);
      });
      bubble.appendChild(bar);
    }
    wrap.appendChild(avatar);
    wrap.appendChild(bubble);
    chat.appendChild(wrap);
    scrollBottom();
    return bubble;
  }

  function addLoading() {
    return addMsg("assistant", '<span class="loading-dots">思考中</span>');
  }

  function renderOptions(options, mode) {
    optBar.innerHTML = "";
    (options || []).forEach(function (o) {
      var b = document.createElement("button");
      b.className = "opt-btn";
      b.textContent = o.key + ". " + o.label;
      b.onclick = function () { doSend(o.send || o.key); };
      optBar.appendChild(b);
    });
  }

  function renderImportedBadge(ip) {
    var el = $("imported-badge");
    if (!el) return;
    if (!ip || !ip.name) {
      el.classList.add("hidden");
      el.innerHTML = "";
      return;
    }
    el.classList.remove("hidden");
    var parts = [];
    if (ip.node_count) parts.push(ip.node_count + " 节点");
    if (ip.package_count) parts.push(ip.package_count + " 包");
    el.innerHTML =
      '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M12 12v4M10 14l2 2 2-2"/></svg>' +
      '<span class="imp-name"></span>' +
      '<span class="imp-meta"></span>';
    el.querySelector(".imp-name").textContent = ip.name;
    el.querySelector(".imp-meta").textContent = parts.join(" · ");
    el.title = "已导入项目，AI 会基于它继续开发";
  }

  // 会话里有没有「可看的架构图」：生成过节点、或导入过别人的工作区。
  // 有就必须给一个常驻入口 —— 只靠「生成完毕」那张一次性卡片，
  // 一旦漏看（或页面是旧版 JS）就再也没有办法打开图了。
  var hasGraphArtifact = false;

  function renderSidebar(data) {
    renderImportedBadge(data.imported_project);
    hasGraphArtifact = !!(data.imported_project && data.imported_project.name) ||
      (data.nodes || []).some(function (n) { return n.generated; });
    // 节点清单
    var nodesEl = $("nodes");
    if (data.nodes && data.nodes.length) {
      nodesEl.innerHTML = "";
      data.nodes.forEach(function (n) {
        var d = document.createElement("div");
        d.className = "node-item";
        var st = n.generated
          ? '<span class="st done">已生成</span>'
          : '<span class="st todo">待生成</span>';
        d.innerHTML = "<span>" + n.node_name + "</span>" + st;
        d.title = n.what_to_do || "";
        nodesEl.appendChild(d);
      });
    }
    // 文件树
    renderFiles(data.files);
  }

  // 文件树单独拎出来：架构图提交后要让左栏立刻看到新生成的文件，
  // 不能等下一轮对话才刷新。
  function renderFiles(list) {
    var filesEl = $("files");
    if (!filesEl) return;
    var has = !!(list && list.length);
    if (has) {
      filesEl.innerHTML = "";
      list.forEach(function (f) {
        var d = document.createElement("div");
        d.className = "file-item";
        d.textContent = "📄 " + f;
        filesEl.appendChild(d);
      });
    } else {
      filesEl.innerHTML = "生成的节点代码会保存在这里";
    }
    $("project-acts").classList.toggle("hidden", !(has || hasGraphArtifact));
  }

  window.RTRefreshFiles = function () {
    return fetch("/api/files/" + sid).then(function (r) { return r.json(); })
      .then(function (d) { renderFiles((d && d.files) || []); })
      .catch(function () {});
  };

  function renderProfile(d) {
    var el = $("profile");
    var p = (d && d.profile) || {};
    var st = (d && d.stats) || {};
    var concepts = p.concepts || [], projects = p.projects || [], errors = p.errors || [];
    if (!st.turns && !concepts.length && !projects.length && !errors.length && !p.summary) {
      el.className = "profile-empty";
      el.textContent = "还没有学习记录。聊几句，我会记住你学过什么。";
      return;
    }
    el.className = "profile";
    var html = "";
    html += '<div class="prof-head">' +
      '<span class="lvl ' + (p.level === "advanced" ? "adv" : "beg") + '">' +
      (p.level === "advanced" ? "进阶" : "入门") + '</span>' +
      '<span class="prof-stat">' + (st.turns || 0) + ' 轮 · ' +
      (st.concepts || 0) + ' 概念 · ' + (st.projects || 0) + ' 项目</span></div>';

    if (concepts.length) {
      html += '<div class="prof-sec"><b>已问过</b><div class="chips">';
      concepts.slice(0, 8).forEach(function (c) {
        html += '<span class="chip">' + c.name + (c.count > 1 ? " ×" + c.count : "") + '</span>';
      });
      html += '</div></div>';
    }
    if (projects.length) {
      html += '<div class="prof-sec"><b>项目</b>';
      projects.slice(0, 3).forEach(function (pr) {
        html += '<div class="prof-item">' + (pr.name_cn || pr.package_name) +
          ' <i>' + (pr.nodes_done || 0) + '/' + (pr.nodes_total || 0) + ' 节点</i></div>';
      });
      html += '</div>';
    }
    if (errors.length) {
      html += '<div class="prof-sec"><b>踩过的坑</b>';
      errors.slice(0, 3).forEach(function (e) {
        html += '<div class="prof-item err" title="' + (e.message || "") + '">' +
          e.type + '</div>';
      });
      html += '</div>';
    }
    if (p.summary) {
      html += '<details class="prof-sum"><summary>长期记忆摘要</summary><p>' +
        p.summary + '</p></details>';
    }
    el.innerHTML = html;
  }

  function refreshProfile() {
    apiFetch("/api/profile")
      .then(function (r) { return r.json(); })
      .then(renderProfile)
      .catch(function (e) {
        if (handleAuthError(e)) return;
        var el = $("profile");
        if (el) {
          el.className = "profile-empty";
          el.textContent = "画像读取失败：" + e.message;
        }
      });
  }

  function refreshSession() {
    apiFetch("/api/session/" + sid)
      .then(function (r) { return r.json(); })
      .then(function (d) { renderSidebar(d); })
      .catch(function (e) { handleAuthError(e); });
  }

  var sending = false;

  // ---------------- 执行进度面板 ----------------
  // 解决的问题：长任务（完整项目 200 秒以上）中间没有任何反馈，
  // 学生分不清「模型在想」和「已经卡死」。这里给三样东西：
  //   1) 分步状态（谁在跑、谁跑完了、每步各自耗时）
  //   2) 实时计时器（本地每 100ms 走一次，不依赖服务端事件）
  //   3) 心跳断流提示（超过 6 秒没新事件就说清楚「还在等」）
  function fmtSec(s) {
    s = Math.max(0, s || 0);
    if (s >= 60) return Math.floor(s / 60) + "m" + Math.round(s % 60) + "s";
    return s.toFixed(1) + "s";
  }

  function makeProgress(bubble) {
    var t0 = Date.now(), last = Date.now(), done = false, open = false;
    var steps = {}, order = [];

    var root = document.createElement("div");
    root.className = "prog";
    root.innerHTML =
      '<div class="prog-head">' +
        '<span class="prog-title"><i class="prog-spin"></i>执行进度</span>' +
        '<span class="prog-time">0.0s</span>' +
      '</div>' +
      '<ul class="prog-steps"></ul>' +
      '<div class="prog-hint hidden"></div>';
    bubble.insertBefore(root, bubble.firstChild);

    var headEl = root.querySelector(".prog-head");
    var titleEl = root.querySelector(".prog-title");
    var timeEl = root.querySelector(".prog-time");
    var listEl = root.querySelector(".prog-steps");
    var hintEl = root.querySelector(".prog-hint");
    var spin = root.querySelector(".prog-spin");

    var timer = setInterval(function () {
      var s = (Date.now() - t0) / 1000;
      timeEl.textContent = fmtSec(s);
      if (done) return;
      var idle = (Date.now() - last) / 1000;
      if (idle > 6) {
        hintEl.textContent = "已 " + fmtSec(s) + " 无新进展，仍在等待模型响应…";
        hintEl.classList.remove("hidden");
      } else {
        hintEl.classList.add("hidden");
      }
    }, 100);

    function ensure(id, label) {
      if (steps[id]) return steps[id];
      var li = document.createElement("li");
      li.className = "prog-step pending";
      li.innerHTML = '<i class="sign"></i><span class="lbl"></span>' +
                     '<span class="det"></span><span class="t"></span>';
      li.querySelector(".lbl").textContent = label || id;
      listEl.appendChild(li);
      steps[id] = li;
      order.push(id);
      return li;
    }

    function setState(li, state) {
      li.className = "prog-step " + state;
      li.querySelector(".sign").textContent =
        state === "done" ? "✓" : state === "fail" ? "!" : state === "skip" ? "·" : "";
    }

    return {
      onEvent: function (ev) {
        last = Date.now();
        var v = ev.value || {};
        if (ev.type === "tick") {
          if (v.detail && order.length) {
            var cur = steps[order[order.length - 1]];
            if (cur && cur.classList.contains("running")) {
              cur.querySelector(".det").textContent = v.detail;
            }
          }
          return;
        }
        var li = ensure(v.id, v.label);
        if (v.label) li.querySelector(".lbl").textContent = v.label;
        if (v.detail) li.querySelector(".det").textContent = v.detail;
        if (v.state === "start") {
          order.forEach(function (id) {           // 上一步没收到 done 也收尾
            if (id !== v.id && steps[id].classList.contains("running")) {
              setState(steps[id], "done");
            }
          });
          setState(li, "running");
        } else if (v.state === "run") {
          if (!li.classList.contains("done")) setState(li, "running");
        } else if (v.state === "done" || v.state === "fail") {
          setState(li, v.state);
          if (v.elapsed != null) li.querySelector(".t").textContent = fmtSec(v.elapsed);
        }
      },
      finish: function (elapsed, summary) {
        if (done) return;
        done = true;
        clearInterval(timer);
        (summary || []).forEach(function (s) {
          var li = ensure(s.id, s.label);
          if (!li.classList.contains("fail")) setState(li, "done");
          if (s.elapsed != null) li.querySelector(".t").textContent = fmtSec(s.elapsed);
        });
        order.forEach(function (id) {
          var li = steps[id];
          if (li.classList.contains("running")) setState(li, "done");
          else if (li.classList.contains("pending")) setState(li, "skip");
        });
        if (spin && spin.parentNode) spin.remove();
        var total = elapsed || (Date.now() - t0) / 1000;
        titleEl.textContent = "✓ " + order.length + " 步完成 · " + fmtSec(total);
        timeEl.textContent = "";
        hintEl.classList.add("hidden");
        var chev = document.createElement("span");
        chev.className = "chev";
        chev.textContent = "展开";
        headEl.appendChild(chev);
        headEl.classList.add("clickable");
        root.classList.add("folded");        // 完成后折叠，只留一行摘要
        headEl.onclick = function () {
          open = !open;
          root.classList.toggle("open", open);
          chev.textContent = open ? "收起" : "展开";
        };
      },
      fail: function (msg) {
        if (done) return;
        done = true;
        clearInterval(timer);
        if (spin && spin.parentNode) spin.remove();
        titleEl.textContent = "执行中断";
        hintEl.textContent = msg || "";
        hintEl.classList.remove("hidden");
      }
    };
  }

  // 流式渲染：边收边显示纯文本 + 光标，结束时再整体渲染 Markdown
  function makeStreamer(bubble, body) {
    var buf = "", textEl = null, timer = null;
    return {
      push: function (text) {
        buf += text;
        if (!textEl) {
          textEl = document.createElement("div");
          textEl.className = "stream-text";
          body.insertBefore(textEl, body.firstChild);
        }
        if (timer) return;
        timer = setTimeout(function () {
          timer = null;
          textEl.textContent = buf;
          scrollBottom();
        }, 60);
      },
      text: function () { return buf; }
    };
  }

  function handleEvent(ev, ctx) {
    if (ev.type === "progress" || ev.type === "tick") {
      ctx.prog.onEvent(ev);
    } else if (ev.type === "delta") {
      ctx.stream.push(ev.value);
    } else if (ev.type === "error") {
      ctx.done = true;
      ctx.prog.fail(ev.error || "出错了");
      ctx.body.innerHTML = "⚠️ " + (ev.error || "出错了");
    } else if (ev.type === "final") {
      ctx.done = true;
      var reply = ev.reply || ctx.stream.text();
      ctx.prog.finish(ev.elapsed, ev.steps);
      ctx.body.innerHTML = md(reply);
      enhanceCodeBlocks(ctx.body);
      if (ev.vision && ev.vision.kind) {
        var tag = document.createElement("div");
        tag.className = "vision-tag";
        tag.textContent = "📷 " + (ev.vision.title || ev.vision.kind);
        ctx.body.appendChild(tag);
      }
      if (ev.stages && ev.stages.length) {
        var bar = document.createElement("div");
        bar.className = "stage-bar";
        ev.stages.forEach(function (s) {
          var el = document.createElement("span");
          el.className = "stage";
          el.textContent = "⚙ " + s;
          bar.appendChild(el);
        });
        ctx.body.appendChild(bar);
      }
      renderOptions(ev.options, ev.options_mode);
      // 代码刚生成完：问一句要不要进架构图手动改（开关关掉就不问）
      if (ev.graph_ready) {
        if (window.RTPulseGraphEntry) window.RTPulseGraphEntry();
        if (askGraphOn()) ctx.body.appendChild(graphOffer(ev.graph_ready));
      }
      renderSidebar(ev);
      renderHistoryList();
      refreshProfile();
      scrollBottom();
    }
  }

  // ---------------- 截图上传（多模态） ----------------
  var MAX_IMGS = 3, MAX_MB = 4;
  var pendingImgs = [];
  var strip = $("img-strip"), fileInput = $("img-file");

  function renderStrip() {
    strip.innerHTML = "";
    if (!pendingImgs.length) {
      strip.classList.add("hidden");
      return;
    }
    strip.classList.remove("hidden");
    pendingImgs.forEach(function (it, i) {
      var d = document.createElement("div");
      d.className = "thumb" + (it.uploading ? " uploading" : "");
      var img = document.createElement("img");
      img.src = it.local;
      img.alt = "截图";
      d.appendChild(img);
      var rm = document.createElement("button");
      rm.className = "rm";
      rm.type = "button";
      rm.textContent = "×";
      rm.onclick = function () { pendingImgs.splice(i, 1); renderStrip(); };
      d.appendChild(rm);
      strip.appendChild(d);
    });
  }

  function addFiles(files) {
    var list = Array.prototype.slice.call(files || []);
    list.forEach(function (f) {
      if (!/^image\//.test(f.type)) return;
      if (pendingImgs.length >= MAX_IMGS) {
        alert("最多一次上传 " + MAX_IMGS + " 张截图");
        return;
      }
      if (f.size > MAX_MB * 1024 * 1024) {
        alert("这张图 " + (f.size / 1048576).toFixed(1) + "MB，请压缩到 " + MAX_MB + "MB 以内");
        return;
      }
      var item = { id: "", local: "", uploading: true };
      pendingImgs.push(item);
      renderStrip();
      var reader = new FileReader();
      reader.onload = function () {
        item.local = reader.result;
        renderStrip();
        apiFetch("/api/upload", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sessionId: sid, dataUrl: reader.result })
        }).then(function (r) { return r.json(); })
          .then(function (d) {
            if (!d.ok) throw new Error(d.message || d.error || "上传失败");
            item.id = d.id;
            item.uploading = false;
            renderStrip();
          })
          .catch(function (e) {
            pendingImgs = pendingImgs.filter(function (x) { return x !== item; });
            renderStrip();
            alert("图片上传失败：" + (e.message || e));
          });
      };
      reader.readAsDataURL(f);
    });
  }

  if (fileInput) {
    fileInput.onchange = function () { addFiles(fileInput.files); fileInput.value = ""; };
  }
  if ($("pick-img")) {
    $("pick-img").onclick = function () { fileInput && fileInput.click(); };
  }

  // ---------------- 项目导入（识别 ROS2 项目并作为后续开发上下文） ----------------
  var projectInput = $("project-file");
  var PROJECT_ALLOWED = /\.(py|xml|yaml|yml|txt|md|launch|msg|srv|action|cfg|json|cpp|hpp|h|c|cc|urdf|xacro|sdf|world|rviz|cmake|ini|toml|sh|bash|csv|zip|tar|gz|bz2|xz|zst|tgz|tbz2|txz|7z|rar|so|a|o|bin|run|exe|appimage)$/i;
  var PROJECT_BINARY = /\.(zip|tar|gz|bz2|xz|zst|tgz|tbz2|txz|7z|rar|so|a|o|bin|run|exe|appimage)$/i;
  function readProjectFile(f) {
    return new Promise(function (resolve) {
      var isBin = PROJECT_BINARY.test(f._rel);
      var r = new FileReader();
      r.onload = function () {
        var content = String(r.result || "");
        if (isBin && content.indexOf("base64,") >= 0) {
          content = content.slice(content.indexOf("base64,") + 7);  // 去掉 dataURL 前缀
        }
        resolve({ path: f._rel, content: content });
      };
      r.onerror = function () { resolve(null); };
      if (isBin) r.readAsDataURL(f); else r.readAsText(f);
    });
  }
  function importProject(files) {
    var list = Array.prototype.slice.call(files || []);
    if (!list.length) return;
    // 一个会话只有一个工作区：再次导入是**覆盖**，不是追加。
    // 覆盖会 rmtree 旧目录，不可恢复，所以先问一句。
    var badge = $("imported-badge");
    if (badge && !badge.classList.contains("hidden")
        && !window.confirm("这个会话里已经有一个项目了。\n\n继续导入会覆盖它：旧文件（含生成过的节点代码）会被清空，无法恢复。\n想留着就先点「下载整个工作区」。\n\n确定覆盖？")) {
      return;
    }
    var items = [], total = 0;
    list.forEach(function (f) {
      var rel = (f.webkitRelativePath || f.name || "").replace(/\\/g, "/");
      var seg = rel.split("/");
      if (seg.length > 1) seg.shift();     // 去掉选中的顶层文件夹名
      rel = seg.join("/");
      if (!rel || !PROJECT_ALLOWED.test(rel)) return;
      // 源码单文件限 1MB；压缩包不能套这个限制（真实项目 zip 常有几 MB），
      // 否则选了 zip 会被静默跳过，用户只会看到「没反应」。总量仍限 100MB。
      var isArchive = /\.(zip|tar|gz|tgz|bz2|xz|zst|7z|rar)$/i.test(rel);
      if (!isArchive && f.size > 1 * 1024 * 1024) return;
      total += f.size;
      if (total > 100 * 1024 * 1024) return;         // 总量 ≤ 100MB
      f._rel = rel;
      items.push(f);
    });
    if (!items.length) {
      addMsg("assistant", md("⚠️ 未找到可导入的文件（支持 .py/.xml/.yaml/.msg/.cpp 源码，以及 .zip/.tar/.so/.bin 等压缩包与可执行文件）。"));
      return;
    }
    addMsg("assistant", md("正在导入项目并识别结构（共 " + items.length + " 个文件）…"));
    Promise.all(items.map(readProjectFile)).then(function (results) {
      var arr = results.filter(function (r) { return r; })
                       .map(function (r) { return { path: r.path, content: r.content }; });
      return apiFetch("/api/project/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sessionId: sid, files: arr })
      }).then(function (r) { return r.json(); });
    }).then(function (d) {
      if (!d.ok) throw new Error(d.message || "导入失败");
      addMsg("assistant", md("✅ " + (d.message || "项目导入成功")));
      refreshSession();
      refreshProfile();
      if (window.RTGraph) window.RTGraph.reload();   // 导入完立刻出图
    }).catch(function (e) {
      if (!handleAuthError(e)) {
        addMsg("assistant", md("⚠️ 项目导入失败：" + (e.message || e)));
      }
    });
  }
  // 导入入口做成二选一：文件夹 / zip。
  // 原来点一下只能选目录（input 带 webkitdirectory），浏览框里 zip 是灰的，选不了。
  var importMenu = $("import-menu");
  var projectZip = $("project-zip");
  function closeImportMenu() { if (importMenu) importMenu.classList.add("hidden"); }
  function openImportMenu(btn) {
    if (!importMenu) return;
    importMenu.classList.remove("hidden");
    var r = btn.getBoundingClientRect();
    var w = importMenu.offsetWidth || 232;
    var h = importMenu.offsetHeight || 96;
    var left = Math.max(10, Math.min(r.left, window.innerWidth - w - 10));
    var top = r.top - h - 8;                       // 默认往上弹（输入框在底部）
    if (top < 10) top = r.bottom + 8;              // 上面放不下就往下
    importMenu.style.left = left + "px";
    importMenu.style.top = top + "px";
  }
  if ($("import-project")) {
    $("import-project").onclick = function (ev) {
      ev.stopPropagation();
      openImportMenu(ev.currentTarget);
    };
  }
  document.addEventListener("click", function (ev) {
    if (importMenu && !importMenu.classList.contains("hidden")
        && !importMenu.contains(ev.target)) closeImportMenu();
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") closeImportMenu();
  });
  if ($("pick-folder")) {
    $("pick-folder").onclick = function () { closeImportMenu(); projectInput && projectInput.click(); };
  }
  if ($("pick-zip")) {
    $("pick-zip").onclick = function () { closeImportMenu(); projectZip && projectZip.click(); };
  }
  if (projectInput) {
    projectInput.onchange = function () { importProject(projectInput.files); projectInput.value = ""; };
  }
  if (projectZip) {
    projectZip.onchange = function () { importProject(projectZip.files); projectZip.value = ""; };
  }

  // 移除当前项目：只清空工作区，聊天记录保留
  var REMOVE_LABEL = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16M9 7V5h6v2M7 7l1 13h8l1-13M10 11v6M14 11v6"/></svg> 移除';
  if ($("remove-project")) {
    $("remove-project").onclick = function () {
      var ok = window.confirm(
        "移除后会清空这个会话里的项目文件（含生成过的节点代码）。\n" +
        "聊天记录会保留，之后可以重新导入或直接说想做什么。\n\n" +
        "如果要连聊天记录一起删，用左边会话列表里的删除。\n\n确定移除？");
      if (!ok) return;
      var btn = $("remove-project");
      btn.disabled = true;
      btn.innerHTML = "正在移除…";
      apiFetch("/api/project/remove", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sessionId: sid })
      }).then(function (r) { return r.json(); }).then(function (d) {
        btn.disabled = false;
        btn.innerHTML = REMOVE_LABEL;
        if (!d.ok) throw new Error(d.message || "移除失败");
        renderFiles([]);
        renderImportedBadge(null);
        addMsg("assistant", md("🗑️ " + (d.message || "已移除项目") +
          "\n\n画布已清空。你可以点左下角文件夹重新导入，也可以直接说「我想做一个避障小车」。"));
        if (window.RTGraph) window.RTGraph.reload();
      }).catch(function (e) {
        btn.disabled = false;
        btn.innerHTML = REMOVE_LABEL;
        if (!handleAuthError(e)) addMsg("assistant", md("⚠️ 移除失败：" + (e.message || e)));
      });
    };
  }

  // 直接粘贴截图（答辩演示时最快的一条路）
  document.addEventListener("paste", function (e) {
    var items = (e.clipboardData && e.clipboardData.items) || [];
    var files = [];
    for (var i = 0; i < items.length; i++) {
      if (items[i].kind === "file") {
        var f = items[i].getAsFile();
        if (f) files.push(f);
      }
    }
    if (files.length) {
      e.preventDefault();
      addFiles(files);
    }
  });

  // 拖拽上传
  var mask = $("drop-mask"), dragDepth = 0;
  window.addEventListener("dragenter", function (e) {
    if (e.dataTransfer && (e.dataTransfer.types || []).indexOf("Files") >= 0) {
      dragDepth++;
      mask.classList.remove("hidden");
    }
  });
  window.addEventListener("dragover", function (e) { e.preventDefault(); });
  window.addEventListener("dragleave", function () {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) mask.classList.add("hidden");
  });
  window.addEventListener("drop", function (e) {
    e.preventDefault();
    dragDepth = 0;
    mask.classList.add("hidden");
    var fl = (e.dataTransfer && e.dataTransfer.files) || [];
    if (!fl.length) return;
    // 拖进来的是 zip：当作项目导入（后端会解压），别当截图去上传
    if (fl.length === 1 && /\.zip$/i.test(fl[0].name)) {
      importProject(fl);
      return;
    }
    addFiles(fl);
  });

  function imgPayload() {
    return pendingImgs.filter(function (i) { return i.id; })
      .map(function (i) { return i.id; });
  }

  function doSend(text) {
    text = (text || "").trim();
    if (sending) return;
    if (!text && !pendingImgs.length) return;
    if (pendingImgs.some(function (i) { return i.uploading; })) {
      alert("截图还在上传，稍等一下再发");
      return;
    }
    if (!uid) {                       // 身份还没就绪，先取到再发
      ensureIdentity().then(function () { doSend(text); }).catch(function () {});
      return;
    }
    var imgs = imgPayload();
    sending = true;
    sendBtn.disabled = true;

    var userBubble = addMsg("user", md(text || "（看图）"));
    var _qc = $("quick-cards");
    if (_qc) _qc.remove();
    pendingImgs.forEach(function (it) {            // 用户气泡里回显缩略图
      var im = document.createElement("img");
      im.className = "user-thumb";
      im.src = it.local;
      userBubble.appendChild(im);
    });
    pendingImgs = [];
    renderStrip();

    input.value = "";
    autoGrow();
    optBar.innerHTML = "";

    var bubble = addMsg("assistant", "");
    var body = document.createElement("div");
    body.className = "bubble-body";
    bubble.appendChild(body);
    var ctx = {
      bubble: bubble,
      body: body,
      done: false,
      prog: makeProgress(bubble),
      stream: makeStreamer(bubble, body)
    };

    apiFetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId: sid, message: text, userId: uid, images: imgs })
    }).then(function (resp) {
      if (!resp.ok) {
        return resp.json().then(function (d) {
          throw new Error(d.error || ("HTTP " + resp.status));
        });
      }
      var reader = resp.body.getReader();
      var decoder = new TextDecoder();
      var tmp = "";
      function pump() {
        return reader.read().then(function (r) {
          if (r.done) return;
          tmp += decoder.decode(r.value, { stream: true });
          var idx;
          while ((idx = tmp.indexOf("\n\n")) >= 0) {
            var block = tmp.slice(0, idx);
            tmp = tmp.slice(idx + 2);
            block.split("\n").forEach(function (line) {
              if (line.indexOf("data:") !== 0) return;
              var payload = line.slice(5).trim();
              if (!payload || payload === "[DONE]") return;
              try { handleEvent(JSON.parse(payload), ctx); } catch (e) {}
            });
          }
          return pump();
        });
      }
      return pump();
    }).catch(function (e) {
      if (!ctx.done) {
        var msg = handleAuthError(e) ? "🔒 需要访问授权，请在上方可输入令牌。"
                                     : "⚠️ 网络异常：" + (e.message || e);
        ctx.prog.fail(msg);
        ctx.body.innerHTML = msg;
      }
    }).finally(function () {
      sending = false;
      sendBtn.disabled = false;
      input.focus();
    });
  }

  function autoGrow() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 140) + "px";
  }

  sendBtn.onclick = function () { doSend(input.value); };
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      doSend(input.value);
    }
  });
  input.addEventListener("input", autoGrow);

  // 提示点击
  document.querySelectorAll(".tips li").forEach(function (li) {
    li.onclick = function () { doSend(li.textContent); };
  });

  // 下载整个工作区：左栏按钮和架构图提交面板共用这一个出口，
  // 保证两处下到的是同一个文件（否则开了令牌校验时架构图那边会 401）。
  function downloadWorkspace() {
    var u = "/api/files/" + sid + "/zip";
    if (token) u += "?token=" + encodeURIComponent(token);   // 浏览器直下带不了 header
    window.location.href = u;
  }
  window.RTDownloadWorkspace = downloadWorkspace;
  $("download-zip").onclick = downloadWorkspace;
  if ($("open-graph")) $("open-graph").onclick = function () { openGraphPane(); };
  window.RTPulseGraphEntry = function () {
    var b = $("open-graph");
    if (!b) return;
    b.classList.remove("pulse");
    void b.offsetWidth;                 // 强制重排，让动画能重播
    b.classList.add("pulse");
    setTimeout(function () { b.classList.remove("pulse"); }, 6000);
  };

  // 健康检查
  function checkHealth() {
    var hEl = $("health");
    if (hEl) hEl.classList.add("loading");
    apiFetch("/api/health").then(function (r) { return r.json(); }).then(function (d) {
      if (hEl) hEl.classList.remove("loading");
      var dot = $("health-dot"), t = $("health-text");
      if (d.ok === false) {
        dot.className = "dot bad";
        t.textContent = "访问受限：" + (d.message || "IP 未授权");
        return;
      }
      if (d.model_configured) {
        dot.className = "dot ok";
        t.textContent = "模型已接入" + (d.auth_required ? " · 已开令牌校验" : "");
      } else {
        dot.className = "dot bad";
        t.textContent = "模型未配置（服务端密钥）";
      }
      $("kb-info").textContent = "知识库：" + d.kb_docs + " 个文档块 / " + d.kb_files + " 个文件";
      renderVersion(d.version);
    }).catch(function (e) {
      if (hEl) hEl.classList.remove("loading");
      if (handleAuthError(e)) return;
      $("health-dot").className = "dot bad";
      $("health-text").textContent = "服务不可达";
    });
  }

  // 版本徽标：目标 ROS 发行版 + 编译验证后端 + 系统版本号
  // 版本号一律由服务端 /api/meta 提供，前端不硬编码任何发行版名
  function renderVersion(v) {
    var box = $("ver-badge");
    if (!box) return;
    if (!v || !v.ros) {
      box.innerHTML = '<span class="ver-row">版本信息不可用</span>';
      return;
    }
    var sb = v.sandbox || {};
    var back = sb.available
      ? "编译验证：可用（" + (sb.backend || "?") + "）"
      : "编译验证：未启用（" + (sb.reason || "环境不支持") + "）";
    box.innerHTML =
      '<div class="ver-row"><b>' + (v.ros.short || "") + '</b>' +
      '<span class="ver-dim">Ubuntu ' + (v.ros.ubuntu || "") +
      ' · Python ' + (v.ros.python || "") + '</span></div>' +
      '<div class="ver-row ver-dim">' + back + '</div>' +
      '<div class="ver-row ver-dim">系统版本 v' + (v.app_version || "?") + '</div>';
  }

  // 令牌输入
  var tokBtn = $("token-ok");
  if (tokBtn) {
    tokBtn.onclick = function () {
      var v = ($("token-input").value || "").trim();
      if (!v) { $("token-err").textContent = "请输入访问令牌"; return; }
      token = v;
      localStorage.setItem(TOKEN_KEY, v);
      hideGate();
      boot();
    };
    $("token-input").addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); tokBtn.click(); }
    });
  }

  // ---------------- 历史会话（切回旧对话） ----------------
  function relTime(sec) {
    if (!sec) return "";
    var d = Math.max(0, (Date.now() / 1000) - sec);
    if (d < 60) return "刚刚";
    if (d < 3600) return Math.floor(d / 60) + " 分钟前";
    if (d < 86400) return Math.floor(d / 3600) + " 小时前";
    if (d < 86400 * 7) return Math.floor(d / 86400) + " 天前";
    var t = new Date(sec * 1000);
    return (t.getMonth() + 1) + "/" + t.getDate();
  }

  function renderHistoryList() {
    var el = $("history-list");
    if (!el) return;
    apiFetch("/api/sessions").then(function (r) { return r.json(); }).then(function (d) {
      var list = (d && d.sessions) || [];
      if (!list.length) {
        el.className = "history-empty";
        el.textContent = "暂无历史会话";
        return;
      }
      el.className = "history-list";
      el.innerHTML = "";
      list.forEach(function (s) {
        var item = document.createElement("div");
        item.className = "hist-item" + (s.sessionId === sid ? " active" : "");
        var meta = [];
        if (s.nodes_total) meta.push(s.nodes_total + " 节点");
        if (s.updated) meta.push(relTime(s.updated));
        item.innerHTML =
          '<div class="hist-body">' +
            '<div class="hist-title"></div>' +
            '<div class="hist-meta"></div>' +
          '</div>' +
          '<div class="hist-actions">' +
            '<button class="hist-btn" data-act="archive" title="归档（打包保留，可找回）">' +
              '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v9a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9"/><path d="M9 13h6"/></svg>' +
            '</button>' +
            '<button class="hist-btn danger" data-act="delete" title="删除（不可恢复）">' +
              '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/><path d="M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13"/></svg>' +
            '</button>' +
          '</div>';
        item.querySelector(".hist-title").textContent = (s.title || "新对话");
        item.querySelector(".hist-meta").textContent = meta.join(" · ") || " ";
        item.title = s.preview || "";
        var body = item.querySelector(".hist-body");
        body.onclick = function () { switchSession(s.sessionId); };
        // 操作按钮：阻止冒泡，避免点按钮误触发切换
        item.querySelector('[data-act="archive"]').onclick = function (e) {
          e.stopPropagation(); sessionAction(s.sessionId, "archive");
        };
        item.querySelector('[data-act="delete"]').onclick = function (e) {
          e.stopPropagation(); sessionAction(s.sessionId, "delete");
        };
        el.appendChild(item);
      });
    }).catch(function (e) { handleAuthError(e); });
  }

  // 删除 / 归档会话：删除需二次确认；操作当前会话时切到新会话
  function sessionAction(targetSid, action) {
    var isDelete = action === "delete";
    var label = isDelete ? "删除" : "归档";
    if (isDelete && !confirm("确定删除这段对话吗？删除后不可恢复。")) return;
    apiFetch("/api/session/" + targetSid + "/" + action, { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) throw new Error(d.message || ("操作失败"));
        // 如果操作的是当前会话，切到新会话
        if (targetSid === sid) {
          var nsid = "s" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
          sid = nsid;
          localStorage.setItem("ros2tutor_sid", nsid);
        }
        renderHistoryList();
        if (targetSid !== sid) return;
        loadCurrentSession();
      })
      .catch(function (e) {
        if (!handleAuthError(e)) alert(label + "失败：" + (e.message || e));
      });
  }

  function clearChat() {
    chat.innerHTML = "";
    optBar.innerHTML = "";
  }

  function renderWelcome() {
    addMsg("assistant", md(
      "你好呀！我是你的 ROS2 学习助手 🤖\n\n" +
      "我可以帮你：\n" +
      "- **做项目**：从需求分析 → 节点规划 → 逐节点生成代码 → 编译验证（ROS 2 Jazzy / Python）\n" +
      "- **写节点**：「帮我写一个发布者节点」\n" +
      "- **排报错**：把报错信息贴给我\n" +
      "- **讲概念 / 出练习题**：随时问我\n\n" +
      "先跟我说说你最近在学什么吧～"));
    renderQuickCards();
  }

  function loadCurrentSession() {
    return apiFetch("/api/session/" + sid)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        renderSidebar(d);
        clearChat();
        // 回放用 transcript（含项目流水线），为空时退回 history，再空才欢迎语
        var hist = (d && d.transcript && d.transcript.length)
          ? d.transcript : ((d && d.history) || []);
        if (!hist.length) {
          renderWelcome();
        } else {
          hist.forEach(function (h) {
            if (h.role === "user") addMsg("user", md(h.content || ""));
            else addMsg("assistant", md(h.content || ""));
          });
        }
        // 恢复待办选项：固定选项（规划完成后的 A/B/C/D、C/D）切回后仍可点
        var pend = (d && d.pending) || "";
        if (pend === "options") {
          renderOptions([
            { key: "A", label: "规划可以，就按这个来", send: "A" },
            { key: "B", label: "我要修改规划（说下怎么改）", send: "B" },
            { key: "C", label: "帮我生成某个节点的代码（说节点名）", send: "C" },
            { key: "D", label: "帮我生成全部节点代码", send: "D" }
          ], "abcd");
        } else if (pend === "options_cd") {
          renderOptions([
            { key: "C", label: "生成某个节点的代码（输入节点名）", send: "C" },
            { key: "D", label: "生成全部节点代码", send: "D" }
          ], "cd");
        }
        scrollBottom();
      })
      .catch(function (e) { handleAuthError(e); });
  }

  function switchSession(targetSid) {
    if (!targetSid || targetSid === sid) return;
    if (sending) { alert("当前消息还在生成中，稍候再切换"); return; }
    sid = targetSid;
    localStorage.setItem("ros2tutor_sid", sid);
    loadCurrentSession().then(renderHistoryList);
  }

  $("new-chat").onclick = function () {
    if (sending) { alert("当前消息还在生成中，稍候再开新对话"); return; }
    var nsid = "s" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
    localStorage.setItem("ros2tutor_sid", nsid);
    location.reload();          // uid 不变，学习画像与历史会话照旧带过来
  };

  // 快捷任务卡片
  function renderQuickCards() {
    var wrap = document.createElement("div");
    wrap.id = "quick-cards";
    wrap.innerHTML =
      '<div class="qc-title">开始你的 ROS2 学习</div>' +
      '<div class="qc-grid">' +
        '<button class="qc-card" data-send="我想做一个避障小车项目"><b>做一个项目</b><span>从需求分析到节点代码、编译验证</span></button>' +
        '<button class="qc-card" data-send="帮我写一个订阅激光雷达的节点"><b>写一个节点</b><span>描述功能，直接生成单个节点</span></button>' +
        '<button class="qc-card" data-send="什么是话题（topic）？"><b>讲一个概念</b><span>话题、服务、QoS 等随时问</span></button>' +
      '</div>';
    chat.appendChild(wrap);
    wrap.querySelectorAll(".qc-card").forEach(function (c) {
      c.onclick = function () { doSend(c.getAttribute("data-send")); };
    });
  }

  // 暗色模式
  function applyTheme(dark) {
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
    var label = $("theme-label");
    if (label) label.textContent = dark ? "浅色模式" : "深色模式";
  }
  function initTheme() {
    var saved = localStorage.getItem("ros2tutor_theme");
    var dark = saved ? saved === "dark"
      : !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
    applyTheme(dark);
    var btn = $("theme-toggle");
    if (btn) btn.onclick = function () {
      var next = document.documentElement.getAttribute("data-theme") !== "dark";
      applyTheme(next);
      localStorage.setItem("ros2tutor_theme", next ? "dark" : "light");
    };
  }

  // 移动端抽屉
  function initDrawer() {
    var menu = $("menu-btn"), mask = $("drawer-mask"), sidebar = $("sidebar");
    if (!menu || !mask || !sidebar) return;
    function open() {
      sidebar.classList.add("open");
      mask.classList.remove("hidden");
      requestAnimationFrame(function () { mask.classList.add("show"); });
    }
    function close() {
      sidebar.classList.remove("open");
      mask.classList.remove("show");
      setTimeout(function () { mask.classList.add("hidden"); }, 220);
    }
    menu.onclick = function () { sidebar.classList.contains("open") ? close() : open(); };
    mask.onclick = close;
  }

  // ---------------- 生成完毕后的「要不要看架构图」邀请 ----------------
  // 项目流水线跑完，代码已经落进 ros2_ws 了，这时候才谈得上"改图"。
  // 是否弹这张卡由顶栏开关控制，关掉就不再打扰。
  var ASK_KEY = "rt_ask_graph";

  function askGraphOn() {
    try { return localStorage.getItem(ASK_KEY) !== "0"; } catch (e) { return true; }
  }
  function setAskGraph(on) {
    try { localStorage.setItem(ASK_KEY, on ? "1" : "0"); } catch (e) {}
    var btn = $("ask-graph-toggle");
    if (!btn) return;
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.title = on
      ? "代码生成完成后会问你：要不要打开架构图手动调整（点击关闭）"
      : "已关闭：生成后不再提示看架构图（点击开启）";
  }
  function openGraphPane() {
    var pane = $("graph-pane");
    if (pane) {
      pane.classList.remove("hidden");
      pane.classList.remove("collapsed");
    }
    if (window.RTGraph) {
      // 面板是我们先展开的，graph.js 的「首次显示自动 fit」就不会触发，
      // 这里必须显式补一次，否则从入口打开时图还是上次的缩放，节点跑出画面。
      window.RTGraph.reload().then(function (d) {
        var n = ((d && d.nodes) || []).length;
        if (!n) {
          // 纯 C++ 项目、或 .py 里没有 rclpy 节点：图里本来就没东西，
          // 面板会自己收起来。不说清楚，用户会以为按钮坏了。
          var cmake = ((d && d.packages) || []).some(function (p) {
            return (p.build_type || "").indexOf("cmake") >= 0;
          });
          window.RT.toast(cmake
            ? "这个项目是 C++（ament_cmake）的，架构图目前只解析 Python 节点，所以图上是空的；左侧文件树、看代码、下载 zip 都正常。"
            : "这个项目里没找到 rclpy 节点（没有 create_publisher / create_subscription），架构图暂时是空的。");
          return;
        }
        setTimeout(function () { window.RTGraph.fit(); }, 80);
      });
    }
  }
  function graphOffer(info) {
    var d = document.createElement("div");
    d.className = "graph-offer";
    d.innerHTML =
      '<div class="go-title">代码已经写进项目了 · ' + (info.nodes || 0) + " 个节点</div>" +
      '<div class="go-desc">要不要打开架构图看一眼？图上能改话题名、右键增删节点、' +
      "从端口拖线重连。这些操作只改图纸，点右上角「提交」才会同步回代码。</div>" +
      '<div class="go-actions">' +
      '<button class="mini-btn primary" data-act="yes">打开架构图</button>' +
      '<button class="mini-btn" data-act="no">先不看</button>' +
      '<label class="go-remember"><input type="checkbox" data-act="quiet">以后不问</label>' +
      "</div>";
    d.addEventListener("click", function (ev) {
      var act = ev.target && ev.target.getAttribute && ev.target.getAttribute("data-act");
      if (act === "yes") { openGraphPane(); d.classList.add("gone"); }
      else if (act === "no") { d.classList.add("gone"); }
    });
    var quiet = d.querySelector('[data-act="quiet"]');
    if (quiet) quiet.onchange = function () { if (quiet.checked) setAskGraph(false); };
    return d;
  }
  function initAskGraph() {
    setAskGraph(askGraphOn());          // 把保存的状态同步到开关上
    var btn = $("ask-graph-toggle");
    if (btn) {
      btn.onclick = function () {
        setAskGraph(btn.getAttribute("aria-pressed") !== "true");
      };
    }
  }

  function boot() {
    ensureIdentity().then(function () {
      checkHealth();
      loadCurrentSession();
      renderHistoryList();
      refreshProfile();
    }).catch(function (e) {
      var dot = $("health-dot"), t = $("health-text");
      if (dot) dot.className = "dot bad";
      if (t) t.textContent = "身份获取失败：" + (e.message || e);
      showGate("无法建立身份，请刷新页面重试");
    });
  }

  // 对外桥接：架构图模块（graph.js）复用同一套身份、令牌与会话号
  window.RT = {
    getSid: function () { return sid; },
    apiFetch: apiFetch,
    md: md,
    toast: function (text, kind) {
      addMsg("assistant", md((kind === "error" ? "⚠️ " : "") + text));
    }
  };

  initTheme();
  initDrawer();
  initAskGraph();
  boot();
  input.focus();
})();
