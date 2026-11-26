"""Ask the researcher MCP server a question about a finding.

By default the questions go to Manhattan's researcher MCP at ``DEFAULT_URL``. That host is on
Manhattan's internal network, so the app must run on a machine that can reach it (office
network or VPN). Settings (environment variables):

- ``PAR_RESEARCHER_MCP``: where the researcher runs, if not the default. A URL (Streamable
  HTTP), a command line that starts it over stdio (e.g. ``npx -y researcher-mcp``), or
  ``dummy`` for the simulated WM expert (par_generator/wm_expert.py), handy offline.
- ``PAR_RESEARCHER_TOKEN`` (optional): sent as ``Authorization: Bearer <token>`` if the
  server asks for authentication.
- ``PAR_RESEARCHER_TOOL`` (optional): the tool to call. By default the only tool, or the
  first one whose name looks like research/ask/question/search.
- ``PAR_RESEARCHER_ARG`` (optional): the tool argument that takes the question. By default
  the first required string argument of the tool's input schema.

The answer is logged in the finding's Researcher notes, which are internal: like the
internal comments, they never appear in the report or the deck.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from contextlib import asynccontextmanager
from urllib.parse import urlparse

DEFAULT_URL = "https://alvp-pso-flow.manhdev.com/researcher"
TOOL_HINT = re.compile(r"research|ask|question|query|search|chat", re.I)
TIMEOUT = float(os.environ.get("PAR_RESEARCHER_TIMEOUT", "180"))


class ResearchError(RuntimeError):
    pass


def _spec() -> str:
    return os.environ.get("PAR_RESEARCHER_MCP", "").strip() or DEFAULT_URL


def status() -> dict:
    spec = _spec()
    if spec.lower() == "dummy":
        return {"configured": False, "dummy": True, "name": "WM Expert (simulated)"}
    where = urlparse(spec).hostname if re.match(r"^https?://", spec) else "local command"
    return {"configured": True, "dummy": False, "name": f"Researcher ({where})"}


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
    structured = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if not parts and structured:
        parts.append(json.dumps(structured, ensure_ascii=False, indent=2))
    return "\n\n".join(parts).strip()


# ---- sessions -----------------------------------------------------------------------------
@asynccontextmanager
async def _http_session(url: str):
    from mcp import ClientSession
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    headers = {}
    if os.environ.get("PAR_RESEARCHER_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["PAR_RESEARCHER_TOKEN"].strip()
    async with create_mcp_http_client(headers=headers or None) as http:
        async with streamable_http_client(url, http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                yield session


@asynccontextmanager
async def _client_session(target):
    """In-process server (tests, dummy) or stdio command, through the SDK's Client."""
    from mcp import Client

    async with Client(target) as client:
        yield client


def _url_candidates(url: str) -> list[str]:
    """The URL as given, then its /mcp endpoint: a page URL such as .../researcher often
    serves MCP at .../researcher/mcp."""
    url = url.rstrip("/")
    return [url] if url.endswith(("/mcp", "/sse")) else [url, url + "/mcp"]


def _explain(e: BaseException, url: str) -> str:
    """A user-facing reason for a failed call, from the innermost error."""
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    host = urlparse(url).hostname or url
    text = f"{e.__class__.__name__}: {e}"
    status = getattr(getattr(e, "response", None), "status_code", None)
    if status in (401, 403) or re.search(r"\b(401|403)\b", text):
        return (f"The researcher at {host} refused the request ({status or 'unauthorised'}). "
                "If it needs a token, set PAR_RESEARCHER_TOKEN.")
    if status in (502, 503, 504) or re.search(r"ConnectError|ConnectTimeout|Name or service|nodename|getaddrinfo|resolve|"
                                              r"unreachable|refused|\b50[234]\b", text, re.I):
        hint = (" It's on Manhattan's internal network: run the app on a PC connected to it (office or VPN)."
                if host.endswith(("manh.com", "manhdev.com")) else " Check that it's running and the address is right.")
        return f"Couldn't reach the researcher at {host}.{hint}"
    return f"The researcher at {host} returned an error: {e}"


