#!/usr/bin/env python3
"""Split a codebase into modules and map how they depend on each other. Standard library only.

usage: map_modules.py REPO [--group-by auto|package|dir|build] [--depth 1] [--focus owner/web]
                      [--max-modules 22] [--include-tests] [--exclude GLOB ...]
                      [--out modules.json] [--scene map.json] [--drilldowns] [--quiet]

Languages: Java, Kotlin, Scala, C#, Python, JavaScript/TypeScript (incl. Vue/Svelte), Go.
  package  JVM/C#: module = package segments after the common root package (org.acme.shop.[order].web)
  dir      module = folder under the source root (src/[billing]/...)
  build    module = sub-project with its own pom.xml / build.gradle / package.json / go.mod / pyproject / csproj
  auto     build if there are 2+ sub-projects, else package for JVM/C#, else dir
--depth N    how many segments make a module (default 1)
--focus X    drill into module X: only its files, split one level deeper
--scene F    also write a ready-to-build "map" scene (module graph + guided tour)
--drilldowns with --scene: add one drill-down scene per big module, linked from the top map (an atlas)

Prints a readable summary (modules, dependencies, cycles, entry points) and writes modules.json.
"""
import argparse, fnmatch, json, os, re, sys
from collections import Counter, defaultdict

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "target", "build", "dist", "out", ".gradle", ".idea", ".vscode", "vendor",
             "venv", ".venv", "env", "__pycache__", ".next", ".nuxt", ".svelte-kit", "coverage", "bin", "obj", "generated",
             "generated-sources", ".mvn", ".tox", ".mypy_cache", ".pytest_cache", "site-packages", "bower_components", ".turbo",
             ".cache", "storybook-static", "Pods", "DerivedData", ".terraform"}
EXT = {".java": "java", ".kt": "kotlin", ".scala": "scala", ".cs": "csharp", ".py": "python", ".js": "js", ".jsx": "js",
       ".mjs": "js", ".cjs": "js", ".ts": "ts", ".tsx": "ts", ".vue": "js", ".svelte": "js", ".go": "go"}
JVM = {"java", "kotlin", "scala"}
BUILD_FILES = {"pom.xml", "build.gradle", "build.gradle.kts", "package.json", "go.mod", "pyproject.toml", "setup.py", "Cargo.toml"}
TEST_RE = re.compile(r"(^|/)(test|tests|__tests__|spec|specs|testing|e2e|it|integration-test|testFixtures)(/|$)|(\.|_)(test|spec)\.[a-z]+$|_test\.go$|Tests?\.(java|kt|cs)$|^test_.*\.py$|/test_[^/]*\.py$")
STRIP_PREFIX = [("src", "main", "java"), ("src", "main", "kotlin"), ("src", "main", "scala"), ("src",), ("lib",), ("app",), ("source",)]
JS_EXTS = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte", ".d.ts"]

ROLE_PATTERNS = [
    ("controller", re.compile(r"@(RestController|Controller|Path\(|RequestMapping)|\b(app|router|bp|blueprint|api)\.(get|post|put|patch|delete|route)\s*\(|@(router|app)\.(get|post|put|patch|delete|api_route)\(|\[ApiController\]|: ?ControllerBase|http\.HandleFunc|\.(GET|POST|PUT|DELETE|HandleFunc)\(\"")),
    ("service", re.compile(r"@(Service|Injectable|Singleton|ApplicationScoped)\b|class \w+Service\b")),
    ("repo", re.compile(r"@Repository\b|extends (Jpa|Crud|PagingAndSorting|Mongo|Reactive\w*)?Repository\b|: ?(Jpa|Crud)Repository|interface \w+Repository\b|class \w+(Repository|Dao|DAO)\b|DbContext\b|\bsqlalchemy\b|\bknex\(|prisma\.|getRepository\(")),
    ("model", re.compile(r"@(Entity|Table|Document|Embeddable|MappedSuperclass)\b|\(models\.Model\)|\(Base\)|\(BaseModel\)|@dataclass|\brecord \w+\(|data class |interface \w+(Dto|DTO|Props)\b")),
    ("config", re.compile(r"@(Configuration|ConfigurationProperties|EnableWebSecurity|SpringBootApplication)\b|\bsettings\.py$")),
    ("listener", re.compile(r"@(KafkaListener|RabbitListener|JmsListener|SqsListener|EventListener|StreamListener|Incoming)\b|\.subscribe\(|consumer\.|@OnEvent\(")),
    ("job", re.compile(r"@(Scheduled|shared_task|periodic_task)\b|@\w+\.task\b|cron\.|@Cron\(|BackgroundService")),
    ("client", re.compile(r"@(FeignClient|RegisterRestClient|HttpExchange)\b|RestTemplate|WebClient\.|HttpClient\b|axios\.|fetch\(|requests\.(get|post)|http\.(Get|Post)\(")),
]
KIND_OF_ROLE = {"controller": "controller", "service": "service", "repo": "repo", "model": "model", "config": "config",
                "listener": "handler", "job": "job", "client": "external"}

