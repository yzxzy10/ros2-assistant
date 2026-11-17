/* 架构图：SVG 自绘的可视化编辑画布（无第三方依赖，断网可用） */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var NS = "http://www.w3.org/2000/svg";
  var W = 138, H = 48;              // 节点尺寸（世界坐标）
  // 间距要留够「话题标签」的宽度，否则悬停/高亮时标签会压到两侧节点上。
  // Y 方向还得多留出日志框：验证时每个节点下方 +12 处挂一个日志框，收起时高 46，
  // 也就是占到 y+H+58。GAP_Y=46 时行距只有 94，日志框会把下一行节点压住
  // （多链图每列 2 个节点时才暴露，单链永远碰不到）。
  var GAP_X = 164, GAP_Y = 74;
  var MIN_K = 0.25, MAX_K = 2;
  var spaceDown = false;            // 按住空格 → 画布进入「抓手」模式

  // 正在输入框里打字时，空格/方向键都归输入框，别被画布抢走
  function editing(el) {
    if (!el || !el.tagName) return false;
    var t = el.tagName.toLowerCase();
    return t === "input" || t === "textarea" || t === "select" || el.isContentEditable === true;
  }

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
  // 找「回边」：A→B 的同时还有 B→A 这种环，Kahn 分层会卡死 ——
  // 环上节点入度永远不归零，剩下的节点全掉到第 0 列，
  // 结果 detector 和 planner 被排进同一列，本该向右的边全变成大拱形。
  //
  // 哪条算回边不能交给 DFS 的遍历顺序（从 battery 走会先把 planner→detector
  // 当成正向，主数据流整个左右颠倒）。先看反向对的条数：主链路通常更「粗」，
  // 一对节点之间两条检测结果 + 一条控制指令，那么少的那条就是回边。
  function backEdges(nodes, edges) {
    var back = {}, cnt = {};
    edges.forEach(function (e) {
      var k = e.from + ">" + e.to;
      cnt[k] = (cnt[k] || 0) + 1;
    });
    edges.forEach(function (e, i) {
      var rev = e.to + ">" + e.from;
      if (!cnt[rev]) return;
      var mine = cnt[e.from + ">" + e.to];
      if (mine < cnt[rev]) back[i] = 1;
      else if (mine === cnt[rev] && e.from > e.to) back[i] = 1;  // 一样粗时稳定选一边
    });
    // 剩下的长环（A→B→C→A，没有直接反向边）再用 DFS 找
    var adj = {}, color = {};
    nodes.forEach(function (n) { adj[n.id] = []; color[n.id] = 0; });
    edges.forEach(function (e, i) {
      if (back[i] || !adj[e.from]) return;
      adj[e.from].push([e.to, i]);
    });
    function dfs(u) {
      color[u] = 1;
      (adj[u] || []).forEach(function (p) {
        if (!adj.hasOwnProperty(p[0])) return;
        if (color[p[0]] === 1) { back[p[1]] = 1; return; }
        if (color[p[0]] === 0) dfs(p[0]);
      });
      color[u] = 2;
    }
    nodes.forEach(function (n) { if (color[n.id] === 0) dfs(n.id); });
    return back;
  }

  function autoLayout(nodes, edges) {
    var ids = {}, indeg = {}, out = {};
    nodes.forEach(function (n) { ids[n.id] = n; indeg[n.id] = 0; out[n.id] = []; });
    var back = backEdges(nodes, edges);
    edges.forEach(function (e, i) {
      if (back[i]) return;      // 回边不参与分层
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
    while (guard++ < 10000) {
      if (!queue.length) {
        // 兜底：还有没分层的（没检出的环），挑入度最小的当入口继续推，
        // 否则这些节点会全部掉到第 0 列，和源节点挤在一起。
        var rest = Object.keys(ids).filter(function (k) { return level[k] === undefined; });
        if (!rest.length) break;
        rest.sort(function (a, b) { return (deg[a] || 0) - (deg[b] || 0); });
        level[rest[0]] = 0;
        queue.push(rest[0]);
      }
      var cur = queue.shift();
      (out[cur] || []).forEach(function (nx) {
        level[nx] = Math.max(level[nx] || 0, (level[cur] || 0) + 1);
        if (--deg[nx] === 0) queue.push(nx);
      });
    }
    Object.keys(ids).forEach(function (k) { if (level[k] === undefined) level[k] = 0; });

    // 有连线的节点走分层；一个线都没有的节点单独排在下方。
    // 为什么分开：孤立节点如果混在分层列里，长连线（跨两列那种）会直接从它们
    // 的框里穿过去，图上就看不出谁连谁了；放到底部横带里两头都干净。
    var linked = {};
    edges.forEach(function (e) {
      if (ids[e.from] && ids[e.to]) { linked[e.from] = 1; linked[e.to] = 1; }
    });

    var cols = {};
    Object.keys(level).forEach(function (k) {
      if (!linked[k]) return;
      var l = level[k];
      (cols[l] = cols[l] || []).push(k);
    });

    // 一列最多排 7 个，超了折成多列。为什么：仓库里一大半节点跟谁都连不上时
    // （话题名是运行时参数、或对端在仓库外），画出来是一根几十格高的竖条——看着像坏了。
    var MAX_ROWS = 7;
    var maxRows = 1;
    Object.keys(cols).forEach(function (l) {
      maxRows = Math.max(maxRows, Math.min(cols[l].length, MAX_ROWS));
    });

    nodePos = {};
    var cursor = 0;
    Object.keys(cols).sort(function (a, b) { return a - b; }).forEach(function (l) {
      var col = cols[l], chunks = [];
      for (var i = 0; i < col.length; i += MAX_ROWS) {
        chunks.push(col.slice(i, i + MAX_ROWS));
      }
      chunks.forEach(function (chunk, ci) {
        var h = chunk.length;
        chunk.forEach(function (id, i) {
          var y = (maxRows - h) * (H + GAP_Y) / 2 + i * (H + GAP_Y);
          nodePos[id] = { x: (cursor + ci) * (W + GAP_X), y: y };
        });
      });
      cursor += chunks.length;
    });

    // 没连线的节点：图下方另起一片，按网格铺开
    var lonely = Object.keys(ids).filter(function (k) { return !linked[k]; });
    if (lonely.length) {
      var placed = Object.keys(nodePos);
      var baseY = 0;
      if (placed.length) {
        var maxY = 0;
        placed.forEach(function (k) { maxY = Math.max(maxY, nodePos[k].y); });
        baseY = maxY + H + GAP_Y + 26;          // 留一条空当，视觉上分开
      }
      var perRow = Math.max(3, Math.ceil(Math.sqrt(lonely.length * 1.8)));
      perRow = Math.min(perRow, 5);
      lonely.forEach(function (id, i) {
        var r = Math.floor(i / perRow), c = i % perRow;
        nodePos[id] = { x: c * (W + GAP_X), y: baseY + r * (H + GAP_Y) };
      });
    }
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
      // 固定 9px：默认 markerUnits 会跟着线宽放大，四五条线汇进同一个节点时，
      // 箭头比端口间距还高，几个箭头叠在一起就成了墨疙瘩。
      markerUnits: "userSpaceOnUse", markerWidth: "9", markerHeight: "9",
      orient: "auto-start-reverse"
    }, defs);
    mk("path", { d: "M2 1.6L8.4 5L2 8.4", fill: "none", stroke: "context-stroke",
                 "stroke-width": "1.5", "stroke-linecap": "round",
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

  // 连线一律走三次贝塞尔：出端口沿水平方向切出、入端口沿水平方向切入，
  // 控制柄长度随两端距离伸缩，长线不会塌成直线，短线也不会打卷。
  function bez(x1, y1, x2, y2, k1, k2, cy) {
    return "M" + x1 + " " + y1 +
      " C" + (x1 + k1) + " " + (cy === undefined ? y1 : cy) + "," +
      (x2 - k2) + " " + (cy === undefined ? y2 : cy) + "," + x2 + " " + y2;
  }

  // 有方向的贝塞尔：控制柄永远朝「要走的方向」伸出去。
  // 直接用 bez 画一条从右往左的线时，控制柄还按 +x 伸，曲线会先向外冲一段再折回来，
  // 终点切线也跟着反过来 —— 箭头就成了「离开节点」，而回传应该是「回到节点」。
  function bezDir(x1, y1, x2, y2, k) {
    var s = x2 >= x1 ? 1 : -1;
    return "M" + x1 + " " + y1 +
      " C" + (x1 + s * k) + " " + y1 + "," +
      (x2 - s * k) + " " + y2 + "," + x2 + " " + y2;
  }

  // off：同一对节点之间的第 2、3 条线要错开，否则两条边完全重合，
  // 看起来就像「只有一个话题」—— 下发与回传本来是两个方向、两条线。
  // oy1 / oy2：出端、入端在节点边缘上的纵向偏移。
  // 三条线都扎在同一个点上看着就是一团，均匀摊在边框上才是「三根并排的线」。
  function edgePath(a, b, off, oy1, oy2, tx1, tx2) {
    off = off || 0;

    if (b.x - (a.x + W) > 26) {
      // 正向：控制柄长度 = 水平距离的 45%，夹在 46~180 之间
      var x1 = a.x + W, y1 = a.y + H / 2 + (oy1 || 0);
      var x2 = b.x, y2 = b.y + H / 2 + (oy2 || 0);
      var dx = x2 - x1;
      var k = clamp(Math.abs(dx) * 0.45, 46, 180);
      return bez(x1, y1, x2, y2, k, k, off ? (y1 + y2) / 2 + off : undefined);
    }

    // 回边（目标在左侧或几乎同列）：从上沿拱过去。
    // 以前是「a 左边出、b 右边进」，那两个点正是正向边的出口和入口，
    // 几条线全撞在同一个位置，绞成一团；而且路径要先向外冲一段再垂直上升，
    // 画出来就像凭空竖起一根弧。改成两头顶边起拱，端口彻底分开，箭头也朝下进门。
    var sx = a.x + W / 2 + (tx1 || 0), sy = a.y;
    var ex = b.x + W / 2 + (tx2 || 0), ey = b.y;
    var rise = clamp(Math.abs(sx - ex) * 0.14 + 28, 30, 62) + off;
    var top = Math.min(sy, ey) - rise;
    return "M" + sx + " " + sy +
      " C" + sx + " " + top + "," + ex + " " + top + "," + ex + " " + ey;
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

    // 连线：先按节点统计进出边，把端口均匀摊在节点边框上
    // 回边（目标在左侧）走顶边，不能混进左右两侧的端口队列，
    // 否则它和正向边的出入点会叠在同一个像素上。
    function isBack(a, b) { return (b.x - (a.x + W)) <= 26; }

    var inList = {}, outList = {}, topIn = {}, topOut = {};
    edges.forEach(function (e, i) {
      if (!byId[e.from] || !byId[e.to]) return;
      if (isBack(posOf(e.from), posOf(e.to))) {
        (topOut[e.from] = topOut[e.from] || []).push(i);
        (topIn[e.to] = topIn[e.to] || []).push(i);
      } else {
        (outList[e.from] = outList[e.from] || []).push(i);
        (inList[e.to] = inList[e.to] || []).push(i);
      }
    });

    // 按「对端节点的高低」排队，而不是按话题名字典序：
    // 话题名和上下顺序恰好相反时，两条线会在节点边上打叉（以前就是这样）。
    function sideOffsets(map, other) {
      var res = {};
      Object.keys(map).forEach(function (k) {
        var list = map[k].slice().sort(function (x, y) {
          var oa = posOf(edges[x][other]), ob = posOf(edges[y][other]);
          var d = (oa ? oa.y : 0) - (ob ? ob.y : 0);
          if (Math.abs(d) > 1) return d;
          return String(edges[x].topic || "").localeCompare(String(edges[y].topic || ""));
        });
        // 间距按「有几条线」摊开，不能再除以 n：
        // 5 条线时 (48-18)/5 只有 6px，五根线加箭头直接糊成一坨。
        var n = list.length, step = Math.min(11, (H - 8) / Math.max(n - 1, 1));
        list.forEach(function (idx, i) { res[idx] = (i - (n - 1) / 2) * step; });
      });
      return res;
    }
    var pOut = sideOffsets(outList, "to"), pIn = sideOffsets(inList, "from");

    // 顶边端口：回边在节点上方起拱，横向排开，互不重叠
    function topOffsets(map, other) {
      var res = {};
      Object.keys(map).forEach(function (k) {
        var list = map[k].slice().sort(function (x, y) {
          var oa = posOf(edges[x][other]), ob = posOf(edges[y][other]);
          var d = (oa ? oa.x : 0) - (ob ? ob.x : 0);
          if (Math.abs(d) > 1) return d;
          return String(edges[x].topic || "").localeCompare(String(edges[y].topic || ""));
        });
        var n = list.length, step = Math.min(42, (W - 34) / Math.max(n - 1, 1));
        list.forEach(function (idx, i) { res[idx] = (i - (n - 1) / 2) * step; });
      });
      return res;
    }
    var tOut = topOffsets(topOut, "to"), tIn = topOffsets(topIn, "from");

    var lanes = {};
    var geom = (state.edgeGeom = []);
    edges.forEach(function (e, idx) {
      if (!byId[e.from] || !byId[e.to]) return;
      var a = posOf(e.from), b = posOf(e.to);
      // 同一对节点之间的多条线，靠两端端口错开就够了。
      // 以前中段还加 ±20 的上下鼓包，结果第二条线鼓出去正好撞上隔壁那条，
      // 图里那些打叉就是这么来的。回边例外：多条回边要一层层抬高拱顶才不会重合。
      var lk = e.from + ">" + e.to;
      var n = (lanes[lk] = (lanes[lk] || 0) + 1);
      var off = isBack(a, b) && n > 1 ? (n - 1) * 14 : 0;
      geom[idx] = { off: off, oy1: pOut[idx], oy2: pIn[idx],
                    tx1: tOut[idx], tx2: tIn[idx] };
      var d = edgePath(a, b, off, pOut[idx], pIn[idx], tOut[idx], tIn[idx]);
      var g = mk("g", { "data-edge": idx, "data-topic": e.topic || "",
                        "data-from": e.from, "data-to": e.to }, gEdge);
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
        if (vyLocked()) return;
        openRename(e, ev);
      });
    });

    // 节点
    nodes.forEach(function (n) {
      var p = posOf(n.id);
      var g = mk("g", { "data-node": n.id, transform: "translate(" + p.x + "," + p.y + ")" }, gNode);
      var isSel = state.sel === n.id;
      var isExt = !!n.external;
      mk("rect", { width: W, height: H, rx: 8, "data-box": "1",
                   class: "gn-box" + (n.status === "draft" ? " draft" : "") +
                          (isExt ? " ext" : "") +
                          (isSel ? " selected" : "") }, g);
      var nm = mk("text", { x: W / 2, y: n.status === "draft" ? H / 2 - 7 : H / 2 - 5,
                            "text-anchor": "middle", class: "gn-name" }, g);
      nm.textContent = truncMid(n.name, 19);
      var sub = n.status === "draft" ? "未生成 · 待提需求"
        : (n.external ? ("外部 · " + (n.package || "已安装包"))
          : (n.package ? n.package : "未识别包"));
      var st = mk("text", { x: W / 2, y: H / 2 + 11, "text-anchor": "middle",
                            class: "gn-sub" }, g);
      st.textContent = trunc(sub, 22);
      if (isExt) {
        // launch 拉起的节点：和「孤立」区分开（它连不上线是因为对端在已安装包）
        var be = mk("text", { x: W - 2, y: -6, "text-anchor": "end", class: "gn-badge gn-badge-ext" }, g);
        be.textContent = "外部";
        mk("title", {}, g).textContent =
          "从 launch 文件拉起的节点，来自已安装的 ROS 包（不是本仓库源码）";
      } else if (n.orphan && n.status !== "draft") {
        // 画在框外上方：框内右下角会和居中的节点名撞在一起（名字长一点就压字）
        var bd = mk("text", { x: W - 2, y: -6, "text-anchor": "end", class: "gn-badge" }, g);
        bd.textContent = "孤立";
        // 提示挂在节点组上：塞进 <text> 里会让 textContent 混进提示文字
        mk("title", {}, g).textContent =
          "没有任何已解析的连线：对端不在这个仓库里，或话题名是运行时才确定的";
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
        if (spaceDown) return;              // 抓手模式：交给画布去平移
        ev.stopPropagation();
        if (vyLocked()) return;
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
        if (spaceDown) return;              // 抓手模式：不拉线，交给画布平移
        ev.stopPropagation(); ev.preventDefault();
        if (vyLocked()) return;
        startLink(n.id, ev);
      });
      pin.addEventListener("pointerdown", function (ev) {
        if (spaceDown) return;
        ev.stopPropagation(); ev.preventDefault();
        if (vyLocked()) return;
        startLink(n.id, ev, true);
      });
    });

    empty.classList.toggle("hidden", nodes.length > 0);
    applyTransform();
    renderMeta();
    if (typeof VY !== "undefined" && VY.on) vyRestore();
  }

  function cssEsc(s) { return String(s).replace(/["\\]/g, "\\$&"); }
  function trunc(s, n) {
    s = String(s == null ? "" : s);
    return s.length > n ? s.slice(0, n - 1) + "…" : s;
  }
  // 长名字从中间省略：只截尾巴的话，同一前缀的节点会变成两个一模一样的
  // 「linorobot2_topic…」——图上根本分不清哪个是 monitor、哪个是 verifier。
  function truncMid(s, n) {
    s = String(s == null ? "" : s);
    if (s.length <= n) return s;
    var keep = n - 1, head = Math.ceil(keep * 0.62), tail = keep - head;
    return s.slice(0, head) + "…" + s.slice(s.length - tail);
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
    if (st.external) txt += " · " + st.external + " 个外部节点";
    if (st.pending) txt += " · " + st.pending + " 处待提交";
    if (st.unresolved) txt += " · " + st.unresolved + " 处未解析";
    if (st.via_env) txt += " · " + st.via_env + " 个话题名取自 .env";
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
    // 空白处拖 = 平移；按住空格时从任何地方拖都是平移（抓手模式）
    if (ev.target !== svg && !spaceDown) return;
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
    var geom = state.edgeGeom || [];
    var paths = gEdge.querySelectorAll("g[data-edge]");
    edges.forEach(function (e, i) {
      var g = paths[i];
      if (!g) return;
      // 端口偏移沿用上次整体渲染的结果，拖动时线不会突然从边上跳回正中
      var q = geom[i] || {};
      var d = edgePath(posOf(e.from), posOf(e.to), q.off, q.oy1, q.oy2, q.tx1, q.tx2);
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
    if (vyLocked()) return;
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
  // 验证运行期间锁住画布：容器里跑的是服务端那份工作区，
  // 这期间在图上改代码、拖节点、连新线都改不到正在编译的那一份，
  // 只会让人以为「我改了它怎么还这样」。要先退出验证再改。
  function vyLocked() {
    if (typeof VY === "undefined" || !VY.on) return false;
    say("验证进行中，画布已锁定 —— 先点「退出验证」，改完再跑", 5200);
    return true;
  }

  function openDrawer(id) {
    if (vyLocked()) return;
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
      (n.external ? " · <span class='gd-warn'>外部节点（launch 拉起）</span>" : "") +
      (n.orphan && !n.external ? " · <span class='gd-warn'>孤立节点（没有连线）</span>" : "") +
      (n.unresolved_count ? " · <span class='gd-warn'>" + n.unresolved_count +
        " 处接口名不是字面量</span>" : "") +
      ((n.dup || []).length
        ? " · <span class='gd-warn'>同名实现在 " +
          esc(n.dup.map(function (d) { return d.file || ""; }).join("、")) + "</span>" : "") +
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
      // 话题名从 .env 里查出来的，标一下：改配置图就变，别以为是代码里写死的
      (it.via === "env" ? " <span class='gd-note-inline'>来自 .env</span>" : "") +
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
          // 上次跑完的验证结果自动回到画布上（绿线/日志/结论），
          // 图改过的话服务端比对指纹后会作废，不会把旧结果贴到新图上
          if (!VY.on) vyRestoreSaved();
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
    if (vyLocked()) return;
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
    // 验证态下注入器在节点上方、下位机可能在任意一侧，不算进来会被裁出视野
    if (typeof VY !== "undefined" && VY.on && VY.layer) {
      Array.prototype.forEach.call(
        VY.layer.querySelectorAll(".vy-inj, .vy-mcu"),
        function (el) {
          try {
            var bb = el.getBBox();
            minX = Math.min(minX, bb.x); minY = Math.min(minY, bb.y);
            maxX = Math.max(maxX, bb.x + bb.width); maxY = Math.max(maxY, bb.y + bb.height);
          } catch (e) { /* 尚未布局 */ }
        });
    }
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
      // 空格 = 临时抓手：按住空格拖哪里都是平移（CAD 的习惯）。
      // 在输入框里敲空格当然还是打空格，不能抢。
      if (ev.key === " " && !editing(ev.target)) {
        spaceDown = true;
        canvas.classList.add("pan-ready");
        ev.preventDefault();          // 否则页面会滚 / 焦点按钮被「按下」
      }
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

    document.addEventListener("keyup", function (ev) {
      if (ev.key === " ") { spaceDown = false; canvas.classList.remove("pan-ready"); }
    });
    // 切走窗口时收不到 keyup，回来会一直以为空格还按着
    window.addEventListener("blur", function () {
      spaceDown = false;
      if (canvas) canvas.classList.remove("pan-ready");
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


  // ================= 链路级验证（真实结果驱动，不预演动画） =================
  var VY = {
    on: false, sid: "", timer: 0, st: null,
    queue: [], playing: false, played: {}, halt: false,
    logs: {}, expand: {}, layer: null,
    review: false           // true = 正在回看上次的结果（画布上锁，但没有活的进程）
  };

  function vyPost(path, body) {
    return api(path, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ sessionId: sid() }, body || {}))
    });
  }

  function vyLayer() {
    if (!gRoot) return null;
    if (!VY.layer || VY.layer.parentNode !== gRoot) {
      VY.layer = mk("g", { id: "vy-layer" }, gRoot);
    }
    return VY.layer;
  }

  // 按 from/to 找边；找不到再按话题兜底（改了节点名也能对上）
  function vyEdge(h) {
    if (!gEdge) return null;
    var all = gEdge.querySelectorAll("g[data-edge]"), i, g;
    // 同一对节点之间可能有多条线（下发 + 回传），光按端点匹配会认错，
    // 先按 (from, to, topic) 三元组精确定位。
    for (i = 0; i < all.length; i++) {
      g = all[i];
      if (g.getAttribute("data-from") === h.from &&
          g.getAttribute("data-to") === h.to &&
          (g.getAttribute("data-topic") || "") === (h.topic || "")) return g;
    }
    for (i = 0; i < all.length; i++) {
      g = all[i];
      if (g.getAttribute("data-from") === h.from &&
          g.getAttribute("data-to") === h.to) return g;
    }
    for (i = 0; i < all.length; i++) {
      g = all[i];
      if ((g.getAttribute("data-topic") || "") === h.topic) return g;
    }
    return null;
  }

  function vyNodeEl(id) {
    if (!gNode) return null;
    return gNode.querySelector('[data-node="' + cssEsc(id) + '"]');
  }

  function vyNodeBox(id) {
    var g = vyNodeEl(id);
    return g ? g.querySelector('[data-box]') : null;
  }

  // 没有连线的节点：本次不参与验证。压暗 + 虚线框，意思是「它没被测」，
  // 不是「它有问题」——单独验一个孤点得替它造上下游，成本远高于收益。
  function vyMarkIsolated(id) {
    var g = vyNodeEl(id);
    if (g) g.classList.add("vy-iso");
  }


  // ---- AI 注入器小框：贴在头节点前方，一条曲线接进它的入口 ----
  function vyInjector(headId, label) {
    var L = vyLayer();
    if (!L || !headId) return;
    var old = L.querySelector("#vy-inject");
    if (old) old.parentNode.removeChild(old);
    var p = posOf(headId);
    var w = 176, h = 50;
    // 放左上方：正左方会压住同一行更上游的节点
    var x = p.x - w - 34, y = p.y - h - 44;
    var g = mk("g", { id: "vy-inject", class: "vy-inj" }, L);

    // 先画连线（在下层），再画框
    var sx = x + w - 24, sy = y + h;
    var ex = p.x + 12, ey = p.y + 6;
    var k = Math.max(40, Math.abs(ex - sx) * 0.5);
    mk("path", { d: "M" + sx + " " + sy + " C" + (sx + k) + " " + sy + "," +
                     (ex - k) + " " + ey + "," + ex + " " + ey,
                 class: "vy-inj-wire", "marker-end": "url(#gArrow)" }, g);

    mk("rect", { x: x, y: y, width: w, height: h, rx: 9,
                 class: "vy-inj-box" }, g);
    var t1 = mk("text", { x: x + w / 2, y: y + 20, "text-anchor": "middle",
                          class: "vy-inj-t1" }, g);
    t1.textContent = "AI 正在生成脚本";
    var t2 = mk("text", { x: x + w / 2, y: y + 36, "text-anchor": "middle",
                          class: "vy-inj-t2" }, g);
    t2.textContent = label || "下发数据 →";
  }

  // 链路上最后一个节点：下位机贴在它右边，不然整张图会被拉宽
  function vyLastNode(st) {
    var hops = (st && st.hops) || [], id = "";
    hops.forEach(function (h) {
      if (h.kind !== "mcu" && h.to) id = h.to;
    });
    return id;
  }

  // ---- 下位机模拟：一台设备一个框，指令下去一条线、状态回来一条线 ----
  // 以前只有一条「末端尾巴」，图上既看不出下位机在哪，也看不出反馈回到谁手里。
  // 现在按设备（motor / battery…）各画一台：下行实线箭头指向下位机，
  // 回传走另一条线、箭头指回订阅它的节点，两条线在框边就分开，不会重叠。
  function vyTakenRects() {
    var out = [];
    ((state.graph && state.graph.nodes) || []).forEach(function (n) {
      var p = posOf(n.id);
      // 预留节点下方的日志框位置，别把下位机摆到日志框上
      out.push({ x: p.x - 10, y: p.y - 26, w: W + 20, h: H + 84 });
    });
    var L = VY.layer;
    if (L) {
      Array.prototype.forEach.call(L.querySelectorAll(".vy-mcu-box"), function (el) {
        out.push({ x: +el.getAttribute("x") - 14, y: +el.getAttribute("y") - 14,
                   w: +el.getAttribute("width") + 28, h: +el.getAttribute("height") + 28 });
      });
    }
    return out;
  }

  function vyHitRect(x, y, w, h, rs) {
    for (var i = 0; i < rs.length; i++) {
      var r = rs[i];
      if (x < r.x + r.w && x + w > r.x && y < r.y + r.h && y + h > r.y) return true;
    }
    return false;
  }

  function vyMcuNode(m, fallbackAnchor, idx) {
    var L = vyLayer();
    if (!L) return null;
    var key = m.id || ("mcu" + idx);
    var old = L.querySelector('[data-mcu="' + cssEsc(key) + '"]');
    if (old) old.parentNode.removeChild(old);

    var fbs = m.feedback || [];
    var anchorId = m.from || (fbs[0] && fbs[0].to) || fallbackAnchor || "";
    var b = anchorId ? posOf(anchorId) : null;
    var w = 176, h = 58, x, y;
    if (b) {
      // 按距离由近到远试八个方位：多台设备时不能都挤在同一个位置，
      // 也不能为了避让被推到图外面去（线会拉得老长）
      var cand = [[b.x + W + 84, b.y + H / 2 - h / 2],
                  [b.x + W + 84, b.y - h - 46],
                  [b.x + W + 84, b.y + H + 78],
                  [b.x - w - 84, b.y + H / 2 - h / 2],
                  [b.x + W / 2 - w / 2, b.y - h - 58],
                  [b.x + W / 2 - w / 2, b.y + H + 88],
                  [b.x - w - 84, b.y - h - 46],
                  [b.x - w - 84, b.y + H + 78]];
      var taken = vyTakenRects();
      for (var i = 0; i < cand.length; i++) {
        if (!vyHitRect(cand[i][0], cand[i][1], w, h, taken)) {
          x = cand[i][0]; y = cand[i][1]; break;
        }
      }
      if (x === undefined) { x = cand[0][0]; y = cand[0][1]; }
    } else {
      x = 40; y = 40;
    }

    var g = mk("g", { id: "vy-mcu-" + key, class: "vy-mcu",
                      "data-mcu": key }, L);

    // 下行：anchor 右边缘 → 下位机左边缘上半
    if (b && m.topic) {
      var right = x > b.x;
      var x1 = right ? b.x + W : b.x, y1 = b.y + H / 2;
      var x2 = right ? x : x + w, y2 = y + 20;
      var k = clamp(Math.abs(x2 - x1) * 0.45, 40, 160);
      mk("path", { d: bezDir(x1, y1, x2, y2, k), class: "vy-mcu-wire",
                   "marker-end": "url(#gArrow)", "data-topic": m.topic }, g);
    }

    // 回传：下位机边缘下半 → 订阅节点的右边缘下半，箭头指回节点
    fbs.forEach(function (fb, n) {
      var t = posOf(fb.to);
      if (!t) return;
      var right = x > t.x;
      var sx = right ? x : x + w, sy = y + h / 2 + 13 + n * 9;
      var ex = right ? t.x + W : t.x, ey = t.y + H / 2 + 15 + n * 9;
      var kk = clamp(Math.abs(sx - ex) * 0.4, 34, 150);
      mk("path", { d: bezDir(sx, sy, ex, ey, kk), class: "vy-mcu-back",
                   "marker-end": "url(#gArrow)", "data-topic": fb.topic }, g);
    });

    mk("rect", { x: x, y: y, width: w, height: h, rx: 9,
                 class: "vy-mcu-box" }, g);
    var t1 = mk("text", { x: x + w / 2, y: y + 22, "text-anchor": "middle",
                          class: "vy-mcu-t1" }, g);
    t1.textContent = "下位机模拟" + (m.id ? " · " + m.id : "");
    var t2 = mk("text", { x: x + w / 2, y: y + 39, "text-anchor": "middle",
                          class: "vy-mcu-t2" }, g);
    var pair = m.topic ? (fbs.length ? m.topic + " ⇄ " + fbs[0].topic : m.topic)
                       : (fbs[0] ? fbs[0].topic + " →" : "");
    t2.textContent = trunc(pair, 30);
    return g;
  }

  function vyClearExtras() {
    var L = VY.layer;
    if (!L) return;
    Array.prototype.forEach.call(L.querySelectorAll(".vy-inj, .vy-mcu"), function (el) {
      el.parentNode.removeChild(el);
    });
    var logs = L.querySelectorAll(".vy-log");
    Array.prototype.forEach.call(logs, function (el) { el.parentNode.removeChild(el); });
    if (canvas) {
      var lp = canvas.querySelector(".vy-logpanel");
      if (lp) lp.parentNode.removeChild(lp);
    }
  }


  // ---- 节点日志小框：挂在节点正下方，默认 3 行预览，点一下展开 ----
  // 之前每行都按 33 个字硬截断，rcl 日志的 `[WARN] [时间戳] [logger]:` 前缀就占满了，
  // 真正的信息全被吃掉。现在展开后按框宽折行（一行变多行），行内不再腰斩；
  // 折行仍放不下的，展开框里有「全文 / 复制」入口，弹可滚动的完整日志面板。
  // 宽度固定不放大：节点横向间距只有 270，框一加宽就会压住邻居的日志框。
  var VY_LOG_W = 232, VY_LOG_LH = 12, VY_LOG_COOL = 3, VY_LOG_MAX = 14;
  var VY_LOG_CW = 5.5;    // 10px 等宽字体单字符实际宽度（实测约 5.4~5.6px）

  // 中文是等宽的 1.7 倍左右：只按字符个数切，含中文的行会冲出框外，
  // 所以统一换算成「等宽字符宽度」再截断/折行。
  function vyUnits(ch) {
    var c = ch.charCodeAt(0);
    if (c >= 0x1100 && (c <= 0x115f || (c >= 0x2e80 && c <= 0xa4cf) ||
        (c >= 0xac00 && c <= 0xd7a3) || (c >= 0xf900 && c <= 0xfaff) ||
        (c >= 0xfe30 && c <= 0xfe6f || (c >= 0xff00 && c <= 0xff60)) ||
        (c >= 0xffe0 && c <= 0xffe6))) return 1.75;
    return 1;
  }

  // 返回「不超过 n 个等宽字符宽度」能放下几个字
  function vyTake(s, n) {
    var w = 0, i = 0;
    for (; i < s.length; i++) {
      var u = vyUnits(s.charAt(i));
      if (w + u > n) break;
      w += u;
    }
    return i;
  }

  function vyClip(str, n) {
    str = String(str == null ? "" : str).replace(/\t/g, "  ");
    var i = vyTake(str, n - 1);
    return i >= str.length ? str : str.slice(0, Math.max(1, i)) + "…";
  }

  // 按字符数折行，优先在空格处断开，避免把话题名 / 字段名切碎。
  // 续行缩进两格，一眼能看出这是上一行接过来的。
  function vyWrap(str, n) {
    var out = [];
    var s = String(str == null ? "" : str).replace(/\t/g, "  ").replace(/\s+$/, "");
    var guard = 0;
    while (s.length && guard++ < 300) {
      var indent = out.length ? "  " : "";
      var i = vyTake(s, n - (out.length ? 2 : 0));
      if (i <= 0) i = 1;
      if (i < s.length) {
        var sp = s.lastIndexOf(" ", i);
        if (sp > i * 0.5) i = sp;      // 能在空格处断更自然
      }
      out.push(indent + s.slice(0, i).replace(/\s+$/, ""));
      s = s.slice(i).replace(/^\s+/, "");
    }
    return out;
  }

  // rcl 默认日志行是 `[INFO] [1794566187.793352840] [motor_node]: 内容`，
  // 光前缀就占满 232 宽的框，三条折出来全是同样的噪音，真正的信息看不到。
  // 显示前压成 `[INFO 18:23:07] 内容`：节点名框上已经有，时间给到秒就够。
  var VY_RCL = /^\s*\[(INFO|WARN|ERROR|DEBUG|FATAL)\]\s*\[([0-9]+(?:\.[0-9]+)?)\]\s*\[[^\]]*\]\s*:?\s?([\s\S]*)$/;

  function vyClean(line) {
    var s = String(line == null ? "" : line).replace(/\x1b\[[0-9;]*[A-Za-z]/g, "");
    var m = VY_RCL.exec(s);
    if (!m) return s;
    var hh = "", t = parseFloat(m[2]);
    if (isFinite(t)) {
      var d = new Date(t * 1000);
      hh = ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2)
        + ":" + ("0" + d.getSeconds()).slice(-2);
    }
    return "[" + m[1] + (hh ? " " + hh : "") + "] " + m[3];
  }

  function vyLogBox(id, text, tone, done) {
    var L = vyLayer();
    if (!L) return;
    var sel = '[data-log="' + cssEsc(id) + '"]';
    var old = L.querySelector(sel);
    if (old) old.parentNode.removeChild(old);

    var raw = String(text || "").split("\n");
    var expanded = !!VY.expand[id];
    var bw = VY_LOG_W;
    var chars = Math.max(18, Math.floor((bw - 16) / VY_LOG_CW));
    var rows = [], total = 0;
    raw.forEach(function (ln) {
      ln = vyClean(ln).replace(/\s+$/, "");
      if (!ln.trim()) return;
      total++;
      if (expanded) {
        vyWrap(ln, chars).forEach(function (x) { rows.push(x); });
      } else {
        rows.push(vyClip(ln, chars));
      }
    });
    // 占位文案要跟状态走：验证都结束了还写「日志收集中」，会让人以为卡住了。
    // 「没有打印日志」不能写成「没有日志」—— 前者是这个节点自己没输出，
    // 后者听起来像采集功能坏了，用户没法分辨（实测被问过）。
    if (!total) {
      rows.push(tone === "warn" ? "（已退出 · 整个验证期间没输出）"
        : tone === "fail" ? "（没输出 · 详见下方结论）"
        : done ? "（进程活着 · 整个验证期间没输出）"
        : "▍ 运行中，日志收集中…");
    }

    var shown, more = "";
    if (expanded) {
      shown = rows.slice(0, VY_LOG_MAX);
      if (rows.length > VY_LOG_MAX) more = "… 还有 " + (rows.length - VY_LOG_MAX) + " 行";
    } else {
      shown = rows.slice(0, VY_LOG_COOL);
      if (total > VY_LOG_COOL) more = "… 共 " + total + " 行，点击展开";
    }
    if (more) shown.push(more);

    var p = posOf(id);
    var x = p.x + W / 2 - bw / 2, y = p.y + H + 12;
    var h = 10 + shown.length * VY_LOG_LH + (expanded ? VY_LOG_LH + 2 : 0);
    var g = mk("g", { class: "vy-log" + (tone ? " " + tone : ""),
                      "data-log": id, "data-exp": expanded ? "1" : "0" }, L);
    var rect = mk("rect", { x: x, y: y, width: bw, height: h, rx: 6,
                            class: "vy-log-box" }, g);
    var txt = mk("text", { x: x + 8, y: y + 14, class: "vy-log-t" }, g);
    shown.forEach(function (ln, i) {
      var ts = mk("tspan", { x: x + 8, dy: i === 0 ? 0 : VY_LOG_LH }, txt);
      ts.textContent = ln;
    });
    if (expanded) {
      // 服务器一次只回最后几十行，折行后也未必全放得下 —— 给一个能看全、能复制的入口
      var full = mk("text", { x: x + bw - 8, y: y + h - 5, "text-anchor": "end",
                              class: "vy-log-more" }, g);
      full.textContent = "看全文 / 复制 ↗";
      full.addEventListener("click", function (ev) {
        ev.stopPropagation();
        vyLogPanel(id);
      });
    }
    // 绑在整组上：点框、点文字都要能展开（文字在矩形上层，绑 rect 会漏掉）
    // 同时拦住 pointerdown，否则画布会用指针捕获抢走这次点击
    // 只能 stopPropagation：在这里 preventDefault 会把后续 click 一起抑制掉
    g.addEventListener("pointerdown", function (ev) { ev.stopPropagation(); });
    g.addEventListener("click", function (ev) {
      ev.stopPropagation();
      VY.expand[id] = !VY.expand[id];
      var st = VY.st, log = "";
      ((st && st.nodes) || []).forEach(function (n) { if (n.id === id) log = n.log; });
      vyLogBox(id, log, tone, !!(st && st.finished));
    });
    // 面板开着时日志还在涨，跟着刷新，别让用户盯着一份停住的快照
    if (expanded && canvas) {
      var lp = canvas.querySelector('[data-logpanel="' + cssEsc(id) + '"]');
      if (lp) {
        var pre = lp.querySelector(".vlp-body");
        var mt = lp.querySelector(".vlp-head span");
        if (pre) pre.textContent = text || vyEmptyNote(id);
        if (mt) mt.textContent = total ? total + " 行 · 完整输出" : "0 行 · 无输出";
      }
    }
    // 记下这次画的内容：下一轮轮询校验时 key 一致就不会把展开态重画回收起。
    // 「跑完没跑完」也要进 key —— 否则空日志的框会一直停在「日志收集中」。
    VY.logCache = VY.logCache || {};
    VY.logCache[id] = text ? ("L:" + text)
      : (tone === "fail" ? "F" : tone === "warn" ? "W" : "P") + (done ? "d" : "r");
    return g;
  }

  function vyLogText(id) {
    var st = VY.st;
    var hit = "";
    ((st && st.nodes) || []).forEach(function (n) { if (n.id === id) hit = n.log; });
    return hit;
  }

  // 完整日志面板：画布右上角浮层，可滚动、可复制。
  // 画布上的小框只适合看个大概，排错时得能一眼看完整行。
  // 空日志不是「功能坏了」：节点多半是跑起来了、只是没往终端写。
  // 但光一句「没有输出」用户分不清是节点沉默还是采集断了 ——
  // 把判据（进程退出码）摊开，谁都能自己对上。
  function vyEmptyNote(id) {
    var st = VY.st, rc = null, seen = false;
    ((st && st.nodes) || []).forEach(function (n) {
      if (n.id === id) { rc = n.rc; seen = true; }
    });
    var out = ["这个节点在验证期间没有向终端输出任何内容。", ""];
    if (!seen) {
      out.push("进程状态：这个节点没有参加本次验证（不在执行计划里）。");
    } else if (rc === null || rc === undefined) {
      out.push("进程状态：没有拿到退出码 —— 脚本没跑到它的收尾。");
      out.push("判断依据：结论里会给断点位置。");
    } else if (rc === 130 || rc === 131) {
      out.push("进程状态：rc=" + rc + " —— 验证结束时被统一中断，不是崩溃。");
      out.push("也就是说：它真跑起来了，只是一行都没打印。");
      out.push("");
      out.push("常见原因：");
      out.push("· 只在收到消息、出错时才打印，这段时间没触发");
      out.push("· 日志写进自己的文件或 logging 框架，没走终端");
    } else if (rc === 0) {
      out.push("进程状态：rc=0 —— 节点自己正常退出了，不是被杀。");
      out.push("如果它本该一直跑，多半是 main() 里没 spin，或者提前 return 了。");
    } else {
      out.push("进程状态：rc=" + rc + " —— 进程异常退出，看下面的诊断。");
    }
    return out.join("\n");
  }

  function vyLogPanel(id) {
    var host = canvas;
    if (!host) return;
    var old = host.querySelector(".vy-logpanel");
    if (old) old.parentNode.removeChild(old);

    var name = id;
    ((state.graph && state.graph.nodes) || []).forEach(function (n) {
      if (n.id === id) name = n.name || n.id;
    });
    var rawText = String(vyLogText(id) || "").replace(/\x1b\[[0-9;]*[A-Za-z]/g, "");
    // 行数只数节点自己的输出；下面那段说明是验证器补的，不能算进「完整输出」
    var rows = rawText.split("\n").filter(function (s) { return s.trim(); }).length;
    var text = rows ? rawText : vyEmptyNote(id);

    var box = document.createElement("div");
    box.className = "vy-logpanel";
    box.setAttribute("data-logpanel", id);
    var head = document.createElement("div");
    head.className = "vlp-head";
    var title = document.createElement("b");
    title.textContent = name + " 的日志";
    var meta = document.createElement("span");
    meta.textContent = rows ? rows + " 行 · 完整输出" : "0 行 · 无输出";
    var copy = document.createElement("button");
    copy.className = "mini-btn vlp-copy";
    copy.textContent = "复制";
    var close = document.createElement("button");
    close.className = "vlp-x";
    close.title = "关闭";
    close.textContent = "×";
    head.appendChild(title); head.appendChild(meta);
    head.appendChild(copy); head.appendChild(close);

    var body = document.createElement("pre");
    body.className = "vlp-body";
    body.textContent = text;
    box.appendChild(head); box.appendChild(body);
    host.appendChild(box);

    close.onclick = function () {
      if (box.parentNode) box.parentNode.removeChild(box);
    };
    copy.onclick = function () {
      var done = function (ok) {
        copy.textContent = ok ? "已复制" : "复制失败";
        setTimeout(function () { copy.textContent = "复制"; }, 1200);
      };
      // 空日志时面板里是说明文字，复制它没有意义 —— 明确告诉用户没东西可复制
      if (!rows) {
        copy.textContent = "无输出";
        setTimeout(function () { copy.textContent = "复制"; }, 1200);
        return;
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () { done(true); },
                                                 function () { done(false); });
        return;
      }
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      document.body.removeChild(ta);
      done(ok);
    };
    return box;
  }


  function vyMarkNode(id, tone) {
    var box = vyNodeBox(id);
    if (!box) return;
    box.classList.remove("vy-ok", "vy-fail", "vy-live", "vy-warn");
    if (tone) box.classList.add("vy-" + tone);
  }

  function vyClearMarks() {
    if (gEdge) {
      Array.prototype.forEach.call(gEdge.querySelectorAll(".vy-ok, .vy-fail"),
        function (el) { el.classList.remove("vy-ok", "vy-fail"); });
    }
    if (gNode) {
      Array.prototype.forEach.call(
        gNode.querySelectorAll(".vy-ok, .vy-fail, .vy-live, .vy-iso, .vy-warn"),
        function (el) {
          el.classList.remove("vy-ok", "vy-fail", "vy-live", "vy-iso", "vy-warn");
        });
    }
    if (VY.layer) {
      Array.prototype.forEach.call(VY.layer.querySelectorAll(".vy-mcu"), function (g) {
        g.classList.remove("vy-mcu-ok", "vy-mcu-fail");
        Array.prototype.forEach.call(g.querySelectorAll(".vy-ok, .vy-fail"),
          function (p) { p.classList.remove("vy-ok", "vy-fail"); });
      });
    }
  }

  function vyMcuEl(id) {
    if (!VY.layer) return null;
    return VY.layer.querySelector('[data-mcu="' + cssEsc(id || "") + '"]');
  }

  // 一跳到一处的视觉：线变绿、两端变绿、下位机框变绿。
  // 抽出来是因为「实时播放」和「重绘后复原」必须长一个样，以前是两份代码。
  function vyPaintHop(h, ok) {
    var tone = ok ? "ok" : "fail";
    if (h.kind === "mcu" || h.kind === "mcu_out") {
      var g = vyMcuEl(h.mcu);
      if (h.kind === "mcu") {
        var src = h.from && h.from !== "__mcu__" ? h.from : "";
        if (!src) {
          ((state.graph && state.graph.edges) || []).forEach(function (e) {
            if (!src && e.topic === h.topic) src = e.from;
          });
        }
        if (src) vyMarkNode(src, tone);
        if (g) {
          var w = g.querySelector(".vy-mcu-wire");
          if (w) w.classList.add("vy-" + tone);
        }
      } else {
        if (h.to) vyMarkNode(h.to, tone);
        if (g) {
          Array.prototype.forEach.call(
            g.querySelectorAll(".vy-mcu-back"),
            function (p) {
              if ((p.getAttribute("data-topic") || "") === (h.topic || "")) {
                p.classList.add("vy-" + tone);
              }
            });
        }
      }
      // 整台设备：只用来给框着色，线的颜色由线自己带，否则一条红一条绿会被框盖掉
      if (g && !g.classList.contains("vy-mcu-fail")) {
        g.classList.add(ok ? "vy-mcu-ok" : "vy-mcu-fail");
      }
      return;
    }
    var el = vyEdge(h);
    if (el) {
      var path = el.querySelector(".ge-path");
      if (path) path.classList.add("vy-" + tone);
    }
    if (h.from) vyMarkNode(h.from, tone);
    if (h.to) vyMarkNode(h.to, tone);
  }

  // 播一跳：把这一段的线变绿、两端节点框变绿、给节点挂上日志框。
  // 只有服务端真的收到过这条消息，才会走到这里 —— 不做「先动画后补结果」。
  function vyPlayHop(i) {
    var st = VY.st, hops = (st && st.hops) || [];
    var h = hops[i];
    if (!h) return;
    var ok = h.result === "ok";
    vyPaintHop(h, ok);

    if (!ok) {
      VY.halt = true;
      VY.queue = [];
      say("卡在第 " + (i + 1) + " 段：" + h.topic + " 没有收到消息", 8000);
    } else {
      var how = h.kind === "mcu" ? "下发给下位机"
        : h.kind === "mcu_out" ? "下位机回传" : "";
      say("第 " + (i + 1) + " 段 " + h.topic +
          (how ? "（" + how + "）" : "") + " 收到真实消息", 4000);
    }
  }

  function vyPump() {
    if (VY.playing || !VY.queue.length) return;
    VY.playing = true;
    var i = VY.queue.shift();
    vyPlayHop(i);
    setTimeout(function () {
      VY.playing = false;
      if (VY.queue.length) { vyPump(); return; }
      // 队列空了且后端已收尾 → 出结论（轮询可能已经停了，这里兜住）
      if (VY.st && VY.st.finished) {
        // 轮询停了就不会再走 vyApply，收尾这一刻必须自己把状态补齐，
        // 否则最后几跳之外的节点永远没日志框。
        vySettleNodes(VY.st);
        vySyncLogs(VY.st);
        vyFinish(VY.st);
      }
    }, 360);
  }


  var VY_STAGE = {
    prepare: "准备环境", deps: "安装依赖", build: "正在编译", run: "启动全部节点",
    inject: "AI 下发数据", hop: "逐链路取证", done: "收尾比对"
  };

  // 「编译这一关没过」：代码编译报错，或者容器里缺件（连 colcon 都没有）
  function vyBlk(st) {
    return !!(st && (st.build_failed || st.env_missing || st.env_fail));
  }

  // 断在第几跳（0 基）；没断返回 -1
  function vyBreakHop(st) {
    var h = (st && st.hops) || [];
    for (var i = 0; i < h.length; i++) if (h[i].result === "fail") return i;
    return -1;
  }

  // 编译进度文案。
  // colcon 的每个包会打「Starting >>> pkg」和「Finished <<< pkg」两行，
  // 后端把它们折算成 build_done / build_total。没有这两个数字时，45 个
  // 包的长编译只能显示「正在编译项目…」原地转圈，用户没法判断是慢还是死。
  function vyBuildText(st) {
    var retry = (st && st.retry_pkg) || "";
    // 补编阶段：并发那一轮已经跑完，正在把被 OOM 杀掉的包降成单线程重来。
    // 这时候 build_done 已经等于 build_total，直接套下面的公式会一直显示
    // 「编译收尾 45/45」，看起来和卡死没区别，所以单独说一句。
    if (retry) return "内存不足，正在单线程补编：" + retry;
    var total = (st && st.build_total) || 0;
    var done = (st && st.build_done) || 0;
    if (!total) return "正在编译项目…";
    if (done >= total) return "编译收尾 " + total + "/" + total;
    var cur = (st && st.build_cur) || "";
    return "正在编译 " + (done + 1) + "/" + total + (cur ? "：" + cur : "");
  }

  function vyStage(st) {
    var blocked = vyBlk(st);
    // 只有「依赖阶段真的失败」才算依赖问题。
    // pkgs_missing 是「包池里没有这个 deb」，后端并不拦编译（只记一行标记），
    // 把它当成致命错误会得出「依赖没补齐，脚本没有下发」这种假话 ——
    // 脚本明明下发了，colcon 也跑了，用户却看到一句没发生的结论。
    // 缺包该提示，但不能盖住真实执行状态。
    var dep = !!(st && st.deps_failed);
    var done = !!(st && st.finished);
    // partial 也是「跑完了但没全绿」，可数据确实在链上跑通了，
    // 说成「数据没跑完全链路」会把一个有 2 个示例崩溃的项目描述成断链。
    var broke = done && !st.ok && !st.partial;
    var at = vyBreakHop(st);
    var upn = (st.nodes || []).filter(function (n) { return !!n.upstream; }).length;
    var t = VY.layer && VY.layer.querySelector("#vy-inject .vy-inj-t2");
    if (t) {
      // stage=done 只能说明「跑完了」，跑完不等于通过 —— 断了要说断在第几跳
      t.textContent = blocked ? "脚本没有下发"
        : dep ? "依赖没补齐，脚本没有下发"
        : broke ? (at >= 0 ? "数据断在第 " + (at + 1) + " 跳" : "数据没跑完全链路")
        : (done && st.partial) ? ("数据已跑完全链路 · "
            + (upn && upn === (st.nodes || []).filter(function (n) {
                return n.result === "fail"; }).length
              ? upn + " 个示例不兼容，与你的代码无关"
              : upn ? "个别节点没起来" : "个别节点没起来"))
        : done ? "数据已跑完全链路"
        : st.stage === "deps" ? "正在装依赖…"
        : st.stage === "build" ? vyBuildText(st)
        : st.stage === "inject" ? "下发数据中…"
        : st.stage === "hop" ? "数据已注入 · 逐跳取证"
        : "准备下发…";
    }
    // 跑完之后还挂着「AI 正在生成脚本」会让人以为它还在跑
    var inj = VY.layer && VY.layer.querySelector("#vy-inject");
    if (inj) {
      inj.classList.toggle("vy-inj-fail", blocked || dep || broke);
      inj.classList.toggle("vy-inj-ok", done && !!st.ok);
      var h = inj.querySelector(".vy-inj-t1");
      if (h) {
        h.textContent = blocked ? "编译没通过"
          : dep ? "缺依赖"
          : broke ? "注入后没跑通"
          : done ? "注入完成" : "AI 正在生成脚本";
      }
    }
    var box = $("gp-compile");
    if (box) {
      box.classList.toggle("busy", !!st.running);
      var info = $("gp-compile-info");
      if (info) {
        info.textContent = blocked ? "编译没通过"
          : (st.finished && st.ok) ? "验证通过"
          : st.finished ? (at >= 0 ? "第 " + (at + 1) + " 跳断了" : "没跑通")
          : st.stage === "build" ? vyBuildText(st)
          : (VY_STAGE[st.stage] || "验证中");
      }
    }
  }

  // 收尾后按「节点自己的结论」把状态色补齐。
  // 逐跳染色是一段一段播的（260~360ms 一跳），72 跳要播二十几秒：
  // 这时候截图，画面上只有播到的节点是亮的，其余的没有日志框 ——
  // 用户会以为「这些节点没跑 / 日志没采到」。节点起没起来服务端早有结论
  // （result 与退出码 rc），收尾时按它一次性补齐，不依赖动画播到哪儿。
  function vySettleNodes(st) {
    (st.nodes || []).forEach(function (n) {
      var box = vyNodeBox(n.id);
      if (!box) return;
      // 官方示例没跟上发行版：给琥珀色，不用红色 —— 那是它自己的兼容问题，
      // 既不是链路断了，也不是用户写错了
      if (n.result === "fail") {
        vyMarkNode(n.id, n.upstream ? "warn" : "fail");
        return;
      }
      // 已经红了的别刷成绿的：某一段失败不该被整体着色盖掉
      if (box.classList.contains("vy-fail")) return;
      // rc 有值 = 这个进程真的起过（130/131 是被我们统一中断）
      if (n.result === "ok" || (n.rc !== null && n.rc !== undefined)) {
        vyMarkNode(n.id, "ok");
      }
    });
  }

  // 日志只在该节点「已运行过」时出现；内容一变就重画，不变不动（避免闪）
  function vySyncLogs(st) {
    var cache = VY.logCache || (VY.logCache = {});
    (st.nodes || []).forEach(function (n) {
      var box = vyNodeBox(n.id);
      if (!box) return;
      if (!box.classList.contains("vy-ok") && !box.classList.contains("vy-fail")
        && !box.classList.contains("vy-warn")) return;
      var tone = n.upstream ? "warn"
        : n.result === "fail" ? "fail" : (n.result === "ok" ? "ok" : "");
      var text = n.log || "";
      var done = !!(st && st.finished);
      // key 里带 done：跑完后即便日志仍为空，也要重画一次把「收集中」换掉
      var key = text ? "L:" + text
        : (box.classList.contains("vy-ok") ? "P"
          : box.classList.contains("vy-warn") ? "W" : "F") + (done ? "d" : "r");
      if (cache[n.id] === key) return;
      cache[n.id] = key;
      vyLogBox(n.id, text, tone || (box.classList.contains("vy-ok") ? "ok" : ""), done);
    });
  }

  // 存盘时间戳 → 「11-14 22:31」，只给人看个「啥时候跑的」
  function vyWhen(ts) {
    var d = new Date(Number(ts) * 1000);
    if (!isFinite(d.getTime())) return "";
    var p = function (n) { return (n < 10 ? "0" : "") + n; };
    return (d.getMonth() + 1) + "-" + p(d.getDate()) + " " +
           p(d.getHours()) + ":" + p(d.getMinutes());
  }

  function vyFinish(st) {
    var bar = $("vy-result");
    if (!bar) return;
    bar.classList.remove("hidden", "ok", "fail", "skip");
    // local 也是真跑（本机有 colcon/ros2），只有 none 才算「没执行」
    // partial：链路逐跳全通，只是个别节点自己没起来。既不该报绿（有节点是坏的），
    // 也不该报红（数据在链上确实通了），给一个中性的灰条单独说清楚。
    var tone = st.ok ? "ok"
      : (st.partial || st.backend === "none") ? "skip" : "fail";
    var sim = st.backend === "mock";
    bar.classList.add(tone);
    if (sim) bar.classList.add("sim"); else bar.classList.remove("sim");
    var head = $("vy-result-head"), body = $("vy-result-body");
    var bfail = vyBlk(st);
    // 同 vyStage：缺包 ≠ 依赖阶段失败。后端缺包也照常编译，
    // 把 pkgs_missing 当失败会让一次真的编译通过被写成「依赖没补齐」。
    var dep = !!(st.deps_failed);
    // 官方示例不兼容（upstream）与用户自己的节点崩了，标题里要分开数：
    // 前者一行代码都不用改，写成「个别节点没起来」会让人白找一遍
    var upn = (st.nodes || []).filter(function (n) { return !!n.upstream; }).length;
    var badn = (st.nodes || []).filter(function (n) { return n.result === "fail"; }).length;
    var mineN = badn - upn;
    // 编译挂在「缺包」上时，标题要直接说清下一步是谁的事：
    // 用户装不了包，写「编译没通过」只会让他反复改自己的 CMakeLists。
    // 只在编译日志确实报缺包时才这么写，避免把语法错也归到缺包上。
    var depMiss = bfail && !!st.hint && /找不到依赖包|镜像里没有包/.test(st.hint);
    // 缺包是「这台机器没有」，不是用户写错了：用琥珀色（未完成）而不是红色，
    // 红色只留给真正要用户改代码的问题。
    if (depMiss) { bar.classList.remove("fail"); bar.classList.add("skip"); }
    if (head) {
      head.textContent = (st.ok ? "✓ 全链路验证通过"
        : depMiss ? "△ 镜像里没有这个包，编译没通过 —— 需要管理员补包"
        : bfail ? "✕ 编译没通过，链路没有验证"
        : dep ? "✕ 依赖没补齐，链路没有验证"
        : st.partial && upn === badn
          ? "△ 链路取证通过，" + upn + " 个官方示例跟发行版不兼容"
        : st.partial ? "△ 链路取证通过，" + (mineN || badn) + " 个节点没起来"
          + (upn ? "（另有 " + upn + " 个示例不兼容）" : "")
        : tone === "skip" ? "· 本次未执行"
        : st.cancelled ? "· 已退出验证" : "✕ 链路没有跑通")
        + (sim ? " · 模拟" : "")
        // 回看时必须写明是「上次」的，否则会被当成刚刚又跑了一遍
        + (st.restored && st.saved_at ? " · 上次运行 " + vyWhen(st.saved_at) : "");
    }
    if (body) {
      var bits = [];
      if (sim) bits.push("当前离线演示：未接 Docker，链路与日志为模拟");
      if (st.reason) bits.push(st.reason);
      // 后端结论里已经写过「逐跳取证 72 段全部通过 / 72 跳全部收到真实消息」，
      // 这里再报一遍 72/72 就是同一句话说两次（实测那条横条读起来像复读）。
      // 只有后端没给结论时，才由界面自己补这段计数。
      if (st.hops && st.hops.length && !bfail && !dep && !st.reason) {
        var okn = st.hops.filter(function (h) { return h.result === "ok"; }).length;
        var nan = st.hops.filter(function (h) { return h.result === "na"; }).length;
        bits.push("逐跳取证 " + okn + "/" + st.hops.length + " 段"
          + (nan ? "（" + nan + " 段接口解析不出来，没取证）" : ""));
      }
      if ((bfail || dep) && st.hops && st.hops.length) {
        bits.push("逐跳取证 " + st.hops.length + " 段全部未开始");
      }
      // 一声不吭的节点要报个数：图上一片节点没有日志框，看着像「没采集到」，
      // 其实多半是这个节点整个验证期间没往终端写。说清楚数量，别让人猜。
      var silent = (st.nodes || []).filter(function (n) {
        return n.result === "ok" && !String(n.log || "").trim();
      }).length;
      if (silent) {
        bits.push(silent + " 个节点全程没打印（进程在跑，只是没输出）");
      }
      // 补装过什么要写出来：镜像里本来没有，是包池临时补的
      if (st.pkgs_installed && st.pkgs_installed.length) {
        bits.push("补装 " + st.pkgs_installed.join("、"));
      }
      if (st.elapsed) bits.push("用时 " + Math.round(st.elapsed) + "s");
      if (st.distro) bits.push("ROS 2 " + st.distro);
      body.textContent = bits.join(" · ");
    }
    // 后端把原始报错翻译成一句能照着改的话；认不出来就留空，不瞎猜。
    // 「官方示例没跟上发行版」单独走 note（灰色说明），不进红色诊断 ——
    // 那种情况用户一行代码都不用改，标成报错只会让他以为是自己写错了。
    var hintEl = $("vy-result-hint"), noteEl = $("vy-result-note");
    var tog = $("vy-result-toggle");
    if (VY.detail === null || VY.detail === undefined) VY.detail = !!st.hint;
    if (noteEl) {
      noteEl.textContent = st.note || "";
      noteEl.classList.toggle("hidden", !st.note || !VY.detail);
    }
    if (hintEl) {
      hintEl.textContent = st.hint ? "诊断：" + st.hint : "";
      hintEl.classList.toggle("hidden", !st.hint || !VY.detail);
    }
    if (tog) {
      var has = !!(st.note || st.hint);
      tog.classList.toggle("hidden", !has);
      tog.textContent = VY.detail ? "收起"
        : (st.hint ? "查看诊断" : "查看原因");
    }
    vyBuildLog(st);
    bar.classList.remove("hidden");
  }

  // 编译失败时把 colcon 的原始输出摊开：只说「没通过」等于没说
  function vyBuildLog(st) {
    var main = document.querySelector("#vy-result .vy-result-main");
    var oldBtn = document.getElementById("vy-result-more");
    var oldPre = document.getElementById("vy-result-log");
    if (oldBtn) oldBtn.parentNode.removeChild(oldBtn);
    if (oldPre) oldPre.parentNode.removeChild(oldPre);
    if (!main || !(st.build_failed || st.env_missing || st.env_fail)) return;
    var text = String(st.build_log || st.log || "").replace(/\x1b\[[0-9;]*[A-Za-z]/g, "");
    if (!text.trim()) {
      text = st.env_missing
        ? "容器镜像里找不到 " + st.env_missing + "。\n" +
          "ros:jazzy-ros-base 不保证自带 colcon，需要额外装：\n" +
          "  docker build -f Dockerfile.verify -t ros2-tutor:jazzy .\n" +
          "然后在 config.json 里把 ros.verify.image 设成 ros2-tutor:jazzy。"
        : "（没有拿到编译输出：容器可能没起来，看看服务端日志）";
    }
    var lines = text.split("\n").filter(function (l) { return l.trim(); });
    var btn = document.createElement("button");
    btn.id = "vy-result-more";
    btn.type = "button";
    btn.className = "vy-result-more";
    btn.textContent = "查看编译输出";
    var pre = document.createElement("pre");
    pre.id = "vy-result-log";
    pre.className = "vy-result-log hidden";
    pre.textContent = lines.slice(-40).join("\n");
    btn.addEventListener("click", function (ev) {
      ev.stopPropagation();
      var on = pre.classList.contains("hidden");
      pre.classList.toggle("hidden", !on);
      btn.textContent = on ? "收起编译输出" : "查看编译输出";
    });
    main.appendChild(btn);
    main.appendChild(pre);
  }

  function vyApply(st) {
    if (!VY.on || !st) return;
    VY.st = st;
    vyStage(st);
    if (!VY.halt) {
      (st.hops || []).forEach(function (h, i) {
        if (VY.played[i]) return;
        if (h.result === "ok" || h.result === "fail") {
          VY.played[i] = 1;
          VY.queue.push(i);
        }
      });
    }
    vyPump();
    // 收尾时先把所有真起过的节点照亮，再同步日志：
    // 顺序反了会漏掉「动画还没播到」的那些，画面上就永远没有它们的日志框。
    var settled = !!(st.finished && !VY.queue.length && !VY.playing);
    if (settled) vySettleNodes(st);
    vySyncLogs(st);
    if (settled) vyFinish(st);
  }


  function vyBtn(running) {
    var b = $("gp-verify");
    if (!b) return;
    b.textContent = running ? "退出验证" : "编译运行";
    b.classList.toggle("running", !!running);
    b.title = running ? "停止验证并回到架构图" : "真实编译并跑通整条链路";
  }

  function vyHeadId(st) {    var h = (st && st.hops) || [];
    if (!h.length || h[0].kind === "mcu") return "";
    return h[0].from || h[0].to || "";
  }

  function vyBoot(st) {
    vyClearMarks();
    vyClearExtras();
    VY.detail = null;
    (st.isolated || []).forEach(function (n) { vyMarkIsolated(n.id); });
    var bar = $("vy-result");
    if (bar) bar.classList.add("hidden");
    var head = vyHeadId(st);
    if (head) vyInjector(head, "准备下发…");
    (st.mcus || []).forEach(function (m, i) { vyMcuNode(m, vyLastNode(st), i); });
    if (!head) say("这张图没有起点节点，只能做整体编译验证", 6000);
    vyApply(st);
    setTimeout(function () { if (VY.on) fit(2); }, 60);
  }

  function vyExit(silent) {
    var was = VY.on, review = VY.review;
    VY.on = false;
    VY.review = false;
    if (canvas) canvas.classList.remove("vy-locked");
    ["gp-add", "gp-commit"].forEach(function (k) {
      var b = $(k);
      if (b) b.classList.remove("locked");
    });
    if (VY.timer) { clearInterval(VY.timer); VY.timer = 0; }
    VY.queue = []; VY.playing = false; VY.halt = false;
    // 回看历史结果时没有活的进程，别去取消一个早跑完的验证
    if (was && !silent && !review) vyPost("/api/graph/verify/cancel").catch(function () {});
    vyClearMarks();
    vyClearExtras();
    VY.layer = null;
    VY.st = null;        // 退出后状态清空，避免残留结果被 render 里的 vyRestore 恢复/叠加
    VY.sid = "";
    var bar = $("vy-result");
    if (bar) bar.classList.add("hidden");
    var box = $("gp-compile");
    if (box) { box.classList.remove("busy"); }
    var info = $("gp-compile-info");
    if (info) info.textContent = "未验证";
    vyBtn(false);
    if (was && !silent) say("已退出验证，回到架构图", 5000);
  }

  function vyRun() {
    VY.on = true;
    VY.review = false;
    // 抽屉里能直接改代码，验证期间不该开着；画布也一并上锁
    if (state.sel) closeDrawer();
    if (canvas) canvas.classList.add("vy-locked");
    ["gp-add", "gp-commit"].forEach(function (k) {
      var b = $(k);
      if (b) b.classList.add("locked");
    });
    VY.sid = sid();
    VY.queue = []; VY.played = {}; VY.playing = false; VY.halt = false;
    VY.expand = {}; VY.logCache = {}; VY.st = null; VY.layer = null;
    VY.detail = null;
    vyBtn(true);
    var bar = $("vy-result");
    if (bar) bar.classList.add("hidden");
    var box = $("gp-compile");
    if (box) box.classList.add("busy");
    var info = $("gp-compile-info");
    if (info) info.textContent = "启动中";

    vyPost("/api/graph/verify").then(function (r) { return r.json(); })
      .then(function (r) {
      if (!VY.on) return;
      if (r.error) {
        vyExit(true);
        say(r.message || r.error, 8000);
        return;
      }
      vyBoot(r);
      if (VY.timer) clearInterval(VY.timer);
      VY.timer = setInterval(vyPoll, 900);
    }).catch(function (e) {
      vyExit(true);
      say("启动验证失败：" + e, 8000);
    });
  }

  function vyStart() {
    if (VY.on) { vyExit(false); return; }
    if (!state.graph || !(state.graph.nodes || []).length) {
      say("先上传项目、生成架构图，再点「编译运行」", 6000);
      return;
    }
    var pending = ((state.graph.stats || {}).pending) || 0;
    if (pending) { vyAskDraft(pending); return; }
    vyRun();
  }


  function vyPoll() {
    if (!VY.on) return;
    api("/api/graph/verify/status?sid=" + encodeURIComponent(VY.sid))
      .then(function (r) { return r.json(); })
      .then(function (st) {
        if (!VY.on || !st) return;
        vyApply(st);
        if (st.finished && VY.timer) { clearInterval(VY.timer); VY.timer = 0; }
      })
      .catch(function () {});
  }

  // 未提交就点运行 → 三选一，明说「不提交跑的是旧的那份」
  function vyAskDraft(n) {
    var box = $("vy-draft");
    if (!box) { vyRun(); return; }
    var num = $("vy-draft-n");
    if (num) num.textContent = n;
    box.classList.remove("hidden");
  }

  function vyCloseDraft() {
    var box = $("vy-draft");
    if (box) box.classList.add("hidden");
  }

  function vyAfterCommit() {
    // 等提交真正落盘（pending 归零）再自动开跑，最长等 40 秒
    var t0 = Date.now();
    var timer = setInterval(function () {
      var pending = ((state.graph && state.graph.stats) || {}).pending || 0;
      if (!pending) { clearInterval(timer); vyRun(); return; }
      if (Date.now() - t0 > 40000) { clearInterval(timer); say("提交还没完成，稍后再点「编译运行」", 7000); }
    }, 700);
  }

  function vyBind() {
    var b = $("gp-verify");
    if (b) b.addEventListener("click", function (ev) { ev.stopPropagation(); vyStart(); });
    var box = $("vy-draft");
    if (box) {
      box.addEventListener("click", function (ev) {
        var act = ev.target && ev.target.getAttribute && ev.target.getAttribute("data-act");
        if (!act) {
          if (ev.target === box) vyCloseDraft();
          return;
        }
        vyCloseDraft();
        if (act === "committed") vyRun();
        if (act === "commit") { commit(); vyAfterCommit(); }
      });
    }
    var close = $("vy-result-close");
    if (close) close.addEventListener("click", function () {
      var bar = $("vy-result");
      if (bar) bar.classList.add("hidden");
    });
    // 结论条默认只占两行：诊断/说明折在「详情」里，免得一整块盖住半张图
    var tog = $("vy-result-toggle");
    if (tog) tog.addEventListener("click", function (ev) {
      ev.stopPropagation();
      VY.detail = !VY.detail;
      if (VY.st) vyFinish(VY.st);
    });
  }


  // 画布一旦整体重绘（改图、缩放重建），验证的视觉要能原样恢复
  function vyRestore() {    var st = VY.st;
    if (!st) return;
    VY.layer = null;
    VY.logCache = {};
    var head = vyHeadId(st);
    if (head) vyInjector(head, "准备下发…");
    (st.mcus || []).forEach(function (m, i) { vyMcuNode(m, vyLastNode(st), i); });
    vyStage(st);
    (st.hops || []).forEach(function (h, i) {
      if (!VY.played[i]) return;
      vyPaintHop(h, h.result === "ok");
    });
    // 重绘后不能只靠「跳过的节点」着色：示例不兼容那种 fail 要走 amber，
    // 而 amber 只有 vySettleNodes 知道（要读 node.upstream）
    vySettleNodes(st);
    vySyncLogs(st);
  }

  // ---------------- 上次的结果：切会话 / 刷新 / 重启后自动回看 ----------------
  // 绿线、节点日志、结论都是上一次真跑出来的，原样端上来，不重放动画：
  // 重放会让人以为是刚刚又跑了一遍。
  function vyReviewBoot(st) {
    // 从接口翻出来的结果必然是「之前跑完的那一次」，标上来源，
    // 界面上才会写「上次运行 11-14 22:31」，不会被当成刚刚又跑了一遍
    st.restored = true;
    VY.on = true;
    VY.review = true;
    VY.sid = sid();
    VY.st = st;
    VY.queue = []; VY.playing = false; VY.halt = false; VY.played = {};
    VY.expand = {}; VY.logCache = {}; VY.layer = null;
    if (state.sel) closeDrawer();
    if (canvas) canvas.classList.add("vy-locked");
    ["gp-add", "gp-commit"].forEach(function (k) {
      var b = $(k);
      if (b) b.classList.add("locked");
    });
    vyBtn(true);
    var bar = $("vy-result");
    if (bar) bar.classList.add("hidden");
    vyClearMarks();
    vyClearExtras();
    VY.layer = null;
    var head = vyHeadId(st);
    if (head) vyInjector(head, "准备下发…");
    (st.mcus || []).forEach(function (m, i) { vyMcuNode(m, vyLastNode(st), i); });
    vyStage(st);
    (st.hops || []).forEach(function (h, i) {
      VY.played[i] = 1;
      vyPaintHop(h, h.result === "ok");
    });
    vySettleNodes(st);
    vySyncLogs(st);
    vyFinish(st);
    setTimeout(function () { if (VY.on) fit(2); }, 60);
  }

  var vyRestoring = false;
  function vyRestoreSaved() {
    if (VY.on || vyRestoring || !sid()) return;
    var want = sid();
    vyRestoring = true;
    api("/api/graph/verify/status?sid=" + encodeURIComponent(want))
      .then(function (r) { return r.json(); })
      .then(function (st) {
        vyRestoring = false;
        if (!st || VY.on || sid() !== want) return;
        // 只回看「已经跑完」的：running 是别的标签页在跑，别抢它的画面
        if (!st.finished || st.running) return;
        // 连一条线都没有的早退（图里没连线/没 Docker）不画，只留结论没意义
        if (!(st.hops || []).length && !st.build_failed && !st.env_missing) return;
        vyReviewBoot(st);
        say("这是上次的验证结果（" + (st.ok ? "通过" : "没通过") +
            "）· 点「退出验证」回到编辑", 8000);
      })
      .catch(function () { vyRestoring = false; });
  }

  window.RTGraph = { reload: function () { return reload(true); }, fit: fit,
                     verify: vyStart, verifyExit: vyExit };

  bind();
  vyBind();
  setTimeout(function () { reload(); }, 600);
})();
