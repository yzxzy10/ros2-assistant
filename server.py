# -*- coding: utf-8 -*-
"""ROS2 学习助手网站服务器（stdlib 实现，无第三方依赖）。

路由（严格模式下除 /api/health 外都要带 X-Auth-Token）：
  GET  /                    → public/index.html
  GET  /static/*            → 前端静态资源
  GET  /api/health          → 健康检查（模型是否已配置、令牌是否开启、版本信息）
  GET  /api/meta            → 版本清单（系统版本 + 目标 ROS 发行版 + 编译沙箱状态）
  POST /api/chat            → 主调度员 {sessionId, message, userId}
  POST /api/chat/stream     → 同上，SSE 流式（progress/tick/delta/final 事件）
  POST /api/upload          → 上传截图 {sessionId, dataUrl} → {id, url}
  GET  /api/uploads/<sid>/<name> → 取回会话内的截图
  GET  /api/profile?uid=    → 学习画像
  GET  /api/session/<sid>   → 会话状态（节点清单/文件树/知识库信息）
  GET  /api/files/<sid>     → 文件树
  GET  /api/files/<sid>/zip → 工作区打包下载
  POST /api/archive         → 手动触发旧会话归档
"""
import io
import json
import os
import re
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.config import load_config, api_configured, key_hint, CONFIG_PATH
from app.kb import get_retriever
from app import (orchestrator, store, archive, auth, env, paths,
                 identity, ratelimit, vision, versions, sandbox,
                 project_import)

_ROOT = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(_ROOT, "public")

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}

_CFG = load_config()
_CFG_MTIME = [os.path.getmtime(CONFIG_PATH)]
_SAFE_SID = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


def current_config():
    """配置文件改动后自动热加载，无需重启服务。"""
    try:
        m = os.path.getmtime(CONFIG_PATH)
        if m != _CFG_MTIME[0]:
            _CFG.clear()
            _CFG.update(load_config())
            _CFG_MTIME[0] = m
            print("配置已热加载")
    except Exception as e:  # noqa: BLE001
        print("配置热加载失败，沿用旧配置:", e)
    return _CFG


