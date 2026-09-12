#!/usr/bin/env python3
"""Chapter galley harness — render a markdown draft as a review galley with an inline flag layer
and a per-flag decision store (Artifact `db` capability).

usage: python3 <skill-dir>/galley.py --md DRAFT.md --flags FLAGS.json --out OUT.html [--config CONFIG.json]
       (the harness runs from wherever it lives; a bare "galley.py" resolves against YOUR project, not the skill)

FLAGS.json: [{"id","severity" (blocker|should-fix|note),"target","line" (1-based line in DRAFT.md),
             "grounding" (ran|read|fetched|assumed),"title","issue","evidence","edit","artifact"?}, ...]
CONFIG.json (optional): {"title","eyebrow","subtitle","governance","verified": [...], "method"}
Each markdown block is emitted with a `<!-- L:n -->` marker (its first source line); a flag is spliced
after the block containing its line. Same page template and tokens as the Paper 1 galley."""
import argparse, json, re, html, datetime

SEV = {"blocker": "Blocker", "should-fix": "Should fix", "note": "Note"}
GRD = {"ran": "ran", "read": "read", "fetched": "fetched", "retrieved": "retrieved", "assumed": "assumed"}
esc = html.escape

# ---------- markdown (subset) → HTML with source-line markers ----------
def inline(s):
    s = esc(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', s)
    return s

def slug(t):
    return re.sub(r"[^a-z0-9]+", "-", re.sub(r"<[^>]+>", "", t).lower()).strip("-")[:60]

def md_to_html(text):
    lines = text.split("\n"); out = []; i = 0; n = len(lines)
    def mark(k): out.append(f"<!-- L:{k+1} -->")
    while i < n:
        ln = lines[i]
        if not ln.strip(): i += 1; continue
        m = re.match(r"^(#{1,4})\s+(.*)$", ln)
        if m:
            lvl = len(m.group(1)); t = inline(m.group(2).strip()); mark(i)
            out.append(f'<h{lvl} id="{slug(t)}">{t}</h{lvl}>'); i += 1; continue
        if re.match(r"^-{3,}\s*$", ln): mark(i); out.append("<hr>"); i += 1; continue
        if ln.startswith(">"):
            mark(i); buf = []
            while i < n and lines[i].startswith(">"): buf.append(lines[i][1:].strip()); i += 1
            out.append(f"<blockquote><p>{inline(' '.join(buf))}</p></blockquote>"); continue
        if ln.lstrip().startswith("|"):
            mark(i); rows = []
            while i < n and lines[i].lstrip().startswith("|"): rows.append(lines[i].strip()); i += 1
            cells = [[c.strip() for c in r.strip("|").split("|")] for r in rows if not re.match(r"^\|?\s*:?-{2,}", r)]
            if cells:
                head = "".join(f"<th>{inline(c)}</th>" for c in cells[0])
                body = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in cells[1:])
                out.append(f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>')
            continue
        lm = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", ln)
        if lm:
            mark(i); ordered = lm.group(2)[0].isdigit(); items = []
            while i < n:
                m2 = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", lines[i])
                if m2: items.append(m2.group(3)); i += 1
                elif lines[i].startswith("  ") and items: items[-1] += " " + lines[i].strip(); i += 1
                else: break
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>"); continue
        mark(i); buf = []
        while i < n and lines[i].strip() and not re.match(r"^(#{1,4}\s|>|\s*\||\s*[-*]\s|\s*\d+\.\s|-{3,}\s*$)", lines[i]):
            buf.append(lines[i].strip()); i += 1
        out.append(f"<p>{inline(' '.join(buf))}</p>")
    return "\n".join(out)

# ---------- flag layer ----------
def flag_html(f):
    art = f.get("artifact")
    artline = f'<span class="flag-art">artifact {esc(art)}</span>' if art else ""
    return f'''
<aside class="flag sev-{f["severity"]}" id="{f["id"]}" data-flag="{f["id"]}" aria-label="Review flag {f["id"]}">
  <header class="flag-head">
    <span class="flag-id">{f["id"]}</span>
    <span class="sev-pill">{SEV[f["severity"]]}</span>
    <span class="flag-target">{esc(f["target"])}</span>
    <span class="flag-anchor"><code>{("line " + str(f["line"])) if f.get("line") else "document-level"}</code></span>
    <span class="flag-ground" title="How this flag was grounded">grounding: <b>{GRD[f["grounding"]]}</b></span>
  </header>
  <h4 class="flag-title">{esc(f["title"])}</h4>
  <dl class="flag-body">
    <dt>Issue</dt><dd>{esc(f["issue"])}</dd>
    <dt>Evidence</dt><dd>{esc(f["evidence"])}</dd>
    <dt>Proposed edit</dt><dd>{esc(f["edit"])}</dd>
  </dl>
  <div class="flag-decide" role="group" aria-label="Decision for {f["id"]}">
    <button type="button" class="btn" data-verdict="accept">Accept</button>
    <button type="button" class="btn" data-verdict="reject">Reject</button>
    <button type="button" class="btn" data-verdict="defer">Defer</button>
    <input type="text" class="flag-note" placeholder="note (optional)" aria-label="Decision note for {f["id"]}">
    <span class="flag-state" data-state>undecided</span>
    {artline}
  </div>
</aside>'''

def splice(body, flags):
    marker = re.compile(r"<!--\s*L:(\d+)\s*-->")
    marks = [(m.start(), int(m.group(1))) for m in marker.finditer(body)]
    inserts = {}
    # A flag with no line is document-level: it lands at the end rather than crashing the build.
    for f in sorted(flags, key=lambda f: f.get("line") or 10**9):
        if not f.get("line"): inserts.setdefault(len(body), []).append(flag_html(f)); continue
        cands = [(pos, ln) for pos, ln in marks if ln <= f["line"]]
        if not cands: inserts.setdefault(len(body), []).append(flag_html(f)); continue
        apos = max(cands, key=lambda t: t[1])[0]
        later = [pos for pos, _ in marks if pos > apos]
        inserts.setdefault(min(later) if later else len(body), []).append(flag_html(f))
    out, last = [], 0
    for pos in sorted(inserts):
        out.append(body[last:pos]); out.extend(inserts[pos]); last = pos
    out.append(body[last:]); return "".join(out)

CSS = r'''
:root{--bg:#F5F7F6;--raised:#FFFFFF;--ink:#1E2A2D;--ink-2:#4A5A5D;--muted:#7E8E8C;--rule:#D5DDDB;--accent:#1A6B66;--accent-soft:#DDEEEC;--code-bg:#ECF1F0;--blocker:#8E2B2B;--blocker-soft:#F6E4E4;--fix:#A5651C;--fix-soft:#F7ECDD;--note:#3D5A78;--note-soft:#E2EAF2;--serif:"Source Serif 4",Georgia,"Times New Roman",serif;--sans:"IBM Plex Sans","Helvetica Neue",Arial,sans-serif;--mono:"IBM Plex Mono",ui-monospace,Menlo,monospace}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#121A1C;--raised:#1A2426;--ink:#E4EAE8;--ink-2:#B5C1BF;--muted:#8A9A98;--rule:#2C393B;--accent:#5CBDB6;--accent-soft:#173D3A;--code-bg:#1F2B2D;--blocker:#E08A8A;--blocker-soft:#3A1E1E;--fix:#E2A65A;--fix-soft:#3A2C18;--note:#8FB0D0;--note-soft:#1E2C3A}}
:root[data-theme="dark"]{--bg:#121A1C;--raised:#1A2426;--ink:#E4EAE8;--ink-2:#B5C1BF;--muted:#8A9A98;--rule:#2C393B;--accent:#5CBDB6;--accent-soft:#173D3A;--code-bg:#1F2B2D;--blocker:#E08A8A;--blocker-soft:#3A1E1E;--fix:#E2A65A;--fix-soft:#3A2C18;--note:#8FB0D0;--note-soft:#1E2C3A}
*,*::before,*::after{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--serif);font-size:17px;line-height:1.55;-webkit-font-smoothing:antialiased}
a{color:var(--accent)} a:focus-visible,button:focus-visible,input:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
code{font-family:var(--mono);font-size:.86em;background:var(--code-bg);padding:.05em .3em;border-radius:3px}
.wrap{max-width:72rem;margin:0 auto;padding:0 1.25rem 4rem}
.galley-head{padding:2.5rem 0 1.5rem;border-bottom:1px solid var(--rule);display:grid;gap:1rem}
.eyebrow{font-family:var(--sans);font-size:.72rem;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
.galley-head h1{font-size:clamp(1.6rem,3.2vw,2.3rem);line-height:1.15;margin:0;text-wrap:balance;font-weight:600}
.galley-head .sub{font-family:var(--sans);color:var(--ink-2);font-size:.95rem;max-width:68ch}
.governance{font-family:var(--sans);font-size:.9rem;line-height:1.5;padding:.9rem 1.1rem;background:var(--accent-soft);border-left:4px solid var(--accent);max-width:68ch} .governance b{color:var(--accent)}
.review-bar{position:sticky;top:0;z-index:5;background:var(--raised);border-bottom:1px solid var(--rule);font-family:var(--sans);font-size:.85rem;display:flex;flex-wrap:wrap;gap:1.25rem;align-items:center;padding:.55rem 0}
.review-bar .stat{display:flex;align-items:center;gap:.4rem;font-variant-numeric:tabular-nums} .review-bar .stat b{font-size:1.05rem}
.sev-dot{display:inline-block;width:.7rem;height:.7rem;border-radius:50%;margin-right:.35rem;vertical-align:-1px}
.sev-dot.sev-blocker{background:var(--blocker)} .sev-dot.sev-should-fix{background:var(--fix)} .sev-dot.sev-note{background:var(--note)}
.progress{margin-left:auto;display:flex;align-items:center;gap:.6rem} .progress .bar{width:11rem;height:.45rem;background:var(--rule);border-radius:99px;overflow:hidden} .progress .bar i{display:block;height:100%;width:0;background:var(--accent);transition:width .25s}
@media (prefers-reduced-motion: reduce){.progress .bar i{transition:none}}
.flag-index{margin:2rem 0 3rem;font-family:var(--sans);font-size:.88rem} .flag-index h2{font-family:var(--serif);font-size:1.25rem;margin:0 0 .6rem} .flag-index .scroll{overflow-x:auto}
.flag-index table{border-collapse:collapse;width:100%;min-width:48rem} .flag-index th,.flag-index td{text-align:left;padding:.45rem .6rem;border-bottom:1px solid var(--rule);vertical-align:top}
.flag-index th{font-weight:600;color:var(--ink-2);font-size:.75rem;letter-spacing:.06em;text-transform:uppercase} .flag-index td.num{font-variant-numeric:tabular-nums;white-space:nowrap}
.verified{font-family:var(--sans);font-size:.88rem;color:var(--ink-2);max-width:68ch;margin:0 0 2.5rem;padding:.9rem 1.1rem;border:1px solid var(--rule);border-radius:4px}
.verified h3{font-size:.8rem;letter-spacing:.08em;text-transform:uppercase;margin:0 0 .5rem;color:var(--muted)} .verified ul{margin:0;padding-left:1.1rem} .verified li{margin:.2rem 0}
.paper{max-width:68ch;margin:0 auto}
.paper h1{font-size:1.9rem;line-height:1.15;text-wrap:balance;margin:2rem 0 .5rem;font-weight:600}
.paper h2{font-size:1.45rem;line-height:1.2;margin:2.6rem 0 .9rem;text-wrap:balance;font-weight:600;border-top:1px solid var(--rule);padding-top:1.4rem}
.paper h3{font-size:1.15rem;margin:1.8rem 0 .5rem;font-weight:600;text-wrap:balance} .paper h4{font-size:1rem;margin:1.3rem 0 .4rem;font-weight:600}
.paper p{margin:0 0 1rem} .paper li{margin:.3rem 0} .paper hr{border:0;border-top:1px solid var(--rule);margin:2rem 0}
.paper table{border-collapse:collapse;font-family:var(--sans);font-size:.85rem;margin:1rem 0;font-variant-numeric:tabular-nums} .paper .table-wrap{overflow-x:auto}
.paper th,.paper td{padding:.35rem .6rem;border-bottom:1px solid var(--rule);text-align:left;vertical-align:top} .paper th{font-weight:600}
.paper blockquote{margin:1.2rem 0;padding:.7rem 1rem;border-left:3px solid var(--accent);background:var(--accent-soft);color:var(--ink-2);font-family:var(--sans);font-size:.88rem;border-radius:0 4px 4px 0} .paper blockquote p{margin:0}
.flag{margin:1.25rem 0 1.75rem;padding:.9rem 1.1rem 1rem;background:var(--raised);border:1px solid var(--rule);border-left:5px solid var(--sev);border-radius:4px;font-family:var(--sans);font-size:.88rem;line-height:1.5;scroll-margin-top:4rem}
.flag.sev-blocker{--sev:var(--blocker);--sev-soft:var(--blocker-soft)} .flag.sev-should-fix{--sev:var(--fix);--sev-soft:var(--fix-soft)} .flag.sev-note{--sev:var(--note);--sev-soft:var(--note-soft)}
.flag-head{display:flex;flex-wrap:wrap;gap:.5rem .9rem;align-items:center;font-size:.76rem;color:var(--ink-2)} .flag-id{font-family:var(--mono);font-weight:600;color:var(--ink)}
.sev-pill{background:var(--sev-soft);color:var(--sev);padding:.1rem .5rem;border-radius:99px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;font-size:.7rem}
.flag-target{text-transform:uppercase;letter-spacing:.06em;font-size:.7rem} .flag-ground b{color:var(--ink)}
.flag-title{font-family:var(--serif);font-size:1.05rem;margin:.5rem 0 .6rem;font-weight:600;line-height:1.3;text-wrap:balance}
.flag-body{display:grid;grid-template-columns:7.5rem 1fr;gap:.35rem .8rem;margin:0} .flag-body dt{font-size:.72rem;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);padding-top:.15rem} .flag-body dd{margin:0}
.flag-decide{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;margin-top:.9rem;padding-top:.7rem;border-top:1px solid var(--rule)}
.btn{font-family:var(--sans);font-size:.8rem;font-weight:500;padding:.35rem .8rem;border:1px solid var(--rule);background:var(--bg);color:var(--ink);border-radius:4px;cursor:pointer} .btn:hover{border-color:var(--accent)} .btn[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);color:#fff}
.flag-note{flex:1 1 14rem;min-width:10rem;font-family:var(--sans);font-size:.82rem;padding:.35rem .5rem;border:1px solid var(--rule);border-radius:4px;background:var(--bg);color:var(--ink)}
.flag-state{font-size:.76rem;color:var(--muted);font-variant-numeric:tabular-nums} .flag-state.decided{color:var(--accent);font-weight:600} .flag-art{margin-left:auto;font-family:var(--mono);font-size:.7rem;color:var(--muted)} .idx-state.decided{color:var(--accent);font-weight:600}
.storage-note{font-family:var(--sans);font-size:.78rem;color:var(--muted);margin:.5rem 0 0}
.method{max-width:68ch;margin:3rem auto 0;font-family:var(--sans);font-size:.85rem;color:var(--ink-2);border-top:1px solid var(--rule);padding-top:1.2rem}
@media (max-width:640px){.flag-body{grid-template-columns:1fr} .flag-body dt{padding-top:.4rem} body{font-size:16px}}
'''

JS = r'''
(() => {
  const FLAGS = __FLAGS__;
  const state = {}; const lsKey = __LSKEY__;
  const $ = (s, r=document) => r.querySelector(s); const $$ = (s, r=document) => Array.from(r.querySelectorAll(s));
  function paint(id) {
    const d = state[id]; const flag = document.getElementById(id); if (!flag) return;
    $$('.btn', flag).forEach(b => b.setAttribute('aria-pressed', String(!!d && b.dataset.verdict === d.verdict)));
    const st = $('[data-state]', flag); st.textContent = d ? `${d.verdict}${d.note ? ' — ' + d.note : ''}` : 'undecided'; st.classList.toggle('decided', !!d);
    const row = $(`.flag-index tr[data-flag="${id}"] .idx-state`); if (row) { row.textContent = d ? d.verdict : 'undecided'; row.classList.toggle('decided', !!d); }
    const n = FLAGS.filter(f => state[f]).length; $('#decided').textContent = n; $('#progress').style.width = (100*n/FLAGS.length) + '%';
  }
  function loadLocal() { try { Object.assign(state, JSON.parse(localStorage.getItem(lsKey) || '{}')); } catch (e) {} FLAGS.forEach(paint); }
  function saveLocal() { try { localStorage.setItem(lsKey, JSON.stringify(state)); } catch (e) {} }
  let db = null;
  async function save(id, verdict) {
    const flag = document.getElementById(id); const note = $('.flag-note', flag).value.trim();
    const rec = { verdict, note, at: new Date().toISOString() }; state[id] = rec; paint(id); saveLocal();
    if (db) { try { await db.doc('decisions/' + id).set(rec); } catch (e) { $('#storage-note').textContent = 'Shared save failed (' + (e && e.code || 'error') + '); kept in this browser.'; } }
  }
  document.addEventListener('click', e => { const b = e.target.closest('.btn[data-verdict]'); if (!b) return; save(b.closest('.flag').dataset.flag, b.dataset.verdict); });
  loadLocal();
  (async () => {
    try { db = await window.claude.use('db'); } catch (e) { db = null; }
    if (!db) return;
    $('#storage-note').textContent = "Decisions save to this artifact's shared store — they follow you across devices and are readable by Claude.";
    db.collection('decisions').onSnapshot(snap => {
      snap.docs.forEach(d => { if (d.exists) { state[d.id] = d.data(); const flag = document.getElementById(d.id); if (flag && state[d.id].note) $('.flag-note', flag).value = state[d.id].note; paint(d.id); } });
      saveLocal();
    }, err => { $('#storage-note').textContent = 'Shared store unavailable (' + err.code + '); decisions kept in this browser.'; });
  })();
})();
'''

def render(md_text, flags, cfg):
    body = splice(md_to_html(md_text), flags)
    counts = {s: sum(1 for f in flags if f["severity"] == s) for s in SEV}
    built = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    index_rows = "\n".join(
        f'<tr data-flag="{f["id"]}"><td><a href="#{f["id"]}">{f["id"]}</a></td><td><span class="sev-dot sev-{f["severity"]}"></span>{SEV[f["severity"]]}</td>'
        f'<td>{esc(f["target"])}</td><td>{esc(f["title"])}</td><td class="num"><code>{("line " + str(f["line"])) if f.get("line") else "document"}</code></td><td class="idx-state" data-state>undecided</td></tr>' for f in flags)
    verified = "".join(f"<li>{v}</li>" for v in cfg.get("verified", []))
    vblock = f'<section class="verified"><h3>Checked and consistent — no flag raised</h3><ul>{verified}</ul></section>' if verified else ""
    return f'''<title>{esc(cfg["title"])}</title>
<!-- No webfont link by design. Whether the Artifact CSP admits fonts.googleapis.com is contested
     between two contract versions and was not observable from either practice; a self-contained page
     renders identically under both readings. The stacks below name Source Serif 4 and IBM Plex first,
     so a reader who has them installed still gets them, and everyone else gets the designed fallback. -->
<style>{CSS}</style>
<div class="wrap">
<header class="galley-head">
  <div class="eyebrow">{esc(cfg.get("eyebrow","Review galley"))} · built {built}</div>
  <h1>{esc(cfg.get("heading", cfg["title"]))}</h1>
  <div class="sub">{cfg.get("subtitle","")}</div>
  <div class="governance">{cfg.get("governance","")}</div>
</header>
<div class="review-bar" id="bar">
  <span class="stat"><span class="sev-dot sev-blocker"></span>Blocker <b>{counts["blocker"]}</b></span>
  <span class="stat"><span class="sev-dot sev-should-fix"></span>Should fix <b>{counts["should-fix"]}</b></span>
  <span class="stat"><span class="sev-dot sev-note"></span>Note <b>{counts["note"]}</b></span>
  <span class="progress"><span>Decided <b id="decided">0</b>/<b>{len(flags)}</b></span><span class="bar"><i id="progress"></i></span></span>
</div>
<section class="flag-index"><h2>Flags</h2><div class="scroll"><table>
  <thead><tr><th>Id</th><th>Severity</th><th>Target</th><th>Flag</th><th>Anchor</th><th>Decision</th></tr></thead><tbody>{index_rows}</tbody></table></div>
  <p class="storage-note" id="storage-note">Decisions save to this artifact's shared store when available, otherwise to this browser only.</p>
</section>
{vblock}
<main class="paper" id="paper">
{body}
</main>
<footer class="method"><p>{cfg.get("method","")}</p></footer>
</div>
<script>{JS.replace("__FLAGS__", json.dumps([f["id"] for f in flags])).replace("__LSKEY__", json.dumps(cfg.get("lskey","galley-decisions")))}</script>
'''

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--md", required=True); ap.add_argument("--flags", required=True); ap.add_argument("--out", required=True); ap.add_argument("--config")
    a = ap.parse_args()
    cfg = json.load(open(a.config)) if a.config else {"title": "Galley"}
    flags = json.load(open(a.flags)); md = open(a.md, encoding="utf-8").read()
    # Anchor by phrase: a flag with "anchor" has its line resolved here, never typed from memory.
    lines = md.split("\n"); unresolved = []; doclevel = []
    for f in flags:
        if f.get("anchor"):
            hits = [i + 1 for i, l in enumerate(lines) if f["anchor"] in l]
            if hits: f["line"] = hits[0]
            else: unresolved.append(f["id"]); f["line"] = f.get("line") or len(lines)
        elif not f.get("line"):
            doclevel.append(f["id"])   # neither anchor nor line: a flag about the document as a whole
    page = render(md, flags, cfg); open(a.out, "w", encoding="utf-8").write(page)
    print("built", a.out, "flags", len(flags), "bytes", len(page),
          "unresolved anchors:", unresolved or "none", "| document-level:", doclevel or "none")