JAVA_MAP = re.compile(r"@(Get|Post|Put|Patch|Delete|Request)Mapping\s*(\((.*)\))?")
JAXRS = re.compile(r"@(GET|POST|PUT|PATCH|DELETE)\b")
PATH_ANN = re.compile(r"@Path\(\s*\"([^\"]*)\"")
STR_IN = re.compile(r"(?:value\s*=\s*|path\s*=\s*)?\{?\s*\"([^\"]*)\"")
METH_IN = re.compile(r"RequestMethod\.(\w+)")
PY_ROUTE = re.compile(r"@(\w+)\.(get|post|put|patch|delete|route|api_route)\(\s*['\"]([^'\"]*)['\"](.*)")
JS_ROUTE = re.compile(r"\b(app|router|server|api|r|route)\.(get|post|put|patch|delete|all)\(\s*['\"`]([^'\"`]+)['\"`]")
NEST = re.compile(r"@(Get|Post|Put|Patch|Delete)\(\s*['\"`]?([^'\"`)]*)")
GO_ROUTE = re.compile(r"\.(GET|POST|PUT|PATCH|DELETE|HandleFunc|Handle|Get|Post|Put|Delete)\(\s*\"([^\"]+)\"\s*,\s*([\w.]+)")
METHOD_SIG = re.compile(r"^\s*(?:@\w+(?:\([^)]*\))?\s*)*(?:public|protected|private|internal|static|final|suspend|override|open|async|def|fun|func|\s)*[\w<>\[\],.? ]*?\b(\w+)\s*\(")


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def strip_comments(src, lang):
    if lang == "python":
        return re.sub(r"(?m)^\s*#.*$", "", src)
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    return re.sub(r"(?m)//.*$", "", src)


class File:
    __slots__ = ("path", "rel", "lang", "loc", "unit", "types", "imports", "roles", "endpoints", "key", "module", "build", "words")

    def __init__(self, path, rel, lang):
        self.path, self.rel, self.lang = path, rel, lang
        self.loc, self.unit, self.types, self.imports = 0, "", [], []
        self.roles, self.endpoints, self.key, self.module, self.build = Counter(), [], [], "", ""
        self.words = set()


def parse(f):
    raw = read(f.path)
    f.loc = sum(1 for line in raw.splitlines() if line.strip())
    src = strip_comments(raw, f.lang)
    L = f.lang
    if L in JVM:
        m = re.search(r"(?m)^\s*package\s+([\w.]+)", src)
        f.unit = m.group(1) if m else ""
        f.imports = re.findall(r"(?m)^\s*import\s+(?:static\s+)?([\w.]+(?:\.\*)?)", src)
        f.types = re.findall(r"\b(?:class|interface|enum|record|object|trait)\s+(\w+)", src) or [os.path.splitext(os.path.basename(f.rel))[0]]
        f.words = set(re.findall(r"\b[A-Z]\w+\b", re.sub(r'"(?:\\.|[^"\\])*"', "", src)))
    elif L == "csharp":
        m = re.search(r"(?m)^\s*namespace\s+([\w.]+)", src)
        f.unit = m.group(1) if m else ""
        f.imports = re.findall(r"(?m)^\s*using\s+(?:static\s+)?([\w.]+)\s*;", src)
        f.types = re.findall(r"\b(?:class|interface|enum|record|struct)\s+(\w+)", src)
    elif L == "python":
        for m in re.finditer(r"(?m)^\s*from\s+(\.*[\w.]*)\s+import\s+\(?([\w\s,*]+)", src):
            base, names = m.group(1), [n.strip().split(" ")[0] for n in m.group(2).split(",") if n.strip()]
            f.imports.append((base, names))
        for m in re.finditer(r"(?m)^\s*import\s+([\w., ]+)", src):
            for part in m.group(1).split(","):
                f.imports.append((part.strip().split(" ")[0], []))
        f.types = re.findall(r"(?m)^class\s+(\w+)", src)
    elif L in ("js", "ts"):
        f.imports = re.findall(r"""(?:import|export)\s[^'"`;]*?from\s*['"]([^'"]+)['"]|import\s*\(\s*['"]([^'"]+)['"]\s*\)|require\(\s*['"]([^'"]+)['"]\s*\)|^\s*import\s+['"]([^'"]+)['"]""", src, re.M)
        f.imports = [next(x for x in t if x) for t in f.imports if any(t)]
        f.types = re.findall(r"\b(?:class|interface|function|const)\s+([A-Z]\w+)", src)[:12]
    elif L == "go":
        m = re.search(r"(?m)^package\s+(\w+)", src)
        f.unit = m.group(1) if m else ""
        blocks = re.findall(r"import\s*\(([^)]*)\)", src, re.S)
        singles = re.findall(r"(?m)^import\s+(?:\w+\s+)?\"([^\"]+)\"", src)
        f.imports = singles + [s for b in blocks for s in re.findall(r"\"([^\"]+)\"", b)]
        f.types = re.findall(r"(?m)^type\s+(\w+)\s+(?:struct|interface)", src)
    for role, rx in ROLE_PATTERNS:
        if rx.search(src):
            f.roles[role] += 1
    endpoints(f, src)


