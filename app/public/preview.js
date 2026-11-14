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

    var body = document.createElement("div");
    body.className = "pv-body";
    wrap.appendChild(body);

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

    var root = new THREE.Group();
    root.rotation.x = -Math.PI / 2;   // URDF 是 z 朝上，three 是 y 朝上
    scene.add(root);

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
    function draw() { updateCam(); renderer.render(scene, camera); }
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
      input.step = j.type === "prismatic" ? 0.001 : 0.01;
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

  function mount(slot, sid, pkg) {
    slot.innerHTML = '<div class="pv-loading">正在生成模型预览…</div>';
    fetchScene(sid, pkg).then(function (data) {
      if (!data || !data.ok) {
        slot.innerHTML = '<div class="pv-empty">无法预览：' +
          esc((data && data.error) || "未知原因") + "</div>";
        return;
      }
      loadThree().then(function (THREE) {
        try {
          var dispose = render(slot, data, THREE);
          LIVE.push(dispose);
          while (LIVE.length > MAX_LIVE) {
            var old = LIVE.shift();
            try { old(); } catch (e) { /* 忽略 */ }
          }
        } catch (e) {
          slot.innerHTML = '<div class="pv-empty">渲染失败：' + esc(e.message) + "</div>";
        }
      }).catch(function () {
        slot.innerHTML = '<div class="pv-empty">3D 组件加载失败（网络受限？）</div>';
      });
    }).catch(function (e) {
      slot.innerHTML = '<div class="pv-empty">预览获取失败：' + esc(e.message) + "</div>";
    });
  }

  window.RTPreview = { mount: mount };
})();
