"""Narrow imports from the pinned, unmodified Google OKF subtree."""

import sys

from .paths import VENDOR_SRC

if str(VENDOR_SRC) not in sys.path:
    sys.path.insert(0, str(VENDOR_SRC))

from reference_agent.bundle.document import OKFDocument, OKFDocumentError, trust_tier  # noqa: E402
from reference_agent.sources.base import ConceptRef, Source  # noqa: E402

__all__ = ["ConceptRef", "OKFDocument", "OKFDocumentError", "Source", "trust_tier"]

