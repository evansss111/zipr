"""
HERALD — The Scout.
Watches the edge of human knowledge, surfaces concrete signals that a paradigm is shifting.
Fetches real arXiv papers, then asks Claude to extract the most significant pattern.
"""

import logging
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

from zipr import ZiprMessage, state
from ..think import think
from ..config import MODEL_HERALD

log = logging.getLogger("council.herald")

SYSTEM = """\
You are Herald, scout for an eternal council of time-archaeologists.
Your mission: given a list of recent research papers on a topic, identify the 2-3 most \
significant signals that suggest a paradigm is actively shifting or a wave is cresting.

Focus on: unexpected capability jumps, convergence from multiple fields, sudden obsolescence \
of established methods, or results that contradict long-held assumptions.

Output ONLY valid JSON (no prose, no markdown):
{
  "topic": "<topic>",
  "signals": [
    {
      "title": "<paper title>",
      "year": <year int>,
      "signal": "<one sentence: what changed>",
      "why_significant": "<one sentence: why this matters historically>"
    }
  ],
  "summary": "<2-3 sentences synthesizing what the signals collectively suggest>"
}"""


def _fetch_arxiv(topic: str, max_results: int = 8) -> list[dict]:
    """Fetch recent arXiv papers for a topic. Returns list of {title, authors, year, abstract}."""
    query = urllib.parse.quote(topic)
    url = (
        f"http://export.arxiv.org/api/query"
        f"?search_query=all:{query}"
        f"&max_results={max_results}"
        f"&sortBy=submittedDate&sortOrder=descending"
    )
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            xml_data = resp.read().decode("utf-8")
    except Exception as e:
        log.warning("arXiv fetch failed: %s", e)
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        return []

    papers = []
    for entry in root.findall("atom:entry", ns):
        title_el   = entry.find("atom:title", ns)
        summary_el = entry.find("atom:summary", ns)
        published  = entry.find("atom:published", ns)
        if title_el is None:
            continue
        year = int(published.text[:4]) if published is not None else 0
        papers.append({
            "title":    title_el.text.strip().replace("\n", " "),
            "abstract": (summary_el.text or "").strip()[:300],
            "year":     year,
        })

    return papers


def register_herald(bus, session_id: str) -> None:
    @bus.agent("herald")
    async def herald(msg: ZiprMessage) -> None:
        topic = msg.body.get("topic", "AI research")
        log.info("[herald] scanning arXiv for: %s", topic)

        import asyncio
        loop = asyncio.get_running_loop()
        papers = await loop.run_in_executor(None, _fetch_arxiv, topic)

        if papers:
            paper_list = "\n".join(
                f"- [{p['year']}] {p['title']}: {p['abstract'][:150]}"
                for p in papers
            )
            prompt = f"Topic: {topic}\n\nRecent arXiv papers:\n{paper_list}"
        else:
            prompt = (
                f"Topic: {topic}\n\n"
                "No arXiv papers could be fetched (network unavailable). "
                "Based on your knowledge of recent research (up to your training cutoff), "
                "describe 2-3 significant signals in this field as if they were recent papers."
            )

        result = await think(SYSTEM, prompt, model=MODEL_HERALD, max_tokens=700)
        log.info("[herald] signals extracted for topic=%r", topic)

        # Send findings to loom as a state message
        signal_msg = state(
            "herald", "loom",
            {
                "topic":   result.get("topic", topic),
                "signals": result.get("signals", []),
                "summary": result.get("summary", ""),
                "session": session_id,
            },
        )
        await bus.publish(signal_msg)
