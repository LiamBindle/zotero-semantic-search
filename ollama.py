"""Ollama helpers for HyDE and query expansion.

All functions degrade gracefully: if Ollama is unreachable or the call fails,
they return None and the caller falls back to standard embedding.
"""

import logging

import httpx

log = logging.getLogger(__name__)

_HYDE_PROMPT = """\
You are generating text that could appear in a scientific paper, policy document, \
regulation, or academic report. Write a concise passage (3-5 sentences) that would \
be semantically relevant to this query:

{query}

Write only the passage, in the style of technical or academic writing. No preamble.\
"""

_EXPAND_PROMPT = """\
Rewrite the following as a clear, natural-language semantic search query for \
finding relevant passages in scientific papers, policy documents, and regulations. \
Write 1-2 sentences describing the concept, finding, or relationship to search for. \
No bullet points, no headers — output only the rewritten query.

Original: {query}
Rewritten:\
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