def endpoints(f, src):
    lines = src.splitlines()
    name = os.path.basename(f.rel)
    if f.lang in JVM or f.lang == "csharp":
        prefix, pending = "", None
        for i, line in enumerate(lines):
            s = line.strip()
            pm = PATH_ANN.search(s)
            m = JAVA_MAP.search(s)
            if m and m.group(1) == "Request" and not any(re.search(r"\b(class|interface)\b", l) for l in lines[i:i + 1]):
                # class-level @RequestMapping if a class declaration follows before a method
                nxt = "\n".join(lines[i + 1:i + 6])
                if re.search(r"\b(class|interface)\s+\w+", nxt) and not re.search(r"\w+\s*\([^)]*\)\s*\{", nxt.split("class")[0]):
                    sm = STR_IN.search(m.group(3) or "")
                    prefix = sm.group(1) if sm else ""
                    continue
            if pm and not m and not JAXRS.search(s):
                nxt = "\n".join(lines[i + 1:i + 4])
                if re.search(r"\b(class|interface)\s+\w+", nxt):
                    prefix = pm.group(1); continue
            if m:
                verb = m.group(1).upper()
                args = m.group(3) or ""
                if verb == "REQUEST":
                    mm = METH_IN.search(args); verb = mm.group(1) if mm else "ANY"
                sm = STR_IN.search(args)
                pending = (verb, sm.group(1) if sm else "", i + 1)
                continue
            jm = JAXRS.search(s)
            if jm:
                pending = (jm.group(1), pm.group(1) if pm else "", i + 1); continue
            if pending and pm:
                pending = (pending[0], pm.group(1), pending[2]); continue
            if pending and "(" in s and not s.startswith("@"):
                mm = METHOD_SIG.match(line)
                if mm:
                    path = "/" + "/".join(p.strip("/") for p in (prefix, pending[1]) if p.strip("/"))
                    f.endpoints.append({"method": pending[0], "path": path or "/", "handler": f"{f.types[0] if f.types else name}.{mm.group(1)}", "file": f.rel, "line": pending[2]})
                pending = None
    elif f.lang == "python":
        for i, line in enumerate(lines):
            m = PY_ROUTE.search(line)
            if m:
                verb = m.group(2).upper()
                if verb in ("ROUTE", "API_ROUTE"):
                    vm = re.search(r"methods\s*=\s*\[\s*['\"](\w+)", m.group(4)); verb = vm.group(1).upper() if vm else "GET"
                h = next((re.match(r"\s*(?:async\s+)?def\s+(\w+)", l) for l in lines[i + 1:i + 6] if re.match(r"\s*(?:async\s+)?def\s+\w+", l)), None)
                f.endpoints.append({"method": verb, "path": m.group(3), "handler": h.group(1) if h else "?", "file": f.rel, "line": i + 1})
    elif f.lang in ("js", "ts"):
        prefix = ""
        for i, line in enumerate(lines):
            m = JS_ROUTE.search(line)
            if m:
                f.endpoints.append({"method": m.group(2).upper(), "path": m.group(3), "handler": name, "file": f.rel, "line": i + 1}); continue
            cm = re.search(r"@Controller\(\s*['\"`]?([^'\"`)]*)", line)
            if cm:
                prefix = cm.group(1); continue
            nm = NEST.search(line)
            if nm:
                h = next((re.search(r"(\w+)\s*\(", l) for l in lines[i + 1:i + 5] if re.search(r"\w+\s*\(", l) and not l.strip().startswith("@")), None)
                path = "/" + "/".join(p.strip("/") for p in (prefix, nm.group(2)) if p.strip("/"))
                f.endpoints.append({"method": nm.group(1).upper(), "path": path, "handler": h.group(1) if h else name, "file": f.rel, "line": i + 1})
    elif f.lang == "go":
        for i, line in enumerate(lines):
            m = GO_ROUTE.search(line)
            if m:
                verb = m.group(1).upper()
                f.endpoints.append({"method": "ANY" if verb.startswith("HANDLE") else verb, "path": m.group(2), "handler": m.group(3), "file": f.rel, "line": i + 1})


NON_PRODUCT_DIRS = {"examples", "example", "samples", "sample", "docs", "doc", "benchmarks", "benchmark", "demo", "demos"}


def walk(repo, include_tests, excludes, include_examples=True):
    files, builds = [], []
    for dirpath, dirnames, filenames in os.walk(repo):
        rel_dir = os.path.relpath(dirpath, repo).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        top = rel_dir.count("/") < 2 and not rel_dir.startswith(("src", "lib", "app"))  # only near the repo root, never inside packages
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
                             and not (not include_examples and top and d in NON_PRODUCT_DIRS)
                             and not any(fnmatch.fnmatch(f"{rel_dir}/{d}".lstrip("/"), e) for e in excludes))
        if any(b in filenames for b in BUILD_FILES) or any(x.endswith(".csproj") for x in filenames):
            builds.append(rel_dir)
        for fn in sorted(filenames):
            ext = ".d.ts" if fn.endswith(".d.ts") else os.path.splitext(fn)[1]
            if ext == ".d.ts" or ext not in EXT:
                continue
            rel = f"{rel_dir}/{fn}".lstrip("/")
            if any(fnmatch.fnmatch(rel, e) for e in excludes):
                continue
            if not include_tests and TEST_RE.search(rel):
                continue
            if fn.endswith((".min.js", ".bundle.js")):
                continue
            files.append(File(os.path.join(dirpath, fn), rel, EXT[ext]))
    return files, builds


