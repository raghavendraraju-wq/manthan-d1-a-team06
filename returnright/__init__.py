"""ReturnRight domain package: eligibility, pricing, events, per-item state machine, refunds, flags, access.

Pure Python with no I/O. Storage goes through the ReturnStore port (store.py); the
backend supplies a PostgreSQL store, and the tests use InMemoryStore.
"""
