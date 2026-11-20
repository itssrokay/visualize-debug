#!/usr/bin/env python3
"""Turn a real failure into an animated flow scene: a stack trace (Java/Kotlin, Python, JS/TS, Go, C#) or a
distributed trace (Jaeger JSON or OTLP JSON). Frames are mapped onto your modules; framework frames collapse.

usage:
  trace_to_scene.py --stack trace.txt --repo REPO [--level class|module] [--max-nodes 14] -o scene.json
  trace_to_scene.py --otel trace.json [--slow-ms 200] [--internal] -o scene.json
  (use --stack - to read stdin)

Then edit the JSON (labels, detail, payloads) and build it with build_scene.py.
"""
import argparse, json, os, re, sys
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import map_modules as mm  # noqa: E402

FRAMEWORKS = [
    ("org.springframework.web", "Spring MVC"), ("org.springframework.security", "Spring Security"), ("org.springframework.transaction", "Spring Tx"),
    ("org.springframework.data", "Spring Data"), ("org.springframework.aop", "Spring AOP proxy"), ("org.springframework.cglib", "Spring AOP proxy"),
    ("org.springframework.kafka", "Spring Kafka"), ("org.springframework.boot", "Spring Boot"), ("org.springframework", "Spring"),
    ("org.apache.ibatis", "MyBatis"), ("org.mybatis", "MyBatis"), ("com.baomidou", "MyBatis-Plus"), ("jakarta.servlet", "Servlet API"),
    ("javax.servlet", "Servlet API"), ("org.jooq", "jOOQ"), ("org.springframework.jdbc", "Spring JDBC"), ("redis.clients", "Jedis"), ("io.lettuce", "Lettuce"),
    ("org.elasticsearch", "Elasticsearch client"), ("co.elastic", "Elasticsearch client"), ("com.mongodb", "Mongo driver"), ("org.mongodb", "Mongo driver"),
    ("org.apache.http", "Apache HttpClient"), ("software.amazon", "AWS SDK"), ("com.amazonaws", "AWS SDK"), ("com.google.cloud", "GCP SDK"),
    ("org.apache.catalina", "Tomcat"), ("org.apache.coyote", "Tomcat"), ("org.apache.tomcat", "Tomcat"), ("io.undertow", "Undertow"),
    ("org.eclipse.jetty", "Jetty"), ("org.hibernate", "Hibernate"), ("jakarta.persistence", "JPA"), ("javax.persistence", "JPA"),
    ("com.zaxxer.hikari", "HikariCP"), ("org.postgresql", "Postgres driver"), ("com.mysql", "MySQL driver"), ("oracle.jdbc", "Oracle driver"),
    ("io.netty", "Netty"), ("reactor.", "Reactor"), ("io.grpc", "gRPC"), ("feign.", "Feign"), ("okhttp3", "OkHttp"),
    ("org.apache.kafka", "Kafka client"), ("com.fasterxml.jackson", "Jackson"), ("io.micrometer", "Micrometer"), ("io.opentelemetry", "OpenTelemetry"),
    ("jdk.internal.reflect", "JDK reflection"), ("java.lang.reflect", "JDK reflection"), ("sun.reflect", "JDK reflection"),
    ("java.util.concurrent", "JDK threads"), ("java.lang.Thread", "JDK threads"), ("java.", "JDK"), ("javax.", "JDK"), ("jdk.", "JDK"), ("sun.", "JDK"),
    ("kotlin.", "Kotlin stdlib"), ("kotlinx.coroutines", "Coroutines"), ("scala.", "Scala stdlib"), ("org.junit", "JUnit"),
    ("System.", ".NET"), ("Microsoft.AspNetCore", "ASP.NET Core"), ("Microsoft.EntityFrameworkCore", "EF Core"), ("Microsoft.", ".NET"),
]
JAVA_FRAME = re.compile(r"^\s*at\s+(?:[\w.\-]+(?:@[\w.\-]+)?/)*([\w.$<>\-]+)\.([\w$<>\-]+)\(([^)]*)\)")
JAVA_HEAD = re.compile(r"^(?:Exception in thread \"[^\"]*\"\s+|Caused by:\s+|Suppressed:\s+)?([\w.$]+(?:Exception|Error|Throwable|Failure|Fault)[\w$]*)(?::\s*(.*))?$")
CS_FRAME = re.compile(r"^\s*at\s+([\w.`<>\[\]+]+)\.([\w<>`]+)\(.*?\)(?:\s+in\s+(.+?):line\s+(\d+))?")
PY_FRAME = re.compile(r'^\s*File "([^"]+)", line (\d+), in (\S+)')
JS_FRAME = re.compile(r"^\s*at\s+(?:(?:async\s+)?(.+?)\s+\()?(.+?):(\d+):(\d+)\)?\s*$")
GO_FUNC = re.compile(r"^([\w./\-]+?)\.(\(?\*?[\w]+\)?\.?[\w]*)\(.*\)$")
GO_FILE = re.compile(r"^\s+(\S+\.go):(\d+)")