async def _ask_session(session, question: str) -> dict:
    tools = (await session.list_tools()).tools
    tool = pick_tool(tools, os.environ.get("PAR_RESEARCHER_TOOL") or None)
    arg = pick_arg(getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None),
                   os.environ.get("PAR_RESEARCHER_ARG") or None)
    result = await session.call_tool(tool.name, {arg: question})
    if getattr(result, "is_error", False) or getattr(result, "isError", False):
        raise ResearchError("The researcher reported an error: " + (_text(result) or "no details"))
    answer = _text(result)
    if not answer:
        raise ResearchError("The researcher answered with no text.")
    return {"answer": answer, "tool": tool.name}


async def ask(question: str, server=None) -> dict:
    """Send the question; returns {"answer", "tool", "source"}. ``server`` overrides the
    configured target: a URL, StdioServerParameters, or an in-process server (tests)."""
    import anyio
    from mcp import StdioServerParameters

    question = (question or "").strip()
    if not question:
        raise ResearchError("Write a question for the researcher first.")
    target = server if server is not None else _spec()
    if isinstance(target, str) and target.lower() == "dummy":
        from . import wm_expert

        target = wm_expert.server()
    if isinstance(target, str) and not re.match(r"^https?://", target):
        parts = shlex.split(target)
        target = StdioServerParameters(command=parts[0], args=parts[1:])

    try:
        with anyio.fail_after(TIMEOUT):
            if isinstance(target, str):  # an HTTP MCP endpoint
                last = None
                for url in _url_candidates(target):
                    try:
                        async with _http_session(url) as session:
                            out = await _ask_session(session, question)
                        out["source"] = url
                        return out
                    except ResearchError:
                        raise
                    except Exception as e:  # noqa: BLE001
                        msg = _explain(e, url)
                        if msg.startswith("Couldn't reach") or "refused the request" in msg:
                            raise ResearchError(msg) from e  # same host: another path won't help
                        last = (e, url)
                raise ResearchError(_explain(*last)) from last[0]
            async with _client_session(target) as session:
                out = await _ask_session(session, question)
            out["source"] = "dummy" if server is None else "local"
            return out
    except ResearchError:
        raise
    except TimeoutError as e:
        raise ResearchError("The researcher took too long to answer. Try again, or ask a narrower question.") from e
    except (OSError, FileNotFoundError) as e:
        raise ResearchError(f"Couldn't start or reach the researcher ({e.__class__.__name__}). Check PAR_RESEARCHER_MCP.") from e
    except Exception as e:  # noqa: BLE001 - MCP/transport errors come in many types
        raise ResearchError(f"The researcher returned an error: {e}") from e


async def list_tools(target: str | None = None) -> list[dict]:
    """The researcher's tools with their arguments: [{"name", "description", "arguments"}]."""
    target = target or _spec()
    last = None
    for url in _url_candidates(target):
        try:
            async with _http_session(url) as session:
                tools = (await session.list_tools()).tools
            return [{"name": t.name, "description": (t.description or "").strip(), "url": url,
                     "arguments": (getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {}).get("properties", {})}
                    for t in tools]
        except Exception as e:  # noqa: BLE001
            last = (e, url)
            if _explain(e, url).startswith("Couldn't reach"):
                break
    raise ResearchError(_explain(*last))


if __name__ == "__main__":  # python -m par_generator.research  -> list the researcher's tools
    import sys

    import anyio

    try:
        for t in anyio.run(list_tools, sys.argv[1] if len(sys.argv) > 1 else None):
            args = ", ".join(f"{k} ({(v or {}).get('type', '?')})" for k, v in t["arguments"].items())
            print(f"{t['name']}({args})\n    {t['description'][:300]}\n    at {t['url']}\n")
    except ResearchError as e:
        sys.exit(str(e))
