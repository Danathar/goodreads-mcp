"""goodreads-mcp — a read-only MCP server for Goodreads, sans API.

Tools (all public data, no auth):

    search_books        JSON autocomplete endpoint
    get_book            __NEXT_DATA__ / Apollo state on book pages (.xml path)
    get_reviews         paginated reviews via the AppSync GraphQL backend
    similar_books       "readers also enjoyed" (GraphQL)
    author_books        an author's bibliography (GraphQL)
    series_books        books in a series, with reading order (GraphQL)
    get_editions        published editions: formats/ISBNs (GraphQL)
    book_lists          Listopia lists a book appears on (GraphQL)
    popular_books       most popular books by release year/month (GraphQL)
    compare_books       rank several books by rating + polarization
    get_shelf           shelf RSS feed
    list_shelves        scraped from the public profile page (best effort)

NOTE: book HTML pages now sit behind an AWS WAF JS challenge (HTTP 202).
get_book routes around it via the .xml path. The client raises WAFChallenge
if it ever gets a challenge body so failures are obvious, not silent. The
review-list page (/review/list/{uid}) went login-only in Sep 2026; the
client raises LoginRequired on a sign-in redirect for the same reason.

get_reviews uses Goodreads' AppSync GraphQL endpoint; the client resolves the
public API key from page-level Next data and the endpoint from the web bundle
at runtime (see client.graphql_config).

The tool bodies are plain `def`s that block on httpx. `OffLoopFastMCP` runs
each call in a worker thread, so the event loop keeps answering pings while a
request is in flight, and `client.MAX_IN_FLIGHT` caps how many of those
requests overlap.
"""

from __future__ import annotations

import functools
import html as html_mod
import importlib.metadata
import inspect
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import unquote

import anyio  # mcp's own async layer (it requires anyio>=4.5), not a new dependency
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .client import BASE, GoodreadsClient, GraphQLError, LoginRequired, ToolCall
from .config import load_user_id

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)


