"""Ask the researcher MCP server a question about a finding.

The server is configured with environment variables:

- ``PAR_RESEARCHER_MCP``: where the researcher runs. Either a URL (Streamable HTTP, e.g.
  ``http://localhost:8765/mcp``) or a command line that starts it over stdio
  (e.g. ``npx -y researcher-mcp`` or ``python -m researcher``). The full app usually runs
  on the consultant's PC, so a local researcher works either way.
- ``PAR_RESEARCHER_TOOL`` (optional): the tool to call. By default the only tool, or
  the first one whose name looks like research/ask/question/search.
- ``PAR_RESEARCHER_ARG`` (optional): the tool argument that takes the question. By
  default the first required string argument of the tool's input schema.

Until a real researcher is set, the DUMMY WM expert (par_generator/wm_expert.py) answers:
it runs as an in-process MCP server, so the same client code is exercised. Setting
``PAR_RESEARCHER_MCP=dummy`` asks for it explicitly.

The answer goes into the finding's Researcher column, which is internal: like the
internal comments, it never appears in the report or the deck.
"""

from __future__ import annotations

import os
import re
import shlex

TOOL_HINT = re.compile(r"research|ask|question|query|search|chat", re.I)
TIMEOUT = float(os.environ.get("PAR_RESEARCHER_TIMEOUT", "180"))


class ResearchError(RuntimeError):
    pass


def status() -> dict:
    spec = os.environ.get("PAR_RESEARCHER_MCP", "").strip()
    dummy = not spec or spec.lower() == "dummy"
    return {"configured": not dummy, "dummy": dummy, "name": "WM Expert (simulated)" if dummy else "Researcher"}


def _target(spec: str):
    from mcp import StdioServerParameters

    if re.match(r"^https?://", spec):
        return spec
    parts = shlex.split(spec)
    return StdioServerParameters(command=parts[0], args=parts[1:])


def pick_tool(tools: list, wanted: str | None = None):
    """The tool to call: the configured one, the only one, or the first research-like one."""
    if wanted:
        for t in tools:
            if t.name == wanted:
                return t
        raise ResearchError(f"The researcher has no tool called '{wanted}'. It offers: {', '.join(t.name for t in tools)}.")
    if len(tools) == 1:
        return tools[0]
    for t in tools:
        if TOOL_HINT.search(t.name):
            return t
    if not tools:
        raise ResearchError("The researcher MCP server offers no tools.")
    raise ResearchError("Set PAR_RESEARCHER_TOOL: the researcher offers " + ", ".join(t.name for t in tools) + ".")


def pick_arg(schema: dict | None, wanted: str | None = None) -> str:
    """The argument that takes the question."""
    if wanted:
        return wanted
    props = (schema or {}).get("properties") or {}
    required = (schema or {}).get("required") or []
    strings = [k for k, v in props.items() if (v or {}).get("type") == "string"]
    for k in required:
        if k in strings:
            return k
    for k in ("question", "query", "prompt", "q", "text", "input"):
        if k in strings:
            return k
    if strings:
        return strings[0]
    return "question"


def _text(result) -> str:
    parts = []
    for block in getattr(result, "content", None) or []:
        if getattr(block, "type", None) == "text" and block.text.strip():
            parts.append(block.text.strip())
    if not parts and getattr(result, "structured_content", None):
        import json

        parts.append(json.dumps(result.structured_content, ensure_ascii=False, indent=2))
    return "\n\n".join(parts).strip()


async def ask(question: str, server=None) -> dict:
    """Send the question; returns {"answer", "tool"}. ``server`` overrides the configured
    target (a URL, StdioServerParameters, or an in-process server in tests)."""
    import anyio
    from mcp import Client

    question = (question or "").strip()
    if not question:
        raise ResearchError("Write a question for the researcher first.")
    target = server
    if target is None:
        spec = os.environ.get("PAR_RESEARCHER_MCP", "").strip()
        if not spec or spec.lower() == "dummy":
            from . import wm_expert

            target = wm_expert.server()  # the dummy WM expert, until the real researcher is connected
        else:
            target = _target(spec)
    try:
        with anyio.fail_after(TIMEOUT):
            async with Client(target) as client:
                tools = (await client.list_tools()).tools
                tool = pick_tool(tools, os.environ.get("PAR_RESEARCHER_TOOL") or None)
                arg = pick_arg(getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None),
                               os.environ.get("PAR_RESEARCHER_ARG") or None)
                result = await client.call_tool(tool.name, {arg: question})
    except ResearchError:
        raise
    except TimeoutError as e:
        raise ResearchError("The researcher took too long to answer. Try again, or ask a narrower question.") from e
    except (OSError, FileNotFoundError) as e:
        raise ResearchError(f"Couldn't start or reach the researcher ({e.__class__.__name__}). Check PAR_RESEARCHER_MCP.") from e
    except Exception as e:  # noqa: BLE001 - MCP/transport errors come in many types
        raise ResearchError(f"The researcher returned an error: {e}") from e
    if getattr(result, "is_error", False) or getattr(result, "isError", False):
        raise ResearchError("The researcher reported an error: " + (_text(result) or "no details"))
    answer = _text(result)
    if not answer:
        raise ResearchError("The researcher answered with no text.")
    return {"answer": answer, "tool": tool.name}
