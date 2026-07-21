# Release notes — veeam-aiops 0.8.0

Previous release: 0.7.0.

## Preview fidelity

A `--dry-run` should run the same guards as the real call and leave an audit row — the line's invariant is "a dry_run MAY read; it must never write." A few write commands still showed a hand-written banner that ran no guard and audited nothing. Those are now routed through the governed twin. The real writes were always guarded and audited; only the previews were blind.


### In this tool

- `job start` / `job retry` / `job enable` / `job disable` gained a `--dry-run` flag (routed through the governed twin), matching `job stop`.
