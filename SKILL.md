---
name: flow-scene
description: Turn a codebase into animated, hand-drawn flow scenes in one self-contained HTML page. Use it to split a big repo into modules and map how they depend on each other (with drill-downs), to animate how a feature or request flows through the code, or to visualize a bug from a stack trace or a Jaeger/OTLP trace. A stick-figure courier carries each call along the path; slow hops show a clock, failures turn red. Use when the user asks to visualize, animate or diagram code flow, architecture, modules, a request path, a call chain, a crash, an exception or a trace, or says "show me how X works", "map this codebase", "walk me through this flow" or "visualize this stack trace".
---

# Flow scene

Animated code-flow scenes: boxes for modules, classes or services, and a little stick figure that walks each call along the edges, carrying an envelope. Returns come back with a green tick, async calls fly as paper planes, slow hops carry a clock, and errors trip the courier and turn the target red. A side panel shows the step's detail, `file:line` refs, the code snippet, the payload and the live call stack. One HTML file, no dependencies, works offline.

There are three jobs. Pick one from the request, or chain them:

| Job | When | Generator |
|---|---|---|
| **Map** | "map / split / explain this codebase", onboarding, "what are the modules" | `map_modules.py` → module graph + drill-down tabs + guided tour |
| **Flow** | "how does X work", "what happens when …", explain an endpoint or feature | `flow_from_entry.py` (Java/Kotlin static draft) or read the code yourself → hand-curated scene |
| **Debug** | stack trace, exception, error log, slow request, Jaeger/OTLP JSON | `trace_to_scene.py` → the path to the failure, mapped onto the user's modules |

## 0. Find the bundle

The runtime and scripts ship next to this file. Find them once per session:

```sh
for d in "$FLOW_SCENE_HOME" "<this-skill-dir>" ~/.claude/skills/flow-scene ~/.flow-scene ~/flow-scene; do
  [ -f "$d/runtime/flowscene.js" ] && B="$d" && break; done
if [ -z "$B" ]; then
  z=$(ls /mnt/user-data/uploads/*flow-scene*.zip ~/Downloads/flow-scene*.zip 2>/dev/null | head -1)
  [ -n "$z" ] && mkdir -p ~/.flow-scene && unzip -oq "$z" -d ~/.flow-scene && B=$(dirname "$(dirname "$(find ~/.flow-scene -path '*runtime/flowscene.js' | head -1)")")
fi
if [ -z "$B" ]; then  # last resort: fetch the bundle from GitHub
  git clone -q --depth 1 https://github.com/itssrokay/visualize-debug ~/.flow-scene 2>/dev/null && [ -f ~/.flow-scene/runtime/flowscene.js ] && B=~/.flow-scene
fi
echo "${B:-MISSING}"
```

If it prints MISSING, ask the user to attach `flow-scene.zip` (the bundle) or check that github.com/itssrokay/visualize-debug is reachable, and stop. Needs `python3` (standard library only). `node` + Playwright are only for the visual check in step 4.

Bundle: `runtime/flowscene.js` (renderer), `scripts/map_modules.py`, `scripts/flow_from_entry.py`, `scripts/trace_to_scene.py`, `scripts/build_scene.py`, `scripts/frames.mjs`, `references/scene-format.md` (full JSON format: read it before hand-writing a scene).

