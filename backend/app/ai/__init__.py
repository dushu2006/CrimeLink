"""AI Gateway.

CrimeLink separates authoritative source data from AI reasoning through a
controlled **AI data boundary**.  The gateway is the *only* path from
operational data to an external model; no other code imports an LLM client
directly.

Pipeline::

    Investigator Question
        -> AI Request Orchestrator
        -> Query Planner / Retrieval (Postgres + Neo4j)
        -> Relevant Evidence Selection
        -> Graph Subgraph Construction
        -> Data Minimization
        -> Reversible Pseudonymization (PERSON_023, PHONE_041, ...)
        -> Model-specific context
        -> LLM
        -> Structured validated result (Pydantic)
        -> Backend validation (evidence refs, no forbidden actions)
        -> De-pseudonymization for authorized UI
        -> Investigator

The mapping table lives only in the trusted backend and is deterministic for
the duration of an investigation context.  An LLM never receives raw PII
unless an administrator explicitly configures ``CRIMELINK_AI_ALLOW_RAW_PII=true``
(which is off by default).
"""

from .gateway import AIGateway, FindingResult, get_ai_gateway  # noqa: F401
from .pseudonymize import PseudonymMap  # noqa: F401
from .router import AIModelRouter  # noqa: F401
