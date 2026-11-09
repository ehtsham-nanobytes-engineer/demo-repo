"""The agent's source-gathering tool.

Web/news search only. LinkedIn is never scraped — no public API exists for the
feed, profiles or search, and scraping it violates their ToS.
"""

from langchain_core.tools import tool
from langchain_tavily import TavilySearch

from app.config import get_settings
from app.logging_setup import get_logger, preview

log = get_logger("search")


@tool
def web_search(query: str) -> str:
    """Search the web for recent, credible sources on a topic.

    Results are already restricted to recent material and each one carries its
    publication date. Use this to gather fresh sources before writing. Call it
    more than once with different angles (news, statistics, expert commentary)
    when one query does not give you enough to work with.

    Args:
        query: A focused search query, e.g. "FDA AI diagnostics clearance 2026".

    Returns:
        Formatted search results — title, URL, publication date and content
        excerpt per source.
    """
    settings = get_settings()
    if not settings.tavily_api_key:
        log.error("Search skipped — TAVILY_API_KEY is not configured")
        return "Search unavailable: TAVILY_API_KEY is not configured."

    log.info("Searching the web for: %s", query)

    search = TavilySearch(
        max_results=settings.search_max_results,
        topic=settings.search_topic,
        # Recency enforced at the API, not left to the model's phrasing: a
        # query without a year still comes back with this month's material.
        time_range=settings.search_time_range,
        include_answer=False,
        tavily_api_key=settings.tavily_api_key,
    )
    response = search.invoke({"query": query})
    results = response.get("results", []) if isinstance(response, dict) else []

    if not results:
        log.warning("No results for: %s", query)
        return f"No results for: {query}"

    log.info("Found %d source(s) from the last %s", len(results),
             settings.search_time_range)
    for i, r in enumerate(results, start=1):
        log.debug("  [%d] %s (%s) — %s", i,
                  preview(r.get("title", "Untitled"), 70),
                  r.get("published_date") or "undated", r.get("url", ""))

    return "\n\n".join(
        f"[{i}] {r.get('title', 'Untitled')}\n"
        f"URL: {r.get('url', '')}\n"
        f"Published: {r.get('published_date') or 'not stated'}\n"
        f"{r.get('content', '')}"
        for i, r in enumerate(results, start=1)
    )
