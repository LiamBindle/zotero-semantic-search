"""Ollama helpers for HyDE, prompt suggestion, and AI summary.

All functions degrade gracefully: if Ollama is unreachable or the call fails,
they return None / yield nothing and the caller falls back to standard behaviour.
"""

import json as _json
import logging
from typing import AsyncIterator

import httpx

log = logging.getLogger(__name__)

_HYDE_PROMPT = """\
You are generating text that could appear in a scientific paper, policy document, \
regulation, or academic report. Write a concise passage (3-5 sentences) that would \
be semantically relevant to this search prompt:

{query}

Write only the passage, in the style of technical or academic writing. No preamble.\
"""

_EXPAND_PROMPT = """\
Rewrite the following as a clear, natural-language semantic search query for \
finding relevant passages in scientific papers, policy documents, and regulations. \
Output exactly one query: 1–2 sentences describing the concept, finding, or relationship to search for. \
Do not provide alternatives, options, or variations. Do not use the word "or" to offer choices. \
No bullet points, no headers — output only the single rewritten query.

Original: {query}
Rewritten:\
"""

_SUMMARY_PROMPT = """\
The user searched their document library with the following prompt. Using only the provided \
source passages, write a response that directly addresses the prompt. Adapt the length to \
what the prompt requires: 1–2 sentences for narrow factual prompts, 3–5 sentences for broader \
synthesis. Cite each source you draw from using its number in square brackets, e.g. [1], [2]. \
If the passages do not contain enough information, say so briefly.

Search prompt: {query}

Sources:
{sources}

Response:\
"""


def check_available(url: str) -> bool:
    try:
        resp = httpx.get(f"{url}/api/tags", timeout=2.0)
        return resp.status_code == 200
    except Exception:
        return False


async def generate(prompt: str, model: str, url: str, timeout: float = 25.0) -> str | None:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{url}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False},
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp.json().get("response", "").strip() or None
    except Exception as e:
        log.debug("Ollama generate failed: %s", e)
        return None


async def hyde_text(query: str, model: str, url: str) -> str | None:
    return await generate(_HYDE_PROMPT.format(query=query), model, url)


async def expand_query(query: str, model: str, url: str) -> str | None:
    return await generate(_EXPAND_PROMPT.format(query=query), model, url)


async def stream_summary(
    query: str, results: list[dict], model: str, url: str, context_chars: int = 3200
) -> AsyncIterator[str]:
    chars_per = max(100, context_chars // len(results)) if results else 100
    sources = "\n\n".join(
        f"[{i+1}] {r['title']} ({r.get('year') or 'n.d.'}):\n{r['text'][:chars_per]}"
        for i, r in enumerate(results)
    )
    prompt = _SUMMARY_PROMPT.format(query=query, sources=sources)
    try:
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST", f"{url}/api/generate",
                json={"model": model, "prompt": prompt, "stream": True},
                timeout=120.0,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    data = _json.loads(line)
                    token = data.get("response", "")
                    if token:
                        yield token
                    if data.get("done"):
                        return
    except Exception as e:
        log.debug("Ollama stream_summary failed: %s", e)
        return
