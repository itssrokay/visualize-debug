#!/usr/bin/env python3
"""Validate one or more flow-scene specs and bundle them with the runtime into ONE self-contained HTML file.

usage: build_scene.py SPEC.json [SPEC2.json ...] -o out.html [--title "Atlas"] [--theme light|dark]
                      [--editor vscode|idea|cursor --root /abs/repo/path] [--courier stick|dot]

A spec file may hold one scene object, a list of scenes, or {"scenes": [...]}.
Several scenes become tabs in the same page (an "atlas"); a node with "href": "#<scene-id>" jumps between them.
Exit code 1 on errors (bad JSON, missing ids). Warnings are printed but don't fail the build.
"""
import argparse, json, os, sys

KINDS = {"call", "return", "async", "event", "error", "slow", "self", "note", "focus"}
NODE_KINDS = {"module", "client", "user", "controller", "handler", "service", "repo", "db", "cache", "queue", "topic",
              "external", "framework", "config", "job", "util", "model", "component", "gateway", "class", "function"}
HERE = os.path.dirname(os.path.abspath(__file__))


def load(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "scenes" in data:
        data = data["scenes"]
    return data if isinstance(data, list) else [data]


def validate(sc, idx, errors, warns):
    tag = f"scene {sc.get('id') or idx + 1}"
    if not isinstance(sc, dict):
        errors.append(f"{tag}: not an object"); return
    nodes = sc.get("nodes") or []
    ids = set()
    for i, n in enumerate(nodes):
        if isinstance(n, str):
            n = {"id": n}
        if "id" not in n:
            errors.append(f"{tag}: node #{i + 1} has no id"); continue
        if n["id"] in ids:
            errors.append(f"{tag}: duplicate node id {n['id']!r}")
        ids.add(n["id"])
        if n.get("kind") and n["kind"] not in NODE_KINDS:
            warns.append(f"{tag}: node {n['id']!r} kind {n['kind']!r} is not a known shape (drawn as a box)")
        if len(str(n.get("label", n["id"]))) > 28:
            warns.append(f"{tag}: node {n['id']!r} label is long; keep labels under ~28 chars")
    steps = sc.get("steps") or []
    for i, s in enumerate(steps):
        a = s.get("from") or s.get("node") or s.get("at")
        b = s.get("to") or a
        if a is None:
            errors.append(f"{tag}: step {i + 1} needs from/to (or node for note/focus)"); continue
        for x in (a, b):
            if str(x) not in ids:
                warns.append(f"{tag}: step {i + 1} refers to unknown node {x!r} (it will be auto-created, dashed)")
        k = s.get("kind", "call")
        if k not in KINDS:
            errors.append(f"{tag}: step {i + 1} kind {k!r} not in {sorted(KINDS)}")
        if len(s.get("label", "")) > 38:
            warns.append(f"{tag}: step {i + 1} label is {len(s['label'])} chars; the canvas tag shows ~36. Put the rest in 'detail'.")
    for e in sc.get("edges") or []:
        for x in (e.get("from"), e.get("to")):
            if str(x) not in ids:
                warns.append(f"{tag}: edge refers to unknown node {x!r}")
    if len(nodes) > 28:
        warns.append(f"{tag}: {len(nodes)} nodes is too many to read. Split into drill-down scenes (one per module).")
    if len(steps) > 40:
        warns.append(f"{tag}: {len(steps)} steps is long. Consider splitting the flow into chapters (several scenes).")
    if not nodes and not steps:
        errors.append(f"{tag}: empty scene")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("specs", nargs="+")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--title")
    ap.add_argument("--theme", choices=["light", "dark"])
    ap.add_argument("--courier", choices=["stick", "dot"])
    ap.add_argument("--editor", choices=["vscode", "idea", "cursor"], help="make file:line refs open in this editor")
    ap.add_argument("--root", help="absolute repo path on the machine where the HTML will be opened (for editor links)")
    ap.add_argument("--runtime", default=os.path.join(HERE, "..", "runtime", "flowscene.js"))
    a = ap.parse_args()

    scenes, errors, warns = [], [], []
    for p in a.specs:
        try:
            scenes += load(p)
        except Exception as ex:  # noqa
            errors.append(f"{p}: {ex}")
    seen = set()
    for i, sc in enumerate(scenes):
        validate(sc, i, errors, warns)
        if isinstance(sc, dict):
            sc.setdefault("id", f"scene{i + 1}")
            if sc["id"] in seen:
                errors.append(f"duplicate scene id {sc['id']!r}")
            seen.add(sc["id"])
    for sc in scenes:
        for n in sc.get("nodes", []):
            h = isinstance(n, dict) and n.get("href")
            if h and h.startswith("#") and h[1:] not in seen:
                warns.append(f"scene {sc['id']}: node {n.get('id')!r} links to missing scene {h}")
    if scenes:
        first = scenes[0]
        for k in ("theme", "courier", "editor", "root"):
            v = getattr(a, k)
            if v:
                for sc in scenes:
                    sc[k] = v
        if a.editor and not a.root:
            warns.append("--editor given without --root: code links stay plain text")
    for w in warns:
        print("warn:", w)
    if errors:
        for e in errors:
            print("ERROR:", e)
        sys.exit(1)

    with open(a.runtime, encoding="utf-8") as f:
        runtime = f.read()
    title = a.title or (scenes[0].get("title") if len(scenes) == 1 else "Code atlas") or "Code flow"
    data = json.dumps(scenes, ensure_ascii=False).replace("</", "<\\/")
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title.replace('<', '&lt;')}</title>
<meta name="generator" content="flow-scene">
</head><body>
<div id="flow-app"></div>
<script>window.FLOW_SCENES = {data};</script>
<script>{runtime.replace('</script', '<\\/script')}</script>
</body></html>
"""
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)
    n_steps = sum(len(s.get("steps", [])) for s in scenes)
    print(f"built {a.out}: {len(scenes)} scene(s), {n_steps} steps, {len(html) // 1024} KB")


if __name__ == "__main__":
    main()
