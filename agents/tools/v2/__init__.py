"""Tools that talk Beckn v2.

One module per capability, named for the capability and not for the version --
the version is the package. Each exports the same function names and
signatures as its v1 twin in the parent package, so `agents/tools/__init__.py`
can pick between them at import time without the agent or the prompts
changing.

Which capabilities are live, and what each migration cost, is in
`migration-plan/`.
"""
