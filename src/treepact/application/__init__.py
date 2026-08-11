"""Application services and queries.

Ownership boundary: each mutating command receives a command ID for
correlation and idempotency and owns one transaction for its database
effects. Queries never mutate.
"""