def clip(s, n=38):
    s = " ".join(str(s).split())
    if len(s) <= n:
        return s
    cut = s[:n - 1]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > n * 0.6 else cut).rstrip(" ,;:·-") + "…"


def framework_name(qual):
    for pre, name in FRAMEWORKS:
        if qual.startswith(pre):
            return name
    parts = re.split(r"[./]", qual)
    return ".".join(parts[:2]) if len(parts) > 2 else qual


class Frame:
    def __init__(self, qual, method, file=None, line=None, raw=""):
        self.qual, self.method, self.file, self.line, self.raw = qual, method, file, line, raw
        self.src = None  # matched repo File

    @property
    def simple(self):
        q = self.qual.split("$")[0]
        return q.split(".")[-1] if "." in q else q


def parse_java(text):
    blocks, cur = [], None
    for line in text.splitlines():
        s = line.rstrip()
        h = JAVA_HEAD.match(s.strip())
        if h and not s.strip().startswith("at "):
            cur = {"type": h.group(1), "msg": (h.group(2) or "").strip(), "frames": [], "more": 0}
            blocks.append(cur); continue
        m = JAVA_FRAME.match(s)
        if m and cur is not None:
            fl = m.group(3)
            fm = re.match(r"([^:]+):(\d+)", fl)
            cur["frames"].append(Frame(m.group(1), m.group(2), fm.group(1) if fm else None, int(fm.group(2)) if fm else None, s.strip()))
            continue
        mo = re.match(r"^\s*\.\.\.\s*(\d+)\s+more", s)
        if mo and cur is not None:
            cur["more"] = int(mo.group(1))
            continue
        c = CS_FRAME.match(s)
        if c:
            if cur is None:
                cur = {"type": "Exception", "msg": "", "frames": [], "more": 0}; blocks.append(cur)
            cur["frames"].append(Frame(c.group(1), c.group(2), c.group(3), int(c.group(4)) if c.group(4) else None, s.strip()))
    blocks = [b for b in blocks if b["frames"] or b is blocks[0]]
    if not blocks or not any(b["frames"] for b in blocks):
        return None
    full = []
    for i, b in enumerate(blocks):
        f = list(b["frames"])
        if b["more"] and i:
            f += full[i - 1][-b["more"]:]
        full.append(f)
    root = blocks[-1]
    return {"lang": "jvm", "type": root["type"], "msg": root["msg"], "stack": list(reversed(full[-1])),
            "outer": blocks[0]["type"] if len(blocks) > 1 else None, "outer_msg": blocks[0]["msg"] if len(blocks) > 1 else None,
            "wrap_frame": full[0][0] if len(blocks) > 1 and full[0] else None, "chain": [b["type"] for b in blocks]}


def parse_python(text):
    tbs, cur, last_exc = [], None, None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("Traceback (most recent call last)"):
            cur = {"frames": [], "type": None, "msg": ""}; tbs.append(cur); continue
        m = PY_FRAME.match(line)
        if m and cur is not None:
            code = lines[i + 1].strip() if i + 1 < len(lines) and not PY_FRAME.match(lines[i + 1]) else ""
            fr = Frame(m.group(1), m.group(3), m.group(1), int(m.group(2)), code)
            cur["frames"].append(fr); continue
        em = re.match(r"^([\w.]+(?:Error|Exception|Exit|Interrupt|Warning|Failure)[\w]*|[\w.]+\.\w+):?\s*(.*)$", line)
        if em and cur is not None and cur["type"] is None and not line.startswith(" "):
            cur["type"], cur["msg"] = em.group(1), em.group(2)
    tbs = [t for t in tbs if t["frames"]]
    if not tbs:
        return None
    t = tbs[-1]
    return {"lang": "python", "type": t["type"] or "Exception", "msg": t["msg"], "stack": t["frames"],
            "outer": None, "chain": [x["type"] for x in tbs], "wrap_frame": None}