def _in_worker_thread(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a blocking tool body in a coroutine that runs it off the loop.

    `functools.wraps` carries the name, docstring and signature across, so
    FastMCP builds the same tool schema it would from `fn` itself. A cancelled
    request (the client sent notifications/cancelled, or went away) returns
    at once and sets the call's cancel flag (`client.ToolCall`), so the thread
    sends no further Goodreads request. A request already on the wire is not
    interrupted: it runs until Goodreads answers or the client's 30 s timeout
    fires, and its result is dropped.
    """

    @functools.wraps(fn)
    async def run_off_loop(*args: Any, **kwargs: Any) -> Any:
        call = ToolCall()
        try:
            return await anyio.to_thread.run_sync(
                functools.partial(call.run, fn, *args, **kwargs), abandon_on_cancel=True
            )
        except anyio.get_cancelled_exc_class():
            call.cancel()
            raise

    return run_off_loop


class OffLoopFastMCP(FastMCP):
    """A FastMCP whose plain `def` tools run in a worker thread.

    The MCP SDK calls a sync tool directly on the event loop, so while a tool
    waited on Goodreads (30 s timeout, plus up to 7 s of 429/503 backoff) the
    server could not answer a ping, act on a cancellation, or start another
    tool call (#92). Registering an async wrapper keeps the loop free. The
    functions themselves stay sync, so `compare_books` can call `get_book`
    and the offline tests call the bodies without an event loop. Cancelling
    a call stops its thread before its next Goodreads request (#206), so an
    abandoned call does not keep taking `client.MAX_IN_FLIGHT` slots.
    """

    def add_tool(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        if not inspect.iscoroutinefunction(fn):
            fn = _in_worker_thread(fn)
        super().add_tool(fn, *args, **kwargs)


SERVER_INSTRUCTIONS = """\
This server returns public Goodreads data (books, reviews, shelves) for research.

Citations by default: every result includes source 'url' fields. When you use
this data in a response, cite it with those links rather than stating facts
unsourced:
  * Books — link the title to the book's 'url' (from get_book / search_books /
    similar_books / etc.).
  * Reviews — when you quote or paraphrase a review, link it to that review's
    'url' and attribute it to the reviewer (by name, optionally their
    'reviewer_url'). Note each review's star 'rating'.
  * Ratings/stats — when citing an average rating or the ratings_histogram,
    point to the book's 'url'.
  * Shelves — link books to their 'link' field.

Prefer markdown links. If a result's url field is null, say so rather than
inventing a link.
"""

mcp = OffLoopFastMCP("goodreads", instructions=SERVER_INSTRUCTIONS)
# FastMCP takes no version, so `initialize` would report the mcp SDK's own
# version as ours. Left None (the SDK default) when not installed as a package.
try:
    mcp._mcp_server.version = importlib.metadata.version("goodreads-mcp-ai")
except importlib.metadata.PackageNotFoundError:
    pass
gr = GoodreadsClient()
DEFAULT_USER_ID = load_user_id()


def _user_id(user_id: str | None) -> str:
    """The Goodreads user id to use: the argument, else the configured
    default. Either must be the number, optionally followed by '-' and the
    name slug, as in goodreads.com/user/show/<ID>-name (#205)."""
    uid = str(user_id or "").strip() or str(DEFAULT_USER_ID or "").strip()
    if not uid:
        raise ValueError(
            "No user_id given and GOODREADS_USER_ID is not configured. "
            "It's the number in goodreads.com/user/show/<ID>-name."
        )
    if not _USER_ID_RE.fullmatch(uid):
        raise ValueError(
            "user_id must be a Goodreads user id: the number, optionally "
            "followed by '-' and the name slug, e.g. '1' or '1-otis-chandler'. "
            "It's the number in goodreads.com/user/show/<ID>-name; pass just "
            f"that, not the URL. Got {uid!r}."
        )
    return uid


def _clean_text(s: str | None) -> str:
    """Strip HTML tags, unescape entities, and collapse whitespace."""
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html_mod.unescape(s)).strip()


def _ms_to_iso(ms: Any) -> str | None:
    """Epoch-milliseconds -> YYYY-MM-DD (UTC), or None.

    Adds the offset to the epoch instead of calling ``fromtimestamp``: the
    Windows C runtime rejects negative timestamps, and Goodreads uses them
    for books published before 1970.
    """
    if not ms:
        return None
    try:
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        return (epoch + timedelta(milliseconds=ms)).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def _fetch_book_apollo(book_id: str) -> dict[str, Any]:
    """Fetch a book page and return its Apollo state.

    The plain /book/show/{id} HTML page now sits behind an AWS WAF JS
    challenge (HTTP 202). The .xml-suffixed path serves the identical
    Next.js page with __NEXT_DATA__ intact and is not challenged.
    """
    bid = _match_book_id(book_id).group(0)
    if not bid.endswith(".xml"):
        bid += ".xml"
    page = gr.get(f"/book/show/{bid}")
    try:
        apollo = gr.parse_next_data(page.text)["props"]["pageProps"]["apolloState"]
    except (KeyError, TypeError):
        apollo = None
    # A renamed key would otherwise reach the model as a bare KeyError,
    # "Error executing tool get_book: 'apolloState'" (#229).
    if not isinstance(apollo, dict):
        raise ValueError(f"No apolloState in the __NEXT_DATA__ of book page {bid!r}.")
    return apollo


def _make_deref(apollo: dict[str, Any]):
    def deref(ref_obj: Any) -> dict:
        if isinstance(ref_obj, dict) and "__ref" in ref_obj:
            return apollo.get(ref_obj["__ref"], {})
        return ref_obj or {}

    return deref


def _find_book(apollo: dict[str, Any], book_id: str) -> dict[str, Any]:
    """The Book the page is for: the one its ROOT_QUERY getBookByLegacyId
    field points at. A book page also carries stub Book entries for other
    editions (legacyId and webUrl, no title), sometimes ahead of the page's
    own book, so "the first Book" is not a safe rule (#229). A page without
    that field falls back to the first Book that has a title.
    """
    root = apollo.get("ROOT_QUERY")
    refs = [
        value
        for key, value in (root.items() if isinstance(root, dict) else ())
        if key.startswith("getBookByLegacyId(")
    ]
    if refs:
        book = _make_deref(apollo)(refs[0])
        if not book.get("title"):
            raise ValueError(
                f"No Book object in Apollo state for '{book_id}': "
                f"getBookByLegacyId points at {refs[0]!r}."
            )
        return book
    book = next(
        (v for k, v in apollo.items() if k.startswith("Book:") and v.get("title")),
        None,
    )
    if not book:
        raise ValueError(f"No Book object in Apollo state for '{book_id}'.")
    return book


# A Goodreads id is a number without a leading zero, optionally followed by
# the title (or user name) slug: '54493401', '11870085-the-fault-in-our-stars',
# or the older dot form '2767052.The_Hunger_Games'. Checking the whole string,
# not just its leading digits, keeps an ISBN, a URL or a query string from
# being read as whatever number it starts with, and from reaching the URL
# path (#205). The slug never holds / ? # & = % or whitespace.
_SLUG = r"[\w.-]+"
_BOOK_ID_RE = re.compile(rf"([1-9][0-9]*)(?:[-.]{_SLUG})?")
_USER_ID_RE = re.compile(rf"([1-9][0-9]*)(?:-{_SLUG})?")
_GRAPHQL_INT_MAX = 2**31 - 1


def _is_isbn(value: str) -> bool:
    """True for a checksum-valid ISBN-13 (978/979) or ISBN-10.

    Hyphens and spaces are ignored in an ISBN-13. An ISBN-10 counts only as
    one run of ten characters or split into its four groups: a book id with a
    short all-digit slug ('100000000-1') is ten characters too, and stays a
    book id.
    """
    s = re.sub(r"[-\s]", "", value).upper()
    if re.fullmatch(r"97[89][0-9]{10}", s):
        return sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(s)) % 10 == 0
    groups = len(re.split(r"[-\s]+", value))
    if re.fullmatch(r"[0-9]{9}[0-9X]", s) and groups in (1, 4):
        total = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(s))
        return total % 11 == 0
    return False


def _match_book_id(book_id: Any) -> re.Match[str]:
    """Validate a book_id (surrounding whitespace ignored). Group 0 is the
    id to put in a URL path, group 1 its number."""
    bid = str(book_id).strip()
    if _is_isbn(bid):
        raise ValueError(
            f"book_id {bid!r} is an ISBN, not a Goodreads book id. "
            "search_books finds a book by ISBN; pass the book_id it returns."
        )
    m = _BOOK_ID_RE.fullmatch(bid)
    # The backend takes the number as a GraphQL Int (32-bit); a longer one is
    # a typo'd ISBN or another number, never a book (#205).
    if m and int(m.group(1)) <= _GRAPHQL_INT_MAX:
        return m
    raise ValueError(
        "book_id must be a Goodreads book id: the number, optionally followed "
        "by '-' or '.' and the title slug, e.g. '54493401', "
        "'11870085-the-fault-in-our-stars' or '2767052.The_Hunger_Games'. "
        "For a goodreads.com/book/show/<ID>... URL pass just the number. "
        f"Got {bid!r}."
    )


def _legacy_id(book_id: str) -> int:
    """The numeric legacy id of a validated book_id: 54493401 for
    '54493401', '54493401-slug' or '54493401.Slug'."""
    return int(_match_book_id(book_id).group(1))


