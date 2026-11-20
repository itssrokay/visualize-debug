#!/usr/bin/env python3
"""Draft a flow scene by statically following calls from an entry point (Java / Kotlin, Spring-style code).

usage: flow_from_entry.py REPO --entry "POST /owners/new"          # an HTTP endpoint (see map_modules.py output)
       flow_from_entry.py REPO --method OwnerController.processCreationForm
       [--max-depth 4] [--max-nodes 12] [--returns] [--group-by auto --depth 1] -o flow.json

It follows calls on injected fields, constructor params, locals and parameters whose type is a class in the repo
(interfaces resolve to their single implementation), Spring Data repositories (→ a database node), HTTP clients
(RestTemplate / WebClient / Feign → external node) and Kafka/Rabbit/JMS templates (→ queue, async).
Branches are not evaluated: every call in the method body is listed in source order. Treat the result as a DRAFT:
cut what doesn't happen on the path you are explaining, then add labels, payloads and detail.
"""
import argparse, json, os, re, sys
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import map_modules as mm  # noqa: E402

ACCESSOR = re.compile(r"^(get|set|is|has|with)[A-Z]|^(builder|build|toString|equals|hashCode|valueOf|of|size|isEmpty|stream|add|put|remove|contains|forEach|map|filter|collect|orElse\w*|ifPresent)$")
COLLAB = re.compile(r"(Service|ServiceImpl|Client|Gateway|Handler|Manager|Facade|Processor|Publisher|Producer|Consumer|Repository|Dao|DAO|Mapper|Adapter|Provider|Validator|Resolver|Engine|Executor|Listener|Port|UseCase|Interactor|Command|Store)$")
KW = {"if", "for", "while", "switch", "catch", "return", "new", "throw", "synchronized", "super", "this", "else", "try", "assert"}
DATA_REPO = re.compile(r"\b(?:extends|:)\s*(?:Jpa|Crud|PagingAndSorting|Mongo|Reactive\w*|Elasticsearch|R2dbc|Neo4j|Redis)?Repository\s*<|@Repository\b|@Mapper\b|interface\s+\w+(?:Mapper|Dao)\b")
QUEUE_T = re.compile(r"^(KafkaTemplate|RabbitTemplate|AmqpTemplate|JmsTemplate|StreamBridge|ApplicationEventPublisher|KafkaProducer|SqsTemplate)$")
HTTP_T = re.compile(r"^(RestTemplate|WebClient|RestClient|HttpClient|OkHttpClient|CloseableHttpClient)$")
CACHE_T = re.compile(r"^(RedisTemplate|StringRedisTemplate|CacheManager|Cache|ReactiveRedisTemplate)$")


class Cls:
    def __init__(self, f, name, src, start):
        self.f, self.name, self.src, self.start = f, name, src, start
        self.fields, self.methods, self.supers, self.annos = {}, {}, [], ""
        self.is_iface = False


def match_brace(src, i):
    depth, j, n = 0, i, len(src)
    in_s = None
    while j < n:
        c = src[j]
        if in_s:
            if c == "\\":
                j += 2; continue
            if c == in_s:
                in_s = None
        elif c in "\"'":
            in_s = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return n - 1


def strip_generics(t):
    t = re.sub(r"<.*>", "", t or "").replace("?", "").strip()
    return t.split(".")[-1].rstrip("[]")


