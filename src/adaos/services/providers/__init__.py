"""Core-owned external provider adapters.

Provider adapters keep credentials, transport policy, and remote API details out
of Application skills.  Skills consume the narrow public SDK facade instead.
"""

from .google_gmail import (
    GMAIL_MODIFY_SCOPE,
    GOOGLE_GMAIL_PROVIDER_ID,
    GoogleGmailProvider,
    GoogleGmailProviderError,
)

__all__ = [
    "GMAIL_MODIFY_SCOPE",
    "GOOGLE_GMAIL_PROVIDER_ID",
    "GoogleGmailProvider",
    "GoogleGmailProviderError",
]
