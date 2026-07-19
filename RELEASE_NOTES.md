# Release notes — veeam-aiops 0.5.1

Previous release: 0.5.0.

## Removed: a dead token cache with a latent id-reuse hazard

`get_token()` and its module-level `_CONN_TOKEN` dict have been deleted. Nothing in
the package read them — authentication has always used the `Authorization` header on
the client — but the cache was keyed by `id(client)` and only cleared in `close()`.
If `close()` was never called, the entry outlived its client, and CPython reuses
object ids: a later client could have read a **different connection's** token. This is
the same defect class that was fixed in another tool in this line, kept from biting
here only by the absence of a caller.

`close()` now clears the `Authorization` header before closing the client, and the
tests assert the header — the mechanism that actually authenticates — instead of a
side cache nothing consumed.

No public API change: `get_token` was never exported in the documented surface.