def _book_id(legacy_id: Any) -> str | None:
    """Render a GraphQL ``legacyId`` (an Int) as the string every tool's
    ``book_id`` parameter takes, so one tool's output feeds the next through
    the MCP layer without the host coercing it (#193). None stays None."""
    return None if legacy_id is None else str(legacy_id)


# --- GraphQL query documents (recovered from the web app's JS bundles) ----
_Q_BOOK_BY_LEGACY = (
    "query($id: Int!){ getBookByLegacyId(legacyId:$id)"
    "{ legacyId titleComplete title work{ id } } }"
)
_Q_REVIEWS = """
query($filters: BookReviewsFilterInput!, $pagination: PaginationInput){
  getReviews(filters: $filters, pagination: $pagination){
    totalCount
    edges{ node{
      rating text spoilerStatus likeCount commentCount createdAt
      creator{ name webUrl }
      shelving{ webUrl }
    }}
    pageInfo{ nextPageToken }
  }
}"""

# Be a polite guest: cap how many reviews one call will page through, and how
# many pages it may request. exclude_spoilers filters after the fetch, so the
# review cap alone does not bound requests; the page cap allows twice the 4
# pages an unfiltered call at _MAX_REVIEWS needs.
_MAX_REVIEWS = 100
_REVIEW_PAGE_SIZE = 30
_MAX_REVIEW_PAGES = 8

# Resolve a book to the kca ids the discovery queries need.
_Q_BOOK_IDS = (
    "query($id: Int!){ getBookByLegacyId(legacyId:$id){"
    " id legacyId titleComplete title"
    " work{ id }"
    " primaryContributorEdge{ node{ id name webUrl } }"
    " bookSeries{ userPosition series{ id title } } } }"
)
_Q_SIMILAR = """
query($id: ID!, $pagination: PaginationInput){
  getSimilarBooks(id: $id, pagination: $pagination){
    pageInfo{ hasNextPage nextPageToken }
    edges{ node{
      legacyId title webUrl imageUrl
      work{ stats{ averageRating ratingsCount } }
      primaryContributorEdge{ node{ name } }
    }}
  }
}"""
_Q_EDITIONS = """
query($id: ID!, $pagination: PaginationInput){
  getEditions(id: $id, pagination: $pagination){
    totalCount
    pageInfo{ hasNextPage nextPageToken }
    edges{ node{
      legacyId title webUrl imageUrl
      details{ format publicationTime publisher isbn13 numPages language{ name } }
    }}
  }
}"""
_Q_SERIES = """
query($input: GetWorksForSeriesInput!, $pagination: PaginationInput){
  getWorksForSeries(getWorksForSeriesInput: $input, pagination: $pagination){
    pageInfo{ hasNextPage nextPageToken }
    edges{
      seriesPlacement isPrimary
      node{ stats{ averageRating ratingsCount } bestBook{
        legacyId title webUrl imageUrl primaryContributorEdge{ node{ name } } } }
    }
  }
}"""
_Q_AUTHOR = """
query($input: GetWorksByContributorInput!, $pagination: PaginationInput){
  getWorksByContributor(getWorksByContributorInput: $input, pagination: $pagination){
    totalCount
    pageInfo{ hasNextPage nextPageToken }
    edges{ node{ stats{ averageRating ratingsCount } bestBook{
      legacyId title webUrl imageUrl primaryContributorEdge{ node{ name } } } }
    }
  }
}"""
_Q_BOOK_LISTS = """
query($id: ID!, $pagination: PaginationInput){
  getBookListsOfBook(id: $id, paginationInput: $pagination){
    pageInfo{ hasNextPage nextPageToken }
    edges{ node{ legacyId title webUrl userListVotesCount listBooksCount } }
  }
}"""
_Q_TOP_LIST = """
query($name: String!, $period: String!, $location: String!,
      $after: String, $limit: Int){
  getTopList(
    getTopListInput: { name: $name, period: $period, location: $location },
    pagination: { after: $after, limit: $limit }
  ){
    pageInfo{ hasNextPage nextPageToken }
    edges{
      ... on TopListBookEdge {
        rank count
        node{ __typename legacyId title webUrl imageUrl
          work{ stats{ averageRating ratingsCount } }
          primaryContributorEdge{ node{ name } } }
      }
      ... on TopListWorkEdge {
        rank count
        node{ __typename stats{ averageRating ratingsCount }
          details{ bestBook{ legacyId title webUrl imageUrl
            primaryContributorEdge{ node{ name } } } } }
      }
    }
  }
}"""

