"""The LinkedIn post generator service.

Nothing but warning setup lives here. A package's `__init__` runs before any of
its submodules, which makes it the only place a filter can be installed early
enough to catch a warning raised at import time by something those submodules
import.
"""

import warnings

# pydantic raises this the moment `pydantic.v1` is imported on Python 3.14+, and
# langchain_core imports it unconditionally (langchain_core/utils/pydantic.py)
# for a deprecated compatibility path. Nothing here uses it — this service is
# pydantic v2 throughout — so the warning is noise on every start and in every
# test run. Scoped to the one message and the one category rather than silencing
# UserWarning at large. Remove it when langchain_core stops importing pydantic.v1.
warnings.filterwarnings(
    "ignore",
    message=r"Core Pydantic V1 functionality isn't compatible with Python 3\.14",
    category=UserWarning,
)