def common_prefix(lists):
    if not lists:
        return []
    out = []
    for parts in zip(*lists):
        if all(p == parts[0] for p in parts):
            out.append(parts[0])
        else:
            break
    return out


def strip_src(parts):
    for pre in STRIP_PREFIX:
        if tuple(parts[:len(pre)]) == pre:
            return parts[len(pre):]
    return parts


def assign_keys(files, builds, group_by):
    langs = Counter(f.lang for f in files)
    NOT_MODULES = {"examples", "example", "samples", "sample", "docs", "doc", "demo", "demos", "test", "tests", "benchmarks", "fixtures", "scripts", "tools", "e2e"}
    sub_builds = sorted([b for b in builds if b and not (set(b.split("/")) & NOT_MODULES)], key=len, reverse=True)
    pkg_langs = JVM | {"csharp"}
    pkg_share = sum(v for k, v in langs.items() if k in pkg_langs) / max(1, len(files))
    if group_by == "auto":
        roots_with_code = {b for b in sub_builds if any(f.rel.startswith(b + "/") for f in files)}
        group_by = "build" if len(roots_with_code) >= 2 else ("package" if pkg_share > 0.6 else "dir")
    for f in files:
        f.build = next((b for b in sub_builds if f.rel.startswith(b + "/")), "")
    root_info = {}
    scopes = defaultdict(list)
    for f in files:
        scopes[f.build if group_by == "build" else ""].append(f)
    for scope, fs in scopes.items():
        pk = [f for f in fs if f.lang in pkg_langs and f.unit]
        pkg_root = common_prefix([f.unit.split(".") for f in pk]) if pk else []
        if pk and len({f.unit for f in pk}) == 1 and len(pkg_root) > 1:
            pkg_root = pkg_root[:-1]  # single package: keep its last segment as the module
        dirs = [strip_src(f.rel[len(scope) + 1 if scope else 0:].split("/")[:-1]) for f in fs if not (f.lang in pkg_langs and f.unit)]
        dir_root = common_prefix(dirs) if len(dirs) > 1 else []
        # don't let the dir root swallow everything when all code sits in one folder
        if dirs and all(len(d) == len(dir_root) for d in dirs):
            dir_root = dir_root[:-1]
        root_info[scope] = (pkg_root, dir_root)
        for f in fs:
            if group_by in ("package", "build") and f.lang in pkg_langs and f.unit:
                key = f.unit.split(".")[len(pkg_root):]
            else:
                parts = strip_src(f.rel[len(scope) + 1 if scope else 0:].split("/")[:-1])
                key = parts[len(dir_root):] if parts[:len(dir_root)] == dir_root else parts
            f.key = ([scope.split("/")[-1] if "/" in scope else scope] if group_by == "build" and scope else ([] if group_by != "build" else ["(root)"])) + key
    return group_by, root_info