# Discovery connections are paginated in small requests and capped in total.
_MAX_DISCOVERY = 100
_DISCOVERY_PAGE_SIZE = 20
# A page of nulls alone does not end a walk, so the cap on results no longer
# bounds the requests; this does, at twice the pages a full walk needs.
_MAX_DISCOVERY_PAGES = 2 * -(-_MAX_DISCOVERY // _DISCOVERY_PAGE_SIZE)
# popular_books paginates; cap total and page size.
_MAX_POPULAR = 50
_POPULAR_PAGE_SIZE = 30
# compare_books fetches one book page per id; cap the fan-out.
_MAX_COMPARE = 10


def _validate_discovery_limit(limit: int) -> int:
    if limit < 0:
        raise ValueError("limit must be zero or greater.")
    return min(limit, _MAX_DISCOVERY)


def _paginated_graphql_edges(
    query: str,
    connection_name: str,
    variables: dict[str, Any],
    limit: int,
) -> tuple[list[dict[str, Any]], bool, int | None]:
    """Collect a bounded number of edges from a GraphQL connection.

    Returns ``(edges, has_more, total_count)``; every returned edge has a
    ``node``. Null edges and edges whose node is null are skipped and not
    counted. Every supported discovery connection uses Goodreads' standard
    PaginationInput and PageInfo shapes.
    """
    want = _validate_discovery_limit(limit)
    if want == 0:
        return [], False, None

    collected: list[dict[str, Any]] = []
    token: str | None = None
    total_count: int | None = None
    has_more = False
    seen_tokens: set[str] = set()
    # Edges read so far, the skipped null ones included: they are not unread
    # results, so they must not make `totalCount` report more.
    read = 0
    pages = 0

    while len(collected) < want and pages < _MAX_DISCOVERY_PAGES:
        # Goodreads' cursor is a page number and the server derives the
        # offset from the limit sent with each request, so the page size
        # must stay constant across the walk. Overshoot is trimmed below.
        pagination: dict[str, Any] = {"limit": _DISCOVERY_PAGE_SIZE}
        if token:
            pagination["after"] = token
        page_variables = {**variables, "pagination": pagination}
        connection = gr.graphql(query, page_variables).get(connection_name) or {}
        pages += 1
        if total_count is None:
            total_count = connection.get("totalCount")

        raw_edges = connection.get("edges") or []
        page_edges = [e for e in raw_edges if e and e.get("node")]
        read += len(raw_edges)
        remaining = want - len(collected)
        collected.extend(page_edges[:remaining])

        info = connection.get("pageInfo") or {}
        next_token = info.get("nextPageToken")
        has_more = bool(info.get("hasNextPage") and next_token)
        if len(page_edges) > remaining:
            has_more = True
        # An empty page ends the walk; a page of nulls alone does not.
        if len(collected) >= want or not raw_edges or not has_more:
            break
        if next_token in seen_tokens:
            break
        seen_tokens.add(next_token)
        token = next_token

    if total_count is not None and read < total_count:
        has_more = True
    return collected, has_more, total_count


def _book_by_legacy_id(query: str, book_id: str) -> dict[str, Any]:
    """Run a `getBookByLegacyId` query for `book_id` and return the book.

    AppSync fails the root field with RESOURCE_NOT_FOUND for an id it does
    not know; that becomes the same ValueError as a null book.
    """
    try:
        book = gr.graphql(query, {"id": _legacy_id(book_id)}).get("getBookByLegacyId")
    except GraphQLError as e:
        if not e.not_found:
            raise
        book = None
    if not book:
        raise ValueError(f"No book found for id {book_id!r}.")
    return book


def _resolve_book_ids(book_id: str) -> dict[str, Any]:
    """Resolve a book_id to its book/work/contributor/series identifiers,
    legacyId, title, and every series membership in one GraphQL call."""
    book = _book_by_legacy_id(_Q_BOOK_IDS, book_id)
    contributor = (book.get("primaryContributorEdge") or {}).get("node") or {}
    series_memberships = []
    for membership in book.get("bookSeries") or []:
        if not membership:
            continue
        series = membership.get("series") or {}
        series_memberships.append(
            {
                "id": series.get("id"),
                "title": series.get("title"),
                "position": membership.get("userPosition"),
            }
        )
    first_series = series_memberships[0] if series_memberships else {}
    return {
        "legacy_id": _book_id(book.get("legacyId")),
        "title": book.get("titleComplete") or book.get("title"),
        "book_kca": book.get("id"),
        "work_kca": (book.get("work") or {}).get("id"),
        "contributor_kca": contributor.get("id"),
        "contributor_name": contributor.get("name"),
        "contributor_url": contributor.get("webUrl"),
        "series_kca": first_series.get("id"),
        "series_title": first_series.get("title"),
        "series_memberships": series_memberships,
    }


def _book_summary(node: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Book node (similar-books shape) to a compact summary."""
    stats = (node.get("work") or {}).get("stats") or {}
    author = (node.get("primaryContributorEdge") or {}).get("node") or {}
    return {
        "book_id": _book_id(node.get("legacyId")),
        "title": node.get("title"),
        "author": author.get("name"),
        "average_rating": stats.get("averageRating"),
        "ratings_count": stats.get("ratingsCount"),
        "cover": node.get("imageUrl"),
        "url": node.get("webUrl"),
    }


def _work_summary(node: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Work node (series/contributor shape) via its bestBook."""
    best = node.get("bestBook") or {}
    stats = node.get("stats") or {}
    author = (best.get("primaryContributorEdge") or {}).get("node") or {}
    return {
        "book_id": _book_id(best.get("legacyId")),
        "title": best.get("title"),
        "author": author.get("name"),
        "average_rating": stats.get("averageRating"),
        "ratings_count": stats.get("ratingsCount"),
        "cover": best.get("imageUrl"),
        "url": best.get("webUrl"),
    }


def _node_summary(node: dict[str, Any]) -> dict[str, Any]:
    """Normalize a node that may be a Book or a Work to a compact summary.

    Work nodes expose their representative book at details.bestBook (the
    top-list shape) or directly at bestBook.
    """
    if node.get("__typename") == "Work":
        best = (node.get("details") or {}).get("bestBook") or node.get("bestBook") or {}
        stats = node.get("stats") or {}
        author = (best.get("primaryContributorEdge") or {}).get("node") or {}
        return {
            "book_id": _book_id(best.get("legacyId")),
            "title": best.get("title"),
            "author": author.get("name"),
            "average_rating": stats.get("averageRating"),
            "ratings_count": stats.get("ratingsCount"),
            "cover": best.get("imageUrl"),
            "url": best.get("webUrl"),
        }
    return _book_summary(node)


# ===================================================================== READ


@mcp.tool(annotations=_READ_ONLY)
def search_books(query: str, max_results: int = 10) -> list[dict[str, Any]]:
    """Search Goodreads for books by title/author/ISBN.

    Uses the JSON autocomplete endpoint (no auth, no HTML parsing).
    Returns book_id, title, author, rating info, and a cover URL.

    max_results: how many to return. The autocomplete endpoint itself
        answers with at most ~5 matches, so a larger value returns what
        Goodreads sent, not more.
    """
    if max_results < 0:
        raise ValueError("max_results must be zero or greater.")
    resp = gr.get("/book/auto_complete", params={"format": "json", "q": query})
    results = []
    for b in resp.json()[:max_results]:
        book_url = b.get("bookUrl")
        results.append(
            {
                "book_id": b.get("bookId"),
                "title": b.get("title"),
                "author": (b.get("author") or {}).get("name"),
                "average_rating": b.get("avgRating"),
                "ratings_count": b.get("ratingsCount"),
                "pages": b.get("numPages"),
                "cover": b.get("imageUrl"),
                # Null is explicit: BASE alone would be a link to the home page.
                "url": BASE + book_url if book_url else None,
                "description": html_mod.unescape(
                    re.sub(r"<[^>]+>", "", (b.get("description") or {}).get("html", ""))
                )[:400],
            }
        )
    return results


@mcp.tool(annotations=_READ_ONLY)
def get_book(book_id: str, review_language_limit: int = 5) -> dict[str, Any]:
    """Get full details for a book by its Goodreads id (numeric, or numeric-slug
    like '11870085-the-fault-in-our-stars' or '2767052.The_Hunger_Games'). An
    ISBN is not a book id: find the book with search_books first.

    Parses the page's embedded __NEXT_DATA__ JSON (Apollo state) rather than
    scraping the DOM, which survives markup changes. Includes the full
    ratings histogram, all series memberships, and review-language breakdown
    — use get_reviews for the actual review text. review_language_limit controls
    how many languages are returned (default 5, maximum 25).

    When you cite details or ratings from this book, link to its 'url'.
    """
    if review_language_limit < 0:
        raise ValueError("review_language_limit must be zero or greater.")
    apollo = _fetch_book_apollo(book_id)
    deref = _make_deref(apollo)
    book = _find_book(apollo, book_id)

    author = deref(deref(book.get("primaryContributorEdge")).get("node"))
    details = book.get("details") or {}
    stats = deref(book.get("work")).get("stats") or book.get("stats") or {}
    genres = [
        (deref(g.get("genre")) or g.get("genre") or {}).get("name")
        for g in (book.get("bookGenres") or [])
    ]

    # Ratings histogram: ratingsCountDist is [1-star, 2-star, ... 5-star].
    dist = stats.get("ratingsCountDist") or []
    histogram = (
        {str(stars): dist[stars - 1] for stars in range(5, 0, -1)}
        if len(dist) == 5
        else None
    )

    # Preserve the original first-series fields for compatibility, while also
    # exposing every series membership in the embedded Apollo state.
    series_memberships = []
    book_series = book.get("bookSeries") or []
    for membership in book_series:
        if not membership:
            continue
        series_node = deref(membership.get("series"))
        series_memberships.append(
            {
                "series": series_node.get("title"),
                "position": membership.get("userPosition"),
            }
        )
    series = series_memberships[0]["series"] if series_memberships else None
    series_position = series_memberships[0]["position"] if series_memberships else None

    language_limit = min(review_language_limit, 25)
    # Review-language breakdown, ordered by text-review count.
    langs = stats.get("textReviewsLanguageCounts") or []
    review_languages = {
        lang.get("isoLanguageCode"): lang.get("count")
        for lang in sorted(langs, key=lambda x: -(x.get("count") or 0))[:language_limit]
        if lang.get("isoLanguageCode")
    } or None

    return {
        "book_id": _book_id(book.get("legacyId")),
        "title": book.get("titleComplete") or book.get("title"),
        "author": author.get("name"),
        "cover": book.get("imageUrl"),
        "description": _clean_text(book.get("description")),
        "average_rating": stats.get("averageRating"),
        "ratings_count": stats.get("ratingsCount"),
        "ratings_histogram": histogram,
        "text_reviews_count": stats.get("textReviewsCount"),
        "review_languages": review_languages,
        "series": series,
        "series_position": series_position,
        "series_memberships": series_memberships,
        "pages": details.get("numPages"),
        "format": details.get("format"),
        "publisher": details.get("publisher"),
        "publication_time": details.get("publicationTime"),
        "publication_date": _ms_to_iso(details.get("publicationTime")),
        "isbn13": details.get("isbn13"),
        "genres": [g for g in genres if g],
        "url": book.get("webUrl"),
    }


@mcp.tool(annotations=_READ_ONLY)
def get_reviews(
    book_id: str,
    limit: int = 10,
    min_rating: int | None = None,
    max_rating: int | None = None,
    exclude_spoilers: bool = False,
) -> dict[str, Any]:
    """Get reader reviews for a book — the actual review text, not just a score.

    Fetches from Goodreads' GraphQL backend with true pagination, so limit
    can exceed the ~30 shown on a page. Reviews come in "most relevant"
    order and aggregate across all editions of the work. Each review has the
    reviewer name, star rating (1-5), full text, like/comment counts, date, a
    spoiler flag, a 'url' permalink (use it to cite/link), and the reviewer's
    profile url.

    limit: max reviews to return (capped at 100 to stay polite).
    min_rating / max_rating: server-side star filters, each 1-5, e.g.
        min_rating=4 for positive reviews, max_rating=2 for the critical ones.
    exclude_spoilers: drop reviews flagged as spoilers. Paging is capped, so
        a book whose reviews are mostly spoilers can return fewer than limit.

    'has_more' is true when Goodreads has reviews this call did not read.
    """
    # Goodreads answers an impossible star filter with an empty page, which
    # would read as "this book has no reviews"; refuse it here instead.
    for name, value in (("min_rating", min_rating), ("max_rating", max_rating)):
        if value is not None and not 1 <= value <= 5:
            raise ValueError(f"{name} must be between 1 and 5.")
    if min_rating is not None and max_rating is not None and min_rating > max_rating:
        raise ValueError("min_rating must not be greater than max_rating.")
    if limit < 0:
        raise ValueError("limit must be zero or greater.")
    want = min(limit, _MAX_REVIEWS)
    book = _book_by_legacy_id(_Q_BOOK_BY_LEGACY, book_id)
    work_id = (book.get("work") or {}).get("id")
    if not work_id:
        raise ValueError(f"Could not resolve work id for book {book_id!r}.")

    filters: dict[str, Any] = {"resourceType": "WORK", "resourceId": work_id}
    if min_rating is not None:
        filters["ratingMin"] = min_rating
    if max_rating is not None:
        filters["ratingMax"] = max_rating

    reviews: list[dict[str, Any]] = []
    total: int | None = None
    token: str | None = None
    has_more = False
    seen_tokens: set[str] = set()
    pages = 0
    while len(reviews) < want and pages < _MAX_REVIEW_PAGES:
        pagination: dict[str, Any] = {"limit": _REVIEW_PAGE_SIZE}
        if token:
            pagination["after"] = token
        conn = gr.graphql(
            _Q_REVIEWS, {"filters": filters, "pagination": pagination}
        ).get("getReviews") or {}
        pages += 1
        if total is None:
            total = conn.get("totalCount")
        # Null edges and null reviews are skipped: they are not reviews, so
        # they count neither toward limit nor as unread. Whether the page was
        # empty is judged on what Goodreads sent, nulls included.
        raw_edges = conn.get("edges") or []
        edges = [e for e in raw_edges if e and e.get("node")]
        unread = 0
        for index, edge in enumerate(edges):
            rev = edge["node"]
            spoiler = bool(rev.get("spoilerStatus"))
            if exclude_spoilers and spoiler:
                continue
            creator = rev.get("creator") or {}
            reviews.append(
                {
                    "reviewer": creator.get("name"),
                    "rating": rev.get("rating"),
                    "text": _clean_text(rev.get("text")),
                    "likes": rev.get("likeCount"),
                    "comments": rev.get("commentCount"),
                    "date": _ms_to_iso(rev.get("createdAt")),
                    "spoiler": spoiler,
                    "url": (rev.get("shelving") or {}).get("webUrl"),
                    "reviewer_url": creator.get("webUrl"),
                }
            )
            if len(reviews) >= want:
                unread = len(edges) - index - 1
                break
        token = (conn.get("pageInfo") or {}).get("nextPageToken")
        # A remaining cursor means unread reviews, even on an empty page.
        has_more = bool(unread or token)
        if not token or not raw_edges:
            break
        # A server that hands back the same cursor must not loop forever.
        if token in seen_tokens:
            break
        seen_tokens.add(token)

    return {
        "book_id": _book_id(book.get("legacyId")),
        "title": book.get("titleComplete") or book.get("title"),
        "total_text_reviews": total,
        "returned": len(reviews),
        "has_more": has_more,
        "reviews": reviews,
    }


@mcp.tool(annotations=_READ_ONLY)
def similar_books(book_id: str, limit: int = 10) -> dict[str, Any]:
    """"Readers also enjoyed" — books similar to the given one.

    Goodreads' own recommendation graph (hard to reproduce with web search).
    Each result has book_id/title/author/rating/url so you can chain into
    get_book or get_reviews. Results paginate in batches and limit is capped
    at 100.
    """
    _validate_discovery_limit(limit)
    ids = _resolve_book_ids(book_id)
    edges, has_more, _ = _paginated_graphql_edges(
        _Q_SIMILAR, "getSimilarBooks", {"id": ids["book_kca"]}, limit
    )
    books = [_book_summary(e["node"]) for e in edges]
    return {
        "book_id": ids["legacy_id"],
        "title": ids["title"],
        "returned": len(books),
        "has_more": has_more,
        "similar": books,
    }


@mcp.tool(annotations=_READ_ONLY)
def author_books(book_id: str, limit: int = 20) -> dict[str, Any]:
    """List an author's works (bibliography), given any of their books.

    Resolves the book's primary author, then returns their works ranked by
    popularity. Each result has book_id/title/author/rating/url. Results
    paginate in batches and limit is capped at 100.
    """
    _validate_discovery_limit(limit)
    ids = _resolve_book_ids(book_id)
    if not ids["contributor_kca"]:
        raise ValueError(f"Could not resolve an author for book {book_id!r}.")
    edges, has_more, total_count = _paginated_graphql_edges(
        _Q_AUTHOR,
        "getWorksByContributor",
        {
            "input": {"id": ids["contributor_kca"]},
        },
        limit,
    )
    works = [_work_summary(e["node"]) for e in edges]
    return {
        "author": ids["contributor_name"],
        "author_url": ids["contributor_url"],
        "total_works": total_count,
        "returned": len(works),
        "has_more": has_more,
        "works": works,
    }


@mcp.tool(annotations=_READ_ONLY)
def series_books(
    book_id: str, limit: int = 20, series_index: int = 0
) -> dict[str, Any]:
    """List the books in a series (with reading-order placement), given any
    book in that series. When a book belongs to multiple series, pass the
    zero-based series_index from get_book's series_memberships order.

    Each entry has the series 'placement' (e.g. '1', '0.5' for a prequel),
    'is_primary' (a main-sequence entry vs companion), and the usual
    book_id/title/author/rating/url. Results paginate in batches and limit is
    capped at 100.
    """
    _validate_discovery_limit(limit)
    if series_index < 0:
        raise ValueError("series_index must be zero or greater.")
    ids = _resolve_book_ids(book_id)
    memberships = ids["series_memberships"]
    if not memberships:
        return {
            "book_id": ids["legacy_id"],
            "title": ids["title"],
            "series": None,
            "note": "This book isn't part of a Goodreads series.",
            "returned": 0,
            "has_more": False,
            "books": [],
        }
    if series_index >= len(memberships):
        raise ValueError(
            f"series_index {series_index} is out of range; this book has "
            f"{len(memberships)} series membership(s)."
        )
    selected_series = memberships[series_index]
    edges, has_more, _ = _paginated_graphql_edges(
        _Q_SERIES,
        "getWorksForSeries",
        {
            "input": {"id": selected_series["id"]},
        },
        limit,
    )
    books = []
    for e in edges:
        summary = _work_summary(e["node"])
        summary["placement"] = e.get("seriesPlacement")
        summary["is_primary"] = e.get("isPrimary")
        books.append(summary)
    return {
        "book_id": ids["legacy_id"],
        "title": ids["title"],
        "series": selected_series["title"],
        "series_index": series_index,
        "returned": len(books),
        "has_more": has_more,
        "books": books,
    }


@mcp.tool(annotations=_READ_ONLY)
def get_editions(book_id: str, limit: int = 20) -> dict[str, Any]:
    """List published editions of a book (formats, ISBNs, publishers, dates).

    Useful for "which edition / format / ISBN" questions. Results paginate in
    batches and limit is capped at 100.
    """
    _validate_discovery_limit(limit)
    ids = _resolve_book_ids(book_id)
    edges, has_more, total_count = _paginated_graphql_edges(
        _Q_EDITIONS, "getEditions", {"id": ids["work_kca"]}, limit
    )
    editions = []
    for e in edges:
        node = e["node"]
        details = node.get("details") or {}
        editions.append(
            {
                "book_id": _book_id(node.get("legacyId")),
                "title": node.get("title"),
                "cover": node.get("imageUrl"),
                "format": details.get("format"),
                "publisher": details.get("publisher"),
                "publication_time": details.get("publicationTime"),
                "isbn13": details.get("isbn13"),
                "pages": details.get("numPages"),
                "language": (details.get("language") or {}).get("name"),
                "url": node.get("webUrl"),
            }
        )
    return {
        "book_id": ids["legacy_id"],
        "title": ids["title"],
        "total_editions": total_count,
        "returned": len(editions),
        "has_more": has_more,
        "editions": editions,
    }


@mcp.tool(annotations=_READ_ONLY)
def book_lists(book_id: str, limit: int = 10) -> dict[str, Any]:
    """List the Listopia lists a book appears on (e.g. "Best Dystopian
    Fiction"), ordered by popularity.

    Each list has its title, total member votes, how many books it contains,
    and a 'url'. Good for "what kind of book is this / what's it grouped with"
    and for discovery. Results paginate in batches and limit is capped at 100.
    """
    _validate_discovery_limit(limit)
    ids = _resolve_book_ids(book_id)
    edges, has_more, _ = _paginated_graphql_edges(
        _Q_BOOK_LISTS, "getBookListsOfBook", {"id": ids["book_kca"]}, limit
    )
    lists = [
        {
            "list_id": (n := e["node"]).get("legacyId"),
            "title": n.get("title"),
            "votes": n.get("userListVotesCount"),
            "books_count": n.get("listBooksCount"),
            "url": n.get("webUrl"),
        }
        for e in edges
    ]
    return {
        "book_id": ids["legacy_id"],
        "title": ids["title"],
        "returned": len(lists),
        "has_more": has_more,
        "lists": lists,
    }


@mcp.tool(annotations=_READ_ONLY)
def popular_books(
    year: int,
    month: int | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Most popular books by release date — Goodreads' "Popular by date" chart.

    Ranks the books/works released in a given year (or a specific month of a
    year) by how many Goodreads members have added them. Mirrors the
    goodreads.com/book/popular_by_date/<year>[/<month>] page. A year or month
    Goodreads has no chart for raises an error rather than returning no books.

    year: 4-digit release year.
    month: optional 1-12 for a single month; omit for the whole year.
    limit: how many to return (capped at 50).

    Each entry has rank, count (members who added it), and the usual
    book_id/title/author/rating/url so you can chain into get_book/get_reviews.
    """
    if not 1000 <= year <= 9999:
        raise ValueError("year must be a 4-digit release year.")
    if month is not None and not 1 <= month <= 12:
        raise ValueError("month must be between 1 and 12.")
    if limit < 0:
        raise ValueError("limit must be zero or greater.")
    name = (
        f"books-by-release-date-{year}-{month}"
        if month is not None
        else f"works-by-release-date-{year}"
    )
    want = min(limit, _MAX_POPULAR)

    entries: list[dict[str, Any]] = []
    token: str | None = None
    has_more = False
    seen_tokens: set[str] = set()
    while len(entries) < want:
        try:
            page = gr.graphql(
                _Q_TOP_LIST,
                {
                    "name": name,
                    "period": "A",
                    "location": "ALL",
                    "after": token,
                    "limit": _POPULAR_PAGE_SIZE,
                },
            ).get("getTopList") or {}
        except GraphQLError as e:
            # Goodreads answers a chart it does not have (a future year, say)
            # with RESOURCE_NOT_FOUND on the first page. Later in the walk
            # the chart exists, so the same answer is a failure to surface.
            if token is not None or not e.not_found:
                raise
            period = str(year) if month is None else f"{year}-{month:02d}"
            raise ValueError(
                f"Goodreads has no popular-by-date chart for {period}."
            ) from e
        edges = [e for e in (page.get("edges") or []) if e and e.get("node")]
        remaining = want - len(entries)
        for edge in edges[:remaining]:
            entry = {"rank": edge.get("rank"), "count": edge.get("count")}
            entry.update(_node_summary(edge["node"]))
            entries.append(entry)
        info = page.get("pageInfo") or {}
        token = info.get("nextPageToken")
        # Same rule as _paginated_graphql_edges: the chart continues if the
        # page says so, or if we stopped at `want` with entries left unread.
        has_more = bool(token and info.get("hasNextPage"))
        if len(edges) > remaining:
            has_more = True
        if not edges or not has_more:
            break
        # A server that hands back the same cursor must not loop forever.
        if token in seen_tokens:
            break
        seen_tokens.add(token)

    return {
        "year": year,
        "month": month,
        "returned": len(entries),
        "has_more": has_more,
        "books": entries,
    }


@mcp.tool(annotations=_READ_ONLY)
def compare_books(book_ids: list[str]) -> dict[str, Any]:
    """Compare several books side by side by rating and rating distribution.

    Fetches each book and returns them ranked best-to-worst by average rating,
    with the ratings_histogram plus 'pct_positive' (share of 4-5 star) and
    'pct_critical' (share of 1-2 star) so you can judge not just the average
    but how divisive each book is. Pass 2-10 book ids (from search_books etc.);
    more than 10 is refused rather than silently trimmed, so split the call.
    """
    if not book_ids:
        raise ValueError("Provide at least one book_id to compare.")
    if len(book_ids) > _MAX_COMPARE:
        raise ValueError(
            f"compare_books takes at most {_MAX_COMPARE} book ids; "
            f"got {len(book_ids)}. Split them across calls."
        )

    results: list[dict[str, Any]] = []
    for bid in book_ids:
        try:
            b = get_book(bid)
        except Exception as e:  # noqa: BLE001 — report per-book, don't abort all
            results.append({"book_id": bid, "error": str(e)})
            continue
        hist = b.get("ratings_histogram") or {}
        total = sum(v for v in hist.values() if isinstance(v, int))
        crit = (hist.get("1") or 0) + (hist.get("2") or 0)
        pos = (hist.get("4") or 0) + (hist.get("5") or 0)
        results.append(
            {
                "book_id": b.get("book_id"),
                "title": b.get("title"),
                "author": b.get("author"),
                "average_rating": b.get("average_rating"),
                "ratings_count": b.get("ratings_count"),
                "text_reviews_count": b.get("text_reviews_count"),
                "ratings_histogram": hist or None,
                "pct_positive": round(100 * pos / total, 1) if total else None,
                "pct_critical": round(100 * crit / total, 1) if total else None,
                "url": b.get("url"),
            }
        )

    ok = [r for r in results if "error" not in r]
    errored = [r for r in results if "error" in r]
    rated = [r for r in ok if r.get("average_rating") is not None]
    unrated = [r for r in ok if r.get("average_rating") is None]
    rated.sort(key=lambda r: r["average_rating"], reverse=True)
    return {
        "compared": len(ok),
        "ranked_by": "average_rating (desc)",
        "books": rated + unrated + errored,
    }


@mcp.tool(annotations=_READ_ONLY)
def get_shelf(
    shelf: str = "to-read",
    user_id: str | None = None,
    page: int = 1,
) -> list[dict[str, Any]]:
    """List books on a shelf via its RSS feed (public shelves; no auth).

    Common shelves: 'read', 'currently-reading', 'to-read', plus any custom
    shelf name. Names are case-sensitive and must match a shelf the user has
    (list_shelves gives them); any other name raises ValueError. An empty
    name lists every shelf. RSS pages hold ~100 items; pass page=2,3,... for
    more (pages start at 1). Defaults to the configured GOODREADS_USER_ID.

    When you cite a book from a shelf, link it to its 'link' field.
    """
    if page < 1:
        raise ValueError("page must be 1 or greater.")
    uid = _user_id(user_id)
    resp = gr.get(f"/review/list_rss/{uid}", params={"shelf": shelf, "page": page})
    served, items = gr.parse_shelf_rss(resp.text)
    # For a name the user has no shelf by, Goodreads answers 200 with the
    # whole library (#204); only the channel title says which shelf it served.
    # An empty name asks for every shelf, which the feed titles "all".
    if served is not None and served != (shelf or "all"):
        raise ValueError(
            f"User {uid} has no shelf named {shelf!r}. Shelf names are "
            "case-sensitive; call list_shelves for this user's valid names."
        )
    return items


# A private profile is served as a normal 200 page with this box in place of
# the bookshelves module, so it has to be told apart from "no shelf links".
_PRIVATE_PROFILE_MARKER = 'id="privateProfile"'


@mcp.tool(annotations=_READ_ONLY)
def list_shelves(user_id: str | None = None) -> list[str]:
    """List a user's shelf names (scraped from their public profile page;
    best effort). Defaults to the configured user.

    Covers the exclusive shelves (read, to-read, ...) and custom shelves;
    every name works as get_shelf's 'shelf' argument. Raises LoginRequired
    for a private profile. The profile page may cap a very long shelf list.
    """
    uid = _user_id(user_id)
    # Not /review/list/{uid}: that page redirects to sign-in since Sep 2026
    # (#91). The profile's bookshelves module links the exclusive shelves as
    # ?shelf= and custom shelves as ?tag=; both feed /review/list_rss as shelf=.
    page = gr.get(f"/user/show/{uid}").text
    if _PRIVATE_PROFILE_MARKER in page:
        raise LoginRequired(
            f"Goodreads profile {uid!r} is private: its shelves are shown only "
            "to signed-in friends, which this read-only server does not do."
        )
    names = re.findall(r'[?&](?:shelf|tag)=([A-Za-z0-9_%\-]+)', page)
    seen: dict[str, None] = {}
    for n in names:
        seen.setdefault(unquote(html_mod.unescape(n)), None)
    return list(seen)


def main() -> None:
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
