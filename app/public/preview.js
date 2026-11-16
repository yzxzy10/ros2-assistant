/* 模型 3D 预览：把服务端解析好的关节树用 three.js 摆出来。
 *
 * 设计取舍
 * --------
 * 1. 不做 URDF 解析：xacro 表达式、material 间接引用、rpy→四元数都在服务端算完，
 *    前端只负责「按树摆位置」，这样两边不会各算一套对不上。
 * 2. 不引 OrbitControls：多一个 CDN 资源就多一个加载失败点，手写 40 行球面相机够了。
 * 3. 按需求渲染：拖视角、拉滑块、改尺寸才重绘，不跑常驻 RAF，
 *    否则一个长会话里堆十几个画布会一起烧 GPU。
 */
(function () {
  var THREE_P = null;
  var LIVE = [];          // 当前还占着 WebGL 上下文的预览
  var MAX_LIVE = 3;       // 超过就释放最早的

  function loadThree() {
    if (!THREE_P) THREE_P = import("three");
    return THREE_P;
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function fetchScene(sid, pkg) {
    var headers = {};
    var t = window.RTToken ? window.RTToken() : "";
    if (t) headers["X-Auth-Token"] = t;
    var u = "/api/model/" + encodeURIComponent(sid);
    if (pkg) u += "?pkg=" + encodeURIComponent(pkg);
    return fetch(u, {
      credentials: "same-origin", headers: headers
    }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function fmt(v, type) {
    if (type === "prismatic") return (v * 1000).toFixed(1) + " mm";
    return (v * 180 / Math.PI).toFixed(0) + "°";
  }

  // 二进制 STL → BufferGeometry。自己解析是为了不额外引 three/addons，
  // 让独立演示页也能离线跑。
  function stlGeometry(THREE, b64, scale) {
    var bin;
    try { bin = atob(b64); } catch (e) { return null; }
    var n = bin.length;
    if (n < 84) return null;
    var buf = new ArrayBuffer(n), u8 = new Uint8Array(buf);
    for (var i = 0; i < n; i++) u8[i] = bin.charCodeAt(i);
    var dv = new DataView(buf);
    var count = dv.getUint32(80, true);
    if (count === 0 || 84 + count * 50 > n) return null;      // ASCII STL 走线框兜底
    var pos = new Float32Array(count * 9);
    for (var t = 0; t < count; t++) {
      var o = 84 + t * 50 + 12;
      for (var k = 0; k < 9; k++) pos[t * 9 + k] = dv.getFloat32(o + k * 4, true);
    }
    var geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.computeVertexNormals();
    if (scale && (scale[0] !== 1 || scale[1] !== 1 || scale[2] !== 1)) {
      geo.scale(scale[0], scale[1], scale[2]);
    }
    return geo;
  }

  function render(slot, data, THREE) {
    slot.innerHTML = "";

    var wrap = document.createElement("div");
    wrap.className = "pv-wrap";

    var head = document.createElement("div");
    head.className = "pv-head";
    head.innerHTML = '<span class="pv-title">模型预览</span>' +
      '<span class="pv-src">' + esc(data.source || "") + "</span>";
    wrap.appendChild(head);

    var canvasBox = document.createElement("div");
    canvasBox.className = "pv-canvas";
    wrap.appendChild(canvasBox);

    // 工具栏：把「只能拖滑块」升级成能直接演示
    var tools = document.createElement("div");
    tools.className = "pv-tools";
    tools.innerHTML =
      '<span class="pv-tools-tip">关节</span>' +
      '<button type="button" data-act="home">零位</button>' +
      '<button type="button" data-act="demo">演示姿态</button>' +
      '<button type="button" data-act="axes">坐标轴</button>' +
      '<button type="button" data-act="spin">自动旋转</button>';
    wrap.appendChild(tools);

    var body = document.createElement("div");
    body.className = "pv-body";
    wrap.appendChild(body);

    // 位置读数：拖动关节时实时显示末端（或选中部件）在 URDF 坐标系下的位姿
    var readout = document.createElement("div");
    readout.className = "pv-readout";
    readout.innerHTML = '<div class="pv-ro-row"><span class="pv-ro-k">对象</span>' +
      '<span class="pv-ro-v" data-ro="who">—</span></div>' +
      '<div class="pv-ro-row"><span class="pv-ro-k">位置</span>' +
      '<span class="pv-ro-v" data-ro="pos">—</span></div>' +
      '<div class="pv-ro-row"><span class="pv-ro-k">姿态 rpy</span>' +
      '<span class="pv-ro-v" data-ro="rpy">—</span></div>';
    wrap.appendChild(readout);

    slot.appendChild(wrap);

    var W = canvasBox.clientWidth || 520;
    var H = 320;

    var scene = new THREE.Scene();
    var camera = new THREE.PerspectiveCamera(45, W / H, 0.001, 100);
    var renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(W, H);
    canvasBox.appendChild(renderer.domElement);

    scene.add(new THREE.HemisphereLight(0xffffff, 0x585858, 1.6));
    var dir = new THREE.DirectionalLight(0xffffff, 1.5);
    dir.position.set(1.2, 2.0, 1.6);
    scene.add(dir);

    // 地面网格（three 坐标：y 朝上，所以地面在 y=0 平面）
    var grid = new THREE.GridHelper(2, 20, 0xc3cad6, 0xe8ebf0);
    grid.visible = false;
    scene.add(grid);

    var root = new THREE.Group();
    root.rotation.x = -Math.PI / 2;   // URDF 是 z 朝上，three 是 y 朝上
    scene.add(root);

    // URDF 坐标轴：挂在 root 里，跟着 -90° 旋转，显示的就是 x/y/z（z 朝上）
    var axes = new THREE.AxesHelper(0.3);
    axes.visible = false;
    root.add(axes);

    var byName = {};
    (data.links || []).forEach(function (l) { byName[l.name] = l; });

    var meshList = [];
    function makeLink(link) {
      var g = new THREE.Group();
      (link.visuals || []).forEach(function (v) {
        var geo, mat, kind = (v.geometry || {}).kind;
        if (kind === "cylinder") {
          geo = new THREE.CylinderGeometry(v.geometry.radius, v.geometry.radius,
                                           v.geometry.length, 28);
          geo.rotateX(Math.PI / 2);       // three 的圆柱沿 y，URDF 沿 z
        } else if (kind === "box") {
          geo = new THREE.BoxGeometry(v.geometry.size[0], v.geometry.size[1],
                                      v.geometry.size[2]);
        } else if (kind === "sphere") {
          geo = new THREE.SphereGeometry(v.geometry.radius, 22, 16);
        } else if (kind === "mesh" && v.geometry.data) {
          geo = stlGeometry(THREE, v.geometry.data, v.geometry.scale);
          if (!geo) {
            geo = new THREE.BoxGeometry(0.04, 0.04, 0.04);
            mat = new THREE.MeshBasicMaterial({ color: 0x9aa0a6, wireframe: true });
          }
        } else {
          geo = new THREE.BoxGeometry(0.03, 0.03, 0.03);
          mat = new THREE.MeshBasicMaterial({ color: 0x9aa0a6, wireframe: true });
        }
        if (!mat) {
          mat = new THREE.MeshStandardMaterial({
            color: new THREE.Color(v.color || "#8a8f98"),
            roughness: 0.6, metalness: 0.15
          });
        }
        var mesh = new THREE.Mesh(geo, mat);
        mesh.position.set(v.xyz[0], v.xyz[1], v.xyz[2]);
        mesh.rotation.set(v.rpy[0], v.rpy[1], v.rpy[2]);
        mesh.userData.link = link.name;
        meshList.push(mesh);
        g.add(mesh);
      });
      return g;
    }

    var jointItems = [];
    var jointGroups = {};
    (data.joints || []).forEach(function (j) {
      var g = new THREE.Group();
      g.position.set(j.xyz[0], j.xyz[1], j.xyz[2]);
      g.quaternion.setFromEuler(new THREE.Euler(j.rpy[0], j.rpy[1], j.rpy[2], "XYZ"));
      j._basePos = g.position.clone();
      j._baseQuat = g.quaternion.clone();
      j._axis = new THREE.Vector3(j.axis[0], j.axis[1], j.axis[2]).normalize();
      if (!isFinite(j._axis.length()) || j._axis.length() < 1e-9) {
        j._axis = new THREE.Vector3(1, 0, 0);
      }
      j.value = Number(j.value || 0);
      jointGroups[j.name] = g;
      jointItems.push({ j: j, g: g });
    });

    var seen = {};
    var linkNodes = {};
    function attach(linkName, parentObj) {
      if (!linkName || seen[linkName]) return;
      seen[linkName] = true;
      var lo = makeLink(byName[linkName] || { visuals: [] });
      linkNodes[linkName] = lo;
      parentObj.add(lo);
      (data.joints || []).forEach(function (j) {
        if (j.parent === linkName && jointGroups[j.name]) {
          var jg = jointGroups[j.name];
          lo.add(jg);
          attach(j.child, jg);
        }
      });
    }
    attach(data.root, root);

    function applyPose() {
      jointItems.forEach(function (it) {
        var j = it.j, g = it.g;
        if (j.type === "revolute" || j.type === "continuous") {
          var q = new THREE.Quaternion().setFromAxisAngle(j._axis, j.value);
          g.quaternion.copy(j._baseQuat).multiply(q);
          g.position.copy(j._basePos);
        } else if (j.type === "prismatic") {
          var d = j._axis.clone().multiplyScalar(j.value).applyQuaternion(j._baseQuat);
          g.position.copy(j._basePos).add(d);
        }
      });
    }
    applyPose();

    // ---- 关节联动高亮：点模型或点关节名，点亮该关节驱动的整条子树 ----
    var childMap = {};
    (data.joints || []).forEach(function (j) {
      (childMap[j.parent] = childMap[j.parent] || []).push({ link: j.child, joint: j.name });
    });

    function subtreeOf(linkName) {
      var out = {}, stack = [linkName];
      while (stack.length) {
        var cur = stack.pop();
        if (!cur || out[cur]) continue;
        out[cur] = true;
        (childMap[cur] || []).forEach(function (n) { stack.push(n.link); });
      }
      return out;
    }

    var sliderRows = {};
    function applyHighlight(linkName) {
      selLink = linkName || "";
      var keep = linkName ? subtreeOf(linkName) : null;
      meshList.forEach(function (m) {
        var on = !keep || keep[m.userData.link];
        var mat = m.material;
        if (mat.emissive) {
          mat.emissive.setHex(keep && on ? 0x7c3aed : 0x000000);
          mat.emissiveIntensity = keep && on ? 0.32 : 0;
        } else if (mat.wireframe) {
          mat.color.setHex(keep && on ? 0x7c3aed : 0x9aa0a6);
        }
        // three.js 里 transparent 参与着色器编译键：只改布尔不置 needsUpdate，
        // 材质不会真的变透明（实测就是「点了没反应」）。
        var want = !on;
        if (mat.transparent !== want) {
          mat.transparent = want;
          mat.needsUpdate = true;
        }
        mat.opacity = on ? 1 : 0.18;
      });
      // 驱动这条子树的关节行也点亮
      Object.keys(sliderRows).forEach(function (name) {
        var row = sliderRows[name];
        var on = keep && keep[row.childLink];
        row.el.classList.toggle("pv-joint-active", !!on);
      });
      draw();
    }

    // 自动取景
    var box = new THREE.Box3().setFromObject(root);
    var size = new THREE.Vector3(), center = new THREE.Vector3();
    box.getSize(size); box.getCenter(center);
    // 用包围球半径 + 垂直视角算距离：比「最大边长 × 系数」稳，
    // 细长物体（机械臂）不会被拉太远，扁平物体（底盘）也不会溢出画面。
    var sphere = box.getBoundingSphere(new THREE.Sphere());
    var radius = Math.max(sphere.radius, 0.05);
    // 默认取「侧前方 3/4 视角」：俯角太大时车体会把轮子完全挡住，
    // 而轮子和转向正是要展示的部分。
    var az = 1.05, el = 0.24, dist = radius / Math.sin(camera.fov * Math.PI / 360);

    function placeCam(d) {
      camera.position.set(
        center.x + d * Math.cos(el) * Math.cos(az),
        center.y + d * Math.sin(el),
        center.z + d * Math.cos(el) * Math.sin(az));
      camera.up.set(0, 1, 0);
      camera.lookAt(center);
      camera.updateMatrixWorld();
    }

    // 按「当前视角下的投影范围」取景，而不是只看包围球：
    // 扁平物体（底盘）在低视角下投影很小，只按球算会显得模型特别小；
    // 细长物体（机械臂）又会被拉太远。用 8 个角点投影迭代收敛，两种都合适。
    var corners = [];
    [box.min.x, box.max.x].forEach(function (x) {
      [box.min.y, box.max.y].forEach(function (y) {
        [box.min.z, box.max.z].forEach(function (z) {
          corners.push(new THREE.Vector3(x, y, z));
        });
      });
    });
    (function fitView() {
      var d = radius / Math.sin(camera.fov * Math.PI / 360) * 1.4;
      var inv = new THREE.Matrix4();
      for (var i = 0; i < 4; i++) {
        placeCam(d);
        inv.copy(camera.matrixWorld).invert();
        var half = 0;
        corners.forEach(function (p) {
          var v = p.clone().applyMatrix4(inv);
          half = Math.max(half, Math.abs(v.y), Math.abs(v.x) / camera.aspect);
        });
        var need = half / Math.tan(camera.fov * Math.PI / 360) * 1.18;
        d = (d + need) / 2;
      }
      dist = d;
      placeCam(d);
    })();

    function updateCam() { placeCam(dist); }
    // 浏览器表单恢复、「手滑」之外的任何改动，只要 DOM 与数据不一致就按数据回写，
    // 保证界面上看到的永远是真实关节值
    function syncSliders() {
      sliders.forEach(function (it) {
        var want = String(it.j.value);
        if (it.input.value === want) return;
        it.input.value = it.j.value;
        it.val.textContent = fmt(it.j.value, it.j.type);
        applyPose();
      });
    }

    function draw() {
      updateCam();
      renderer.render(scene, camera);
      if (roReady) {
        syncSliders();
        updateReadout();      // 每次重绘同步刷新位置读数
      }
    }
    draw();

    // 拖拽旋转 / 滚轮缩放
    var dragging = false, lastX = 0, lastY = 0;
    var cv = renderer.domElement;
    cv.style.cursor = "grab";
    cv.addEventListener("pointerdown", function (e) {
      dragging = true; lastX = e.clientX; lastY = e.clientY;
      cv.style.cursor = "grabbing";
      if (cv.setPointerCapture) cv.setPointerCapture(e.pointerId);
    });
    cv.addEventListener("pointermove", function (e) {
      if (!dragging) return;
      az -= (e.clientX - lastX) * 0.01;
      el = Math.max(-1.4, Math.min(1.4, el + (e.clientY - lastY) * 0.008));
      lastX = e.clientX; lastY = e.clientY;
      draw();
    });
    cv.addEventListener("pointerup", function () {
      dragging = false; cv.style.cursor = "grab";
    });
    cv.addEventListener("wheel", function (e) {
      e.preventDefault();
      dist = Math.max(radius * 0.8, Math.min(radius * 12, dist * (1 + e.deltaY * 0.001)));
      draw();
    }, { passive: false });

    var raycaster = new THREE.Raycaster();
    var ptr = new THREE.Vector2();
    var downAt = { x: 0, y: 0, moved: false };
    cv.addEventListener("pointerdown", function (e) {
      downAt = { x: e.clientX, y: e.clientY, moved: downAt.moved };
    });
    cv.addEventListener("pointermove", function (e) {
      if (Math.abs(e.clientX - downAt.x) + Math.abs(e.clientY - downAt.y) > 6) {
        downAt.moved = true;
      }
    });
    cv.addEventListener("click", function (e) {
      if (downAt.moved) { downAt.moved = false; return; }   // 拖视角不算点击
      var r = cv.getBoundingClientRect();
      ptr.x = ((e.clientX - r.left) / r.width) * 2 - 1;
      ptr.y = -((e.clientY - r.top) / r.height) * 2 + 1;
      raycaster.setFromCamera(ptr, camera);
      var hits = raycaster.intersectObjects(meshList, false);
      if (hits.length) applyHighlight(hits[0].object.userData.link);
      else applyHighlight(null);
    });

    // 关节滑块
    var movable = (data.joints || []).filter(function (j) {
      return (j.type === "revolute" || j.type === "prismatic" || j.type === "continuous")
        && Array.isArray(j.limit);
    });
    if (movable.length) {
      var tip = document.createElement("div");
      tip.className = "pv-tip";
      tip.textContent = "拖滑块看关节运动 · 点模型或关节名高亮该关节链 · 拖画面转视角 · 滚轮缩放";
      body.appendChild(tip);
    }
    // 构建顺序很重要：先把元素放进 DOM，再设范围与取值，最后才绑 oninput。
    // 反过来做的话，元素插入瞬间如果被浏览器还原/校正过取值，会先触发一次
    // input 事件，把 j.value 改掉——表现就是「一打开模型就是歪的」。
    var sliders = [];
    movable.slice(0, 8).forEach(function (j) {
      var row = document.createElement("div");
      row.className = "pv-joint";
      var label = document.createElement("span");
      label.className = "pv-jname";
      label.textContent = j.name;
      var input = document.createElement("input");
      input.type = "range";
      var val = document.createElement("span");
      val.className = "pv-jval";
      row.appendChild(label);
      row.appendChild(input);
      row.appendChild(val);
      body.appendChild(row);

      input.min = j.limit[0];
      input.max = j.limit[1];
      // 有的关节零位不在行程内（Panda 的 joint4 是 [-176°, -4°]），
      // 直接设 0 会被浏览器夹到边界，看起来像「初值错了」——取行程中点更合理
      if (j.value < j.limit[0] || j.value > j.limit[1]) {
        j.value = (j.limit[0] + j.limit[1]) / 2;
      }
      // step 不能用固定值：min 是 -3.1416，网格起点不在 0 上，
      // 结果是「零位」被吸附成 -0.0016 rad（-0.09°），滑块永远回不到正零。
      input.step = "any";
      input.autocomplete = "off";   // 浏览器会「恢复」上次的表单值，关节初值会被污染
      input.dataset.round = j.type === "prismatic" ? "0.0005" : "0.001";
      input.value = j.value;
      val.textContent = fmt(j.value, j.type);

      label.title = "点击高亮这条关节链";
      label.style.cursor = "pointer";
      label.onclick = function () {
        applyHighlight(j.child);
        if (sliderRows[j.name]) {
          sliderRows[j.name].el.classList.add("pv-joint-active");
        }
      };
      sliderRows[j.name] = { childLink: j.child, el: row };

      // 只有用户真的按下过这个滑块（或键盘操作过）才认它的 input 事件。
      // 浏览器还原表单、滚动误触、自动聚焦都会触发 input，但不会先有 pointerdown，
      // 用这个判据比看 activeElement 可靠 —— 后者会被自动聚焦骗过去。
      var armed = false;
      input.addEventListener("pointerdown", function () { armed = true; });
      input.addEventListener("keydown", function () { armed = true; });

      input.oninput = function () {
        var v = parseFloat(input.value);
        if (!isFinite(v)) return;
        var r = parseFloat(input.dataset.round || "0");
        if (r > 0) v = Math.round(v / r) * r;   // 收一下浮点毛刺，读数好看些
        if (!armed) {
          input.value = j.value;
          val.textContent = fmt(j.value, j.type);
          return;
        }
        j.value = v;
        val.textContent = fmt(j.value, j.type);
        applyPose();
        draw();
      };
      sliders.push({ input: input, val: val, j: j });
    });

    function resetSliders() {
      sliders.forEach(function (it) {
        it.input.value = it.j.value;
        it.val.textContent = fmt(it.j.value, it.j.type);
      });
      applyPose();
      draw();
    }
    // 兜底：若插入后仍被外部改过取值，下一帧按数据源复位
    // ---------- 位置读数：用世界矩阵做正运动学 ----------
    var roReady = false;
    var rootInv = new THREE.Matrix4();
    var selLink = "";

    // 末端：优先 tool0，否则沿关节链取最深的那片叶子
    var endName = (function () {
      if (linkNodes.tool0) return "tool0";
      // 车上没有末端，读车体位姿更有意义；否则会显示某个轮子的坐标
      if (linkNodes.base_link) return "base_link";
      var best = data.root, bestD = -1, stack = [[data.root, 0]];
      while (stack.length) {
        var cur = stack.pop(), d = cur[1];
        if (d > bestD) { bestD = d; best = cur[0]; }
        (childMap[cur[0]] || []).forEach(function (n) { stack.push([n.link, d + 1]); });
      }
      return best;
    })();

    function nodePose(name) {
      var n = linkNodes[name];
      if (!n) return null;
      root.updateMatrixWorld(true);
      rootInv.copy(root.matrixWorld).invert();
      var m = new THREE.Matrix4().multiplyMatrices(rootInv, n.matrixWorld);
      var p = new THREE.Vector3().setFromMatrixPosition(m);
      var q = new THREE.Quaternion().setFromRotationMatrix(m);
      var e = new THREE.Euler().setFromQuaternion(q, "ZYX");   // URDF 的 rpy = 固定轴 XYZ
      return { p: p, e: e };
    }

    function mm(v) { return (v * 1000).toFixed(1); }
    function dg(v) { return (v * 180 / Math.PI).toFixed(1); }

    function updateReadout() {
      var tgt = selLink || endName;
      var ps = nodePose(tgt);
      var who = readout.querySelector('[data-ro="who"]');
      var posEl = readout.querySelector('[data-ro="pos"]');
      var rpyEl = readout.querySelector('[data-ro="rpy"]');
      if (!ps) {
        who.textContent = tgt || "—";
        posEl.textContent = "—";
        rpyEl.textContent = "—";
        return;
      }
      who.textContent = tgt;
      posEl.textContent = "x " + mm(ps.p.x) + "  y " + mm(ps.p.y) + "  z " + mm(ps.p.z) + " mm";
      rpyEl.textContent = "r " + dg(ps.e.x) + "°  p " + dg(ps.e.y) + "°  y " + dg(ps.e.z) + "°";
    }
    roReady = true;

    // ---------- 演示方式：零位 / 演示姿态 / 坐标轴 / 自动旋转 ----------
    var spinning = false;
    function setJointValues(vals) {
      sliders.forEach(function (it, i) {
        var v = vals[i];
        if (v == null) return;
        var lim = it.j.limit || [-3.1416, 3.1416];
        v = Math.max(lim[0], Math.min(lim[1], v));
        it.j.value = v;
        it.input.value = v;
        it.val.textContent = fmt(v, it.j.type);
      });
      applyPose();
      draw();
    }

    function demoPose() {
      // 机械臂给一个能看出各段连杆的姿态；车给一点转向。
      // 按滑块数量填满：多出来的关节（比如夹爪两指）按类型给一个合理值，
      // 否则会出现「只合了一根手指」这种半截状态。
      if (linkNodes.tool0) {
        var arm = [0.42, -0.55, 1.15, -0.35, 0.85, 0.25];
        return sliders.map(function (it, i) {
          if (i < arm.length) return arm[i];
          return it.j.type === "prismatic" ? 0.015 : 0;   // 两指各合一半
        });
      }
      return sliders.map(function (it) {
        return it.j.name.indexOf("steer") >= 0 ? 0.35 : 0;
      });
    }

    function spinStep() {
      if (!spinning) return;
      az += 0.008;
      draw();
      requestAnimationFrame(spinStep);
    }

    tools.addEventListener("click", function (ev) {
      var b = ev.target.closest ? ev.target.closest("button") : null;
      if (!b) return;
      var act = b.getAttribute("data-act");
      if (act === "home") {
        setJointValues(sliders.map(function () { return 0; }));
      } else if (act === "demo") {
        setJointValues(demoPose());
      } else if (act === "axes") {
        axes.visible = !axes.visible;
        grid.visible = axes.visible;
        b.classList.toggle("on", axes.visible);
        draw();
      } else if (act === "spin") {
        spinning = !spinning;
        b.classList.toggle("on", spinning);
        if (spinning) spinStep();
      }
    });

    if (window.requestAnimationFrame) requestAnimationFrame(resetSliders);
    setTimeout(resetSliders, 260);

    if ((data.warnings || []).length) {
      var warn = document.createElement("div");
      warn.className = "pv-warn";
      warn.textContent = "⚠ " + data.warnings.join("；");
      body.appendChild(warn);
    }

    // 释放：一个会话里可能生成很多次，WebGL 上下文有上限（Chrome 约 16 个），
    // 不主动释放的话后面的预览会直接渲染失败，整个页面也会卡。
    function dispose() {
      try {
        if (ro) ro.disconnect();
        renderer.dispose();
        if (renderer.forceContextLoss) renderer.forceContextLoss();
      } catch (e) { /* 已经丢了就算了 */ }
      slot.innerHTML = '<div class="pv-empty">（较早的预览已释放显存，需要时重新生成一次即可）</div>';
    }

    // 容器宽度变了（窗口缩放 / 面板拖动）要重设画布
    var ro = null;
    if (window.ResizeObserver) {
      ro = new ResizeObserver(function () {
        var w = canvasBox.clientWidth;
        if (!w || Math.abs(w - W) < 2) return;
        W = w;
        camera.aspect = W / H;
        camera.updateProjectionMatrix();
        renderer.setSize(W, H);
        draw();
      });
      ro.observe(canvasBox);
    }
    return dispose;
  }

  // 失败时留一个重试按钮：网络抽风或显存被释放后，用户点一下就能回来，
  // 而不是只看到一句「无法展示」然后只能重新生成
  function fail(slot, msg, sid, pkg) {
    slot.innerHTML = '<div class="pv-empty">' + msg +
      ' <button type="button" class="pv-retry">重试</button></div>';
    var b = slot.querySelector(".pv-retry");
    if (b) b.onclick = function () { mount(slot, sid, pkg); };
  }

  function mount(slot, sid, pkg) {
    slot.innerHTML = '<div class="pv-loading">正在生成模型预览…</div>';
    fetchScene(sid, pkg).then(function (data) {
      if (!data || !data.ok) {
        fail(slot, "无法预览：" + esc((data && data.error) || "未知原因"), sid, pkg);
        return;
      }
      loadThree().then(function (THREE) {
        try {
          var dispose = render(slot, data, THREE);
          var entry = function () {
            dispose();
            slot.innerHTML = '<div class="pv-lazy">较早的预览已释放显存' +
              ' <button type="button" class="pv-retry">重新加载</button></div>';
            var b = slot.querySelector(".pv-retry");
            if (b) b.onclick = function () { mount(slot, sid, pkg); };
          };
          LIVE.push(entry);
          while (LIVE.length > MAX_LIVE) {
            var old = LIVE.shift();
            try { old(); } catch (e) { /* 忽略 */ }
          }
        } catch (e) {
          fail(slot, "渲染失败：" + esc(e.message), sid, pkg);
        }
      }).catch(function () {
        fail(slot, "3D 组件加载失败（网络受限？）", sid, pkg);
      });
    }).catch(function (e) {
      fail(slot, "预览获取失败：" + esc(e && e.message), sid, pkg);
    });
  }

  // 历史回放用：滚到附近才真正挂载，避免一进页面就建一堆 WebGL 上下文
  function mountLazy(slot, sid, pkg) {
    slot.innerHTML = '<div class="pv-lazy">滚动到这里加载三维预览…</div>';
    if (!window.IntersectionObserver) { mount(slot, sid, pkg); return; }
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) {
        if (e.isIntersecting) { io.disconnect(); mount(slot, sid, pkg); }
      });
    }, { rootMargin: "300px" });
    io.observe(slot);
  }

  window.RTPreview = { mount: mount, mountLazy: mountLazy };
})();
