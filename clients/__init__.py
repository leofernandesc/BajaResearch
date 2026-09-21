"""Academic source clients used by BAJA Research."""

from .bdtd import BdtdClient
from .crossref import CrossrefClient
from .oasisbr import OasisbrClient
from .openalex import OpenAlexClient
from .repositories import RepositoryResolver
from .semantic_scholar import SemanticScholarClient
from .unpaywall import UnpaywallClient

__all__ = [
    "BdtdClient",
    "CrossrefClient",
    "OasisbrClient",
    "OpenAlexClient",
    "RepositoryResolver",
    "SemanticScholarClient",
    "UnpaywallClient",
]