def resolve_imports(files, repo):
    by_fqn, by_pkg, by_dotted, by_path, ns_files = {}, defaultdict(list), {}, {}, defaultdict(list)
    for f in files:
        by_path[f.rel] = f
        if f.lang in JVM:
            by_pkg[f.unit].append(f)
            for t in f.types:
                by_fqn.setdefault(f"{f.unit}.{t}", f)
            by_fqn.setdefault(f"{f.unit}.{os.path.splitext(os.path.basename(f.rel))[0]}", f)
        elif f.lang == "csharp":
            ns_files[f.unit].append(f)
        elif f.lang == "python":
            parts = f.rel[:-3].split("/")
            if parts[-1] == "__init__":
                parts = parts[:-1]
            for i in range(len(parts)):
                by_dotted.setdefault(".".join(parts[i:]), f)
    go_mod = ""
    gm = read(os.path.join(repo, "go.mod"))
    m = re.search(r"(?m)^module\s+(\S+)", gm)
    if m:
        go_mod = m.group(1)
    go_dirs = defaultdict(list)
    for f in files:
        if f.lang == "go":
            go_dirs[os.path.dirname(f.rel)].append(f)
    ws_pkgs = {}
    for dirpath, _, fns in os.walk(repo):
        if "node_modules" in dirpath or "/." in dirpath:
            continue
        if "package.json" in fns:
            try:
                name = json.loads(read(os.path.join(dirpath, "package.json"))).get("name")
            except Exception:
                name = None
            if name:
                ws_pkgs[name] = os.path.relpath(dirpath, repo).replace(os.sep, "/")
    aliases = {}
    for tsc in ("tsconfig.json", "jsconfig.json", "tsconfig.base.json"):
        txt = re.sub(r"//.*|/\*.*?\*/", "", read(os.path.join(repo, tsc)), flags=re.S)
        try:
            co = json.loads(re.sub(r",(\s*[}\]])", r"\1", txt)).get("compilerOptions", {}) if txt.strip() else {}
        except Exception:
            co = {}
        base = co.get("baseUrl", ".")
        for k, v in (co.get("paths") or {}).items():
            if v:
                aliases[k.rstrip("*")] = os.path.normpath(os.path.join(base, v[0].rstrip("*"))).replace(os.sep, "/").rstrip("/") + "/"
    for a in ("@/", "~/"):
        if a not in aliases:
            for d in ("src/", "app/", ""):
                if os.path.isdir(os.path.join(repo, d or ".")):
                    aliases[a] = d; break

    def js_file(base):
        base = os.path.normpath(base).replace(os.sep, "/")
        if base in by_path:
            return by_path[base]
        for e in JS_EXTS:
            if base + e in by_path:
                return by_path[base + e]
        for e in JS_EXTS:
            if f"{base}/index{e}" in by_path:
                return by_path[f"{base}/index{e}"]
        return None

    externals = defaultdict(Counter)
    targets = defaultdict(list)  # file -> [target files]
    for f in files:
        out = []
        if f.lang in JVM:
            for imp in f.imports:
                if imp.endswith(".*"):
                    out += by_pkg.get(imp[:-2], [])[:1] or ([by_fqn[imp[:-2]]] if imp[:-2] in by_fqn else [])
                    continue
                parts = imp.split(".")
                hit = None
                while len(parts) > 1 and not hit:
                    hit = by_fqn.get(".".join(parts)); parts = parts[:-1]
                if hit:
                    out.append(hit)
                elif not imp.startswith(("java.", "javax.", "kotlin.", "scala.")):
                    externals[f.rel][".".join(imp.split(".")[:2])] += 1
            # same-package references need no import
            own = set(f.types)
            for g in by_pkg.get(f.unit, []):
                if g is not f and (set(g.types) - own) & f.words:
                    out.append(g)
        elif f.lang == "csharp":
            for imp in f.imports:
                if imp in ns_files:
                    out += ns_files[imp][:1]
                elif not imp.startswith("System"):
                    externals[f.rel][imp.split(".")[0]] += 1
        elif f.lang == "python":
            pkg = f.rel[:-3].split("/")[:-1]
            for base, names in f.imports:
                if base.startswith("."):
                    lvl = len(base) - len(base.lstrip("."))
                    anchor = pkg[:len(pkg) - (lvl - 1)] if lvl > 1 else pkg
                    base = ".".join(anchor + ([base.lstrip(".")] if base.lstrip(".") else []))
                hit = None
                for n in names or [None]:
                    cand = f"{base}.{n}" if n and n != "*" else base
                    hit = by_dotted.get(cand) or by_dotted.get(base)
                    if hit:
                        out.append(hit)
                if not hit and base and not base.startswith("."):
                    externals[f.rel][base.split(".")[0]] += 1
        elif f.lang in ("js", "ts"):
            here = os.path.dirname(f.rel)
            for imp in f.imports:
                hit = None
                if imp.startswith("."):
                    hit = js_file(os.path.join(here, imp))
                else:
                    for a, tgt in sorted(aliases.items(), key=lambda kv: -len(kv[0])):
                        if a and imp.startswith(a):
                            hit = js_file(tgt + imp[len(a):]); break
                    if not hit:
                        name = "/".join(imp.split("/")[:2]) if imp.startswith("@") else imp.split("/")[0]
                        if name in ws_pkgs:
                            rest = imp[len(name):].lstrip("/")
                            hit = js_file(f"{ws_pkgs[name]}/{rest}" if rest else f"{ws_pkgs[name]}/src/index") or js_file(f"{ws_pkgs[name]}/index")
                            if not hit:
                                hit = next((x for x in files if x.rel.startswith(ws_pkgs[name] + "/")), None)
                        elif not imp.startswith(("node:", "http", "data:")):
                            externals[f.rel][name] += 1
                if hit:
                    out.append(hit)
        elif f.lang == "go":
            for imp in f.imports:
                if go_mod and imp.startswith(go_mod + "/"):
                    out += go_dirs.get(imp[len(go_mod) + 1:], [])[:1]
                elif "." in imp.split("/")[0]:
                    externals[f.rel]["/".join(imp.split("/")[:3])] += 1
        targets[f.rel] = [t for t in out if t is not f]
    return targets, externals


def tarjan(nodes, adj):
    idx, low, on, st, out, c = {}, {}, set(), [], [], [0]
    sys.setrecursionlimit(10000)

    def go(v):
        idx[v] = low[v] = c[0]; c[0] += 1; st.append(v); on.add(v)
        for w in adj.get(v, ()):
            if w not in idx:
                go(w); low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], idx[w])
        if low[v] == idx[v]:
            comp = []
            while True:
                w = st.pop(); on.discard(w); comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                out.append(sorted(comp))
    for v in nodes:
        if v not in idx:
            go(v)
    return out


