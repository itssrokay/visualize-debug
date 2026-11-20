# visualize-debug · flow-scene skill

Animated, hand-drawn code-flow scenes for coding agents, in one self-contained HTML file. A stick figure carries each call through your code; slow hops get a clock, failures turn red.

- **Map** a big codebase into modules + drill-downs: `scripts/map_modules.py`
- **Flow**: animate a request or feature: `scripts/flow_from_entry.py` (Java/Kotlin draft) + curation
- **Debug**: stack traces (Java, C#, Python, JS/TS, Go) and Jaeger/OTLP traces: `scripts/trace_to_scene.py`
- **Build + check**: `scripts/build_scene.py`, `scripts/frames.mjs`

## Install

```sh
npx skills add itssrokay/visualize-debug          # any agent that reads Agent Skills
# or, Claude Code by hand:
git clone https://github.com/itssrokay/visualize-debug ~/.claude/skills/flow-scene
```

Then ask: "map this codebase", "animate what happens on POST /orders", or paste a stack trace and say "visualize this".

Needs python3 (standard library). Optional: node + Playwright for the frame check.

Inspired by [nilbuild/stick-scene](https://github.com/nilbuild/stick-scene) (MIT).