def parse_js(text):
    lines = text.splitlines()
    head = next((l for l in lines if l.strip() and not l.strip().startswith("at ")), "Error")
    hm = re.match(r"^\s*(?:Uncaught\s+)?([\w.$]*(?:Error|Exception)[\w$]*)(?::\s*(.*))?", head)
    frames = []
    for l in lines:
        m = JS_FRAME.match(l)
        if m:
            fn = (m.group(1) or "(anonymous)").split(" [as ")[0]
            path = re.sub(r"^file://", "", m.group(2))
            frames.append(Frame(path, fn, path, int(m.group(3)), l.strip()))
    if not frames:
        return None
    return {"lang": "js", "type": hm.group(1) if hm else "Error", "msg": (hm.group(2) if hm else head).strip() if hm else head,
            "stack": list(reversed(frames)), "outer": None, "chain": [], "wrap_frame": None}


def parse_go(text):
    lines = text.splitlines()
    pm = next((re.match(r"^panic:\s*(.*)", l) for l in lines if l.startswith("panic:")), None)
    frames = []
    for i, l in enumerate(lines[:-1]):
        fm = GO_FUNC.match(l.strip()) if not l.startswith(("\t", " ", "goroutine", "panic")) else None
        nm = GO_FILE.match(lines[i + 1])
        if fm and nm:
            frames.append(Frame(fm.group(1), fm.group(2).strip("()*."), nm.group(1), int(nm.group(2)), l.strip()))
    if not frames:
        return None
    frames = [f for f in frames if not (f.qual == "panic" or f.qual.startswith("runtime") and f.method in ("gopanic", "panic", "goexit", "sigpanic", "panicmem"))] or frames
    return {"lang": "go", "type": "panic", "msg": pm.group(1) if pm else "", "stack": list(reversed(frames)), "outer": None, "chain": [], "wrap_frame": None}


def parse_stack(text):
    if "Traceback (most recent call last)" in text:
        return parse_python(text)
    if re.search(r"^goroutine \d+ \[", text, re.M):
        return parse_go(text)
    if JAVA_FRAME.search(text) and re.search(r"\.(java|kt|scala|groovy):\d+\)|Native Method|Unknown Source", text):
        return parse_java(text)
    if re.search(r"\s+in\s+.+:line\s+\d+", text):
        return parse_java(text)
    return parse_js(text) or parse_java(text)


def load_repo(repo, group_by, depth):
    files, builds = mm.walk(repo, True, [])
    for f in files:
        f.unit = ""
        raw = mm.read(f.path)
        if f.lang in mm.JVM or f.lang == "csharp":
            m = re.search(r"(?m)^\s*(?:package|namespace)\s+([\w.]+)", raw)
            f.unit = m.group(1) if m else ""
            f.types = re.findall(r"\b(?:class|interface|enum|record|object|struct)\s+(\w+)", raw) or [os.path.splitext(os.path.basename(f.rel))[0]]
        for role, rx in mm.ROLE_PATTERNS:
            if rx.search(raw):
                f.roles[role] += 1
    gb, _ = mm.assign_keys(files, builds, group_by)
    for f in files:
        f.module = "/".join(f.key[:depth]) or "(root)"
    fqn, by_rel = {}, {}
    for f in files:
        by_rel[f.rel] = f
        for t in f.types:
            fqn.setdefault(f"{f.unit}.{t}" if f.unit else t, f)
    return files, fqn, by_rel


def match_frame(fr, lang, fqn, files, by_rel):
    if lang == "jvm":
        q = fr.qual.split("$")[0]
        if "$$" in fr.qual or re.search(r"\$Proxy\d+|CGLIB|ByteBuddy|HibernateProxy", fr.qual):
            return None
        hit = fqn.get(q)
        if hit:
            return hit
        if fr.file:  # fall back to a file name + package dir match
            pkgdir = "/".join(q.split(".")[:-1])
            for f in files:
                if f.rel.endswith(pkgdir + "/" + fr.file):
                    return f
        return None
    if not fr.file:
        return None
    p = fr.file.replace("\\", "/")
    if "/node_modules/" in p or "site-packages/" in p or "dist-packages/" in p or p.startswith(("node:", "internal/", "<")):
        return None
    best, blen = None, 0
    for f in files:
        rel = f.rel
        if p.endswith("/" + rel) or p == rel:
            if len(rel) > blen:
                best, blen = f, len(rel)
    if best:
        return best
    tail = "/".join(p.split("/")[-2:])
    cands = [f for f in files if f.rel.endswith(tail)]
    return cands[0] if len(cands) == 1 else None