def ranks(nodes, edges):
    adj = defaultdict(set)
    for (a, b) in edges:
        adj[a].add(b)
    state, dag = {}, defaultdict(set)

    def dfs(u):
        state[u] = 1
        for v in sorted(adj[u]):
            if state.get(v) == 1:
                continue
            dag[u].add(v)
            if v not in state:
                dfs(v)
        state[u] = 2
    for n in nodes:
        if n not in state:
            dfs(n)
    indeg = Counter(v for u in dag for v in dag[u])
    r = {n: 0 for n in nodes}
    q = [n for n in nodes if not indeg[n]]
    while q:
        u = q.pop(0)
        for v in dag[u]:
            r[v] = max(r[v], r[u] + 1); indeg[v] -= 1
            if not indeg[v]:
                q.append(v)
    return r


def refine(files, depth, keep, group_by):
    """Make the split useful when one folder/package holds almost everything: step into it, or split it by file."""
    notes = []
    if group_by == "build" and keep == 0:
        return notes
    for _ in range(5):
        tops = Counter(tuple(f.key[:depth]) for f in files)
        if len(tops) >= 14 or len(files) < 6:
            break
        dom, cnt = tops.most_common(1)[0]
        if cnt < 0.6 * len(files):
            break
        members = [f for f in files if tuple(f.key[:depth]) == dom]
        nested = [f for f in members if len(f.key) > depth]
        children = {f.key[depth] for f in nested}
        if dom and len(dom) > keep and len(children) >= 2 and len(nested) >= 0.5 * len(members):
            for f in members:
                f.key = f.key[:depth - 1] + f.key[depth:]
            notes.append("stepped into " + "/".join(dom))
        else:
            if len(dom) < keep or len(members) > 30:
                break  # a big flat folder stays one box; splitting it per file would be unreadable
            for f in members:
                rest = f.key[depth:]
                f.key = f.key[:depth - 1] + (rest if rest else [os.path.splitext(os.path.basename(f.rel))[0]])
            notes.append(("/".join(dom) or "root") + " split by file")
            break
    return notes


def build_map(repo, a):
    files, builds = walk(repo, a.include_tests, a.exclude or [], a.include_examples)
    if not files:
        sys.exit(f"no source files found under {repo}")
    for f in files:
        parse(f)
    group_by, root_info = assign_keys(files, builds, a.group_by)
    targets, externals = resolve_imports(files, repo)
    focus = [p for p in (a.focus or "").split("/") if p]
    depth = len(focus) + a.depth
    if focus:
        files_in = [f for f in files if f.key[:len(focus)] == focus]
        if not files_in:
            known = sorted({"/".join(f.key[:len(focus)]) for f in files})
            sys.exit(f"--focus {a.focus!r} matched no files. Modules at that level: {', '.join(known[:40])}")
    else:
        files_in = files
    refined = refine(files_in, depth, len(focus), group_by)
    for f in files:
        f.module = "/".join(f.key[:depth]) or "(root)"
    scope = {f.rel for f in files_in}
    mods = defaultdict(list)
    for f in files_in:
        mods[f.module].append(f)
    edge_w, edge_samples = Counter(), defaultdict(list)
    ext_out = Counter()
    for f in files_in:
        for t in targets[f.rel]:
            if t.rel in scope:
                if t.module != f.module:
                    edge_w[(f.module, t.module)] += 1
                    if len(edge_samples[(f.module, t.module)]) < 4:
                        edge_samples[(f.module, t.module)].append(f"{os.path.basename(f.rel)} → {os.path.basename(t.rel)}")
            elif focus:
                ext_out[(f.module, "/".join(t.key[:len(focus)]) or "(root)")] += 1
    # cap module count: merge smallest into (other)
    note = None
    if len(mods) > a.max_modules:
        deg = Counter()
        for (x, y), w in edge_w.items():
            deg[x] += w; deg[y] += w
        keep = set(sorted(mods, key=lambda m: -(sum(f.loc for f in mods[m]) + 40 * deg[m]))[:a.max_modules - 1])
        merged = [m for m in mods if m not in keep]
        note = f"{len(merged)} small modules merged into '(other)': {', '.join(sorted(merged)[:12])}{'…' if len(merged) > 12 else ''}"
        for m in merged:
            for f in mods.pop(m):
                f.module = "(other)"; mods["(other)"].append(f)
        ew2, es2 = Counter(), defaultdict(list)
        for (x, y), w in edge_w.items():
            x = x if x in keep else "(other)"; y = y if y in keep else "(other)"
            if x != y:
                ew2[(x, y)] += w; es2[(x, y)] += edge_samples[(x, y)][:2]
        edge_w, edge_samples = ew2, es2
    names = list(mods)
    r = ranks(names, list(edge_w))
    cycles = tarjan(names, {m: {y for (x, y) in edge_w if x == m} for m in names})
    langs = Counter(f.lang for f in files_in)
    # shorter labels: drop a prefix every module shares (spring-petclinic-customers-service → customers-service)
    lasts = [m.split("/")[-1] for m in names if not m.startswith("(")]
    pre = os.path.commonprefix(lasts) if len(lasts) > 1 else ""
    pre = pre[:max(pre.rfind("-"), pre.rfind("_"), pre.rfind(".")) + 1] if pre else ""
    short = {m: (m.split("/")[-1][len(pre):] if pre and m.split("/")[-1].startswith(pre) and len(m.split("/")[-1]) > len(pre) else m.split("/")[-1]) for m in names}
    out_mods = []
    for m in sorted(names, key=lambda m: (r[m], -sum(f.loc for f in mods[m]))):
        fs = mods[m]
        roles = Counter()
        for f in fs:
            roles.update(f.roles)
        top_role = next((ro for ro, _ in roles.most_common() if ro in KIND_OF_ROLE), None)
        ext = Counter()
        for f in fs:
            ext.update(externals.get(f.rel, {}))
        eps = [e for f in fs for e in f.endpoints]
        out_mods.append({
            "id": m, "label": (short[m] if sum(1 for x in names if short[x] == short[m]) == 1 else m)[:28],
            "files": len(fs), "loc": sum(f.loc for f in fs), "rank": r[m],
            "kind": KIND_OF_ROLE.get(top_role, "module") if roles and top_role and roles[top_role] >= max(1, len(fs) * 0.34) else "module",
            "roles": dict(roles.most_common()), "langs": dict(Counter(f.lang for f in fs)),
            "top_files": [f.rel for f in sorted(fs, key=lambda f: -f.loc)[:15]],
            "types": [t for f in sorted(fs, key=lambda f: -f.loc)[:25] for t in f.types[:2]][:30],
            "externals": [k for k, _ in ext.most_common(8)],
            "endpoints": eps[:60], "endpoint_count": len(eps),
            "uses": sorted([y for (x, y) in edge_w if x == m]), "used_by": sorted([x for (x, y) in edge_w if y == m]),
        })
    return {
        "repo": os.path.abspath(repo), "group_by": group_by, "depth": a.depth, "focus": a.focus or None,
        "roots": {k or "(repo)": {"package_root": ".".join(v[0]), "dir_root": "/".join(v[1])} for k, v in root_info.items()},
        "files": len(files_in), "loc": sum(f.loc for f in files_in), "languages": dict(langs.most_common()),
        "modules": out_mods,
        "edges": [{"from": x, "to": y, "weight": w, "samples": edge_samples[(x, y)]} for (x, y), w in edge_w.most_common()],
        "outside_deps": [{"from": x, "to": y, "weight": w} for (x, y), w in ext_out.most_common(20)],
        "cycles": cycles, "note": "; ".join(x for x in [note] + refined if x) or None,
        "submodules": sorted({"/".join(f.key[:depth + 1]) for f in files_in if len(f.key) > depth})[:200],
    }


