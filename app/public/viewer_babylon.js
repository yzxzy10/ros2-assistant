/* viewer_babylon.js —— 基于 Babylon.js 的模型查看器
 *
 * 为什么换引擎
 * ------------
 * 之前 three.js 是手搭的：两盏灯 + MeshStandardMaterial，没有环境光照、没有阴影、
 * 没有色调映射，CAD 件出来像塑料块。Babylon 的 PBR 管线直接给了这些能力：
 *   - HDR 预过滤环境贴图（IBL）：金属/塑料有真实反射，不靠补光硬凑
 *   - 指数模糊软阴影：有接触阴影，零件才知道「落在哪」
 *   - ACES 色调映射：高光不死白
 *   - MSAA + 设备像素比上限：细杆、薄板边缘不锯齿
 *
 * 坐标系处理（关键）
 * -----------------
 * URDF 用右手系 z 朝上，Babylon 默认左手系 y 朝上。Babylon 的 Matrix.RotationX 等
 * 函数按左手约定生成，直接把 URDF 的 rpy 塞进去会翻符号。
 * 所以这里所有局部变换都用「列向量约定」自己算（与 URDF 一致），再转置成 Babylon 的
 * 行向量矩阵。这样旋转方向与世界坐标都不需要猜，也不会出现镜像。
 *
 * 对外接口与 preview.js 完全一致（window.RTPreview.mount / mountLazy）。
 */