def bucket(fr, lang):
    p = (fr.file or "").replace("\\", "/")
    if lang == "jvm":
        if "$$" in fr.qual or "$Proxy" in fr.qual or "CGLIB" in fr.qual:
            return "Spring AOP proxy" if "Spring" in fr.qual or "CGLIB" in fr.qual else "Proxy"
        return framework_name(fr.qual)
    if lang == "python":
        m = re.search(r"(?:site|dist)-packages/([\w\-]+)", p)
        if m:
            return m.group(1)
        return "Python stdlib" if re.search(r"/lib/python\d", p) else (os.path.basename(p) or "python")
    if lang == "js":
        m = re.search(r"node_modules/((?:@[\w.\-]+/)?[\w.\-]+)", p)
        if m:
            return m.group(1)
        return "Node runtime" if p.startswith(("node:", "internal/")) or "(" in p else "js"
    if lang == "go":
        if fr.qual.startswith(("runtime", "net/http", "sync", "reflect", "syscall")):
            return "Go runtime" if fr.qual.startswith(("runtime", "sync", "reflect", "syscall")) else "net/http"
        return "/".join(fr.qual.split("/")[:3])
    return "framework"


def snippet(f, line, ctx=1):
    if not f or not line:
        return None
    lines = mm.read(f.path).splitlines()
    lo, hi = max(1, line - ctx), min(len(lines), line + ctx)
    w = len(str(hi))
    return "\n".join(f"{'→' if i == line else ' '} {i:>{w}} │ {lines[i - 1].rstrip()}" for i in range(lo, hi + 1))


def kind_of(f):
    if not f:
        return "class"
    top = next((r for r, _ in f.roles.most_common() if r in mm.KIND_OF_ROLE), None)
    return mm.KIND_OF_ROLE.get(top, "class") if top else "class"


