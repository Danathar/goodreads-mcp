# Research a book

A prompt for using the server, not editing it.

---

Using the goodreads-mcp tools, give me a research brief on **<TITLE>**.

1. `search_books` to resolve the title to a `book_id`.
2. `get_book` for the core record — rating, ratings histogram, series
   memberships, publication date, genres.
3. `get_reviews` twice: once with `min_rating=5` and once with `max_rating=2`,
   so praise and criticism are both represented. Use `exclude_spoilers=True`.
4. `similar_books` for what readers who liked it also read.

Then write the brief: what the book is, how it's rated, what enthusiasts
praise, what critics object to, and what to read next.

Cite everything with the `url` fields the tools return — link book titles to
their book url, attribute each quoted review to its reviewer and link its
permalink, and note star ratings. If a `url` is null, say so rather than
inventing one.