Work in a scratch folder (`.flow-scene/` in the repo, or the session's working directory). Never edit the user's source.

## 1. Map: split the codebase into modules

```sh
python3 $B/scripts/map_modules.py REPO --out modules.json --scene map.json --drilldowns
```

It reads Java, Kotlin, Scala, C#, Python, JS/TS and Go. It works out modules (Maven/Gradle/npm/Go sub-projects, else packages after the common root, else folders under `src/`), resolves imports into module→module edges, finds cycles and HTTP endpoints, and steps into a folder that holds nearly everything. Read the printed summary first, then judge it:

- **Too coarse** (1–3 boxes): `--depth 2`, or `--focus <module>` to split one module a level deeper.
- **Too fine** (more than ~16 boxes): `--max-modules 12` merges the small ones into `(other)`, or use `--group-by build`.
- **Wrong axis**: `--group-by package|dir|build`. Exclude noise with `--exclude 'legacy/*'`. Tests, examples and docs are skipped unless `--include-tests` / `--include-examples`.
- **No edges between modules**: they talk over HTTP or messaging. Say so, then draw that with a Flow scene, or a trace if one is available.

`--drilldowns` adds one tab per big module, linked from the top map (double-click a box or press "Open ↗" in its popover). Then **curate `map.json`**. The generator gives structure; you add meaning:
- Rename labels to what the team calls things. Write each module's `note` as one sentence on what it is *for* (read its top files to find out), not a list of class names.
- Rewrite each tour step's `label`/`detail` around what a newcomer needs: what it owns, who calls it, what to read first. Re-order the tour from the entry point inwards. Delete tour steps for trivial modules.
- Call out cycles and suspicious dependencies (a `util` that imports a `web` module) with a `note` step.

`modules.json` lists every module's top files, types, roles, external libraries and endpoints. Use it to pick the flows worth animating next.

## 2. Flow: animate how something works

1. **Pick one question** per scene: "what happens on POST /orders", "how does a login token get refreshed". Use 6–12 nodes and 8–25 steps. If it needs more, split it into chapters (several scenes → tabs).
2. **Find the entry point.** The endpoint list from `map_modules.py` gives `METHOD /path → Handler.method (file:line)`. Otherwise grep for the route, listener, job or CLI command.
3. **Draft.** For Java/Kotlin:
   ```sh
   python3 $B/scripts/flow_from_entry.py REPO --entry "POST /orders" -o flow.json [--max-depth 3] [--returns]
   ```
   It follows calls on injected fields, constructor params and locals into repo classes (interface → single impl), turns Spring Data/MyBatis repos into a `db` hop, HTTP clients into an `external` hop, and Kafka/Rabbit/JMS templates into an `async` hop to a `queue`. It lists every call in source order without evaluating branches, so it is a draft. For other languages, read the handler and follow the calls yourself.
4. **Curate by reading the code.** This step is the job; don't skip it:
   - Keep the happy path, plus at most one interesting branch (the validation failure, the cache miss). Cut calls that don't move the story (logging, mappers, getters).
   - Choose the level. Use classes for one feature, modules or services for a cross-cutting flow. Merge a chain of helpers into one `self` step.
   - `label` ≤ 36 chars and verb-first ("save order", "INCR order sn", "publish OrderPlaced"). Put the why in `detail`, real `file:line` in `code` (verify each line exists), and a realistic example in `payload` (field names from the actual DTO).
   - Add `return` steps only when the value matters ("user #42", "403", "cache miss"). Use `async` for anything fire-and-forget, `slow` with `ms` for known hot spots, and `note` for gotchas ("runs inside the @Transactional, so the INSERT is not visible yet").
   - Give every node a `module` so colours match the map, and a `files` entry so a click shows where it lives.
5. To compare behaviours, put two scenes side by side as tabs ("cache hit" vs "cache miss", "expected" vs "what happened").

## 3. Debug: visualize a failure

**Stack trace** (Java/Kotlin/Scala, C#, Python, JS/TS, Go), pasted or from a log file:
```sh
python3 $B/scripts/trace_to_scene.py --stack trace.txt --repo REPO -o crash.json   # or --stack - < log
```
It maps frames onto the repo's classes and modules and collapses framework runs into one dashed hop each ("Spring MVC", "Hibernate", "express"). It drops reflection/proxy/async plumbing, follows `Caused by` / `... N more` to the root cause, and marks the throw point red with the exception and source snippet. It then walks back to your code with "rethrown as …" when the logged exception wraps the real one. More than 14 classes switches to module level (`--level module`).

**Distributed trace** (Jaeger UI "Download JSON", or OTLP JSON from the collector):
```sh
python3 $B/scripts/trace_to_scene.py --otel trace.json -o trace.json [--slow-ms 200] [--internal]
```
Services become nodes, spans become call/return steps with `ms`, spans that take ≥ 40% of the request (or `--slow-ms`) become `slow`, and failing spans are `error`.

**No trace, only logs or a description?** Build the actual path by hand from the log lines (timestamps → `ms`, thread or request id → one scene per request), then do the debugging work in the scene:
- Put the **observed values** in `payload` at each hop. The bug is usually the first hop where a value is already wrong.
- Add `note` steps for hypotheses ("suspect: pool exhausted, 10/10 busy") and mark the divergence point with `error` or `slow`.
- Optionally add an "expected" tab next to the "actual" one.
Tell the user which hop you think is guilty and why, in plain words. The scene supports the explanation; it doesn't replace it.

## 4. Build and check

```sh
python3 $B/scripts/build_scene.py map.json flow.json crash.json -o atlas.html --title "<repo> atlas" [--editor idea --root /abs/repo/path]
```
It validates (unknown node refs, bad kinds, long labels, too many nodes or steps) and inlines everything into one HTML file. Several specs become tabs. `--editor idea|vscode|cursor --root <absolute repo path on the user's machine>` makes every `file:line` open in their IDE.

Then look at it. Install Playwright once in a scratch dir (`npm i playwright@1.56.1`):
```sh
NODE_PATH=<scratch>/node_modules node $B/scripts/frames.mjs --html atlas.html --scene all --out sheet.png
```
Read `sheet.png` (frames: `--frames "1:0.5,4:0.8,last"`) and fix until:
- there are no `error` lines and no "edge … passes through" warnings. Fix layout with `layer`/`row` on nodes, or `direction: "TB"` for deep chains;
- scale is ≥ 0.6 (otherwise there are too many nodes: split the scene);
- every tag is readable and no tag hides the box it's about;
- the error or slow hop is obvious at a glance.

If Playwright is not available, at least run `build_scene.py` (it fails on structural errors) and say the visual check was skipped.

## 5. Deliver

- **claude.ai**: publish the HTML as an artifact (or send the file), and say in one line what each tab shows.
- **Claude Code / local**: write to `docs/flows/<name>.html` (or `.flow-scene/` if they don't want it committed) and open it (`open` / `xdg-open`). Keep the JSON specs next to it so the scenes can be regenerated after code changes.
- Mention the controls once: Space/arrow keys step through, click a box for its files, ● rec exports a WebM for a PR or Slack, PNG exports the current frame.

## Style rules

- One idea per scene. Readers come back to scenes, so spend the effort on labels and notes: they are the explanation.
- Colours mean something: yellow = active/on the stack, red = failure, orange = slow, blue/dashed = async, green = returned value. Don't fight them.
- Names come from the code (real class, module and endpoint names), never invented. Every `code` ref must exist.
- Prefer fewer, truer hops over complete-but-unreadable ones. Collapse framework code unless the bug lives there.
- Never put secrets, tokens or real customer data in payloads; use shaped examples.
