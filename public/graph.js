/* 架构图：SVG 自绘的可视化编辑画布（无第三方依赖，断网可用） */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var NS = "http://www.w3.org/2000/svg";
  var W = 138, H = 48;              // 节点尺寸（世界坐标）
  // 间距要留够「话题标签」的宽度，否则悬停/高亮时标签会压到两侧节点上
  var GAP_X = 132, GAP_Y = 46;
  var MIN_K = 0.25, MAX_K = 2;

  var svg = $("gp-svg"), canvas = $("gp-canvas"), pane = $("graph-pane");
  var gRoot, gEdge, gNode, gLabel, gTemp;
  var drawer = $("gp-drawer"), hint = $("gp-hint"), empty = $("gp-empty");

  var state = {
    graph: null,
    k: 1, tx: 0, ty: 0,
    positions: {},        // id -> {x,y}（用户拖过的位置）
    renames: {},
    deleted: [],
    draftNodes: [],
    draftLinks: [],
    sel: null,
    tab: "overview",
    code: null,           // {id, path, sha, content, editing}
    intros: {},
    drag: null,
    linking: null,
    saving: false
  };

  // ---------------- 基础工具 ----------------
  function sid() {
    try { return (window.RT && RT.getSid()) || localStorage.getItem("ros2tutor_sid") || ""; }
    catch (e) { return ""; }
  }
  function api(url, opt) {
    if (window.RT && RT.apiFetch) return RT.apiFetch(url, opt);
    return fetch(url, opt);
  }
  function mk(tag, attrs, parent) {
    var el = document.createElementNS(NS, tag);
    for (var k in attrs) if (attrs[k] !== undefined && attrs[k] !== null) el.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(el);
    return el;
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function say(text, ms) {
    if (!hint) return;
    hint.textContent = text;
    hint.classList.add("show");
    clearTimeout(say._t);
    say._t = setTimeout(function () { hint.classList.remove("show"); }, ms || 4200);
  }
  function debounce(fn, ms) {
    var t = null;
    return function () {
      var a = arguments, self = this;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(self, a); }, ms);
    };
  }

  // ---------------- 布局 ----------------
  function autoLayout(nodes, edges) {
    var ids = {}, indeg = {}, out = {};
    nodes.forEach(function (n) { ids[n.id] = n; indeg[n.id] = 0; out[n.id] = []; });
    edges.forEach(function (e) {
      if (indeg[e.to] === undefined || out[e.from] === undefined) return;
      indeg[e.to]++;
      out[e.from].push(e.to);
    });
    // 最长路径分层：传感器/驱动在最左，输出在最右
    var level = {}, queue = [], deg = {};
    Object.keys(indeg).forEach(function (k) {
      deg[k] = indeg[k];
      if (!indeg[k]) { queue.push(k); level[k] = 0; }
    });
    var guard = 0;
    while (queue.length && guard++ < 10000) {
      var cur = queue.shift();
      (out[cur] || []).forEach(function (nx) {
        level[nx] = Math.max(level[nx] || 0, level[cur] + 1);
        if (--deg[nx] === 0) queue.push(nx);
      });
    }
    Object.keys(ids).forEach(function (k) { if (level[k] === undefined) level[k] = 0; });

    var cols = {};
    Object.keys(level).forEach(function (k) {
      var l = level[k];
      (cols[l] = cols[l] || []).push(k);
    });
    var maxRows = 1;
    Object.keys(cols).forEach(function (l) { maxRows = Math.max(maxRows, cols[l].length); });

    nodePos = {};
    Object.keys(cols).sort(function (a, b) { return a - b; }).forEach(function (l) {
      var col = cols[l], h = col.length;
      col.forEach(function (id, i) {
        var y = (maxRows - h) * (H + GAP_Y) / 2 + i * (H + GAP_Y);
        nodePos[id] = { x: Number(l) * (W + GAP_X), y: y };
      });
    });
  }

  var nodePos = {};

  function posOf(id) {
    if (state.positions[id]) return state.positions[id];
    return nodePos[id] || { x: 0, y: 0 };
  }

  // ---------------- 渲染 ----------------
  function ensureLayers() {
    svg.innerHTML = "";
    var defs = mk("defs", {}, svg);
    var marker = mk("marker", {
      id: "gArrow", viewBox: "0 0 10 10", refX: "9", refY: "5",
      markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse"
    }, defs);
    mk("path", { d: "M1 1L9 5L1 9", fill: "none", stroke: "context-stroke",
                 "stroke-width": "1.6", "stroke-linecap": "round",
                 "stroke-linejoin": "round" }, marker);
    gRoot = mk("g", { id: "gRoot" }, svg);
    gEdge = mk("g", { id: "gEdge" }, gRoot);
    gNode = mk("g", { id: "gNode" }, gRoot);
    gLabel = mk("g", { id: "gLabel" }, gRoot);
    gTemp = mk("g", { id: "gTemp" }, gRoot);
  }

  function applyTransform() {
    if (gRoot) gRoot.setAttribute("transform",
      "translate(" + state.tx + "," + state.ty + ") scale(" + state.k + ")");
    var z = $("gp-zoom");
    if (z) z.textContent = Math.round(state.k * 100) + "%";
  }

  function edgePath(a, b) {
    var x1 = a.x + W, y1 = a.y + H / 2;
    var x2 = b.x, y2 = b.y + H / 2;
    if (x2 > x1 + 30) {
      var mx = (x1 + x2) / 2, r = 7;
      var dir = y2 >= y1 ? 1 : -1;
      var dy = Math.abs(y2 - y1) < 2 ? 0 : 1;
      if (!dy) return "M" + x1 + " " + y1 + " H" + x2;
      return "M" + x1 + " " + y1 +
        " H" + (mx - r) + " Q" + mx + " " + y1 + " " + mx + " " + (y1 + dir * r) +
        " V" + (y2 - dir * r) + " Q" + mx + " " + y2 + " " + (mx + r) + " " + y2 +
        " H" + x2;
    }
    // 回边：从上方绕回来，避免穿过节点
    var top = Math.min(a.y, b.y) - 34;
    return "M" + x1 + " " + y1 + " H" + (x1 + 26) +
      " V" + top + " H" + (x2 - 26) + " V" + y2 + " H" + x2;
  }

  function render() {
    if (!state.graph) return;
    ensureLayers();
    var nodes = state.graph.nodes || [];
    var edges = state.graph.edges || [];

    autoLayout(nodes, edges);
    Object.keys(state.positions).forEach(function (id) {
      if (nodePos[id]) nodePos[id] = state.positions[id];
    });

    var byId = {};
    nodes.forEach(function (n) { byId[n.id] = n; });

    // 连线
    edges.forEach(function (e, idx) {
      if (!byId[e.from] || !byId[e.to]) return;
      var a = posOf(e.from), b = posOf(e.to);
      var d = edgePath(a, b);
      var g = mk("g", { "data-edge": idx }, gEdge);
      var hit = mk("path", { d: d, class: "ge-hit" }, g);
      var p = mk("path", { d: d, class: "ge-path" + (e.draft ? " draft" : ""),
                           "marker-end": "url(#gArrow)" }, g);
      var label = null;

      function showLabel(show) {
        if (show && !label) {
          // 标签挂在「路径上的实际一点」，不是两个节点的几何中点。
          // 线一旦绕行（节点被拖远、跨层），几何中点会飘到空白处，
          // 取 path 长度 50% 处的坐标才永远落在线身上。
          var mx = (a.x + W + b.x) / 2, my = (a.y + b.y) / 2 + H / 2 - 12;
          try {
            var L = p.getTotalLength();
            if (L > 0) {
              var pt = p.getPointAtLength(L * 0.5);
              if (pt && isFinite(pt.x) && isFinite(pt.y)) { mx = pt.x; my = pt.y; }
            }
          } catch (err) { /* 刚插入还没布局，退回几何中点 */ }
          var text = (e.topic || "（未命名）") + (e.type ? " · " + shortType(e.type) : "");
          var w = text.length * 6.4 + 14;
          label = mk("g", { opacity: "0", "data-edge": idx }, gLabel);
          mk("rect", { x: mx - w / 2, y: my - 9, width: w, height: 18,
                       rx: 4, class: "ge-label-bg" }, label);
          var t = mk("text", { x: mx, y: my, "text-anchor": "middle",
                               "dominant-baseline": "central", class: "ge-label" }, label);
          t.textContent = text;
        }
        if (label) label.setAttribute("opacity", show ? "1" : "0");
      }

      hit.addEventListener("pointerenter", function () {
        p.classList.add("hl");
        nodes.forEach(function (n) {
          var gEl = document.querySelector('[data-node="' + cssEsc(n.id) + '"]');
          if (gEl && n.id !== e.from && n.id !== e.to) gEl.classList.add("g-dim");
        });
        showLabel(true);
        say(e.topic + "  ·  " + (e.type || "类型未知") + "  ·  " +
            name(byId[e.from]) + " → " + name(byId[e.to]) +
            "（点线可改话题名）", 6000);
      });
      hit.addEventListener("pointerleave", function () {
        p.classList.remove("hl");
        Array.prototype.forEach.call(document.querySelectorAll("#gNode .g-dim"),
          function (el) { el.classList.remove("g-dim"); });
        // 只认悬停：鼠标走了就收起来。以前这里还判断「节点被选中就留着」，
        // 结果点一下节点，它所有连线的名字都钉在图上，看着像 bug。
        showLabel(false);
      });
      hit.addEventListener("click", function (ev) {
        ev.stopPropagation();
        openRename(e, ev);
      });
    });

    // 节点
    nodes.forEach(function (n) {
      var p = posOf(n.id);
      var g = mk("g", { "data-node": n.id, transform: "translate(" + p.x + "," + p.y + ")" }, gNode);
      var isSel = state.sel === n.id;
      mk("rect", { width: W, height: H, rx: 8,
                   class: "gn-box" + (n.status === "draft" ? " draft" : "") +
                          (isSel ? " selected" : "") }, g);
      var nm = mk("text", { x: W / 2, y: n.status === "draft" ? H / 2 - 7 : H / 2 - 5,
                            "text-anchor": "middle", class: "gn-name" }, g);
      nm.textContent = trunc(n.name, 17);
      var sub = n.status === "draft" ? "未生成 · 待提需求"
        : (n.package ? n.package : "未识别包");
      var st = mk("text", { x: W / 2, y: H / 2 + 11, "text-anchor": "middle",
                            class: "gn-sub" }, g);
      st.textContent = trunc(sub, 22);
      if (n.orphan && n.status !== "draft") {
        var bd = mk("text", { x: W - 7, y: 13, "text-anchor": "end", class: "gn-badge" }, g);
        bd.textContent = "孤立";
      }

      var ports = mk("g", { opacity: "0" }, g);
      var pin = mk("circle", { cx: 0, cy: H / 2, r: 4.5, class: "gport" }, ports);
      var pout = mk("circle", { cx: W, cy: H / 2, r: 4.5, class: "gport" }, ports);
      mk("title", {}, pin).textContent = "从这里拖到另一个节点：当前节点发布 → 对方订阅";
      mk("title", {}, pout).textContent = "从输出端拖到对方的输入端";

      g.addEventListener("pointerenter", function () { ports.setAttribute("opacity", "1"); });
      g.addEventListener("pointerleave", function () { ports.setAttribute("opacity", "0"); });
      g.addEventListener("pointerdown", function (ev) {
        if (ev.button !== 0) return;
        ev.stopPropagation();
        state.drag = { id: n.id, sx: ev.clientX, sy: ev.clientY,
                       ox: p.x, oy: p.y, moved: false };
        svg.setPointerCapture(ev.pointerId);
      });
      g.addEventListener("click", function (ev) {
        if (state.suppressClick) { state.suppressClick = false; return; }
        ev.stopPropagation();
        openDrawer(n.id);
      });

      pout.addEventListener("pointerdown", function (ev) {
        ev.stopPropagation(); ev.preventDefault();
        startLink(n.id, ev);
      });
      pin.addEventListener("pointerdown", function (ev) {
        ev.stopPropagation(); ev.preventDefault();
        startLink(n.id, ev, true);
      });
    });

    empty.classList.toggle("hidden", nodes.length > 0);
    applyTransform();
    renderMeta();
  }

  function cssEsc(s) { return String(s).replace(/["\\]/g, "\\$&"); }
  function trunc(s, n) {
    s = String(s == null ? "" : s);
    return s.length > n ? s.slice(0, n - 1) + "…" : s;
  }
  function shortType(t) {
    if (!t) return "";
    var p = String(t).split("/");
    return p.length === 3 ? p[2] : t;
  }
  function nodeById(id) {
    return (state.graph && state.graph.nodes || []).filter(function (n) { return n.id === id; })[0];
  }
  function name(n) { return n ? n.name : "?"; }

  function renderMeta() {
    var g = state.graph || {}, st = g.stats || {};
    var txt = (st.nodes || 0) + " 节点 · " + (st.edges || 0) + " 连线";
    if (st.pending) txt += " · " + st.pending + " 处待提交";
    if (st.unresolved) txt += " · " + st.unresolved + " 处未解析";
    var m = $("gp-meta");
    if (m) m.textContent = txt;
    var btn = $("gp-commit");
    if (btn) {
      btn.textContent = st.pending ? "提交（" + st.pending + "）" : "提交";
      btn.classList.toggle("has-pending", !!st.pending);
    }
  }

  // ---------------- 画布缩放 / 平移 ----------------
  function worldPoint(ev) {
    var r = canvas.getBoundingClientRect();
    return { x: (ev.clientX - r.left - state.tx) / state.k,
             y: (ev.clientY - r.top - state.ty) / state.k };
  }

  svg.addEventListener("wheel", function (ev) {
    ev.preventDefault();
    var r = canvas.getBoundingClientRect();
    var mx = ev.clientX - r.left, my = ev.clientY - r.top;
    var factor = ev.deltaY < 0 ? 1.12 : 1 / 1.12;
    var nk = clamp(state.k * factor, MIN_K, MAX_K);
    if (nk === state.k) return;
    state.tx = mx - (mx - state.tx) * (nk / state.k);
    state.ty = my - (my - state.ty) * (nk / state.k);
    state.k = nk;
    applyTransform();
  }, { passive: false });

  svg.addEventListener("pointerdown", function (ev) {
    if (ev.target !== svg) return;
    state.pan = { sx: ev.clientX, sy: ev.clientY, tx: state.tx, ty: state.ty };
    svg.setPointerCapture(ev.pointerId);
    canvas.classList.add("dragging");
  });

  svg.addEventListener("pointermove", function (ev) {
    if (state.pan) {
      state.tx = state.pan.tx + (ev.clientX - state.pan.sx);
      state.ty = state.pan.ty + (ev.clientY - state.pan.sy);
      applyTransform();
      return;
    }
    if (state.drag) {
      var dx = (ev.clientX - state.drag.sx) / state.k;
      var dy = (ev.clientY - state.drag.sy) / state.k;
      if (Math.abs(dx) > 2 || Math.abs(dy) > 2) state.drag.moved = true;
      var nx = state.drag.ox + dx, ny = state.drag.oy + dy;
      nodePos[state.drag.id] = { x: nx, y: ny };
      state.positions[state.drag.id] = { x: Math.round(nx), y: Math.round(ny) };
      var el = document.querySelector('[data-node="' + cssEsc(state.drag.id) + '"]');
      if (el) el.setAttribute("transform", "translate(" + nx + "," + ny + ")");
      redrawEdgesOnly();
      return;
    }
    if (state.linking) {
      var w = worldPoint(ev);
      var d = "M" + state.linking.x + " " + state.linking.y +
              " C" + (state.linking.x + 60) + " " + state.linking.y + " " +
              (w.x - 60) + " " + w.y + " " + w.x + " " + w.y;
      state.linking.path.setAttribute("d", d);
    }
  });

  svg.addEventListener("pointerup", function (ev) {
    canvas.classList.remove("dragging");
    if (state.pan) { state.pan = null; return; }
    if (state.drag) {
      var moved = state.drag.moved;
      state.drag = null;
      if (moved) { state.suppressClick = true; saveDraft(); }
      return;
    }
    if (state.linking) {
      var w = worldPoint(ev);
      var target = hitNode(w.x, w.y);
      finishLink(target, ev);
    }
  });

  function redrawEdgesOnly() {
    var edges = (state.graph && state.graph.edges) || [];
    var paths = gEdge.querySelectorAll("g[data-edge]");
    edges.forEach(function (e, i) {
      var g = paths[i];
      if (!g) return;
      var d = edgePath(posOf(e.from), posOf(e.to));
      g.querySelectorAll("path").forEach(function (p) { p.setAttribute("d", d); });
    });
  }

  function hitNode(x, y) {
    var hit = null;
    (state.graph.nodes || []).forEach(function (n) {
      var p = posOf(n.id);
      if (x >= p.x - 6 && x <= p.x + W + 6 && y >= p.y - 6 && y <= p.y + H + 6) hit = n.id;
    });
    return hit;
  }

  // ---------------- 拖线建连 ----------------
  function startLink(fromId, ev, reverse) {
    var p = posOf(fromId);
    state.linking = {
      from: fromId, reverse: !!reverse,
      x: reverse ? p.x : p.x + W, y: p.y + H / 2,
      path: mk("path", { class: "gtemp" }, gTemp)
    };
    try { svg.setPointerCapture(ev.pointerId); } catch (e) {}
    say("拖到目标节点后松手，会问你话题名和消息类型", 6000);
  }

  function finishLink(targetId, ev) {
    var link = state.linking;
    state.linking = null;
    if (link.path) link.path.remove();
    if (!targetId || targetId === link.from) {
      if (targetId === link.from) say("不能连自己");
      return;
    }
    var from = link.reverse ? targetId : link.from;
    var to = link.reverse ? link.from : targetId;
    connectPop(from, to);
  }

  function guessTopic(from, to) {
    var a = nodeById(from), b = nodeById(to);
    if (!a || !b) return { topic: "", type: "" };
    // 1) 两端已有同名话题（很可能是同一个数据流）
    var ap = (a.publishes || []).filter(function (i) { return i.resolved !== false; });
    var bs = (b.subscribes || []).filter(function (i) { return i.resolved !== false; });
    for (var i = 0; i < ap.length; i++) {
      for (var j = 0; j < bs.length; j++) {
        if (ap[i].topic === bs[j].topic) return { topic: ap[i].topic, type: ap[i].type };
      }
    }
    // 2) 已有发布者的话题，直接沿用
    if (ap.length) return { topic: ap[0].topic, type: ap[0].type };
    // 3) 订阅方已有的话题
    if (bs.length) return { topic: bs[0].topic, type: bs[0].type };
    return { topic: "/" + (a.name || "topic") + "_out", type: "" };
  }

  function connectPop(from, to) {
    closePops();
    var a = nodeById(from), b = nodeById(to);
    var g = guessTopic(from, to);
    var topics = (state.graph.topics || []);
    var types = state.graph.msg_types || [];
    var r = canvas.getBoundingClientRect();
    var pop = document.createElement("div");
    pop.className = "gp-pop";
    pop.style.left = Math.max(10, Math.min(r.width - 230, (state.lastX || 200))) + "px";
    pop.style.top = Math.max(10, Math.min(r.height - 250, (state.lastY || 120))) + "px";
    pop.innerHTML =
      '<h4>' + esc(a.name) + " → " + esc(b.name) + " · 新建连接</h4>" +
      '<input id="pp-topic" value="' + esc(g.topic) + '" placeholder="话题名，如 /scan">' +
      '<div class="row"><select id="pp-type">' +
      '<option value="">（待定，生成时再定）</option>' +
      types.map(function (t) {
        return '<option value="' + t + '"' + (t === g.type ? " selected" : "") + ">" + t + "</option>";
      }).join("") +
      "</select></div>" +
      '<div class="tip" id="pp-tip"></div>' +
      '<div class="row"><button class="mini-btn" id="pp-cancel">取消</button>' +
      '<button class="mini-btn primary" id="pp-ok">连上</button></div>';
    canvas.appendChild(pop);
    var tip = pop.querySelector("#pp-tip");
    function updateTip() {
      var t = pop.querySelector("#pp-topic").value.trim();
      var same = topics.filter(function (x) { return x.name === t; })[0];
      if (same) {
        tip.textContent = "已有同名话题（" + (same.types[0] || "类型未知") +
          "），会直接接上去，类型自动继承。";
        var sel = pop.querySelector("#pp-type");
        if (same.types[0] && !sel.value) sel.value = same.types[0];
      } else {
        tip.textContent = "新建话题。类型留空也能连，但提交前必须定下来。";
      }
    }
    pop.querySelector("#pp-topic").addEventListener("input", updateTip);
    updateTip();
    pop.querySelector("#pp-cancel").onclick = function () { pop.remove(); };
    pop.querySelector("#pp-ok").onclick = function () {
      var topic = pop.querySelector("#pp-topic").value.trim() || g.topic;
      var type = pop.querySelector("#pp-type").value;
      if (!topic.startsWith("/")) topic = "/" + topic;
      addLink(from, to, topic, type);
      pop.remove();
    };
    pop.querySelector("#pp-topic").focus();
  }

  function addLink(from, to, topic, type) {
    state.draftLinks = state.draftLinks.filter(function (l) {
      return !(l.from === from && l.to === to && l.topic === topic);
    });
    state.draftLinks.push({ from: from, to: to, topic: topic, type: type });

    // 一端是图纸节点：把接口写进它的契约，生成代码时照这个写
    [from, to].forEach(function (id, idx) {
      var dn = state.draftNodes.filter(function (d) { return d.id === id; })[0];
      if (!dn) return;
      var field = idx === 0 ? "publishes" : "subscribes";
      dn[field] = dn[field] || [];
      if (!dn[field].some(function (i) { return i.topic === topic; })) {
        dn[field].push({ topic: topic, type: type, resolved: true, line: 0 });
      }
    });
    saveDraft().then(function () { reload(true); });
  }

  // ---------------- 改话题名 ----------------
  function openRename(edge, ev) {
    closePops();
    var same = (state.graph.topics || []).filter(function (t) { return t.name === edge.topic; })[0];
    var refs = same ? (same.pubs.length + same.subs.length) : 2;
    var r = canvas.getBoundingClientRect();
    var pop = document.createElement("div");
    pop.className = "gp-pop";
    pop.style.left = Math.max(10, Math.min(r.width - 230, ev.clientX - r.left)) + "px";
    pop.style.top = Math.max(10, Math.min(r.height - 190, ev.clientY - r.top)) + "px";
    pop.innerHTML =
      '<h4>改话题名 · 同名一起改</h4>' +
      '<input id="rn-val" value="' + esc(edge.topic) + '">' +
      '<div class="tip">这个话题有 ' + refs + ' 个端点，改名会同时影响它们。' +
      '只改图纸，点「提交」才写进代码。</div>' +
      '<div class="row"><button class="mini-btn" id="rn-cancel">取消</button>' +
      '<button class="mini-btn primary" id="rn-ok">改名</button></div>';
    canvas.appendChild(pop);
    var input = pop.querySelector("#rn-val");
    input.focus(); input.select();
    pop.querySelector("#rn-cancel").onclick = function () { pop.remove(); };
    function apply() {
      var v = input.value.trim();
      if (!v) return;
      if (!v.startsWith("/")) v = "/" + v;
      if (v === edge.topic) { pop.remove(); return; }
      // 同一话题之前改过名的话，把旧名字串起来
      var oldName = edge.topic;
      Object.keys(state.renames).forEach(function (k) {
        if (state.renames[k] === oldName) oldName = k;
      });
      state.renames[oldName] = v;
      pop.remove();
      saveDraft().then(function () { reload(true); });
    }
    pop.querySelector("#rn-ok").onclick = apply;
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); apply(); }
      if (e.key === "Escape") pop.remove();
    });
  }

  function closePops() {
    Array.prototype.forEach.call(canvas.querySelectorAll(".gp-pop"),
      function (p) { p.remove(); });
  }

  // ---------------- 新建节点 ----------------
  function newNodePop() {
    closePops();
    var pkg = (state.graph && state.graph.park) || "";
    var pop = document.createElement("div");
    pop.className = "gp-pop";
    pop.style.left = Math.round(canvas.clientWidth / 2 - 110) + "px";
    pop.style.top = "70px";
    pop.innerHTML =
      "<h4>新建节点</h4>" +
      '<input id="nn-name" placeholder="节点名，如 my_filter">' +
      '<div style="height:6px"></div>' +
      '<textarea id="nn-req" rows="3" placeholder="这个节点要做什么？例如：把 /scan 降采样后转发到 /scan_filtered"></textarea>' +
      '<div class="tip">建出来是虚线空壳，不会立刻生成代码。先拉线定好接口，提交时才写代码。</div>' +
      '<div class="row"><button class="mini-btn" id="nn-cancel">取消</button>' +
      '<button class="mini-btn primary" id="nn-ok">建节点</button></div>';
    canvas.appendChild(pop);
    var nm = pop.querySelector("#nn-name");
    nm.focus();
    pop.querySelector("#nn-cancel").onclick = function () { pop.remove(); };
    pop.querySelector("#nn-ok").onclick = function () {
      var name = nm.value.trim().replace(/[^0-9a-zA-Z_]/g, "_").replace(/^_+|_+$/g, "");
      if (!name) { nm.focus(); return; }
      if (/^[0-9]/.test(name)) name = "n_" + name;
      var id = "draft:" + name + ":" + Date.now().toString(36);
      // 放到现有节点下方，避免压在已有节点上（固定坐标会直接叠在第一个节点上）
      var ys = Object.keys(nodePos).map(function (k) { return nodePos[k].y; });
      var x = 0;
      Object.keys(nodePos).forEach(function (k) { x = Math.min(x, nodePos[k].x); });
      var y = ys.length ? Math.max.apply(null, ys) + H + GAP_Y : 40;
      var p = { x: x, y: y + state.draftNodes.length * (H + GAP_Y) };
      state.positions[id] = p;
      state.draftNodes.push({
        id: id, name: name, package: pkg,
        requirement: pop.querySelector("#nn-req").value.trim(),
        publishes: [], subscribes: []
      });
      pop.remove();
      saveDraft().then(function () { return reload(true, id); })
        .then(function () {
          // 新节点落在下方，可能超出当前视野；出界时自动适应一下
          requestAnimationFrame(function () { ensureVisible(id); });
        });
    };
  }

  // ---------------- 抽屉 ----------------
  function openDrawer(id) {
    state.sel = id;
    state.tab = "overview";
    if (state.code && state.code.id !== id) state.code = null;
    var n = nodeById(id);
    if (!n) return;
    drawer.classList.remove("hidden");
    render();
    renderDrawer();
    // 抽屉会把画布挤窄，原本在右边的节点可能被挤出可视区。
    // 只在选中节点看不到时才整体适应，避免每次点节点都打断用户的手动缩放。
    requestAnimationFrame(function () { ensureVisible(id); });
  }

  function ensureVisible(id) {
    var p = posOf(id);
    if (!p) return;
    var cw = canvas.clientWidth, ch = canvas.clientHeight;
    if (!cw || !ch) return;
    var x0 = p.x * state.k + state.tx, y0 = p.y * state.k + state.ty;
    var x1 = x0 + W * state.k, y1 = y0 + H * state.k;
    var pad = 12;
    if (x0 < pad || y0 < pad || x1 > cw - pad || y1 > ch - pad) fit();
  }

  function closeDrawer() {
    state.sel = null;
    drawer.classList.add("hidden");
    render();
  }

  function renderDrawer() {
    var n = nodeById(state.sel);
    if (!n) { drawer.classList.add("hidden"); return; }
    var tabs = [["overview", "概览"], ["iface", "接口"], ["code", "代码"], ["req", "需求"]];
    var head =
      '<div class="gd-head"><div><div class="gd-name">' + esc(n.name) + "</div>" +
      '<div class="gd-path">' + esc(n.file || "（还没生成文件）") + "</div></div>" +
      '<button class="gd-close" title="关闭">×</button></div>' +
      '<div class="gd-tabs">' + tabs.map(function (t) {
        return '<span class="gd-tab' + (state.tab === t[0] ? " on" : "") +
          '" data-tab="' + t[0] + '">' + t[1] + "</span>";
      }).join("") + "</div>";
    var body = '<div class="gd-body" id="gd-body"></div>';
    var actions = "";
    if (n.status === "draft") {
      actions = '<div class="gd-actions"><button class="mini-btn" id="gd-del">删除这个空壳</button></div>';
    } else {
      actions = '<div class="gd-actions"><button class="mini-btn danger" id="gd-del">' +
        "从图上删除</button></div>";
    }
    drawer.innerHTML = head + body + actions;

    drawer.querySelector(".gd-close").onclick = closeDrawer;
    Array.prototype.forEach.call(drawer.querySelectorAll(".gd-tab"), function (t) {
      t.onclick = function () { state.tab = t.getAttribute("data-tab"); renderDrawer(); };
    });
    var del = drawer.querySelector("#gd-del");
    if (del) del.onclick = function () { removeNode(n); };

    var box = drawer.querySelector("#gd-body");
    if (state.tab === "overview") renderOverview(box, n);
    else if (state.tab === "iface") renderIface(box, n);
    else if (state.tab === "code") renderCode(box, n);
    else renderReq(box, n);
  }

  function facts(n) {
    return [
      [(n.publishes || []).length, "发布"],
      [(n.subscribes || []).length, "订阅"],
      [((n.service_servers || []).length + (n.service_clients || []).length +
        (n.actions || []).length), "服务/动作"],
      [n.params ? n.params.length : 0, "参数"]
    ];
  }

  function renderOverview(box, n) {
    var f = facts(n);
    box.innerHTML =
      '<div class="gd-label">AI 简介</div>' +
      '<div class="gd-intro" id="gd-intro">' +
      (state.intros[n.id] || (n.status === "draft" ? (n.requirement || "还没写需求") : "生成中…")) +
      "</div>" +
      '<div class="gd-facts">' + f.map(function (x) {
        return '<div class="gd-fact"><b>' + x[0] + "</b><span>" + x[1] + "</span></div>";
      }).join("") + "</div>" +
      '<div class="gd-label">硬信息</div>' +
      '<div class="gd-note">' +
      (n.loc || 0) + " 行 · " + (n.package || "未识别包") +
      (n.orphan ? " · <span class='gd-warn'>孤立节点（没有连线）</span>" : "") +
      (n.unresolved_count ? " · <span class='gd-warn'>" + n.unresolved_count +
        " 处接口名不是字面量</span>" : "") +
      "</div>";

    if (n.status === "draft") return;
    if (state.intros[n.id]) return;
    api("/api/graph/intro", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId: sid(), id: n.id })
    }).then(function (r) { return r.json(); }).then(function (d) {
      var el = drawer.querySelector("#gd-intro");
      if (!el) return;
      if (d.ok) { state.intros[n.id] = d.intro; el.textContent = d.intro; }
      else { el.innerHTML = '<span class="gd-warn">' + esc(d.error || "生成失败") +
        "</span><br>" + esc(d.fallback || ""); }
    }).catch(function () {
      var el = drawer.querySelector("#gd-intro");
      if (el) el.innerHTML = '<span class="gd-warn">简介生成失败（模型未连通）</span>';
    });
  }

  function ifaceRow(dir, it, peers) {
    var arrow = dir === "pub" ? "↑" : "↓";
    return '<div>' + arrow + " " + esc(it.topic) +
      (it.type ? " · " + esc(shortType(it.type)) : " · <span class='gd-warn'>类型未知</span>") +
      (it.pending ? " <span class='gd-warn'>待提交</span>" : "") +
      (it.resolved === false
        ? " <span class='gd-warn'>未解析（第 " + (it.line || "?") + " 行）</span>"
        : (peers.length ? " ← " + peers.map(function (p) {
            return '<span class="peer" data-peer="' + esc(p.id) + '">' + esc(p.name) + "</span>";
          }).join("、") : "")) +
      "</div>";
  }

  function renderIface(box, n) {
    var peersOf = function (topic, dir) {
      var out = [];
      (state.graph.edges || []).forEach(function (e) {
        if (e.topic !== topic) return;
        if (dir === "pub" && e.from === n.id) {
          var t = nodeById(e.to); if (t) out.push(t);
        }
        if (dir === "sub" && e.to === n.id) {
          var f = nodeById(e.from); if (f) out.push(f);
        }
      });
      return out;
    };
    var html = "";
    if ((n.publishes || []).length) {
      html += '<div class="gd-label">发布</div><div class="gd-iface">' +
        n.publishes.map(function (i) { return ifaceRow("pub", i, peersOf(i.topic, "pub")); }).join("") +
        "</div>";
    }
    if ((n.subscribes || []).length) {
      html += '<div class="gd-label">订阅</div><div class="gd-iface">' +
        n.subscribes.map(function (i) { return ifaceRow("sub", i, peersOf(i.topic, "sub")); }).join("") +
        "</div>";
    }
    var others = [].concat(n.service_servers || [], n.service_clients || [], n.actions || []);
    if (others.length) {
      html += '<div class="gd-label">服务 / 动作</div><div class="gd-iface">' +
        others.map(function (i) {
          return "<div>" + (i.role === "server" ? "◉ " : "◎ ") + esc(i.topic) +
            (i.type ? " · " + esc(shortType(i.type)) : "") + "</div>";
        }).join("") + "</div>";
    }
    if ((n.params || []).length) {
      html += '<div class="gd-label">参数</div><div class="gd-iface">' +
        n.params.map(function (p) {
          return "<div>" + esc(p.name) + (p.default ? " = " + esc(p.default) : "") + "</div>";
        }).join("") + "</div>";
    }
    if (!html) html = '<div class="gd-note">没有解析到接口。</div>';
    html += '<div class="gd-note">话题名可以直接在画布上点连线改（同名一起改）。</div>';
    box.innerHTML = html;
    Array.prototype.forEach.call(box.querySelectorAll(".peer"), function (p) {
      p.onclick = function () { openDrawer(p.getAttribute("data-peer")); };
    });
  }

  function renderCode(box, n) {
    if (n.status === "draft" || !n.file) {
      box.innerHTML = '<div class="gd-note">这个节点还没生成代码。先写需求、拉好连线，再点右上角「提交」。</div>';
      return;
    }
    if (!state.code || state.code.id !== n.id) {
      box.innerHTML = '<div class="gd-note">读取中…</div>';
      api("/api/graph/node?sid=" + encodeURIComponent(sid()) +
          "&path=" + encodeURIComponent(n.file))
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (!d.ok) { box.innerHTML = '<div class="gd-warn">' + esc(d.error || "读不到文件") + "</div>"; return; }
          state.code = { id: n.id, path: d.path, sha: d.sha, content: d.content, editing: false };
          renderDrawer();
        }).catch(function () { box.innerHTML = '<div class="gd-warn">读取失败</div>'; });
      return;
    }
    var c = state.code;
    box.classList.add("gd-fill");       // 让编辑器撑满抽屉剩余高度
    box.innerHTML =
      '<textarea class="gd-code" id="gd-code" spellcheck="false" ' +
      (c.editing ? "" : "readonly") + ">" + esc(c.content) + "</textarea>" +
      '<div class="gd-row">' + (c.editing
        ? '<button class="mini-btn primary" id="gd-save">保存</button>' +
          '<button class="mini-btn" id="gd-cancel">取消</button>'
        : '<button class="mini-btn" id="gd-edit">编辑</button>') +
      "</div>" +
      '<div class="gd-note">保存写进云端会话工作区，<b>你磁盘上的文件不会变</b>；' +
      "保存后图会自动重新解析。每次保存都会留一份快照。</div>" +
      '<div class="gd-note" id="gd-code-msg"></div>';

    var ta = box.querySelector("#gd-code");
    var editBtn = box.querySelector("#gd-edit");
    if (editBtn) editBtn.onclick = function () { c.editing = true; renderDrawer(); };
    var cancel = box.querySelector("#gd-cancel");
    if (cancel) cancel.onclick = function () { c.editing = false; renderDrawer(); };
    var save = box.querySelector("#gd-save");
    if (save) save.onclick = function () {
      save.disabled = true;
      api("/api/graph/node", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sessionId: sid(), path: c.path,
                               content: ta.value, sha: c.sha })
      }).then(function (r) { return r.json(); }).then(function (d) {
        save.disabled = false;
        var msg = drawer.querySelector("#gd-code-msg");
        if (d.ok) {
          c.editing = false;
          c.content = ta.value;
          c.sha = d.sha;
          if (msg) msg.innerHTML = '<span class="ok">已保存 · 已重新解析图</span>';
          reload(true, n.id);
        } else {
          if (msg) msg.innerHTML = '<span class="gd-warn">' +
            esc((d.error || "保存失败") + (d.snapshot ? "（快照：" + d.snapshot + "）" : "")) + "</span>";
          if (d.conflict) reload(true, n.id);
        }
      }).catch(function (e) {
        save.disabled = false;
        var msg = drawer.querySelector("#gd-code-msg");
        if (msg) msg.innerHTML = '<span class="gd-warn">' + esc(e.message || "保存失败") + "</span>";
      });
    };
  }

  function renderReq(box, n) {
    if (n.status === "draft") {
      box.innerHTML =
        '<div class="gd-label">这个节点要做什么</div>' +
        '<textarea class="gd-code" id="gd-req" style="min-height:120px" ' +
        'placeholder="例如：把 /scan 降采样后发布到 /scan_filtered，只保留前方 180 度">' +
        esc(n.requirement || "") + "</textarea>" +
        '<div class="gd-row"><button class="mini-btn primary" id="gd-req-save">保存需求</button></div>' +
        '<div class="gd-note">需求只是图纸上的一段文字。生成代码要看两端连线的接口，' +
        '所以先把线连好，再点右上角「提交」。</div>';
      var ta = box.querySelector("#gd-req");
      box.querySelector("#gd-req-save").onclick = function () {
        var dn = state.draftNodes.filter(function (d) { return d.id === n.id; })[0];
        if (dn) dn.requirement = ta.value;
        saveDraft().then(function () {
          state.intros[n.id] = ta.value.slice(0, 40);
          say("需求已记在图纸上"); renderDrawer();
        });
      };
      return;
    }
    box.innerHTML =
      '<div class="gd-label">改造这个节点</div>' +
      '<div class="gd-note">这个节点已经有代码。按约定，节点名和话题名是契约，' +
      "不能靠一次需求就改掉——要改接口，请直接在画布上改连线或话题名。<br><br>" +
      "想改内部算法：切到「代码」页直接编辑保存；想整段重写，就在聊天区描述需求，走完整流程。<br><br>" +
      '（「按需求重写节点内部实现」在下一版接入，避免现在半成品功能误导你。）</div>';
  }

  function removeNode(n) {
    if (n.status === "draft") {
      state.draftNodes = state.draftNodes.filter(function (d) { return d.id !== n.id; });
      state.draftLinks = state.draftLinks.filter(function (l) {
        return l.from !== n.id && l.to !== n.id;
      });
      delete state.positions[n.id];
      closeDrawer();
      saveDraft().then(function () { reload(true); });
      return;
    }
    if (state.deleted.indexOf(n.id) < 0) state.deleted.push(n.id);
    closeDrawer();
    saveDraft().then(function () {
      reload(true);
      say("已从图上移除（提交时才删文件）", 5200);
    });
  }

  // ---------------- 数据 ----------------
  function pickDraftFields() {
    return {
      positions: state.positions,
      renames: state.renames,
      deleted: state.deleted,
      draft_nodes: state.draftNodes,
      draft_links: state.draftLinks
    };
  }

  function saveDraft() {
    return api("/api/graph/draft", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId: sid(), draft: pickDraftFields() })
    }).then(function (r) { return r.json(); }).catch(function () { return { ok: false }; });
  }

  function reload(keepSel, selId) {
    var keep = keepSel === true ? (selId || state.sel) : null;
    return api("/api/graph?sid=" + encodeURIComponent(sid()))
      .then(function (r) { console.log('[gp] status', r.status); return r.json(); })
      .then(function (d) {
        console.log('[gp] loaded nodes', (d.nodes || []).length, 'ok', d.ok);
        if (!d || !d.ok) return;
        state.graph = d;
        state.renames = (d.draft && d.draft.renames) || {};

        state.draftNodes = d.nodes.filter(function (n) { return n.status === "draft"; })
          .map(function (n) {
            return { id: n.id, name: n.name, package: n.package,
                     requirement: n.requirement || "",
                     publishes: n.publishes || [], subscribes: n.subscribes || [] };
          });
        state.draftLinks = (d.edges || []).filter(function (e) { return e.draft; })
          .map(function (e) {
            return { from: e.from, to: e.to, topic: e.topic, type: e.type };
          });
        (d.nodes || []).forEach(function (n) {
          if (n.x != null && n.y != null) state.positions[n.id] = { x: n.x, y: n.y };
        });
        var hasNodes = (d.nodes || []).length > 0;
        if (!hasNodes) {
          pane.classList.add("hidden");
          // 项目被移除后，SVG 里的旧节点不能留着：虽然面板藏了，
          // 下次展开会先看到一张「幽灵图」，而且选中态还指着已不存在的节点。
          state.graph = null;
          state.sel = null;
          state.renames = {};
          state.draftNodes = [];
          state.draftLinks = [];
          if (svg) svg.innerHTML = "";
          closeDrawer();
          closePops();
        } else {
          // 首次显示时画布尺寸还是 0，必须等浏览器排完版再 fit，否则缩放全错
          var firstShow = pane.classList.contains("hidden");
          pane.classList.remove("hidden");
          render();
          if (firstShow) {
            requestAnimationFrame(function () { fit(); });
          }
          if (keep && nodeById(keep)) { state.sel = keep; renderDrawer(); }
          else if (state.sel && !nodeById(state.sel)) { closeDrawer(); }
        }
        if (d.conflicts && d.conflicts.length) {
          say("类型冲突：" + d.conflicts.map(function (c) {
            return c.topic + "（" + c.types.join(" / ") + "）";
          }).join("；"), 9000);
        } else if (d.warnings && d.warnings.length) {
          say(d.warnings[0], 6000);
        }
        return d;
      })
      .catch(function (e) { console.log('[gp] fail', e && e.message); });
  }

  // ---------------- 提交 ----------------
  var commitMask = null, commitTick = null, committing = false;

  function openCommitBox() {
    closeCommitBox();
    var mask = document.createElement("div");
    mask.className = "gp-commit-mask";
    mask.innerHTML = '<div class="gp-commit-card" role="dialog" aria-live="polite"></div>';
    canvas.appendChild(mask);
    // 面板开着的时候，滚轮别再去缩放后面的画布
    mask.addEventListener("wheel", function (ev) { ev.stopPropagation(); }, { passive: true });
    commitMask = mask;
    return mask.querySelector(".gp-commit-card");
  }

  function closeCommitBox() {
    if (commitTick) { clearInterval(commitTick); commitTick = null; }
    if (commitMask && commitMask.parentNode) commitMask.remove();
    commitMask = null;
  }

  // 提交中：转圈 + 进度条 + 计时。以前只有一个 50ms 就恢复的按钮文字，
  // 用户根本分不清「没反应 / 卡住 / 正在生成」，所以要一直显示到结果出来。
  function commitRunning(pending) {
    var card = openCommitBox();
    card.innerHTML =
      '<div class="gpc-head"><span class="gpc-spin"></span>' +
      '<span>正在提交 ' + pending + " 处改动…</span></div>" +
      '<div class="gpc-bar"><i></i></div>' +
      '<div class="gpc-hint" id="gpc-hint">正在把图纸上的改动写进代码…</div>';
    var t0 = Date.now();
    var hint = card.querySelector("#gpc-hint");
    commitTick = setInterval(function () {
      var s = Math.round((Date.now() - t0) / 1000);
      var msg = "已等待 " + s + " 秒";
      hint.textContent = s >= 5
        ? "正在调用模型写节点代码，通常 10–40 秒，请别关页面 · " + msg
        : "正在把图纸上的改动写进代码… · " + msg;
    }, 400);
  }

  function commitResult(changes, errors) {
    var card = openCommitBox();
    var title = errors.length
      ? (changes.length ? "部分完成 · " + errors.length + " 项没落地"
                        : "没有落地任何改动")
      : (changes.length ? "提交成功 · 代码已更新" : "没有可落地的改动");
    var icon = errors.length ? "!" : "✓";
    card.innerHTML =
      '<div class="gpc-head"><span class="gpc-ic ' + (errors.length ? "bad" : "good") +
      '">' + icon + "</span><span>" + esc(title) + "</span></div>" +
      '<div class="gpc-list">' +
      changes.map(function (c) {
        return '<div class="gpc-item"><span class="ic">✓</span><span>' +
               esc(c.summary || "") + "</span></div>";
      }).join("") +
      errors.map(function (e) {
        return '<div class="gpc-item err"><span class="ic">!</span><span>' +
               esc(e) + "</span></div>";
      }).join("") +
      "</div>" +
      '<div class="gpc-hint">' +
      (changes.length
        ? "改的是会话工作区里的文件，本地磁盘没动。要拿走就用下面的下载。"
        : "") +
      "</div>" +
      '<div class="gpc-acts">' +
      (errors.length ? '<button class="mini-btn" id="gpc-retry">重试</button>' : "") +
      (changes.length ? '<button class="mini-btn primary" id="gpc-dl">下载项目 zip</button>' : "") +
      '<button class="mini-btn" id="gpc-close">关闭</button></div>';
    var dl = card.querySelector("#gpc-dl");
    if (dl) dl.onclick = function () {
      if (window.RTDownloadWorkspace) { window.RTDownloadWorkspace(); return; }
      window.location.href = "/api/files/" + encodeURIComponent(sid()) + "/zip";
    };
    var rt = card.querySelector("#gpc-retry");
    if (rt) rt.onclick = function () { startCommit(); };
    card.querySelector("#gpc-close").onclick = closeCommitBox;
  }

  function commitIdle(pending) {
    var card = openCommitBox();
    card.innerHTML =
      '<div class="gpc-head"><span class="gpc-ic good">i</span>' +
      "<span>没有待提交的改动</span></div>" +
      '<div class="gpc-hint">改话题名、加节点、拉新线之后，右上角按钮会变成' +
      "「提交（N）」，按它才会写进代码。图纸上的拖动位置、缩放不需要提交。</div>" +
      '<div class="gpc-acts"><button class="mini-btn" id="gpc-close">知道了</button></div>';
    card.querySelector("#gpc-close").onclick = closeCommitBox;
  }

  function commit() {
    if (committing) return;
    var st = (state.graph && state.graph.stats) || {};
    if (!st.pending) { commitIdle(0); return; }
    var tbd = (state.graph.edges || []).filter(function (e) { return e.draft && !e.type; });
    if (tbd.length) {
      commitResult([], ["还有 " + tbd.length + " 条线没定消息类型（TBD），" +
                        "先点线选个类型再提交"]);
      return;
    }
    startCommit();
  }

  function startCommit() {
    var st = (state.graph && state.graph.stats) || {};
    var pending = st.pending || 1;
    var btn = $("gp-commit");
    var t0 = Date.now();
    committing = true;
    closeCommitBox();
    commitRunning(pending);
    if (btn) { btn.disabled = true; btn.textContent = "提交中…"; }
    // 改名是纯字符串替换，50ms 就完事。太快反而看不见，用户会以为没反应。
    // 所以结果至少等 700ms 再显示，"提交→成功"这个因果才看得出来。
    function finish(changes, errors, graph) {
      var wait = Math.max(0, 700 - (Date.now() - t0));
      setTimeout(function () {
        if (commitTick) { clearInterval(commitTick); commitTick = null; }
        if (graph) {
          state.graph = graph;
          state.renames = {};
          state.deleted = [];
          state.draftNodes = [];
          state.draftLinks = [];
        }
        commitResult(changes, errors);
        if (graph) {
          reload(true, state.sel);
          renderMeta();
          // 左栏「项目文件」同步刷新，否则新生成的节点看不见
          if (window.RTRefreshFiles) window.RTRefreshFiles();
        }
      }, wait);
    }
    api("/api/graph/commit", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId: sid() })
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        committing = false;
        if (btn) { btn.disabled = false; btn.textContent = "提交"; }
        if (!d || d.ok === false) {
          finish([], [d && (d.error || d.message) || "服务端没返回结果"]);
          return;
        }
        finish(d.changes || [], d.errors || [], d.graph);
      })
      .catch(function (e) {
        committing = false;
        if (btn) { btn.disabled = false; btn.textContent = "提交"; }
        finish([], ["请求失败：" + (e.message || "网络或服务中断")]);
      });
  }

  // ---------------- 工具栏 ----------------
  function fit(tries) {
    var nodes = (state.graph && state.graph.nodes) || [];
    if (!nodes.length) return;
    var cw = canvas.clientWidth, ch = canvas.clientHeight;
    // 面板刚从隐藏切到显示时尺寸是 0，这一帧算出来的缩放会全错（图被压到 30%），
    // 等排完版再来。tries 只用数字，避免被 onclick 传进来的 event 污染。
    if ((cw < 80 || ch < 80) && (typeof tries === "number" ? tries : 0) < 12) {
      setTimeout(function () {
        fit((typeof tries === "number" ? tries : 0) + 1);
      }, 70);
      return;
    }
    var minX = 1e9, minY = 1e9, maxX = -1e9, maxY = -1e9;
    nodes.forEach(function (n) {
      var p = posOf(n.id);
      minX = Math.min(minX, p.x); minY = Math.min(minY, p.y);
      maxX = Math.max(maxX, p.x + W); maxY = Math.max(maxY, p.y + H);
    });
    var pad = 28;
    var k = Math.min((cw - pad * 2) / (maxX - minX || 1),
                     (ch - pad * 2) / (maxY - minY || 1), 1.6);
    state.k = clamp(k, MIN_K, MAX_K);
    state.tx = (cw - (maxX - minX) * state.k) / 2 - minX * state.k;
    state.ty = (ch - (maxY - minY) * state.k) / 2 - minY * state.k;
    applyTransform();
    render();
  }

  // ---------------- 左右分屏拖拽 ----------------
  // 五五分是拍脑袋定的：节点一多画布就挤，图纸区该由用户自己决定占多宽。
  // 存 key 升到 v2：早先版本在布局没量出宽度时会把 clamp 后的最小值（340）
  // 写进存储，之后每次打开画布都卡在最窄 —— 换 key 让这种脏值直接失效。
  var SPLIT_KEY = "rt_gp_split2";
  try { localStorage.removeItem("rt_gp_split"); } catch (e) {}

  function bindSplitter() {
    var sp = $("gp-split");
    if (!sp) return;
    var MIN_G = 340;      // 画布再窄，节点标题就折行了
    var MIN_C = 320;      // 对话区同理，太窄气泡没法读

    function clamp(w) {
      var box = pane.parentNode.clientWidth - sp.offsetWidth;
      var max = Math.max(MIN_G, box - MIN_C);
      return Math.max(MIN_G, Math.min(Math.round(w), max));
    }
    function setWidth(w, save) {
      w = clamp(w);
      pane.style.flex = "0 0 " + w + "px";
      if (save) { try { localStorage.setItem(SPLIT_KEY, String(w)); } catch (e) {} }
      return w;
    }
    function reset() {
      pane.style.flex = "";
      try { localStorage.removeItem(SPLIT_KEY); } catch (e) {}
      pane.classList.remove("collapsed");
      fit();
    }

    // 恢复上次拖的宽度：刷新一次不用重拖。
    // 页面刚加载时 #workbench 可能还没量出宽度，这时候算 clamp 会被压到最小值，
    // 所以布局就绪前先等一会儿再套用。
    var tries = 0;
    function restore() {
      var saved = 0;
      try { saved = parseInt(localStorage.getItem(SPLIT_KEY) || "", 10); } catch (e) {}
      if (!(saved > 0)) return;
      if (pane.parentNode.clientWidth < 400 && tries++ < 20) {
        setTimeout(restore, 120);
        return;
      }
      var applied = setWidth(saved, false);
      // clamp 把宽度压小了，说明容器还没排好版 —— 这时写死会让画布永远最窄，
      // 所以不认这个结果，等下一轮再试；重试耗尽就用默认比例。
      if (applied !== saved && tries++ < 20) setTimeout(restore, 120);
    }
    restore();

    var dragging = false, startX = 0, startW = 0;
    sp.addEventListener("pointerdown", function (ev) {
      if (ev.button !== 0) return;
      dragging = true;
      startX = ev.clientX;
      pane.classList.remove("collapsed");   // 收起状态下拖它，就是要拉出来
      startW = pane.getBoundingClientRect().width;   // 先展开再量，避免从 30px 起跳
      sp.classList.add("on");
      document.body.classList.add("gp-resizing");
      try { sp.setPointerCapture(ev.pointerId); } catch (e) {}
      ev.preventDefault();
      ev.stopPropagation();
    });
    sp.addEventListener("pointermove", function (ev) {
      if (!dragging) return;
      setWidth(startW + (ev.clientX - startX), false);
      ev.preventDefault();
    });
    function endDrag(ev) {
      if (!dragging) return;
      dragging = false;
      sp.classList.remove("on");
      document.body.classList.remove("gp-resizing");
      try { sp.releasePointerCapture(ev.pointerId); } catch (e) {}
      // 只记用户真的拖出来的宽度：单击一下分隔条（拖了个 0 距离）不该覆盖上次的比例
      var nowW = Math.round(pane.getBoundingClientRect().width);
      if (nowW !== Math.round(startW)) setWidth(nowW, true);
      fit();                    // 视口变了，把图重新居中适应
    }
    sp.addEventListener("pointerup", endDrag);
    sp.addEventListener("pointercancel", endDrag);
    sp.addEventListener("dblclick", reset);      // 双击回到默认比例

    // 窗口缩小后夹紧，别让画布把对话区挤没。
    // 只按用户原本的意图重新夹，不把夹紧后的值存回去 —— 否则窗口一小
    // 就把 340 这种最小值写进存储，之后永远展开不回来。
    var timer = 0;
    window.addEventListener("resize", function () {
      if (!pane.style.flex) return;
      clearTimeout(timer);
      timer = setTimeout(function () {
        var saved = 0;
        try { saved = parseInt(localStorage.getItem(SPLIT_KEY) || "", 10); } catch (e) {}
        setWidth(saved > 0 ? saved : pane.getBoundingClientRect().width, false);
      }, 160);
    });
  }

  function bind() {
    var add = $("gp-add"), fitBtn = $("gp-fit"), zin = $("gp-zoom-in"),
        zout = $("gp-zoom-out"), cm = $("gp-commit"), col = $("gp-collapse"),
        rail = $("gp-rail"), tg = $("toggle-graph");
    if (add) add.onclick = newNodePop;
    if (fitBtn) fitBtn.onclick = function () { fit(); };
    if (zin) zin.onclick = function () { zoomBy(1.2); };
    if (zout) zout.onclick = function () { zoomBy(1 / 1.2); };
    if (cm) cm.onclick = commit;
    if (col) col.onclick = function () { pane.classList.add("collapsed"); };
    if (rail) rail.onclick = function () { pane.classList.remove("collapsed"); };
    if (tg) tg.onclick = function () {
      pane.classList.toggle("hidden");
      if (!pane.classList.contains("hidden")) reload();
    };

    bindSplitter();

    // 右键：新建节点（画布）/ 删除（节点）
    canvas.addEventListener("contextmenu", function (ev) {
      ev.preventDefault();
      var g = ev.target.closest ? ev.target.closest("[data-node]") : null;
      if (g) {
        var id = g.getAttribute("data-node");
        openDrawer(id);
      } else {
        newNodePop();
      }
    });

    canvas.addEventListener("pointerdown", function (ev) {
      if (ev.target === canvas) closePops();
    });

    // Esc 关闭抽屉
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") { closePops(); if (state.sel) closeDrawer(); }
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "z") {
        // 撤销最近一次话题改名
        var keys = Object.keys(state.renames);
        if (state.sel === null && keys.length) {
          var last = keys[keys.length - 1];
          delete state.renames[last];
          saveDraft().then(function () { reload(true); });
        }
      }
    });

    window.addEventListener("resize", debounce(function () {
      if (state.graph && state.graph.nodes.length) render();
    }, 200));
  }

  function zoomBy(f) {
    var cw = canvas.clientWidth, ch = canvas.clientHeight;
    var nk = clamp(state.k * f, MIN_K, MAX_K);
    state.tx = cw / 2 - (cw / 2 - state.tx) * (nk / state.k);
    state.ty = ch / 2 - (ch / 2 - state.ty) * (nk / state.k);
    state.k = nk;
    applyTransform();
  }

  // 记录最近一次指针位置，给弹卡定位用
  document.addEventListener("pointermove", function (ev) {
    if (!canvas) return;
    var r = canvas.getBoundingClientRect();
    if (ev.clientX >= r.left && ev.clientX <= r.right &&
        ev.clientY >= r.top && ev.clientY <= r.bottom) {
      state.lastX = ev.clientX - r.left;
      state.lastY = ev.clientY - r.top;
    }
  }, true);

  window.RTGraph = { reload: function () { return reload(true); }, fit: fit };

  bind();
  setTimeout(function () { reload(); }, 600);
})();
