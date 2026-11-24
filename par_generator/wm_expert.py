"""A DUMMY "WM expert" researcher, served as a real MCP server.

Stands in for the researcher MCP until the real one is connected: same transport (MCP),
same tool shape (one question in, text out), so the page and the client code don't change
when the real server is plugged in through PAR_RESEARCHER_MCP.

Answers are built from a small knowledge base of generic warehouse-management guidance
(wm_expert_kb.json, shared with the web preview) and are labelled as simulated.

Run it on its own, over stdio:  python -m par_generator.wm_expert
"""

from __future__ import annotations

import json
import re
from pathlib import Path

KB = json.loads((Path(__file__).parent / "wm_expert_kb.json").read_text())
TOOL = "ask_wm_expert"
LABEL = "WM Expert (simulated)"


def match_topics(question: str, limit: int = 2) -> list[dict]:
    """Topics ranked by how many of their keywords the question mentions."""
    q = question.lower()
    scored = []
    for i, t in enumerate(KB["topics"]):
        # multi-word keywords ("pick face") say more than single words ("pick")
        hits = sum(len(re.findall(r"\b" + re.escape(k), q)) * len(k.split()) for k in t["keywords"])
        if hits:
            scored.append((-hits, i, t))
    return [t for _, _, t in sorted(scored)[:limit]]


def answer(question: str) -> str:
    topics = match_topics(question) or [KB["generic"]]
    main = topics[0]
    lines = [f"{LABEL} · {main['title']}", "", main["summary"], "", "What to look at:"]
    lines += [f"- {x}" for x in main["look_at"]]
    lines += ["", "Watch out for:"] + [f"- {x}" for x in main["watch_out"]]
    if len(topics) > 1:
        other = topics[1]
        lines += ["", f"Related ({other['title']}): {other['summary']}"]
    lines += ["", f"Suggested next step: {main['next_step']}",
              "", "Simulated answer from the dummy WM expert: check it before using it."]
    return "\n".join(lines)


def server():
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError:  # older SDK
        from mcp.server.fastmcp import FastMCP as MCPServer

    srv = MCPServer("wm-expert")

    @srv.tool(name=TOOL)
    def ask_wm_expert(question: str) -> str:
        """Ask the warehouse-management expert a question about a finding (simulated)."""
        return answer(question)

    return srv


if __name__ == "__main__":
    server().run()