def human(mp):
    L = []
    root = "; ".join(f"{k}: {v['package_root'] or v['dir_root'] or '-'}" for k, v in mp["roots"].items())
    L.append(f"{mp['repo']}  ·  {mp['files']} files, {mp['loc']:,} loc  ·  {', '.join(f'{k} {v}' for k, v in mp['languages'].items())}")
    L.append(f"group-by {mp['group_by']}, depth {mp['depth']}{', focus ' + mp['focus'] if mp['focus'] else ''}  ·  root {root}")
    if mp["note"]:
        L.append("note: " + mp["note"])
    L.append(f"\nmodules ({len(mp['modules'])}), ordered callers → callees:")
    w = max([len(m["id"]) for m in mp["modules"]] + [6])
    for m in mp["modules"]:
        roles = " ".join(f"{k}×{v}" for k, v in list(m["roles"].items())[:4])
        L.append(f"  [{m['rank']}] {m['id']:<{w}} {m['files']:>4} files {m['loc']:>7,} loc  {m['kind']:<10} {roles}")
        if m["uses"]:
            L.append(f"  {'':<{w + 4}} → {', '.join(m['uses'][:10])}")
        if m["endpoint_count"]:
            L.append(f"  {'':<{w + 4}} {m['endpoint_count']} endpoints, e.g. " + "; ".join(f"{e['method']} {e['path']}" for e in m["endpoints"][:3]))
    L.append(f"\nheaviest dependencies:")
    for e in mp["edges"][:15]:
        L.append(f"  {e['from']} → {e['to']}  ({e['weight']} imports, e.g. {e['samples'][0] if e['samples'] else ''})")
    if not mp["edges"] and len(mp["modules"]) > 1:
        L.append("  (none: these modules don't import each other, so they probably talk over HTTP, messaging or a shared DB."
                 " Show that with a flow scene, or feed a Jaeger/OTLP trace to trace_to_scene.py --otel)")
    if mp["cycles"]:
        L.append("\ncycles (modules that import each other):")
        for c in mp["cycles"][:10]:
            L.append("  " + " ↔ ".join(c))
    eps = [e for m in mp["modules"] for e in m["endpoints"]]
    if eps:
        L.append(f"\nentry points ({sum(m['endpoint_count'] for m in mp['modules'])}):")
        for e in eps[:25]:
            L.append(f"  {e['method']:<6} {e['path']:<40} {e['handler']}  ({e['file']}:{e['line']})")
    if mp["submodules"] and not mp["focus"]:
        big = sorted(mp["modules"], key=lambda m: -m["loc"])[:3]
        L.append("\ndrill down: --focus " + " | --focus ".join(m["id"] for m in big if m["id"] not in ("(other)", "(root)")))
    return "\n".join(L)


