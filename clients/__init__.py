"""Academic source clients used by BAJA Research."""

from .bdtd import BdtdClient
from .crossref import CrossrefClient
from .oasisbr import OasisbrClient
from .openalex import OpenAlexClient
from .semantic_scholar import SemanticScholarClient

__all__ = [
    "BdtdClient",
    "CrossrefClient",
    "OasisbrClient",
    "OpenAlexClient",
    "SemanticScholarClient",
]
