"""SDK facade for RootDeveloperService.

Skills should depend on the SDK surface, while implementation lives in
``adaos.services``.
"""

from __future__ import annotations

from adaos.services.root.service import RootDeveloperService, RootServiceError, TemplateResolutionError

__all__ = ["RootDeveloperService", "RootServiceError", "TemplateResolutionError"]