def scene_from_map(mp, scene_id, title, href_for=None):
    nodes, steps = [], []
    for m in mp["modules"]:
        roles = ", ".join(f"{v} {k}" + ("s" if v > 1 else "") for k, v in list(m["roles"].items())[:3])
        n = {"id": m["id"], "label": m["label"], "kind": m["kind"], "sub": f"{m['files']} files · {m['loc']:,} loc",
             "files": m["top_files"], "note": (roles + ". " if roles else "") + (f"Key types: {', '.join(m['types'][:8])}." if m["types"] else "")}
        if "/" in m["id"] and not mp["focus"]:
            n["group"] = m["id"].split("/")[0]
        if href_for and href_for(m["id"]):
            n["href"] = "#" + href_for(m["id"])
        nodes.append(n)
    for m in mp["modules"]:
        bits = []
        if m["uses"]:
            bits.append("uses " + ", ".join(m["uses"][:6]))
        if m["used_by"]:
            bits.append("used by " + ", ".join(m["used_by"][:6]))
        detail = "; ".join(bits) or "no internal dependencies"
        if m["endpoint_count"]:
            detail += f"\n{m['endpoint_count']} entry points: " + "; ".join(f"{e['method']} {e['path']}" for e in m["endpoints"][:4])
        if m["externals"]:
            detail += "\nexternal libs: " + ", ".join(m["externals"][:6])
        steps.append({"kind": "focus", "node": m["id"], "label": f"{m['label']} · {m['files']} files", "detail": detail,
                      "code": m["top_files"][:4]})
    sc = {"id": scene_id, "title": title, "mode": "map", "autoplay": False,
          "subtitle": f"{mp['files']} files · {mp['loc']:,} loc · grouped by {mp['group_by']}" + (" · " + mp["note"] if mp["note"] else ""),
          "description": "Arrows point from a module to the modules it imports (callers on the left, callees on the right). "
                         "Thicker = more imports. Press play for a guided tour, or click a module." +
                         (" Cycles: " + "; ".join(" ↔ ".join(c) for c in mp["cycles"][:4]) if mp["cycles"] else ""),
          "nodes": nodes, "edges": [{"from": e["from"], "to": e["to"], "weight": e["weight"]} for e in mp["edges"]], "steps": steps}
    return sc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo")
    ap.add_argument("--group-by", default="auto", choices=["auto", "package", "dir", "build"])
    ap.add_argument("--depth", type=int, default=1)
    ap.add_argument("--focus")
    ap.add_argument("--max-modules", type=int, default=22)
    ap.add_argument("--include-tests", action="store_true")
    ap.add_argument("--include-examples", action="store_true", help="also map examples/, samples/, docs/, benchmarks/")
    ap.add_argument("--exclude", action="append", help="glob on repo-relative paths, e.g. 'legacy/*' (repeatable)")
    ap.add_argument("--out", default="modules.json")
    ap.add_argument("--scene", help="write a map scene JSON here")
    ap.add_argument("--drilldowns", action="store_true", help="with --scene: add a linked drill-down scene per big module")
    ap.add_argument("--min-drill-files", type=int, default=6)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    mp = build_map(a.repo, a)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(mp, f, indent=1)
    if not a.quiet:
        print(human(mp))
        print(f"\nwrote {a.out}")
    if a.scene:
        top_id = "map" if not a.focus else "map-" + a.focus.replace("/", "-")
        scenes, drill = [], {}
        if a.drilldowns:
            for m in mp["modules"]:
                if m["id"] in ("(other)", "(root)") or m["files"] < a.min_drill_files:
                    continue
                sub = argparse.Namespace(**{**vars(a), "focus": m["id"]})
                try:
                    smp = build_map(a.repo, sub)
                except SystemExit:
                    continue
                if len(smp["modules"]) < 2:
                    continue
                sid = "map-" + sub.focus.replace("/", "-")
                drill[m["id"]] = sid
                scenes.append(scene_from_map(smp, sid, f"{m['label']} — inside", None))
                scenes[-1]["tab"] = m["label"]
                scenes[-1]["nodes"].insert(0, {"id": "__up", "label": "↑ all modules", "kind": "framework", "href": "#" + top_id, "layer": 0})
        top = scene_from_map(mp, top_id, "Module map" + (f" — {a.focus}" if a.focus else ""), lambda mid: drill.get(mid))
        top["tab"] = "Module map"
        scenes.insert(0, top)
        with open(a.scene, "w", encoding="utf-8") as f:
            json.dump(scenes if len(scenes) > 1 else top, f, indent=1)
        if not a.quiet:
            print(f"wrote {a.scene}: {len(scenes)} scene(s)" + (f" (drill-downs: {', '.join(drill)})" if drill else ""))


if __name__ == "__main__":
    main()
