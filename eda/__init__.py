"""Enterprise Data Agent -- a read-only business data analysis agent.

Local, reproducible portfolio project. Not production ready.

Stage 1 scope (this package so far):
  * eda.domain   -- enums and Pydantic row models
  * eda.data     -- schema, fixture dataset, seeded demo generator, build script
  * eda.metrics  -- metric definitions (SQL text) and core metric computation
  * eda.db       -- the single place where SQLite connections/queries are made
"""

__version__ = "0.1.0"