def stack_scene(tr, repo, a):
    files, fqn, by_rel = load_repo(repo, a.group_by, a.depth) if repo else ([], {}, {})
    lang = tr["lang"]
    visits = []
    PLUMBING = {"JDK reflection", "Proxy", "Spring AOP proxy", "JDK", "JDK threads", "Kotlin stdlib", "Python stdlib", "Node runtime", "Go runtime"}
    for fr in tr["stack"]:
        f = match_frame(fr, lang, fqn, files, by_rel) if files else None
        fr.src = f
        if f:
            key = ("m:" + f.module) if a.level == "module" else ("c:" + (fr.qual.split("$")[0] if lang == "jvm" else f.rel))
            label = f.module.split("/")[-1] if a.level == "module" else (fr.simple if lang == "jvm" else os.path.basename(f.rel))
            kind, mod = ("module" if a.level == "module" else kind_of(f)), f.module
            if visits and visits[-1]["key"] == key:
                visits[-1]["frames"].append(fr)
            else:
                visits.append({"key": key, "label": label, "kind": kind, "module": mod, "file": f, "frames": [fr]})
        else:
            b = bucket(fr, lang)
            if visits and visits[-1]["kind"] == "framework":
                visits[-1]["frames"].append(fr)
                if b not in visits[-1]["buckets"]:
                    visits[-1]["buckets"].append(b)
            else:
                visits.append({"key": None, "label": b, "kind": "framework", "module": None, "file": None, "frames": [fr], "buckets": [b]})
    # drop pure plumbing hops (reflection, proxies, async ticks) sitting between two of your own frames
    cleaned = []
    for i, v in enumerate(visits):
        if v["kind"] == "framework" and 0 < i < len(visits) - 1 and all(b in PLUMBING for b in v["buckets"]):
            continue
        if cleaned and cleaned[-1]["key"] == v["key"] and v["kind"] != "framework":
            cleaned[-1]["frames"] += v["frames"]; continue
        cleaned.append(v)
    visits = cleaned
    for i, v in enumerate(visits):
        if v["kind"] != "framework":
            continue
        real = [b for b in v["buckets"] if b not in PLUMBING] or v["buckets"]
        primary = real[-1]  # the layer closest to where the call goes next (or where it blew up)
        v["key"], v["label"] = "f:" + primary, primary
        v["via"] = " → ".join(v["buckets"])
    # trim: keep at most a couple of leading framework visits (thread/servlet plumbing)
    first_proj = next((i for i, v in enumerate(visits) if v["kind"] != "framework"), 0)
    if first_proj > 1:
        dropped = sum(len(v["frames"]) for v in visits[:first_proj - 1])
        visits = visits[first_proj - 1:]
        visits[0]["note"] = f"{dropped} earlier frames (thread start, server plumbing) hidden"
    # node table
    nodes = OrderedDict()
    for v in visits:
        if v["key"] not in nodes:
            n = {"id": re.sub(r"[^\w.\-/:]", "_", v["key"])[:80], "label": v["label"][:28], "kind": v["kind"]}
            if v["module"]:
                n["module"] = v["module"].split("/")[-1] if a.level != "module" else None
                if n["module"] is None:
                    n.pop("module")
            if v["file"]:
                n["files"] = [v["file"].rel]
            nodes[v["key"]] = n
    # dedupe labels
    seen = {}
    for n in nodes.values():
        seen[n["label"]] = seen.get(n["label"], 0) + 1
    for k, n in nodes.items():
        if seen[n["label"]] > 1 and k.startswith("c:"):
            n["label"] = (k[2:].split(".")[-2] + "." if lang == "jvm" and "." in k[2:] else "") + n["label"]
    if len(nodes) > a.max_nodes and a.level == "class" and files:
        print(f"note: {len(nodes)} classes > --max-nodes {a.max_nodes}; switching to --level module", file=sys.stderr)
        a.level = "module"
        return stack_scene(tr, repo, a)
    steps = []

    def ref(fr):
        f = fr.src
        return f"{f.rel}:{fr.line}" if f and fr.line else (f"{fr.file}:{fr.line}" if fr.file and fr.line else None)

    for i in range(1, len(visits)):
        A, B = visits[i - 1], visits[i]
        call_site, callee = A["frames"][-1], B["frames"][0]
        methods = [f"{x.simple if lang == 'jvm' else ''}{'.' if lang == 'jvm' else ''}{x.method}" for x in B["frames"]]
        label = (callee.method + "()") if B["kind"] != "framework" else f"via {B['label']}" + (f" (+{len(B['buckets']) - 1})" if len(B['buckets']) > 1 else "")
        if B["kind"] != "framework" and a.level == "module":
            label = f"{callee.simple}.{callee.method}()" if lang == "jvm" else callee.method + "()"
        st = {"from": nodes[A["key"]]["id"], "to": nodes[B["key"]]["id"], "kind": "call", "label": label[:38],
              "detail": (f"framework layers: {B['via']}\n" if B["kind"] == "framework" else "") + " → ".join(methods[:8]) + (f" … +{len(methods) - 8}" if len(methods) > 8 else ""),
              "code": [r for r in (ref(call_site), ref(callee)) if r]}
        sn = snippet(call_site.src, call_site.line) if call_site.src else (call_site.raw if lang == "python" and call_site.raw else None)
        if sn:
            st["snippet"] = sn
        steps.append(st)
    exc = tr["type"].split(".")[-1]
    msg = (tr["msg"] or "").strip()
    short = exc if len(exc) <= 34 else re.sub(r"(Exception|Error)$", "", exc)[:34]
    last = visits[-1]
    top = last["frames"][-1]
    err_detail = f"{tr['type']}: {msg}" if msg else tr["type"]
    if tr.get("chain") and len(tr["chain"]) > 1:
        err_detail += "\nexception chain: " + " → caused by → ".join(c.split(".")[-1] for c in tr["chain"] if c)
    sn = snippet(top.src, top.line, 2) if top.src else (top.raw if lang == "python" else None)
    if steps:
        steps[-1]["kind"] = "error"
        steps[-1]["label"] = short
        steps[-1]["detail"] = err_detail + "\n\nframes inside: " + steps[-1]["detail"]
        if sn:
            steps[-1]["snippet"] = sn
        if ref(top):
            steps[-1]["code"] = [ref(top)] + [c for c in steps[-1]["code"] if c != ref(top)]
    else:
        n = nodes[last["key"]]
        steps.append({"from": n["id"], "to": n["id"], "kind": "error", "label": short, "detail": err_detail,
                      "code": [ref(top)] if ref(top) else [], **({"snippet": sn} if sn else {})})
    # wrapped / rethrown: walk back to where the outer exception was created
    if tr.get("outer") and tr["outer"] != tr["type"]:
        back = next((v for v in reversed(visits[:-1]) if v["kind"] != "framework"), None)
        if back and back["key"] != last["key"]:
            outer = tr["outer"].split(".")[-1]
            outer = outer if len(outer) <= 26 else re.sub(r"(Exception|Error)$", "", outer)[:26]
            steps.append({"from": nodes[last["key"]]["id"], "to": nodes[back["key"]]["id"], "kind": "return",
                          "label": ("rethrown as " + outer)[:38], "detail": f"{tr['outer']}: {(tr.get('outer_msg') or '').strip()}\n"
                          f"This is the exception your logs show first; the root cause is the {exc} above.",
                          "code": [ref(back["frames"][-1])] if ref(back["frames"][-1]) else []})
    title = exc
    sc = {"id": a.id or "crash", "title": title, "subtitle": (msg[:110] + " · " if msg else "") + f"{len(tr['stack'])} frames → {len(visits)} hops",
          "description": "The path the code took up to the failure. Dashed boxes are framework code; red is where it broke.",
          "nodes": list(nodes.values()), "steps": steps}
    if visits and visits[0].get("note"):
        sc["nodes"][0]["note"] = visits[0]["note"]
    return sc


