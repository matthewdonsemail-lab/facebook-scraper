"""Compatibility helpers for optional third-party dependencies."""
from __future__ import annotations

import logging
import sys
from types import ModuleType

logger = logging.getLogger(__name__)


def ensure_lxml_html_clean() -> None:
    """Ensure :mod:`lxml.html.clean` can be imported.

    Starting with lxml 5 the ``html.clean`` helpers were extracted into the
    :mod:`lxml_html_clean` project.  ``requests_html`` still imports
    ``lxml.html.clean`` directly which raises an :class:`ImportError` if the
    optional package is missing.  When that happens we install a lightweight
    shim that exposes a no-op :class:`Cleaner` class so dependants keep working
    without having to install the additional wheel.
    """

    if "lxml.html.clean" in sys.modules:
        return

    try:
        from lxml.html import clean as _clean  # type: ignore

        # Import succeeded, nothing to do.
        sys.modules.setdefault("lxml.html.clean", _clean)
        return
    except Exception:  # pragma: no cover - fallback path
        # Either the import truly failed or raised the advisory ImportError
        # introduced in lxml 5.
        pass

    shim = ModuleType("lxml.html.clean")

    class Cleaner:  # type: ignore
        """Minimal stub that mirrors :class:`lxml_html_clean.Cleaner`."""

        def __init__(self, **_: object) -> None:
            self.javascript = True
            self.style = True

        def clean_html(self, html: str) -> str:
            return html

    shim.Cleaner = Cleaner  # type: ignore[attr-defined]
    sys.modules["lxml.html.clean"] = shim
    logger.warning(
        "lxml.html.clean is unavailable; installed a minimal Cleaner shim. "
        "Install 'lxml-html-clean' for full sanitising support."
    )
