# Scene format

A scene is JSON. A file can hold one scene, a list, or `{"scenes": [...]}`. Several scenes become tabs in one HTML page (an atlas).

```json
{
  "id": "create-owner",                 // unique, used for tabs, deep links (#create-owner:5) and node hrefs
  "title": "Create owner",
  "subtitle": "POST /owners/new · petclinic",
  "tab": "Flow: create owner",          // optional shorter tab label
  "description": "Shown before step 1 / in map mode.",
  "mode": "flow",                       // "flow" (default when steps exist) or "map" (dependency graph + tour)
  "direction": "LR",                    // optional: "LR" or "TB". Auto: TB for deep thin chains
  "autoplay": true,                     // flows autoplay once; maps default to false
  "courier": "stick",                   // "stick" figure or "dot"
  "theme": "light",                     // or "dark"
  "editor": "idea", "root": "/abs/path/to/repo",   // makes file:line refs open in IntelliJ ("vscode", "cursor" too)
  "nodes": [ ... ],
  "edges": [ ... ],                     // map mode: static dependencies
  "groups": [ {"id": "owner", "label": "owner module", "color": "#3d7a8c"} ],   // optional; auto-made from node.module
  "steps": [ ... ]
}
```

## Nodes

| field | meaning |
|---|---|
| `id` | required, unique |
| `label` | shown in the box. Keep it ≤ 28 chars (class name, module name, service name) |
| `kind` | shape and subtitle: `client`/`user` (little figure), `controller`, `handler`, `service`, `repo`, `db`/`cache` (cylinder), `queue`/`topic` (striped), `external`/`framework` (dashed), `module`, `class`, `job`, `config`, `gateway`, `component`, `model`, `function` |
| `module` | colour stripe + legend entry; nodes in the same module share a colour |
| `sub` | override the small second line (default: `kind · module`) |
| `files` | list of `path` or `path:line`, shown when the box is clicked (links open in the editor when `editor`+`root` are set) |
| `note` | one or two sentences shown when the box is clicked |
| `href` | `"#other-scene-id"` jumps to another tab on double-click (drill-down), or a URL |
| `layer`, `row` | pin the column (rank) and the position inside it when auto layout puts something awkwardly |
| `badge` | tiny tag on the corner, e.g. `"hot"`, `"3×"` |

## Steps

Each step animates one hop. `from`/`to` are node ids (`node` alone for `note`/`focus`).

| `kind` | how it looks | use for |
|---|---|---|
| `call` | stick figure walks the edge carrying an envelope | sync call / request |
| `return` | dashed edge, green envelope with a tick | response worth showing (a value, a status) |
| `self` | walks a loop above the box | internal step inside one class/module |
| `async`, `event` | paper plane, dotted edge | fire-and-forget, message published, event emitted |
| `slow` | orange, a clock, takes longer | latency hot spot; set `ms` |
| `error` | red; the courier trips, the target box shakes and turns red | where it broke |
| `note` | a speech tag above the node | an annotation or hypothesis, no movement |
| `focus` | highlights the node, dims everything unrelated | map tours |

Fields: `label` (≤ 36 chars, shown on the canvas tag), `detail` (any length, panel only), `code` (string or list of `path:line`), `snippet` (code text shown in a mono box), `payload` (string or object shown pretty-printed), `ms` (number).

The call-stack panel is derived from the steps: `call`/`slow` push, `return` pops to its target, and a step that starts from a frame lower in the stack implicitly returns the frames above it.

## Edges (map mode)

`{"from": "web", "to": "core", "weight": 14}`: weight sets the thickness (log scale). Edges are drawn even without steps; particles drift along them.

## Example: a minimal flow

```json
{"id": "login", "title": "Login", "nodes": [
  {"id": "ui", "label": "Browser", "kind": "client"},
  {"id": "api", "label": "AuthController", "kind": "controller", "module": "auth"},
  {"id": "svc", "label": "TokenService", "kind": "service", "module": "auth"},
  {"id": "db", "label": "users table", "kind": "db"}],
 "steps": [
  {"from": "ui", "to": "api", "label": "POST /login", "payload": {"email": "a@b.c"}},
  {"from": "api", "to": "db", "label": "find user by email"},
  {"from": "db", "to": "api", "kind": "return", "label": "user #42"},
  {"from": "api", "to": "svc", "label": "issue JWT"},
  {"from": "svc", "to": "api", "kind": "return", "label": "token (15 min)"},
  {"from": "api", "to": "ui", "kind": "return", "label": "200 + Set-Cookie"}]}
```

## Runtime API (for checks and automation)

`window.FLOW.seek(stepIndex0, u)`, `.play()`, `.pause()`, `.tab(idOrIndex)`, `.state()`, `.check()` → `{nodes, steps, dir, scale, issues:[{level,msg}]}`. Add `?debug` to the URL to show layout warnings on the canvas.

Keyboard: Space play/pause · ← → step · Home/End · + − zoom · F fit · Esc close. Drag to pan, Ctrl/⌘+scroll to zoom, click a box for details, double-click a box with `href` to drill in. Toolbar: stick/dot courier, theme, PNG of the current frame, ● rec (WebM of the whole flow).
