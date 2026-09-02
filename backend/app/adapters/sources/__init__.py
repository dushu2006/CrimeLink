"""Source adapters.

Every external input to CrimeLink — a file upload, the synthetic development
corpus, a future authorised government database feed — flows through the
``SourceAdapter`` interface defined here.  That boundary is what makes the
ingestion pipeline treat synthetic data, real documents and future government
feeds identically, and is what allows the system to claim *without fabrication*
that it is ready for a future authorised CCNS/CCTNS adapter without claiming
that one currently exists.

Adapters register themselves at import time. Importing this package is enough
to populate ``available_adapters()``.
"""

from .protocol import SourceAdapter, SourceRecord  # noqa: F401
from .registry import (  # noqa: F401
    available_adapters,
    get_source_adapter,
    register_source_adapter,
)

# Import concrete adapters so they self-register.
from . import synthetic as _synthetic  # noqa: E402,F401
from . import file_import as _file_import  # noqa: E402,F401
from . import synthetic_external as _synthetic_external  # noqa: E402,F401