def otel_scene(data, a):
    spans, svc_of = [], {}
    if isinstance(data, dict) and "data" in data:  # Jaeger
        tr = data["data"][0]
        procs = {k: v.get("serviceName", k) for k, v in tr.get("processes", {}).items()}
        for s in tr["spans"]:
            parent = next((r["spanID"] for r in s.get("references", []) if r.get("refType") == "CHILD_OF"), None)
            tags = {t["key"]: t.get("value") for t in s.get("tags", [])}
            err = tags.get("error") in (True, "true") or str(tags.get("otel.status_code", "")).upper() == "ERROR"
            spans.append({"id": s["spanID"], "parent": parent, "name": s["operationName"], "svc": procs.get(s.get("processID"), "?"),
                          "start": s["startTime"] / 1000.0, "dur": s["duration"] / 1000.0, "err": err,
                          "status": tags.get("http.status_code") or tags.get("http.response.status_code"), "msg": tags.get("error.message") or tags.get("exception.message")})
    else:  # OTLP JSON
        rs = data.get("resourceSpans") or data.get("resource_spans") or []
        for r in rs:
            attrs = {x["key"]: list(x["value"].values())[0] for x in r.get("resource", {}).get("attributes", [])}
            svc = attrs.get("service.name", "?")
            for ss in r.get("scopeSpans") or r.get("instrumentationLibrarySpans") or []:
                for s in ss.get("spans", []):
                    at = {x["key"]: list(x["value"].values())[0] for x in s.get("attributes", [])}
                    st = int(s.get("startTimeUnixNano", 0)) / 1e6
                    en = int(s.get("endTimeUnixNano", 0)) / 1e6
                    code = (s.get("status") or {}).get("code")
                    evmsg = next((x["value"].get("stringValue") for ev in s.get("events", []) for x in ev.get("attributes", []) if x["key"] == "exception.message"), None)
                    spans.append({"id": s.get("spanId"), "parent": s.get("parentSpanId") or None, "name": s.get("name"), "svc": svc,
                                  "start": st, "dur": en - st, "err": code in (2, "STATUS_CODE_ERROR", "ERROR"),
                                  "status": at.get("http.status_code") or at.get("http.response.status_code"), "msg": evmsg or (s.get("status") or {}).get("message")})
    if not spans:
        sys.exit("no spans found")
    by = {s["id"]: s for s in spans}
    root0 = next((s for s in spans if not by.get(s["parent"])), spans[0])
    # default: a child span is "slow" when it eats 40%+ of the whole request (min 100 ms)
    slow = a.slow_ms if a.slow_ms is not None else max(100.0, 0.4 * root0["dur"])
    events = []
    for s in spans:
        p = by.get(s["parent"])
        if not p:
            continue
        if p["svc"] == s["svc"] and not a.internal:
            continue
        events.append((s["start"], 0, s, p))
        if p["svc"] != s["svc"]:
            events.append((s["start"] + s["dur"], 1, s, p))
    events.sort(key=lambda e: (e[0], e[1]))
    nodes = OrderedDict()
    root = next((s for s in spans if not by.get(s["parent"])), spans[0])
    nodes[root["svc"]] = {"id": root["svc"], "label": root["svc"][:28], "kind": "service", "note": f"root span: {root['name']} ({root['dur']:.0f} ms)"}
    steps = []
    for t, typ, s, p in events:
        for svc in (p["svc"], s["svc"]):
            if svc not in nodes:
                nodes[svc] = {"id": svc, "label": svc[:28], "kind": "db" if re.search(r"(sql|postgres|mysql|mongo|redis|db)", svc, re.I) else "service"}
        if typ == 0:
            k = "error" if s["err"] and a.errors_on_call else ("slow" if s["dur"] >= slow else "call")
            if p["svc"] == s["svc"]:
                k = "self" if k == "call" else k
            steps.append({"from": p["svc"], "to": s["svc"], "kind": k, "label": clip(s["name"]), "ms": round(s["dur"], 1),
                          "detail": f"{s['name']} · {s['dur']:.1f} ms · starts +{s['start'] - root['start']:.1f} ms" + (f"\nERROR: {s['msg']}" if s["err"] and s["msg"] else "")})
        else:
            lab = (str(s["status"]) if s["status"] else ("error" if s["err"] else "ok"))
            steps.append({"from": s["svc"], "to": p["svc"], "kind": "error" if s["err"] else "return", "label": clip(lab + (" · " + s["msg"] if s["err"] and s["msg"] else "")),
                          "ms": round(s["dur"], 1), "detail": f"{s['name']} returned {lab} after {s['dur']:.1f} ms" + (f"\n{s['msg']}" if s["msg"] else "")})
    if len(steps) > a.max_steps:
        keep = [x for x in steps if x["kind"] in ("error", "slow")]
        rest = [x for x in steps if x["kind"] not in ("error", "slow")]
        steps = sorted(keep + rest[:max(0, a.max_steps - len(keep))], key=steps.index)
        print(f"note: trimmed to {len(steps)} steps (kept all slow/error)", file=sys.stderr)
    errs = sum(1 for s in spans if s["err"])
    return {"id": a.id or "trace", "title": f"{root['name']} — {root['dur']:.0f} ms", "subtitle": f"{len(spans)} spans · {len(nodes)} services · slow ≥ {slow:.0f} ms" + (f" · {errs} errors" if errs else ""),
            "nodes": list(nodes.values()), "steps": steps}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stack")
    ap.add_argument("--otel")
    ap.add_argument("--repo")
    ap.add_argument("--level", default="class", choices=["class", "module"])
    ap.add_argument("--group-by", default="auto", choices=["auto", "package", "dir", "build"])
    ap.add_argument("--depth", type=int, default=1)
    ap.add_argument("--max-nodes", type=int, default=14)
    ap.add_argument("--max-steps", type=int, default=50)
    ap.add_argument("--slow-ms", type=float)
    ap.add_argument("--internal", action="store_true", help="otel: include spans inside the same service")
    ap.add_argument("--errors-on-call", action="store_true", help="otel: colour the call (not the return) of failing spans")
    ap.add_argument("--id")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    if a.otel:
        with open(a.otel, encoding="utf-8") as f:
            sc = otel_scene(json.load(f), a)
    elif a.stack:
        text = sys.stdin.read() if a.stack == "-" else mm.read(a.stack)
        tr = parse_stack(text)
        if not tr:
            sys.exit("could not find stack frames in the input")
        if not a.repo:
            print("note: no --repo, so every frame is treated as framework code; pass --repo to map frames to your modules", file=sys.stderr)
        sc = stack_scene(tr, a.repo, a)
    else:
        sys.exit("need --stack or --otel")
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(sc, f, indent=1, ensure_ascii=False)
    print(f"wrote {a.out}: {len(sc['nodes'])} nodes, {len(sc['steps'])} steps")
    for s in sc["steps"]:
        print(f"  {s['kind']:<6} {s['from']} → {s['to']}: {s['label']}")


if __name__ == "__main__":
    main()
