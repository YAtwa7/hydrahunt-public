"""HydraHunt — QA job-search pipeline.

Layering, outermost first:

    collectors -> pipeline -> scoring/apply -> notify -> db

`pipeline` is deterministic and costs nothing. `scoring` is the only stage
that calls an LLM, and it is bounded to the top ~20 jobs per scan. Nothing
here reaches the network except a collector.

`__version__` is the single source of truth for `/healthz` and the FastAPI
app's reported version. `tests/test_config.py` asserts it matches the latest
heading in CHANGELOG.md, so bump both together and the suite will tell you if
you forget.
"""

__version__ = "0.5.12"