def index(repo):
    files, _ = mm.walk(repo, False, [])
    classes = {}
    for f in files:
        if f.lang not in ("java", "kotlin"):
            continue
        raw = mm.read(f.path)
        src = mm.strip_comments(raw, f.lang)
        for m in re.finditer(r"((?:@\w+(?:\([^)]*\))?\s*)*)(?:public\s+|private\s+|protected\s+|abstract\s+|final\s+|static\s+|open\s+|data\s+|sealed\s+|internal\s+)*(class|interface|object)\s+(\w+)([^{]*)\{", src):
            name = m.group(3)
            c = Cls(f, name, src, m.start())
            c.is_iface = m.group(2) == "interface"
            c.annos = m.group(1) + src[max(0, m.start() - 300):m.start()]
            head = m.group(4)
            c.supers = [strip_generics(x) for x in re.split(r",|\bextends\b|\bimplements\b|:", head) if x.strip() and not x.strip().startswith("(")]
            # Kotlin primary constructor params: class X(private val repo: Repo)
            for pm in re.finditer(r"(?:val|var)\s+(\w+)\s*:\s*([\w.<>?, ]+?)[,)=]", head):
                c.fields[pm.group(1)] = strip_generics(pm.group(2))
            body_end = match_brace(src, m.end() - 1)
            body = src[m.end():body_end]
            c.body_off = m.end()
            # fields (Java): Type name;  / Type name = ...;
            for fm in re.finditer(r"(?m)^\s*(?:@\w+(?:\([^)]*\))?\s*)*(?:private|protected|public)?\s*(?:static\s+)?(?:final\s+)?([A-Z][\w.]*(?:<[^;=()]*>)?)\s+(\w+)\s*(?:=[^;]*)?;", body):
                c.fields[fm.group(2)] = strip_generics(fm.group(1))
            for fm in re.finditer(r"(?:private|protected|internal)?\s*(?:lateinit\s+)?va[lr]\s+(\w+)\s*:\s*([\w.<>?]+)", body):
                c.fields.setdefault(fm.group(1), strip_generics(fm.group(2)))
            # methods
            for mm_ in re.finditer(r"(?m)^[ \t]*(?:@\w+(?:\([^)]*\))?\s*)*(?:(?:public|private|protected|static|final|synchronized|abstract|default|override|open|suspend|internal)\s+)*(?:fun\s+(?:<[^>]*>\s*)?(\w+)\s*\(([^)]*)\)\s*(?::\s*([\w.<>?, ]+))?|([\w.<>\[\]?, ]+?)\s+(\w+)\s*\(([^)]*)\))\s*(?:throws [\w., ]+)?\s*(\{|=|;)", body):
                if mm_.group(1):
                    name_, params, ret = mm_.group(1), mm_.group(2), mm_.group(3) or "Unit"
                else:
                    ret, name_, params = mm_.group(4), mm_.group(5), mm_.group(6)
                if name_ in KW or (ret and ret.strip() in KW | {"else"}):
                    continue
                if mm_.group(7) == "{":
                    s0 = c.body_off + mm_.end() - 1
                    e0 = match_brace(src, s0)
                    mbody = src[s0:e0]
                else:
                    s0 = c.body_off + mm_.end(); mbody = ""
                ptypes = {}
                for p in [x.strip() for x in params.split(",") if x.strip()]:
                    p = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", p).replace("final ", "")
                    if ":" in p:
                        n_, t_ = p.split(":", 1); ptypes[n_.strip().split(" ")[-1]] = strip_generics(t_)
                    else:
                        bits = p.split()
                        if len(bits) >= 2:
                            ptypes[bits[-1]] = strip_generics(bits[-2])
                line = src.count("\n", 0, c.body_off + mm_.start()) + 1
                c.methods.setdefault(name_, {"ret": strip_generics(ret), "body": mbody, "body_at": s0, "params": ptypes, "line": line})
            classes.setdefault(name, c)
    impls = {}
    for c in classes.values():
        for s in c.supers:
            impls.setdefault(s, []).append(c)
    return files, classes, impls


def role(c):
    a = c.annos
    if re.search(r"@(RestController|Controller)\b", a):
        return "controller"
    if DATA_REPO.search(c.src[c.start:c.start + 400] + a) or c.name.endswith(("Repository", "Mapper", "Dao", "DAO")):
        return "repo"
    if re.search(r"@Service\b", a) or c.name.endswith(("Service", "ServiceImpl")):
        return "service"
    if re.search(r"@(Component|Configuration)\b", a):
        return "component"
    if re.search(r"@FeignClient\b", a):
        return "external"
    return "class"


