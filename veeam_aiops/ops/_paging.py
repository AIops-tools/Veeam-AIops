"""Page a VBR REST collection to completion.

VBR collection endpoints return ``{"data": [...], "pagination": {"total",
"count", "skip", "limit"}}`` and accept ``skip`` / ``limit``. Since REST
revision 1.3 the server applies ``limit=200`` when the caller sends none
(Veeam's published OpenAPI spec), so reading only the first response silently
drops everything past the 200th item. For anything that *sums* over a
collection — a VM's backup footprint is exactly that — a dropped page is an
under-reported bill, and a repeated page is a double-billed one.

So the loop trusts nothing it can check:

  * ``skip`` advances by what the server actually returned, not by the
    ``limit`` asked for — a server that caps pages below our size still pages
    correctly instead of leaving gaps.
  * items are de-duplicated by ``id``, and a page that adds nothing new (a
    server ignoring ``skip``) is an error, not a reason to loop or to count the
    first page again.
  * the loop ends on the server's own ``pagination.total``; a response without
    a pagination block is taken as the whole collection (nothing to page on).
  * falling short of ``total`` — an empty page, a stalled server, or the page
    budget running out — raises. A bare list has no way to say it is partial.
"""

from __future__ import annotations

from typing import Any

PAGE_SIZE = 200
MAX_PAGES = 500  # a runaway guard, not a quota


class IncompleteCollection(ValueError):  # noqa: N818 — teaching error, reads as a statement
    """The collection could not be read completely; the result would be partial.

    A ``ValueError`` so the MCP layer passes the message through verbatim — the
    remediation is in it, and a generic "operation failed" would drop it.
    """


def _items(data: Any) -> list:
    if isinstance(data, dict):
        items = data.get("data", [])
    else:
        items = data
    return list(items) if isinstance(items, list) else []


def _total(data: Any) -> int | None:
    if not isinstance(data, dict):
        return None
    pagination = data.get("pagination")
    if not isinstance(pagination, dict):
        return None
    total = pagination.get("total")
    if isinstance(total, bool) or not isinstance(total, int):
        return None
    return total


def _key(item: Any) -> Any:
    """Identity for de-duplication; items without an id are never merged."""
    if isinstance(item, dict) and item.get("id") is not None:
        return ("id", str(item["id"]))
    return ("obj", id(item))


def _collect(
    conn: Any,
    path: str,
    *,
    want: int | None,
    params: dict | None,
    headers: dict | None,
    page_size: int,
    max_pages: int,
    lazy: bool,
) -> list[dict]:
    """Page until ``want`` items (``None``: all of them) or the server's total."""
    collected: list[dict] = []
    seen: set = set()
    skip = 0
    total: int | None = None
    for page_no in range(max_pages):
        query = dict(params or {})
        if not (lazy and page_no == 0):
            remaining = page_size if want is None else want - len(collected)
            query.update(skip=skip, limit=min(page_size, remaining))
        kwargs: dict[str, Any] = {"params": query}
        if headers:
            kwargs["headers"] = headers
        data = conn.get(path, **kwargs)
        batch = _items(data)
        total = _total(data)
        fresh = [item for item in batch if _key(item) not in seen]
        seen.update(_key(item) for item in fresh)
        collected.extend(fresh)
        skip += len(batch)
        if total is None or len(collected) >= total or (
            want is not None and len(collected) >= want
        ):
            return collected if want is None else collected[:want]
        if not batch:
            raise IncompleteCollection(
                f"{path}: the server reported {total} items but stopped returning "
                f"them after {len(collected)}. Refusing to return a partial result."
            )
        if not fresh:
            raise IncompleteCollection(
                f"{path}: the server returned a page it had already sent (it does "
                f"not honour skip), after {len(collected)} of {total} items. "
                f"Refusing to return a partial result."
            )
    raise IncompleteCollection(
        f"{path}: stopped after {max_pages} pages with {len(collected)} of {total} "
        f"items; refusing to return a partial result. Narrow the query (a name "
        f"filter, or a single backup)."
    )


def fetch_all(
    conn: Any,
    path: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    page_size: int = PAGE_SIZE,
    max_pages: int = MAX_PAGES,
    lazy: bool = False,
) -> list[dict]:
    """Return every item of a VBR collection, following ``skip``/``limit``.

    ``lazy=True`` sends no paging parameters on the first request, for an
    endpoint whose pinned revision does not declare them (``/backups/{id}/
    objects`` under 1.1-rev1); paging follows only if the server's own
    pagination block shows there is more than it returned.
    """
    return _collect(conn, path, want=None, params=params, headers=headers,
                    page_size=page_size, max_pages=max_pages, lazy=lazy)


def fetch_first(
    conn: Any,
    path: str,
    count: int,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    page_size: int = PAGE_SIZE,
    max_pages: int = MAX_PAGES,
) -> list[dict]:
    """The first ``count`` items in the server's order — never reading further.

    For histories too large to return whole (restore points, sessions): ask for
    ``limit + 1`` and a caller can *measure* truncation instead of guessing it
    from "exactly limit came back".
    """
    return _collect(conn, path, want=count, params=params, headers=headers,
                    page_size=page_size, max_pages=max_pages, lazy=False)


HISTORY_LIMIT_MAX = 1000


def history_limit(value: Any, ceiling: int = HISTORY_LIMIT_MAX) -> int:
    """Validate a caller's ``limit`` for an enveloped history read."""
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= ceiling:
        raise ValueError(f"limit must be an integer between 1 and {ceiling}.")
    return value