class Handler(BaseHTTPRequestHandler):
    server_version = "ROS2Tutor/1.0"

    def log_message(self, fmt, *args):  # 精简日志
        pass

    # ---------- helpers ----------
    def _json(self, obj, status=200, headers=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _static(self, rel):
        path = os.path.normpath(os.path.join(PUBLIC, rel))
        if not path.startswith(PUBLIC) or not os.path.isfile(path):
            self._json({"error": "not found"}, 404)
            return
        ext = os.path.splitext(path)[1].lower()
        body = open(path, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", _MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def _query(self):
        from urllib.parse import urlparse, parse_qs
        try:
            q = parse_qs(urlparse(self.path).query)
            return {k: v[0] for k, v in q.items() if v}
        except Exception:
            return {}

    def _read_body(self, max_bytes=1 << 20):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > max_bytes:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def _raw_body(self, max_bytes):
        """读原始字节（上传接口用），超限返回 None。"""
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > max_bytes:
            return None
        return self.rfile.read(n)

    def _guard(self, kind="api"):
        """访问控制流水线：IP 白名单 → 令牌 → 身份 Cookie → 限流。

        返回 (ok, uid)；uid 来自令牌绑定或签名 Cookie，绝不采信请求里自报的 uid。
        """
        ip = auth.client_ip(self.headers, self.client_address[0])
        ok, bound, why, code = auth.check(self.headers, self._query(), ip)
        if not ok:
            body = {
                "error": "forbidden" if code == 403 else "unauthorized",
                "need_token": code == 401,
                "message": why or "无权访问",
            }
            if code == 403:
                body["client_ip"] = ip
            self._json(body, code or 401)
            return False, None

        uid = bound or identity.from_cookie(self.headers)
        if not uid:
            self._json({"error": "unauthorized", "need_identity": True,
                        "message": "缺少身份凭证，正在重新获取…"}, 401)
            return False, None

        limit = ratelimit.chat_limit() if kind == "chat" else ratelimit.api_limit()
        allowed, retry = ratelimit.hit("%s:%s" % (kind, uid), limit)
        if not allowed:
            self._json({"error": "rate_limited",
                        "message": "请求太频繁，请 %d 秒后再试" % retry},
                       429, {"Retry-After": str(retry)})
            return False, None

        if kind == "chat":                    # 全站每日保险丝，防一夜被刷爆
            ok_day, used, cap = ratelimit.hit_daily()
            if not ok_day:
                self._json({"error": "daily_quota",
                            "message": "今天的对话额度已用完（%d 次），明天再来" % cap},
                           429)
                return False, None
        return True, uid

    def _claim(self, sess, uid):
        """首次被访问的会话归当前身份所有（老数据里 user_id 默认等于 sid）。"""
        if not uid:
            return
        if not sess.user_id or sess.user_id == sess.sid:
            sess.user_id = uid
            try:
                sess.save()
            except Exception:      # noqa: BLE001
                pass

    def _may_access(self, bound, owner_uid):
        """严格模式且令牌绑定了 uid 时，只允许访问本人的会话/画像。"""
        return not (bound and owner_uid and bound != owner_uid)

    def _https(self):
        if (os.environ.get("ROS2_COOKIE_SECURE") or "").strip() in ("1", "true", "yes"):
            return True
        try:
            return (self.headers.get("X-Forwarded-Proto") or "").lower() == "https"
        except Exception:          # noqa: BLE001
            return False

    # ---------- GET ----------
    def do_GET(self):
        path = self.path.split("?")[0]
        bound = None
        if path.startswith("/api/") and path not in ("/api/health", "/api/identity",
                                                     "/api/meta"):
            ok, bound = self._guard()
            if not ok:
                return
        try:
            if path in ("/", "/index.html"):
                self._static("index.html")
            elif path.startswith("/static/"):
                self._static(path[len("/static/"):])
            elif path == "/favicon.svg":
                self._static("favicon.svg")
            elif path == "/api/identity":
                # 公开分享时的身份发放入口：首次给新 uid，之后每次续期
                ip = auth.client_ip(self.headers, self.client_address[0])
                okip, why_ip = auth.allowed_ip(ip)
                if not okip:
                    return self._json({"ok": False, "error": "forbidden",
                                       "message": why_ip}, 403)
                uid = identity.from_cookie(self.headers)
                fresh = False
                if not uid:
                    uid, fresh = identity.new_uid(), True
                self._json(
                    {"user_id": uid, "fresh": fresh},
                    200,
                    {"Set-Cookie": identity.cookie_header(uid, self._https())})
            elif path == "/api/meta":
                # 版本口径单独开口子：评审/部署都靠它确认「跑的是哪个版本」
                cfg = current_config()
                info = versions.meta(cfg)
                info["sandbox"] = sandbox.status(cfg)
                info["kb"] = {"docs": get_retriever().N,
                              "files": len({s for s, _t in get_retriever().docs})}
                self._json(info)
            elif path == "/api/health":
                ip = auth.client_ip(self.headers, self.client_address[0])
                okip, why_ip = auth.allowed_ip(ip)
                if not okip:
                    return self._json({"ok": False, "error": "forbidden",
                                       "message": why_ip}, 403)
                retr = get_retriever()
                cfg = current_config()
                base = {
                    "ok": True,
                    "model_configured": api_configured(cfg),
                    "key_source": cfg.get("keySource", "none"),
                    "auth_required": auth.enabled(),
                    "ip_restricted": auth.ip_restricted(),
                    "identity": "cookie",
                    "rate_limit": ratelimit.describe(),
                    "daily_used": ratelimit.daily_usage(),
                    # 版本口径：健康检查就带上，前端徽标直接读这里
                    "version": dict(versions.meta(cfg),
                                    sandbox=sandbox.status(cfg)),
                }
                if not api_configured(cfg):
                    base["key_hint"] = key_hint(cfg)
                # 严格模式下不对外暴露模型清单与上游地址
                if auth.enabled():
                    base["kb_docs"] = retr.N
                    base["kb_files"] = len({s for s, _t in retr.docs})
                else:
                    base.update({
                        "base_url": cfg.get("baseUrl", ""),
                        "models": cfg.get("models", {}),
                        "kb_docs": retr.N,
                        "kb_files": len({s for s, _t in retr.docs}),
                    })
                self._json(base)
            elif path == "/api/sessions":
                # 列出当前用户的历史会话（按最后活动倒序），供前端「历史会话」切换
                uid = bound or identity.from_cookie(self.headers)
                if not uid:
                    return self._json({"error": "unauthorized",
                                       "need_identity": True}, 401)
                items = orchestrator.list_user_sessions(uid)
                self._json({"sessions": items})
            elif path == "/api/profile":
                uid = self._query().get("uid", "")
                if bound:                 # 令牌已绑定身份：忽略请求里的 uid
                    uid = bound           # 管理员配置的 uid 不受前端格式规则约束
                elif uid and not _SAFE_SID.match(uid):
                    return self._json({"error": "bad user id"}, 400)
                prof = store.get(uid) if uid else {}
                self._json({
                    "user_id": uid,
                    "profile": prof or {},
                    "stats": store.stats(prof) if prof else {},
                    "rendered": store.render(prof) if prof else "",
                    "memory": current_config().get("memory", {}),
                })
            elif path.startswith("/api/uploads/"):
                seg = path[len("/api/uploads/"):].split("/")
                if len(seg) != 2:
                    return self._json({"error": "not found"}, 404)
                sid, name = seg
                if not _SAFE_SID.match(sid) or not re.fullmatch(
                        r"[0-9a-f]{20}\.(png|jpg|webp|gif)", name):
                    return self._json({"error": "bad image id"}, 400)
                sess = orchestrator.get_session(sid)
                self._claim(sess, bound)
                if not self._may_access(bound, sess.user_id):
                    return self._json({"error": "forbidden",
                                       "message": "无权访问该会话的图片"}, 403)
                full = os.path.join(vision.upload_dir(sid), name)
                if not os.path.isfile(full):
                    return self._json({"error": "not found"}, 404)
                body = open(full, "rb").read()
                _ext, mime = vision.sniff(body)
                self.send_response(200)
                self.send_header("Content-Type", mime or "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "private, max-age=86400")
                self.end_headers()
                self.wfile.write(body)
            elif path.startswith("/api/session/"):
                sid = path[len("/api/session/"):]
                if not _SAFE_SID.match(sid):
                    return self._json({"error": "bad session id"}, 400)
                sess = orchestrator.get_session(sid)
                self._claim(sess, bound)
                if not self._may_access(bound, sess.user_id):
                    return self._json({"error": "forbidden", "message": "无权访问该会话"}, 403)
                self._json({
                    "sessionId": sid,
                    "nodes": [{"node_name": n.get("node_name"),
                               "generated": n.get("generated", False),
                               "file_name": n.get("file_name", ""),
                               "what_to_do": n.get("what_to_do", "")}
                              for n in sess.node_list],
                    "files": sess.file_tree(),
                    "imported_project": project_import.to_json(
                        getattr(sess, "imported_project", None)),
                    "pending": sess.pending,
                    "level": sess.level,
                    "userId": sess.user_id,
                    "history": sess.history,
                    "transcript": sess.transcript,
                })
            elif path.startswith("/api/files/") and path.endswith("/zip"):
                sid = path[len("/api/files/"):-len("/zip")]
                if not _SAFE_SID.match(sid):
                    return self._json({"error": "bad session id"}, 400)
                sess = orchestrator.get_session(sid)
                self._claim(sess, bound)
                if not self._may_access(bound, sess.user_id):
                    return self._json({"error": "forbidden", "message": "无权访问该会话"}, 403)
                root = sess.ws_root()
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                    for dirpath, _d, files in os.walk(root):
                        for fn in files:
                            full = os.path.join(dirpath, fn)
                            z.write(full, "ros2_ws/" + os.path.relpath(full, root).replace("\\", "/"))
                data = buf.getvalue()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition",
                                 "attachment; filename=ros2_ws_%s.zip" % sid[:8])
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif path.startswith("/api/files/"):
                sid = path[len("/api/files/"):]
                if not _SAFE_SID.match(sid):
                    return self._json({"error": "bad session id"}, 400)
                sess = orchestrator.get_session(sid)
                self._claim(sess, bound)
                if not self._may_access(bound, sess.user_id):
                    return self._json({"error": "forbidden", "message": "无权访问该会话"}, 403)
                self._json({"files": sess.file_tree()})
            else:
                self._json({"error": "not found"}, 404)
        except BrokenPipeError:
            pass
        except Exception as e:  # noqa: BLE001
            self._json({"error": repr(e)}, 500)

    # ---------- POST ----------
    def do_POST(self):
        path = self.path.split("?")[0]
        # 会话删除 / 归档：单独分发
        if path.startswith("/api/session/"):
            if not (path.endswith("/delete") or path.endswith("/archive")):
                return self._json({"error": "not found"}, 404)
            ok, bound = self._guard("api")
            if not ok:
                return
            return self._session_action(bound, path)
        if path not in ("/api/archive", "/api/chat", "/api/chat/stream",
                        "/api/upload", "/api/project/import"):
            return self._json({"error": "not found"}, 404)
        kind = "chat" if path.startswith("/api/chat") else "api"
        ok, bound = self._guard(kind)
        if not ok:
            return
        if path == "/api/upload":
            return self._upload(bound)
        if path == "/api/project/import":
            return self._project_import(bound)
        if path == "/api/archive":
            body = self._read_body()
            days = body.get("days")
            dry = bool(body.get("dry_run"))
            res = archive.archive_old_sessions(
                days=days, keep=orchestrator.active_sids(), dry_run=dry)
            return self._json({"dry_run": dry,
                               "result": [{"session": s, "status": r} for s, r in res]})
        body = self._read_body()
        sid = str(body.get("sessionId") or "")
        message = str(body.get("message") or "")
        images = body.get("images") or []
        if not isinstance(images, list):
            images = []
        images = [str(i) for i in images[:vision.MAX_IMAGES]]
        if not _SAFE_SID.match(sid):
            return self._json({"error": "bad session id"}, 400)
        # 身份只认服务端签发（Cookie）或令牌绑定，请求里自报的 userId 不再采信
        uid = bound or str(body.get("userId") or "").strip()
        if not _SAFE_SID.match(uid):
            uid = sid                # 未提供时退化为按会话区分
        cfg = current_config()
        if not api_configured(cfg):
            return self._json({
                "error": "模型未配置：请在服务端 config.json 填入 apiKey，"
                         "或设置环境变量 DASHSCOPE_API_KEY 后重启服务。"}, 503)
        if path == "/api/chat/stream":
            return self._stream(cfg, sid, message, uid, images)
        try:
            result = orchestrator.handle(cfg, sid, message, uid, images)
            self._json(result)
        except Exception as e:  # noqa: BLE001
            self._json({"reply": "处理请求时出现内部错误：%r" % e,
                        "options": [], "stages": []}, 500)

    def _upload(self, bound):
        """接收 base64 图片并落盘，返回图片 id。

        为什么用 base64 JSON 而不是 multipart：stdlib 没有可靠的 multipart
        解析，自己写容易在边界字符上翻车；4MB 以内的截图用 base64 完全够用。
        """
        cap = int(((current_config().get("multimodal") or {})
                   .get("max_bytes")) or vision.MAX_BYTES)
        raw = self._raw_body(int(cap * 1.45) + 4096)   # base64 膨胀约 4/3
        if raw is None:
            return self._json({"error": "too_large",
                               "message": "图片太大，请压缩到 %dMB 以内"
                                          % (cap // 1048576)}, 413)
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            return self._json({"error": "bad body"}, 400)

        sid = str(body.get("sessionId") or "")
        if not _SAFE_SID.match(sid):
            return self._json({"error": "bad session id"}, 400)
        sess = orchestrator.get_session(sid)
        self._claim(sess, bound)

        data = vision.decode_data_url(str(body.get("dataUrl") or ""))
        if data is None:
            return self._json({"error": "bad image",
                               "message": "这不是有效的 base64 图片数据"}, 400)
        res = vision.save(sid, data, max_bytes=cap)
        if not res.get("ok"):
            return self._json({"error": "rejected",
                               "message": res.get("error", "图片无法保存")}, 400)
        self._json({"ok": True, "id": res["id"], "bytes": res["bytes"],
                    "mime": res["mime"],
                    "url": "/api/uploads/%s/%s.%s" % (sid, res["id"], res["ext"])})


    def _project_import(self, bound):
        """接收用户导入的 ROS2 项目（文件列表），识别并落到会话工作区。

        前端用文件夹选择器读取文件后，以 {sessionId, files:[{path,content}]}
        形式提交；path 是相对项目根目录的路径。体积上限由 project_import 控制。
        """
        raw = self._raw_body(140 * 1024 * 1024)   # 100MB 总量 + base64 膨胀约 4/3
        if raw is None:
            return self._json({"error": "too_large",
                               "message": "项目文件过大（上限约 100MB）"}, 413)
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            return self._json({"error": "bad body"}, 400)

        sid = str(body.get("sessionId") or "")
        if not _SAFE_SID.match(sid):
            return self._json({"error": "bad session id"}, 400)
        sess = orchestrator.get_session(sid)
        self._claim(sess, bound)

        files = body.get("files")
        if not isinstance(files, list):
            return self._json({"error": "bad body",
                               "message": "files 必须是数组"}, 400)

        ok, message, struct = project_import.import_to_session(sess, files)
        if not ok:
            return self._json({"ok": False, "message": message,
                               "recognized": project_import.to_json(
                                   getattr(sess, "imported_project", None))}, 400)
        return self._json({
            "ok": True,
            "message": message,
            "imported_project": project_import.to_json(sess.imported_project),
            "files": sess.file_tree(),
            "nodes": [],
        })


    def _session_action(self, bound, path):
        """删除 / 归档单个会话（POST /api/session/<sid>/delete|archive）。

        归属校验：只能操作属于自己的会话（严格模式令牌绑定则对齐 bound）。
        删除不可恢复；归档打包进 archive/ 可找回。
        """
        rest = path[len("/api/session/"):]
        sid, _, action = rest.rpartition("/")
        if action not in ("delete", "archive") or not _SAFE_SID.match(sid):
            return self._json({"error": "bad request"}, 400)

        # 先确认会话真实存在：get_session 对不存在的 sid 会新建空会话，
        # _claim 还会把未认领会话落盘，导致「删除不存在会话」反而凭空建目录。
        sess_path = os.path.join(paths.sessions(create=False), sid)
        if not os.path.isdir(sess_path):
            return self._json({"ok": False, "action": action,
                               "message": "会话不存在", "sessionId": sid}, 404)

        sess = orchestrator.get_session(sid)
        self._claim(sess, bound)
        if not self._may_access(bound, sess.user_id):
            return self._json({"error": "forbidden", "message": "无权操作该会话"}, 403)

        if action == "archive":
            orchestrator.forget_session(sid)
            ok, msg = archive.archive_session(sid)
        else:
            orchestrator.forget_session(sid)
            ok, msg = archive.delete_session(sid)
        return self._json({"ok": ok, "action": action, "message": msg,
                           "sessionId": sid},
                          200 if ok else 400)


    def _stream(self, cfg, sid, message, uid=None, images=None):
        """SSE 流式输出：data 行携带 JSON 事件，结束时发 [DONE]。

        事件类型：progress（分步状态）/ tick（心跳，每 2s）/
                  delta（增量文本）/ final / error
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            for kind, payload in orchestrator.handle_stream(cfg, sid, message,
                                                            uid, images):
                if kind == "final":
                    data = {"type": "final"}
                    data.update(payload)
                elif kind == "error":
                    data = {"type": "error", "error": payload}
                else:
                    data = {"type": kind, "value": payload}
                self._sse(data)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            try:
                self._sse({"type": "error", "error": "处理请求时出现内部错误：%r" % e})
            except Exception:  # noqa: BLE001
                pass

    def _sse(self, obj):
        chunk = "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"
        self.wfile.write(chunk.encode("utf-8"))
        self.wfile.flush()


def main():
    env.load()                       # 先读 .env：密钥、令牌、数据目录
    paths.ensure_all()
    port = int(os.environ.get("PORT") or 8000)
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    retr = get_retriever()
    cfg = current_config()
    sandbox.warm()                   # 后台测编译沙箱后端，避免首屏卡住
    print("ROS2 学习助手已启动: http://0.0.0.0:%s" % port)
    print("系统版本: v%s | 目标版本: %s" % (versions.APP_VERSION, versions.label(cfg)))
    print("知识库: %d 个文档块 | 模型已配置: %s" % (retr.N, api_configured(cfg)))
    print(paths.describe())
    print("访问控制: %s" % auth.describe())
    print("身份: %s" % identity.describe())
    print("限流: %s" % ratelimit.describe())
    sb = sandbox.status(cfg)
    print("编译验证: %s" % ("可用（%s）" % sb["backend"] if sb["available"]
                            else "未启用（%s）" % sb["reason"]))
    if not api_configured(cfg):
        print("[!] " + key_hint(cfg))
    days = (_CFG.get("memory") or {}).get("archive_days", 30)
    archive.start_scheduler(interval_hours=24, days=days,
                            keep_fn=orchestrator.active_sids)
    print("旧会话归档：启动完成，%s 天未活动的会话会被打包" % days)
    server.serve_forever()


if __name__ == "__main__":
    main()
