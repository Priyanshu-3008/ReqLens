"""Build the Mermaid use-case diagram in code from structured JSON.

The original project asked the LLM to write Mermaid directly and then patched it with regex,
which often broke. Here the LLM only names actors and use cases; the syntax is generated
deterministically, so the diagram always renders.
"""
from __future__ import annotations

import html
import re


def _label(text: str) -> str:
    text = re.sub(r'["`<>{}\[\]|#;]', "", str(text)).strip()
    return text[:60] or "Unnamed"


def build_mermaid(uc: dict) -> str:
    actors = uc.get("actors", [])
    ucs = uc.get("use_cases", [])
    a_id = {a["name"]: f"A{i}" for i, a in enumerate(actors)}
    u_id = {u["name"]: f"U{i}" for i, u in enumerate(ucs)}

    humans = [a for a in actors if str(a.get("kind", "human")).lower() != "system"]
    systems = [a for a in actors if str(a.get("kind", "human")).lower() == "system"]

    lines = ["flowchart LR"]
    for a in humans:
        lines.append(f'    {a_id[a["name"]]}["👤 {_label(a["name"])}"]:::actor')
    lines.append('    subgraph SYS["System boundary"]')
    for u in ucs:
        lines.append(f'        {u_id[u["name"]]}(["{_label(u["name"])}"]):::usecase')
    lines.append("    end")
    for a in systems:
        lines.append(f'    {a_id[a["name"]]}[["⚙ {_label(a["name"])}"]]:::extsys')

    for u in ucs:
        for name in u.get("actors", []) or []:
            if name not in a_id:
                continue
            if any(a["name"] == name for a in systems):
                lines.append(f"    {u_id[u['name']]} --- {a_id[name]}")
            else:
                lines.append(f"    {a_id[name]} --- {u_id[u['name']]}")
    for inc in uc.get("includes", []):
        b, i = inc.get("base"), inc.get("included")
        if b in u_id and i in u_id:
            lines.append(f"    {u_id[b]} -. include .-> {u_id[i]}")

    lines += [
        "    classDef actor fill:#E8EAF6,stroke:#3949AB,color:#1A237E",
        "    classDef usecase fill:#FFFFFF,stroke:#5C6BC0,color:#1A237E",
        "    classDef extsys fill:#FFF8E1,stroke:#F9A825,color:#5D4037",
    ]
    return "\n".join(lines)


def mermaid_html(code: str, height: int = 600) -> str:
    """Standalone HTML that renders Mermaid via CDN (used with st.components.v1.html)."""
    return f"""
<div id="wrap" style="overflow:auto;height:{height - 20}px;background:#fff;border-radius:8px">
  <pre class="mermaid">{html.escape(code)}</pre>
</div>
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
  mermaid.initialize({{ startOnLoad: false, theme: "default", securityLevel: "strict" }});
  await mermaid.run({{ querySelector: ".mermaid" }});
</script>
"""
