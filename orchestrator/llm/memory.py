"""ArcadeDB-backed conversation memory for the LLM."""

from datetime import datetime, timezone
from typing import Optional

from orchestrator.core.database import arcadedb_query


async def store_interaction(
    user_id: str,
    query: str,
    response: str,
    action: Optional[str] = None,
) -> str:
    """Store a single interaction in the graph.

    Graph schema:
    (User)-[:ASKED {timestamp}]->(Query)-[:GENERATED {timestamp}]->
    (Response)-[:TRIGGERED]->(Action)
    """
    ts = datetime.now(timezone.utc).isoformat()

    # Create Query vertex
    q_resp = await arcadedb_query(
        "sql",
        f"CREATE VERTEX Query SET text = '{_escape(query)}', timestamp = '{ts}'",
        readonly=False,
    )
    query_rid = _extract_rid(q_resp)

    # Create Response vertex
    r_resp = await arcadedb_query(
        "sql",
        f"CREATE VERTEX Response SET text = '{_escape(response)}', timestamp = '{ts}'",
        readonly=False,
    )
    response_rid = _extract_rid(r_resp)

    # Link Query -> Response
    await arcadedb_query(
        "sql",
        f"CREATE EDGE GENERATED FROM {query_rid} TO {response_rid} SET timestamp = '{ts}'",
        readonly=False,
    )

    # Link User -> Query
    await arcadedb_query(
        "sql",
        f"CREATE EDGE ASKED FROM {user_id} TO {query_rid} SET timestamp = '{ts}'",
        readonly=False,
    )

    # Optional Action vertex and link
    if action:
        a_resp = await arcadedb_query(
            "sql",
            f"CREATE VERTEX Action SET name = '{_escape(action)}', timestamp = '{ts}'",
            readonly=False,
        )
        action_rid = _extract_rid(a_resp)
        await arcadedb_query(
            "sql",
            f"CREATE EDGE TRIGGERED FROM {response_rid} TO {action_rid}",
            readonly=False,
        )

    return query_rid or ""


async def get_recent_interactions(user_id: str, n: int = 5) -> list[dict]:
    """Return the most recent N interactions for a user."""
    result = await arcadedb_query(
        "gremlin",
        (
            f"g.V('{user_id}').out('ASKED').order().by('timestamp', desc).limit({n})"
            f".as('query').out('GENERATED').as('response')"
            f".select('query','response').by(valueMap())"
        ),
    )
    return result.get("result", [])


async def get_similar_queries(user_id: str, query_text: str, n: int = 3) -> list[dict]:
    """Return past queries that contain similar keywords.

    Simple keyword overlap; can be upgraded to vector similarity.
    """
    keywords = [k.lower() for k in query_text.split() if len(k) > 3]
    if not keywords:
        return []

    # Gremlin filter for keyword containment
    conditions = " || ".join(
        f"it.get().property('text').value().toLowerCase().contains('{k}')"
        for k in keywords
    )
    result = await arcadedb_query(
        "gremlin",
        (
            f"g.V('{user_id}').out('ASKED').filter{{{conditions}}}"
            f".order().by('timestamp', desc).limit({n}).valueMap()"
        ),
    )
    return result.get("result", [])


async def summarize_thread(user_id: str) -> str:
    """Return a plain-text summary of the user's recent conversation thread.

    This is a lightweight placeholder; in production it could call the LLM
    to generate a real summary.
    """
    recent = await get_recent_interactions(user_id, n=10)
    if not recent:
        return "No prior conversation."
    lines = []
    for item in recent:
        q = item.get("query", {}).get("text", ["?"])[0]
        r = item.get("response", {}).get("text", ["?"])[0]
        lines.append(f"Q: {q}\nA: {r}")
    return "\n---\n".join(lines)


def _escape(text: str) -> str:
    """Basic escaping for ArcadeDB string literals."""
    return text.replace("'", "\\'").replace("\n", " ")


def _extract_rid(response: dict) -> Optional[str]:
    results = response.get("result", [])
    if results and isinstance(results[0], dict):
        return results[0].get("@rid")
    return None
