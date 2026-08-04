---
name: render
description: "Use when the user says '/render', 'render this', 'render diagrams', 'generate SVG', 'mdview render', or wants to render markdown files with ASCII art diagrams to HTML via mdview. This skill generates DiagramSpec JSON for ASCII art blocks, embeds specs as HTML comments in the markdown, and calls mdview to produce themed SVG output. The AI provides the structural intelligence that makes asciisvg rendering accurate."
version: 1.0.0
---

# Render: ASCII Art → Themed SVG via mdview

**Generate DiagramSpec for ASCII art. Embed in markdown. Render via mdview.**

AIs know what they're drawing. This skill serializes that knowledge as DiagramSpec JSON
so mdview's asciisvg renderer produces accurate, themed SVG — no heuristics, no network
calls to kroki.io.

---

## How to Run

```
/render EPISTEMIC_MAP.md              # Render specific file
/render                               # Render all .md files in current directory
/render --serve                       # Render and start live-reload server
/render --check                       # Audit: show which files have/lack specs
```

---

## Phase 1: Discover ASCII Art Blocks

Read the target markdown file(s). For each file:

1. Find all fenced code blocks (` ``` `)
2. Classify each block:
   - **`mermaid`** → skip (mdview handles these via mermaid.ink)
   - **`svgbob`/`ditaa`** → skip (explicitly typed)
   - **Unmarked or `text`/`ascii`** → check if ASCII art (box-drawing chars, arrows, borders)
3. For each ASCII art block, check if a `<!-- diagram-spec: {...} -->` comment already exists in the preceding lines
4. Blocks WITH specs → already handled, skip
5. Blocks WITHOUT specs → need spec generation (Phase 2)

Report what was found:
```
Found 3 ASCII art blocks in EPISTEMIC_MAP.md:
  Block 1 (lines 15-77): No spec — NEEDS GENERATION
  Block 2 (lines 83-103): No spec — NEEDS GENERATION
  Block 3 (lines 120-145): Has spec — OK
```

---

## Phase 2: Generate DiagramSpec JSON

For each ASCII art block without a spec, analyze the diagram and generate a DiagramSpec.

### DiagramSpec Schema

```json
{
  "type": "<flow|sequence|box|table|wireframe|state_machine>",
  "title": "Optional title",
  "layout": "<auto|horizontal|vertical|grid|nested|sequence>",
  "elements": [
    {
      "id": "unique_id",
      "label": "Display text",
      "type": "<node|actor|box|panel|header|row|decision>",
      "children": ["child_id_1"],
      "properties": {
        "sections": [["line 1", "line 2"], ["section 2 line 1"]]
      }
    }
  ],
  "connections": [
    {
      "from": "source_id",
      "to": "target_id",
      "label": "Optional label",
      "style": "solid"
    }
  ]
}
```

### Type Selection Guide

| ASCII Pattern | DiagramSpec Type | Layout |
|---------------|-----------------|--------|
| Boxes with arrows between them | `flow` | `horizontal` or `vertical` |
| Vertical lifelines with horizontal messages | `sequence` | `sequence` |
| Standalone boxes with sections/content | `box` | `horizontal` or `vertical` |
| Rows and columns of data | `table` | `grid` |
| UI mockup with panels | `wireframe` | `nested` |
| States with transitions | `state_machine` | `auto` |
| Nested boxes (box within box) | `box` with `children` | `vertical` |

### Box Diagram Guide (Most Common)

For boxes with sections (headers + content lines):

```json
{
  "type": "box",
  "title": "System Overview",
  "layout": "horizontal",
  "elements": [
    {
      "id": "auth",
      "label": "Auth Service",
      "type": "box",
      "properties": {
        "sections": [
          ["JWT validation", "Role guards", "Session mgmt"]
        ]
      }
    },
    {
      "id": "api",
      "label": "API Gateway",
      "type": "box",
      "properties": {
        "sections": [
          ["Rate limiting", "Request routing"],
          ["Health checks"]
        ]
      }
    }
  ],
  "connections": [
    {"from": "auth", "to": "api", "label": "validates"}
  ]
}
```

Each entry in `sections` is a group of lines separated by a visual divider in the box.

### Flow Diagram Guide

For nodes connected by arrows:

```json
{
  "type": "flow",
  "layout": "vertical",
  "elements": [
    {"id": "start", "label": "PREFLIGHT", "type": "node"},
    {"id": "check", "label": "Ready?", "type": "decision"},
    {"id": "work", "label": "Do Work", "type": "node"},
    {"id": "end", "label": "POSTFLIGHT", "type": "node"}
  ],
  "connections": [
    {"from": "start", "to": "check"},
    {"from": "check", "to": "work", "label": "yes"},
    {"from": "work", "to": "end"}
  ]
}
```

Elements with `"type": "decision"` render as diamonds.

### Sequence Diagram Guide

For actors exchanging messages:

```json
{
  "type": "sequence",
  "layout": "sequence",
  "elements": [
    {"id": "client", "label": "Client", "type": "actor"},
    {"id": "server", "label": "Server", "type": "actor"},
    {"id": "db", "label": "Database", "type": "actor"}
  ],
  "connections": [
    {"from": "client", "to": "server", "label": "POST /login", "properties": {"order": 1}},
    {"from": "server", "to": "db", "label": "SELECT user", "properties": {"order": 2}},
    {"from": "db", "to": "server", "label": "user record", "properties": {"order": 3, "direction": "return"}},
    {"from": "server", "to": "client", "label": "JWT token", "properties": {"order": 4, "direction": "return"}}
  ]
}
```

### Critical Rules

1. **Every element needs a unique `id`** — use short, descriptive slugs (e.g., `"auth"`, `"phase2_goal1"`)
2. **Connections reference element IDs** via `"from"` and `"to"` keys (NOT `from_id`/`to_id`)
3. **Read the ASCII art carefully** — the diagram structure is already there, you're just serializing it
4. **Preserve all text content** — every label, annotation, status marker in the ASCII art should appear in the spec
5. **Nested boxes** — use `children` array on parent elements to reference child element IDs
6. **Layout matters** — horizontal for wide diagrams, vertical for tall ones

---

## Phase 3: Embed Specs in Markdown

For each generated spec, insert a `<!-- diagram-spec: {...} -->` HTML comment on the
line immediately before the opening ` ``` ` fence of the code block.

**Format:** Single line, minified JSON (no pretty-printing — it's a comment, not for humans):

```markdown
<!-- diagram-spec: {"type":"box","layout":"horizontal","elements":[...],"connections":[...]} -->
` `` `
┌─────────────┐    ┌─────────────┐
│  Auth        │───>│  API        │
└─────────────┘    └─────────────┘
` `` `
```

**Important:** The ASCII art stays unchanged. The spec comment is metadata that tells
mdview how to render it. Humans still see the ASCII art in their editor.

Use the Edit tool to insert the comment line before each code block that needs one.

---

## Phase 4: Render

After embedding all specs, render via mdview:

```bash
# Render to HTML
mdview <file>.md --output rendered/<name>.html --no-open

# Or render and open in browser
mdview <file>.md --output rendered/<name>.html
```

---

## Phase 5: Verify

Check the rendered output:

```bash
# Count asciisvg-rendered diagrams (should match total ASCII blocks)
grep -c 'mdview-diagram' rendered/<name>.html

# Count kroki/svgbob fallbacks (should be 0)
grep -c 'class="svgbob"' rendered/<name>.html

# Count render failures (should be 0)
grep -c 'diagram-fallback' rendered/<name>.html
```

Report results:
```
Rendered EPISTEMIC_MAP.md:
  2 diagrams via asciisvg (themed SVG) ✓
  0 kroki/svgbob fallbacks ✓
  0 render failures ✓
  Output: rendered/epistemic-map.html
```

If any block fell through to svgbob or failed, the spec JSON is wrong — fix it and re-render.

---

## --check Mode

When run with `--check`, don't modify files. Just audit and report:

```
ASCII Art Spec Coverage:
  EPISTEMIC_MAP.md:     0/2 blocks have specs  ← NEEDS WORK
  INTEGRATION_MAP.md:   1/1 blocks have specs  ✓
  ARCHITECTURE.md:      0/3 blocks have specs  ← NEEDS WORK
  DIAGRAMS.md:          0/0 ASCII blocks        ✓ (all Mermaid)
  SPEC.md:              0/1 blocks have specs  ← NEEDS WORK

Total: 1/7 ASCII blocks have embedded DiagramSpec (14%)
```

---