def resolve(t, classes, impls):
    if t in classes:
        c = classes[t]
        if c.is_iface and t in impls:
            cands = [x for x in impls[t] if not x.is_iface]
            pick = next((x for x in cands if x.name == t + "Impl"), None) or (cands[0] if len(cands) == 1 else None)
            return pick or c
        return c
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo")
    ap.add_argument("--entry", help='"METHOD /path" from the endpoint list')
    ap.add_argument("--method", help="Class.method")
    ap.add_argument("--max-depth", type=int, default=4)
    ap.add_argument("--max-nodes", type=int, default=12)
    ap.add_argument("--max-steps", type=int, default=40)
    ap.add_argument("--returns", action="store_true", help="add a return step after each call")
    ap.add_argument("--all-calls", action="store_true", help="also follow getters/setters and calls on data classes")
    ap.add_argument("--group-by", default="auto")
    ap.add_argument("--depth", type=int, default=1)
    ap.add_argument("--id")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    files, classes, impls = index(a.repo)
    # module of each file
    _, builds = mm.walk(a.repo, False, [])
    jf = [f for f in files]
    for f in jf:
        raw = mm.read(f.path)
        m = re.search(r"(?m)^\s*package\s+([\w.]+)", raw)
        f.unit = m.group(1) if m else ""
    mm.assign_keys(jf, builds, a.group_by)
    modof = {f.rel: ("/".join(f.key[:a.depth]) or "(root)") for f in jf}

    entry_label, start = None, None
    if a.entry:
        verb, _, path = a.entry.partition(" ")
        for f in jf:
            f.types = [c.name for c in classes.values() if c.f is f] or f.types
            mm.endpoints(f, mm.strip_comments(mm.read(f.path), f.lang))
            for e in f.endpoints:
                if e["path"] == path.strip() and (e["method"] == verb.upper() or verb.upper() == "ANY"):
                    start = tuple(e["handler"].split(".", 1)); entry_label = f"{e['method']} {e['path']}"
                    break
            if start:
                break
        if not start:
            sys.exit(f"endpoint {a.entry!r} not found. Run map_modules.py to list endpoints.")
    elif a.method:
        start = tuple(a.method.split(".", 1))
    else:
        sys.exit("need --entry or --method")
    c0 = classes.get(start[0])
    if not c0 or start[1] not in c0.methods:
        sys.exit(f"{'.'.join(start)} not found")

    nodes, steps = OrderedDict(), []

    def node_for(c=None, special=None, label=None, kind=None):
        key = special or c.name
        if key not in nodes:
            if len(nodes) >= a.max_nodes:
                return None
            if c:
                n = {"id": c.name, "label": c.name[:28], "kind": role(c), "module": modof.get(c.f.rel, "").split("/")[-1] or None,
                     "files": [c.f.rel]}
                if not n["module"]:
                    n.pop("module")
            else:
                n = {"id": special, "label": label, "kind": kind}
            nodes[key] = n
        return nodes[key]["id"]

    if entry_label:
        nodes["__client"] = {"id": "client", "label": "Client", "kind": "client"}
        steps.append({"from": "client", "to": node_for(c0), "kind": "call", "label": entry_label,
                      "code": [f"{c0.f.rel}:{c0.methods[start[1]]['line']}"], "detail": f"handled by {c0.name}.{start[1]}()"})
    else:
        node_for(c0)
    seen = set()

    def walk(c, meth, depth):
        info = c.methods.get(meth)
        if not info or not info["body"] or (c.name, meth) in seen or len(steps) >= a.max_steps:
            return
        seen.add((c.name, meth))
        body, base = info["body"], info["body_at"]
        locals_ = dict(info["params"])
        for lm in re.finditer(r"\b([A-Z][\w.]*(?:<[^;=()]*>)?)\s+(\w+)\s*=", body):
            locals_[lm.group(2)] = strip_generics(lm.group(1))
        for lm in re.finditer(r"\bva[lr]\s+(\w+)\s*:\s*([\w.<>?]+)", body):
            locals_[lm.group(1)] = strip_generics(lm.group(2))
        for cm in re.finditer(r"(?<![\w.])(this\.)?(\w+)\s*\.\s*(\w+)\s*\(|(?<![\w.])(\w+)\s*\(", body):
            if len(steps) >= a.max_steps:
                return
            line = c.src.count("\n", 0, base + cm.start()) + 1
            ref = f"{c.f.rel}:{line}"
            stmt = body[cm.start():body.find("\n", cm.start())].strip()[:120]
            if cm.group(4):  # bare call: same-class method
                name = cm.group(4)
                if name in KW or name not in c.methods or name == meth or (not a.all_calls and ACCESSOR.search(name) and len(c.methods[name]["body"]) < 120):
                    continue
                me = node_for(c)
                steps.append({"from": me, "to": me, "kind": "self", "label": f"{name}()", "code": [ref], "snippet": stmt})
                if depth < a.max_depth:
                    walk(c, name, depth + 1)
                continue
            recv, name = cm.group(2), cm.group(3)
            t = c.fields.get(recv) or locals_.get(recv)
            if not t:
                continue
            me = node_for(c)
            if QUEUE_T.match(t):
                q = node_for(special="queue", label="message broker", kind="queue")
                if q:
                    steps.append({"from": me, "to": q, "kind": "async", "label": f"{recv}.{name}()", "code": [ref], "snippet": stmt})
                continue
            if HTTP_T.match(t) or re.search(r"(webClient|restTemplate|restClient|httpClient)", recv, re.I):
                x = node_for(special="http", label="remote HTTP service", kind="external")
                if x:
                    steps.append({"from": me, "to": x, "kind": "call", "label": f"{name}()", "code": [ref], "snippet": stmt})
                    if a.returns:
                        steps.append({"from": x, "to": me, "kind": "return", "label": "response"})
                continue
            if CACHE_T.match(t):
                x = node_for(special="cache", label="cache", kind="cache")
                if x:
                    steps.append({"from": me, "to": x, "kind": "call", "label": f"{name}()", "code": [ref], "snippet": stmt})
                continue
            tc = resolve(t, classes, impls)
            if not tc or tc is c:
                continue
            if not a.all_calls and role(tc) == "class" and not COLLAB.search(tc.name) and not COLLAB.search(t):
                continue  # getters/setters and calls on data objects are noise for a flow
            tid = node_for(tc)
            if not tid:
                continue
            m_info = tc.methods.get(name) or classes.get(t, tc).methods.get(name)
            steps.append({"from": me, "to": tid, "kind": "call", "label": f"{name}()", "code": [ref] + ([f"{tc.f.rel}:{m_info['line']}"] if m_info else []),
                          "snippet": stmt})
            if role(tc) == "repo" and (not m_info or not m_info["body"]):
                db = node_for(special="db", label="database", kind="db")
                if db:
                    steps.append({"from": tid, "to": db, "kind": "call", "label": "SQL", "detail": f"{tc.name}.{name}() runs a query (generated or mapped)"})
                    if a.returns:
                        steps.append({"from": db, "to": tid, "kind": "return", "label": "rows"})
            elif depth < a.max_depth and m_info:
                walk(tc, name, depth + 1)
            if a.returns:
                rt = (m_info or {}).get("ret") or ""
                steps.append({"from": tid, "to": me, "kind": "return", "label": "void" if rt in ("void", "Unit", "") else rt[:30]})

    walk(c0, start[1], 1)
    dedup = []
    for st in steps:
        if dedup and all(dedup[-1].get(k) == st.get(k) for k in ("from", "to", "kind", "label")):
            dedup[-1]["detail"] = (dedup[-1].get("detail", "") + " (called again)").strip(); continue
        dedup.append(st)
    steps[:] = dedup
    if entry_label:
        steps.append({"from": node_for(c0), "to": "client", "kind": "return", "label": "response",
                      "detail": f"{c0.name}.{start[1]}() returns {c0.methods[start[1]]['ret']}"})
    sc = {"id": a.id or re.sub(r"[^\w]+", "-", (entry_label or ".".join(start))).strip("-").lower(),
          "title": entry_label or ".".join(start), "subtitle": f"static draft from {c0.name}.{start[1]}()",
          "description": "Drafted from the source: every call in each method body, in order (branches not evaluated).",
          "nodes": list(nodes.values()), "steps": steps}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(sc, f, indent=1)
    print(f"wrote {a.out}: {len(sc['nodes'])} nodes, {len(steps)} steps" + (" (hit --max-steps)" if len(steps) >= a.max_steps else ""))
    for s in steps:
        print(f"  {s['kind']:<6} {s['from']} → {s['to']}: {s['label']}")


if __name__ == "__main__":
    main()