(function () {
  "use strict";

  var BABYLON_P = null;
  var ENV_TEX = null;
  var LIVE = [];
  var MAX_LIVE = 3;

  var CDN_CORE = "https://cdn.babylonjs.com/babylon.js";
  // 预过滤过的环境贴图：IBL 要按 roughness 分级采样，普通立方体贴图做不出正确反射
  var ENV_URL = "https://assets.babylonjs.com/environments/environmentSpecular.env";
  var ENV_WAIT = 8000;

  // ---------- 基础 ----------

  function loadBabylon() {
    if (window.BABYLON) return Promise.resolve(window.BABYLON);
    if (BABYLON_P) return BABYLON_P;
    BABYLON_P = new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = CDN_CORE;
      s.async = true;
      s.onload = function () {
        if (window.BABYLON) resolve(window.BABYLON);
        else reject(new Error("Babylon 加载后没有全局对象"));
      };
      s.onerror = function () { reject(new Error("Babylon 脚本加载失败")); };
      document.head.appendChild(s);
    });
    return BABYLON_P;
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
    return fetch(u, { credentials: "same-origin", headers: headers })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      });
  }

  function fmt(v, type) {
    if (type === "prismatic") return (v * 1000).toFixed(1) + " mm";
    return (v * 180 / Math.PI).toFixed(0) + "\u00b0";
  }

  // ---------- 4x4 矩阵（列向量约定，与 URDF / OpenGL 一致） ----------

  function mI() {
    return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
  }

  // 列主序：m[c * 4 + r]
  function mMul(a, b) {
    var o = new Array(16);
    for (var c = 0; c < 4; c++) {
      for (var r = 0; r < 4; r++) {
        o[c * 4 + r] = a[0 * 4 + r] * b[c * 4 + 0] + a[1 * 4 + r] * b[c * 4 + 1] +
          a[2 * 4 + r] * b[c * 4 + 2] + a[3 * 4 + r] * b[c * 4 + 3];
      }
    }
    return o;
  }

  function mT(x, y, z) {
    var m = mI();
    m[12] = x; m[13] = y; m[14] = z;
    return m;
  }

  function mRx(a) {
    var c = Math.cos(a), s = Math.sin(a), m = mI();
    m[5] = c; m[6] = s; m[9] = -s; m[10] = c;
    return m;
  }

  function mRy(a) {
    var c = Math.cos(a), s = Math.sin(a), m = mI();
    m[0] = c; m[2] = -s; m[8] = s; m[10] = c;
    return m;
  }

  function mRz(a) {
    var c = Math.cos(a), s = Math.sin(a), m = mI();
    m[0] = c; m[1] = s; m[4] = -s; m[5] = c;
    return m;
  }

  // 绕任意单位轴旋转（Rodrigues）
  function mAxis(ax, ang) {
    var x = ax[0], y = ax[1], z = ax[2];
    var n = Math.sqrt(x * x + y * y + z * z) || 1;
    x /= n; y /= n; z /= n;
    var c = Math.cos(ang), s = Math.sin(ang), C = 1 - c;
    var m = mI();
    m[0] = x * x * C + c;     m[1] = y * x * C + z * s; m[2] = z * x * C - y * s;
    m[4] = x * y * C - z * s; m[5] = y * y * C + c;     m[6] = z * y * C + x * s;
    m[8] = x * z * C + y * s; m[9] = y * z * C - x * s; m[10] = z * z * C + c;
    return m;
  }

  // URDF 的 rpy = 固定轴 X→Y→Z，即 R = Rz * Ry * Rx
  function mRpy(rpy) {
    return mMul(mRz(rpy[2] || 0), mMul(mRy(rpy[1] || 0), mRx(rpy[0] || 0)));
  }

  /* 列向量（URDF）矩阵 -> Babylon 矩阵：直接对应，不做转置
   *
   * Babylon 文档写的是「行向量 + 行主序」，但它的 Matrix 内存布局与列主序一致：
   *   RotationX 写 m[5]=c, m[6]=s, m[9]=-s, m[10]=c；Translation 写 m[12..14]，
   * 与标准列主序 4x4 完全相同，TransformCoordinates 也是 v * M 展开成同样结果。
   * 所以原样喂进去即可。之前多做一次转置，等于把旋转取逆：
   * 包围盒实测 y = -82..33（把 URDF 的 z 映射成了 -y），模型上下颠倒。
   */
  function toBMatrix(B, m) {
    return B.Matrix.FromArray(m);
  }

  function applyLocal(B, node, m) {
    var scale = new B.Vector3(), quat = new B.Quaternion(), pos = new B.Vector3();
    toBMatrix(B, m).decompose(scale, quat, pos);
    node.position = pos;
    node.rotationQuaternion = quat;
    node.scaling = new B.Vector3(1, 1, 1);
  }

  // 四元数 -> URDF rpy（R = Rz*Ry*Rx）
  function quatToRpy(q) {
    var x = q.x, y = q.y, z = q.z, w = q.w;
    var r = Math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y));
    var s = 2 * (w * y - z * x);
    s = Math.max(-1, Math.min(1, s));
    var p = Math.asin(s);
    var yw = Math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z));
    return [r, p, yw];
  }

  // ---------- STL ----------

  /* 二进制 STL -> {positions, indices, normals}
   * 逐面复制顶点：CAD 件的棱角必须保持硬边，共享顶点做平滑法线会把倒角抹平。 */
  function stlVertexData(b64, scale) {
    var bin;
    try { bin = atob(b64); } catch (e) { return null; }
    var n = bin.length;
    if (n < 84) return null;
    var u8 = new Uint8Array(n);
    for (var i = 0; i < n; i++) u8[i] = bin.charCodeAt(i);
    var dv = new DataView(u8.buffer);
    var count = dv.getUint32(80, true);
    if (!count || 84 + count * 50 > n) return null;

    var pos = new Float32Array(count * 9);
    var nor = new Float32Array(count * 9);
    var idx = new Uint32Array(count * 3);
    var sx = scale ? scale[0] : 1, sy = scale ? scale[1] : 1, sz = scale ? scale[2] : 1;

    for (var t = 0; t < count; t++) {
      var o = 84 + t * 50;
      var nx = dv.getFloat32(o, true), ny = dv.getFloat32(o + 4, true), nz = dv.getFloat32(o + 8, true);
      var len = Math.sqrt(nx * nx + ny * ny + nz * nz) || 1;
      nx /= len; ny /= len; nz /= len;
      for (var k = 0; k < 9; k++) {
        var v = dv.getFloat32(o + 12 + k * 4, true);
        var axis = k % 3;
        pos[t * 9 + k] = axis === 0 ? v * sx : (axis === 1 ? v * sy : v * sz);
        nor[t * 9 + k] = axis === 0 ? nx : (axis === 1 ? ny : nz);
      }
      idx[t * 3] = t * 3;
      idx[t * 3 + 1] = t * 3 + 1;
      idx[t * 3 + 2] = t * 3 + 2;
    }
    return { positions: pos, indices: idx, normals: nor };
  }

  // ---------- 渲染 ----------

  function render(slot, data, B) {
    slot.innerHTML = "";

    var wrap = document.createElement("div");
    wrap.className = "pv-wrap";

    var head = document.createElement("div");
    head.className = "pv-head";
    head.innerHTML = '<span class="pv-title">模型预览</span>' +
      '<span class="pv-src">Babylon \u00b7 PBR</span>';
    wrap.appendChild(head);

    var canvasBox = document.createElement("div");
    canvasBox.className = "pv-canvas";
    wrap.appendChild(canvasBox);

    var tools = document.createElement("div");
    tools.className = "pv-tools";
    tools.innerHTML =
      '<span class="pv-tools-tip">关节</span>' +
      '<button type="button" data-act="home">\u96f6\u4f4d</button>' +
      '<button type="button" data-act="demo">\u6f14\u793a\u59ff\u6001</button>' +
      '<button type="button" data-act="axes">\u5750\u6807\u8f74</button>' +
      '<button type="button" data-act="spin">\u81ea\u52a8\u65cb\u8f6c</button>';
    wrap.appendChild(tools);

    var body = document.createElement("div");
    body.className = "pv-body";
    wrap.appendChild(body);

    var readout = document.createElement("div");
    readout.className = "pv-readout";
    readout.innerHTML = '<div class="pv-ro-row"><span class="pv-ro-k">\u5bf9\u8c61</span>' +
      '<span class="pv-ro-v" data-ro="who">\u2014</span></div>' +
      '<div class="pv-ro-row"><span class="pv-ro-k">\u4f4d\u7f6e</span>' +
      '<span class="pv-ro-v" data-ro="pos">\u2014</span></div>' +
      '<div class="pv-ro-row"><span class="pv-ro-k">\u59ff\u6001 rpy</span>' +
      '<span class="pv-ro-v" data-ro="rpy">\u2014</span></div>';
    wrap.appendChild(readout);

    slot.appendChild(wrap);

    var W = canvasBox.clientWidth || 520;
    var H = Math.max(240, Math.min(620, data.canvas_height || 320));
    canvasBox.style.height = H + "px";

    var canvas = document.createElement("canvas");
    canvas.style.width = "100%";
    canvas.style.height = H + "px";
    canvas.style.outline = "none";
    canvas.style.touchAction = "none";
    canvasBox.appendChild(canvas);

    // ---------- 引擎与场景 ----------

    var engine = new B.Engine(canvas, true, {
      preserveDrawingBuffer: true, stencil: true, alpha: true, antialias: true
    }, false);
    engine.setHardwareScalingLevel(1 / Math.min(window.devicePixelRatio || 1, 2));

    var scene = new B.Scene(engine);
    scene.clearColor = new B.Color4(0, 0, 0, 0);      // 透明背景，交给 CSS 舞台
    scene.useRightHandedSystem = true;                // 与 URDF 一致，拾取/法线不出怪问题

    // ---------- 环境光与灯 ----------

    if (ENV_TEX) {
      scene.environmentTexture = ENV_TEX;
    } else {
      var tex = new B.CubeTexture(ENV_URL, scene, null, false);
      tex.onLoadObservable.addOnce(function () {
        if (scene.isDisposed) return;
        scene.environmentTexture = tex;
        ENV_TEX = tex;
      });
      // 拿不到就降级：没有 IBL 也不会黑，只是反射弱一些
      setTimeout(function () {
        if (!scene.isDisposed && !scene.environmentTexture) {
          scene.environmentTexture = null;
        }
      }, ENV_WAIT);
    }
    scene.environmentIntensity = 0.55;

    // ACES 色调映射：CAD 件多是接近纯白的材质，线性输出会糊成一片死白，
    // 走电影级曲线后高光有滚降、板件之间的明暗关系才出来
    var ip = scene.imageProcessingConfiguration;
    ip.toneMappingEnabled = true;
    ip.toneMappingType = B.ImageProcessingConfiguration.TONEMAPPING_ACES;
    ip.exposure = 1.0;
    ip.contrast = 1.25;

    var hemi = new B.HemisphericLight("hemi", new B.Vector3(0.15, 1, 0.25), scene);
    hemi.intensity = 0.38;
    hemi.groundColor = new B.Color3(0.18, 0.20, 0.24);

    var dir = new B.DirectionalLight("dir", new B.Vector3(-0.5, -1.0, -0.42), scene);
    dir.intensity = 3.0;
    dir.diffuse = new B.Color3(1.0, 0.99, 0.97);

    var shadowGen = new B.ShadowGenerator(2048, dir);
    shadowGen.useBlurExponentialShadowMap = true;
    shadowGen.blurKernel = 48;
    shadowGen.darkness = 0.55;

    // ---------- 模型 ----------

    var root = new B.TransformNode("root", scene);
    // URDF z 朝上 -> Babylon y 朝上：p' = (x, z, -y)
    applyLocal(B, root, mRx(-Math.PI / 2));

    var linkNodes = {};
    var childMap = {};
    var pickables = [];
    var linkMats = {};

    function matFor(linkName, color) {
      if (linkMats[linkName]) return linkMats[linkName];
      var m = new B.PBRMaterial("mat_" + linkName, scene);
      m.albedoColor = B.Color3.FromHexString(color || "#8a8f98");
      m.metallic = 0.12;
      m.roughness = 0.36;
      m.backFaceCulling = false;        // CAD 导出的面朝向不一定统一
      m.twoSidedLighting = true;
      linkMats[linkName] = m;
      return m;
    }

    (data.links || []).forEach(function (l) {
      var node = new B.TransformNode("link_" + l.name, scene);
      node.parent = root;
      linkNodes[l.name] = node;

      (l.visuals || []).forEach(function (v, vi) {
        var g = v.geometry || {};

        // 每个 visual 一个 holder，专门承载 URDF 的 origin（位置 + rpy），
        // 几何自身的轴向修正放在 holder 里面，两者不互相干扰
        var holder = new B.TransformNode("vis_" + l.name + "_" + vi, scene);
        holder.parent = node;
        var extra = (g.kind === "cylinder") ? mRx(Math.PI / 2) : mI();   // 圆柱轴 y -> z
        applyLocal(B, holder, mMul(mMul(mT(v.xyz[0], v.xyz[1], v.xyz[2]),
          mRpy(v.rpy || [0, 0, 0])), extra));

        var mesh = null;

        if (g.kind === "mesh" && g.data) {
          var vd = stlVertexData(g.data, g.scale);
          if (vd) {
            mesh = new B.Mesh("mesh_" + l.name + "_" + vi, scene);
            var vdata = new B.VertexData();
            vdata.positions = vd.positions;
            vdata.indices = vd.indices;
            vdata.normals = vd.normals;
            vdata.applyToMesh(mesh, false);
          }
        }

        if (!mesh) {
          if (g.kind === "box" && g.size) {
            mesh = B.MeshBuilder.CreateBox("mesh_" + l.name + "_" + vi,
              { width: g.size[0], height: g.size[1], depth: g.size[2] }, scene);
          } else if (g.kind === "sphere" && g.radius) {
            mesh = B.MeshBuilder.CreateSphere("mesh_" + l.name + "_" + vi,
              { diameter: g.radius * 2, segments: 20 }, scene);
          } else if (g.radius && g.length) {
            mesh = B.MeshBuilder.CreateCylinder("mesh_" + l.name + "_" + vi,
              { diameter: g.radius * 2, height: g.length, tessellation: 28 }, scene);
          } else {
            mesh = B.MeshBuilder.CreateBox("mesh_" + l.name + "_" + vi,
              { size: 0.03 }, scene);
          }
        }

        mesh.parent = holder;
        mesh.metadata = { link: l.name };
        mesh.material = matFor(l.name, v.color);
        mesh.receiveShadows = true;
        shadowGen.addShadowCaster(mesh);
        pickables.push(mesh);
      });
    });

    // ---------- 关节 ----------

    var sliders = [];
    var jointNodes = {};

    function jointMatrix(j) {
      var v = (j.type === "fixed") ? 0 : (j.value || 0);
      var M = mMul(mT(j.xyz[0], j.xyz[1], j.xyz[2]), mRpy(j.rpy || [0, 0, 0]));
      if (j.type === "fixed" || !v) return M;

      var ax = j.axis || [0, 0, 1];
      if (j.type === "prismatic") {
        // 平动关节的值是「沿轴位移（米）」，不是角度。
        // 之前一律套 mAxis() 做旋转，于是伸缩轴拖滑块只是原地微转、看着完全没动。
        var n = Math.sqrt(ax[0] * ax[0] + ax[1] * ax[1] + ax[2] * ax[2]) || 1;
        M = mMul(M, mT(ax[0] / n * v, ax[1] / n * v, ax[2] / n * v));
      } else {
        M = mMul(M, mAxis(ax, v));      // revolute / continuous：绕轴转角
      }
      return M;
    }

    (data.joints || []).forEach(function (j) {
      var node = new B.TransformNode("joint_" + j.name, scene);
      node.parent = linkNodes[j.parent] || root;
      jointNodes[j.name] = node;
      applyLocal(B, node, jointMatrix(j));

      var child = linkNodes[j.child];
      if (child) child.parent = node;

      if (!childMap[j.parent]) childMap[j.parent] = [];
      childMap[j.parent].push({ joint: j.name, link: j.child });

      if (j.type === "fixed") return;

      var row = document.createElement("div");
      row.className = "pv-joint";
      var label = document.createElement("span");
      label.className = "pv-jname";
      label.textContent = j.name;
      var input = document.createElement("input");
      input.type = "range";
      input.autocomplete = "off";
      var val = document.createElement("span");
      val.className = "pv-jval";
      row.appendChild(label);
      row.appendChild(input);
      row.appendChild(val);
      body.appendChild(row);

      var lim = j.limit || (j.type === "prismatic" ? [0, 0.05] : [-Math.PI, Math.PI]);
      input.min = lim[0];
      input.max = lim[1];
      input.step = "any";
      if (j.value < lim[0] || j.value > lim[1]) j.value = (lim[0] + lim[1]) / 2;
      input.value = j.value;
      val.textContent = fmt(j.value, j.type);

      var armed = false;
      input.addEventListener("pointerdown", function () { armed = true; });
      input.addEventListener("keydown", function () { armed = true; });
      input.oninput = function () {
        var v = parseFloat(input.value);
        if (!isFinite(v)) return;
        if (!armed) {                       // 浏览器还原表单值时的伪事件
          input.value = j.value;
          val.textContent = fmt(j.value, j.type);
          return;
        }
        j.value = v;
        val.textContent = fmt(v, j.type);
        applyLocal(B, jointNodes[j.name], jointMatrix(j));
        recomputePose();
        refreshReadout();
      };

      sliders.push({ j: j, input: input, val: val });
      label.onclick = function () { highlight(j.child); };
    });

    // 浏览器（Chrome/Edge）会在页面空闲后「恢复」上一次的表单值，时机晚于首次赋值。
    // 开一个 4 秒的限时自愈窗口：每 200ms 按数据源对齐一次，之后自动停，不常驻空转。
    function resetSliders() {
      sliders.forEach(function (it) {
        var want = Number(it.j.value);
        if (Number(it.input.value) === want) return;
        it.input.value = want;
        it.val.textContent = fmt(want, it.j.type);
        applyLocal(B, jointNodes[it.j.name], jointMatrix(it.j));
      });
      recomputePose();
      refreshReadout();
    }
    if (window.requestAnimationFrame) requestAnimationFrame(resetSliders);
    setTimeout(resetSliders, 260);
    var heal = 0;
    var healTimer = setInterval(function () {
      heal += 1;
      resetSliders();
      if (heal >= 20) clearInterval(healTimer);
    }, 200);

    // ---------- 尺寸与相机 ----------

    // 只算世界矩阵，不 render：此时相机还没建，Babylon 会直接抛 "No camera defined"
    root.computeWorldMatrix(true);
    var bounds = root.getHierarchyBoundingVectors(true);
    var bmin = bounds.min, bmax = bounds.max;
    var center = bmin.add(bmax).scale(0.5);
    var radius = Math.max(bmax.subtract(bmin).length() / 2, 0.005);

    var az = (typeof data.view_az === "number") ? data.view_az : 1.05;
    var el = (typeof data.view_el === "number") ? data.view_el : 0.28;
    var dist = radius / Math.sin(45 * Math.PI / 360) * 1.06;

    // 视角对齐实测：Babylon 在右手系下 alpha 的方向与 three 的方位角相反，
    // 用 az 会从模型背面看过去（轮廓完全不同），取 -az 才与 three 版一致
    var cam = new B.ArcRotateCamera("cam", -az, Math.PI / 2 - el, dist, center, scene);
    cam.attachControl(canvas, true);
    cam.upVector = new B.Vector3(0, 1, 0);
    cam.lowerRadiusLimit = radius * 0.5;
    cam.upperRadiusLimit = radius * 30;
    cam.wheelDeltaPercentage = 0.02;
    cam.pinchDeltaPercentage = 0.02;
    cam.panningSensibility = 3000 / Math.max(radius * 1000, 1);
    cam.minZ = radius * 0.005;
    cam.maxZ = radius * 300;
    cam.inertia = 0.72;

    dir.position = center.add(new B.Vector3(radius * 1.6, radius * 3.2, radius * 2.2));
    dir.shadowMinZ = radius * 0.1;
    dir.shadowMaxZ = radius * 14;

    var ground = B.MeshBuilder.CreateGround("ground",
      { width: radius * 7, height: radius * 7 }, scene);
    var gmat = new B.StandardMaterial("gmat", scene);
    gmat.diffuseColor = new B.Color3(0.05, 0.06, 0.08);
    gmat.specularColor = new B.Color3(0, 0, 0);
    gmat.alpha = 0.13;
    gmat.backFaceCulling = false;
    ground.material = gmat;
    ground.receiveShadows = true;
    ground.isPickable = false;
    ground.position = new B.Vector3(center.x, bmin.y - radius * 0.004, center.z);

    // ---------- 坐标轴与网格 ----------

    var helpers = new B.TransformNode("helpers", scene);
    applyLocal(B, helpers, mRx(-Math.PI / 2));      // 与 root 同向：x/y/z 就是 URDF 的轴

    var L = radius * 0.95;
    var axesLines = B.MeshBuilder.CreateLines("axisX", {
      points: [new B.Vector3(0, 0, 0), new B.Vector3(L, 0, 0)]
    }, scene);
    axesLines.color = new B.Color3(0.86, 0.26, 0.26);
    axesLines.parent = helpers;
    axesLines.isPickable = false;
    axesLines.isVisible = false;

    var axisY = B.MeshBuilder.CreateLines("axisY", {
      points: [new B.Vector3(0, 0, 0), new B.Vector3(0, L, 0)]
    }, scene);
    axisY.color = new B.Color3(0.22, 0.62, 0.30);
    axisY.parent = helpers;
    axisY.isPickable = false;
    axisY.isVisible = false;

    var axisZ = B.MeshBuilder.CreateLines("axisZ", {
      points: [new B.Vector3(0, 0, 0), new B.Vector3(0, 0, L)]
    }, scene);
    axisZ.color = new B.Color3(0.24, 0.44, 0.86);
    axisZ.parent = helpers;
    axisZ.isPickable = false;
    axisZ.isVisible = false;

    var gridSeg = [];
    var step = L / 4;
    for (var i = -4; i <= 4; i++) {
      gridSeg.push([new B.Vector3(-4 * step, i * step, 0), new B.Vector3(4 * step, i * step, 0)]);
      gridSeg.push([new B.Vector3(i * step, -4 * step, 0), new B.Vector3(i * step, 4 * step, 0)]);
    }
    var grid = B.MeshBuilder.CreateLineSystem("grid", { lines: gridSeg }, scene);
    grid.color = new B.Color3(0.70, 0.75, 0.82);
    grid.parent = helpers;
    grid.isPickable = false;
    grid.isVisible = false;

    // ---------- 读数 ----------

    var selLink = "";

    var endName = (function () {
      if (linkNodes.tool0) return "tool0";
      var hasWheel = Object.keys(linkNodes).some(function (n) { return n.indexOf("wheel_link") >= 0; });
      if (hasWheel && linkNodes.base_link) return "base_link";
      var best = data.root, bestD = -1, stack = [[data.root, 0]];
      while (stack.length) {
        var cur = stack.pop(), d = cur[1];
        if (d > bestD) { bestD = d; best = cur[0]; }
        (childMap[cur[0]] || []).forEach(function (n) { stack.push([n.link, d + 1]); });
      }
      return best;
    })();

    // 读数直接用自己算的 FK：Babylon 的世界矩阵是「行向量 + 行主序」，
    // 求逆取相对变换要非常小心乘的顺序，一旦弄反就是「位置全错但画面看不出」。
    // 这里沿关节链把 4x4 乘下来，结果与 URDF 定义严格一致。
    var urdfPose = {};

    function recomputePose() {
      urdfPose = {};
      urdfPose[data.root] = mI();
      var pending = (data.joints || []).slice();
      var guard = 0;
      while (pending.length && guard++ < 400) {
        var next = [];
        for (var i = 0; i < pending.length; i++) {
          var j = pending[i];
          var pm = urdfPose[j.parent];
          if (!pm) { next.push(j); continue; }
          urdfPose[j.child] = mMul(pm, jointMatrix(j));
        }
        if (next.length === pending.length) break;      // 有环路，放弃
        pending = next;
      }
    }

    function rpyFromM(m) {
      // R = Rz*Ry*Rx，列主序取元素
      var sy = -m[2];
      sy = Math.max(-1, Math.min(1, sy));
      return [
        Math.atan2(m[6], m[10]),
        Math.asin(sy),
        Math.atan2(m[1], m[0])
      ];
    }

    function nodePose(name) {
      var M = urdfPose[name];
      if (!M) return null;
      return { p: [M[12], M[13], M[14]], rpy: rpyFromM(M) };
    }

    function mm(v) { return (v * 1000).toFixed(1); }
    function dg(v) { return (v * 180 / Math.PI).toFixed(1); }

    function refreshReadout() {
      var tgt = selLink || endName;
      var ps = nodePose(tgt);
      var who = readout.querySelector('[data-ro="who"]');
      var posEl = readout.querySelector('[data-ro="pos"]');
      var rpyEl = readout.querySelector('[data-ro="rpy"]');
      if (!ps) {
        who.textContent = tgt || "\u2014";
        posEl.textContent = "\u2014";
        rpyEl.textContent = "\u2014";
        return;
      }
      who.textContent = tgt;
      posEl.textContent = "x " + mm(ps.p[0]) + "  y " + mm(ps.p[1]) + "  z " + mm(ps.p[2]) + " mm";
      rpyEl.textContent = "r " + dg(ps.rpy[0]) + "\u00b0  p " + dg(ps.rpy[1]) + "\u00b0  y " + dg(ps.rpy[2]) + "\u00b0";
    }
    recomputePose();
    refreshReadout();

    // ---------- 高亮 ----------

    function subtreeOf(linkName) {
      var out = {}, stack = [linkName];
      while (stack.length) {
        var cur = stack.pop();
        if (out[cur]) continue;
        out[cur] = true;
        (childMap[cur] || []).forEach(function (n) { stack.push(n.link); });
      }
      return out;
    }

    function highlight(linkName) {
      selLink = linkName || "";
      var keep = linkName ? subtreeOf(linkName) : null;
      Object.keys(linkMats).forEach(function (name) {
        var m = linkMats[name];
        var on = !keep || !!keep[name];
        if (on) {
          m.emissiveColor = keep ? new B.Color3(0.15, 0.08, 0.26) : new B.Color3(0, 0, 0);
          m.alpha = 1;
          m.transparencyMode = B.Material.MATERIAL_OPAQUE;
        } else {
          m.emissiveColor = new B.Color3(0, 0, 0);
          m.alpha = 0.15;
          m.transparencyMode = B.Material.MATERIAL_ALPHABLEND;
        }
      });
      refreshReadout();
    }

    var downX = 0, downY = 0;
    canvas.addEventListener("pointerdown", function (e) {
      downX = e.clientX; downY = e.clientY;
    });
    canvas.addEventListener("pointerup", function (e) {
      if (Math.abs(e.clientX - downX) > 6 || Math.abs(e.clientY - downY) > 6) return;
      var pick = scene.pick(scene.pointerX, scene.pointerY, function (m) {
        return !!(m.metadata && m.metadata.link);
      });
      if (pick && pick.hit && pick.pickedMesh) {
        var name = pick.pickedMesh.metadata.link;
        highlight(selLink === name ? "" : name);
      }
    });

    // ---------- 工具栏 ----------

    function demoPose() {
      if (linkNodes.tool0) {
        var arm = [0.42, -0.55, 1.15, -0.35, 0.85, 0.25];
        return sliders.map(function (it, i) {
          if (i < arm.length) return arm[i];
          return it.j.type === "prismatic" ? 0.015 : 0;
        });
      }
      if (sliders.some(function (it) { return it.j.name.indexOf("steer") >= 0; })) {
        return sliders.map(function (it) {
          return it.j.name.indexOf("steer") >= 0 ? 0.35 : 0;
        });
      }
      return sliders.map(function (it, i) {
        var lim = it.j.limit || [-1, 1];
        if (it.j.type === "prismatic") return lim[1] * 0.6;
        var hi = Math.min(Math.abs(lim[0]), Math.abs(lim[1]));
        return (i % 2 ? -1 : 1) * hi * 0.18;
      });
    }

    function setJointValues(vals) {
      sliders.forEach(function (it, i) {
        var v = vals[i];
        if (v == null) return;
        var lim = it.j.limit || [-Math.PI, Math.PI];
        v = Math.max(lim[0], Math.min(lim[1], v));
        it.j.value = v;
        it.input.value = v;
        it.val.textContent = fmt(v, it.j.type);
      });
      sliders.forEach(function (it) { applyLocal(B, jointNodes[it.j.name], jointMatrix(it.j)); });
      recomputePose();
      refreshReadout();
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
        var on = !axesLines.isVisible;
        axesLines.isVisible = on;
        axisY.isVisible = on;
        axisZ.isVisible = on;
        grid.isVisible = on;
        b.classList.toggle("on", on);
      } else if (act === "spin") {
        var on2 = !cam.useAutoRotationBehavior;
        cam.useAutoRotationBehavior = on2;
        if (on2 && cam.autoRotationBehavior) {
          cam.autoRotationBehavior.idleRotationSpeed = 0.28;
          cam.autoRotationBehavior.idleRotationWaitTime = 0;
        }
        b.classList.toggle("on", on2);
      }
    });

    // ---------- 启动 ----------

    engine.runRenderLoop(function () { scene.render(); });

    var ro = null;
    if (window.ResizeObserver) {
      ro = new ResizeObserver(function () {
        var w = canvasBox.clientWidth;
        if (!w || Math.abs(w - W) < 2) return;
        W = w;
        engine.resize();
      });
      ro.observe(canvasBox);
    }

    function dispose() {
      try {
        if (ro) ro.disconnect();
        scene.dispose();
        engine.dispose();
      } catch (e) { /* 已经释放了 */ }
      slot.innerHTML = '<div class="pv-empty">\uff08\u8f83\u65e9\u7684\u9884\u89c8\u5df2\u91ca\u653e\u663e\u5b58\uff09</div>';
    }
    return dispose;
  }

  // ---------- 挂载 ----------

  function fail(slot, msg, sid, pkg) {
    // 调试用：异常被 catch 吞掉时，标题是唯一能读到原因的地方
    try { document.title = "PVFAIL " + String(msg).replace(/<[^>]*>/g, "").slice(0, 90); } catch (e) { }
    slot.innerHTML = '<div class="pv-empty">' + msg +
      ' <button type="button" class="pv-retry">\u91cd\u8bd5</button></div>';
    var b = slot.querySelector(".pv-retry");
    if (b) b.onclick = function () { mount(slot, sid, pkg); };
  }

  function mount(slot, sid, pkg) {
    slot.innerHTML = '<div class="pv-loading">\u6b63\u5728\u751f\u6210\u6a21\u578b\u9884\u89c8\u2026</div>';
    fetchScene(sid, pkg).then(function (data) {
      if (!data || !data.ok) {
        fail(slot, "\u65e0\u6cd5\u9884\u89c8\uff1a" + esc((data && data.error) || "\u672a\u77e5\u539f\u56e0"), sid, pkg);
        return;
      }
      loadBabylon().then(function (B) {
        try {
          var dispose = render(slot, data, B);
          var entry = function () {
            dispose();
            slot.innerHTML = '<div class="pv-lazy">\u8f83\u65e9\u7684\u9884\u89c8\u5df2\u91ca\u653e\u663e\u5b58' +
              ' <button type="button" class="pv-retry">\u91cd\u65b0\u52a0\u8f7d</button></div>';
            var b = slot.querySelector(".pv-retry");
            if (b) b.onclick = function () { mount(slot, sid, pkg); };
          };
          LIVE.push(entry);
          while (LIVE.length > MAX_LIVE) {
            var old = LIVE.shift();
            try { old(); } catch (e) { /* 忽略 */ }
          }
        } catch (e) {
          fail(slot, "\u6e32\u67d3\u5931\u8d25\uff1a" + esc(e.message), sid, pkg);
        }
      }).catch(function (e) {
        fail(slot, "3D \u7ec4\u4ef6\u52a0\u8f7d\u5931\u8d25\uff08" + esc(e && e.message) + "\uff09", sid, pkg);
      });
    }).catch(function (e) {
      fail(slot, "\u9884\u89c8\u83b7\u53d6\u5931\u8d25\uff1a" + esc(e && e.message), sid, pkg);
    });
  }

  function mountLazy(slot, sid, pkg) {
    slot.innerHTML = '<div class="pv-lazy">\u6eda\u52a8\u5230\u8fd9\u91cc\u52a0\u8f7d\u4e09\u7ef4\u9884\u89c8\u2026</div>';
    if (!window.IntersectionObserver) { mount(slot, sid, pkg); return; }
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) {
        if (e.isIntersecting) { io.disconnect(); mount(slot, sid, pkg); }
      });
    }, { rootMargin: "300px" });
    io.observe(slot);
  }

  window.RTPreview = { mount: mount, mountLazy: mountLazy, engine: "babylon" };
})();
