# -*- coding: utf-8 -*-
"""ROS2 知识库检索：加载 kb_data/ 下全部文本，分块 + BM25 检索。

对应原工作流中的「检索ROS2知识库」插件节点（question + top_k=5 → context）。
检索器为服务端内置实现（BM25 + 中文 bigram 分词），检索结果以
【知识库检索结果】文本注入各 LLM 节点，替代原平台插件。
"""
import json
import math
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB_DIR = os.path.join(_ROOT, "kb_data")

_ASCII = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")
_CHUNK_MAX = 2200


def _flatten_strings(obj, out, depth=0):
    if depth > 4:
        return
    if isinstance(obj, str):
        if obj.strip():
            out.append(obj.strip())
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("source_url", "commit", "source_sha256"):
                continue
            if isinstance(v, str) and k in ("file_path", "title", "rule_id",
                                            "rule_text_zh", "syntax", "category"):
                out.append(str(v))
            else:
                _flatten_strings(v, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _flatten_strings(v, out, depth + 1)


def _load_docs():
    """返回 [(source, text)] 列表；JSONL 按行成块，普通文本按段落成块。"""
    docs = []
    if not os.path.isdir(KB_DIR):
        return docs
    for root, _dirs, files in os.walk(KB_DIR):
        for fn in sorted(files):
            if not fn.lower().endswith((".txt", ".md")):
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, KB_DIR).replace("\\", "/")
            try:
                raw = open(path, "r", encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            for para in re.split(r"\n\s*\n", raw):
                para = para.strip()
                if not para:
                    continue
                if para.startswith("{"):
                    # JSONL：一条记录一块。
                    # 之前是「整段拼成一块再截断」，1003 条错误数据只剩前十几条
                    # 能被检索到，等于知识库九成内容被判了死刑；而且检索命中的
                    # 是一坨混杂条目，引用时说不清到底依据哪一条。
                    for line in para.splitlines():
                        line = line.strip()
                        if not line.startswith("{"):
                            continue
                        try:
                            obj = json.loads(line)
                        except Exception:
                            docs.append((rel, line[:_CHUNK_MAX]))
                            continue
                        parts = []
                        _flatten_strings(obj, parts)
                        # 版本标注：知识库采集自不同发行版，不标清楚模型会
                        # 把旧版本文档当成目标版本的权威说明
                        d = obj.get("ros_distro")
                        if d:
                            parts.insert(0, "【来源版本】ROS 2 %s" % d)
                        text = "\n".join(parts)[:_CHUNK_MAX]
                        if text:
                            docs.append((rel, text))
                else:
                    if len(para) > _CHUNK_MAX:
                        for i in range(0, len(para), _CHUNK_MAX):
                            docs.append((rel, para[i:i + _CHUNK_MAX]))
                    else:
                        docs.append((rel, para))
    return docs


def _tokenize(text):
    toks = [t.lower() for t in _ASCII.findall(text)]
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    for i in range(len(cjk) - 1):
        toks.append(cjk[i] + cjk[i + 1])
    if len(cjk) == 1:
        toks.append(cjk[0])
    return toks


class Retriever:
    def __init__(self):
        self.docs = _load_docs()
        self.tok_lists = [_tokenize(t) for _s, t in self.docs]
        self.tf = [{} for _ in self.tok_lists]
        for i, toks in enumerate(self.tok_lists):
            for t in toks:
                self.tf[i][t] = self.tf[i].get(t, 0) + 1
        self.df = {}
        for tfm in self.tf:
            for t in tfm:
                self.df[t] = self.df.get(t, 0) + 1
        self.N = len(self.docs)
        self.avgdl = (sum(len(x) for x in self.tok_lists) / self.N) if self.N else 1.0

    def search(self, query, top_k=5):
        if not self.N:
            return []
        k1, b = 1.5, 0.75
        qtoks = _tokenize(query)
        if not qtoks:
            return []
        scores = [0.0] * self.N
        for i in range(self.N):
            dl = len(self.tok_lists[i])
            s = 0.0
            for t in set(qtoks):
                f = self.tf[i].get(t)
                if not f:
                    continue
                idf = math.log(1 + (self.N - self.df.get(t, 0) + 0.5) / (self.df[t] + 0.5))
                s += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / self.avgdl))
            scores[i] = s
        ranked = sorted(range(self.N), key=lambda i: scores[i], reverse=True)
        out = []
        for idx in ranked[:top_k]:
            if scores[idx] <= 0:
                break
            out.append({"source": self.docs[idx][0], "score": round(scores[idx], 2),
                        "text": self.docs[idx][1]})
        return out

    def context(self, query, top_k=5, max_chars=4500):
        """与原插件 output 一致的形态：检索原文拼接 + source_count。"""
        hits = self.search(query, top_k)
        if not hits:
            return "", 0
        parts = []
        used = 0
        for i, h in enumerate(hits, 1):
            piece = "【资料%d | 来源: %s】\n%s" % (i, h["source"], h["text"])
            if used + len(piece) > max_chars:
                piece = piece[:max(200, max_chars - used)] + "…(截断)"
            parts.append(piece)
            used += len(piece)
            if used >= max_chars:
                break
        return "\n\n".join(parts), len(parts)


_RETR = None


def get_retriever():
    global _RETR
    if _RETR is None:
        _RETR = Retriever()
    return _RETR
