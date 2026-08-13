# Legacy manual prompt probes

These scripts predate the current state-authoritative runtime and are retained only as historical diagnostics. They are not part of the pytest suite, may reference removed private methods, and should not be treated as correctness evidence.

The files no longer load `.env` automatically. If one is deliberately revived, pass its configuration through an explicit caller-owned environment and update it to the current public runtime boundary first.
