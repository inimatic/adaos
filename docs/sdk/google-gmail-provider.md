# Google Gmail provider

Status: bounded beta provider for the Gmail Application E2E.

AdaOS Core owns the OAuth exchange, PKCE verifier, access and refresh tokens,
token refresh, and all HTTP calls to `gmail.googleapis.com`. Application skills
use the typed `adaos.sdk.providers.gmail` facade. They must not use a generic
HTTP SDK, credential slots, or receive OAuth tokens.

This boundary is intentionally narrower than a general email abstraction. It
is the first governed external-provider primitive needed by the Gmail beta;
it is not yet a reusable CBS `CapabilityContract`, a registry entry, or a
provider-selection mechanism.

## Release declaration

The immutable Application permission profile declares the permission and the
exact provider:

```yaml
required:
  - id: providers.google.gmail
    purpose: Use the connected Gmail account.
    approval_policy: explicit
    sensitive: true
external_providers:
  - id: google.gmail
    account_id: google.gmail
    title: Gmail account
    purpose: Read, organize, trash and send mail requested by the user.
    required: true
    destination: gmail.googleapis.com
    account_modes: [delegated_user]
    scopes:
      - https://www.googleapis.com/auth/gmail.modify
```

The owning skill declares capability `providers.google.gmail`. Each public
tool uses `application_access.permission: providers.google.gmail` and accurate
read/external-write side effects. Core verifies the active Application release,
resolved user subject, declared account, destination, and exact scope again at
execution time.

## SDK

```python
from adaos.sdk.providers import gmail

gmail.begin_connection()
gmail.connection_status()
gmail.list_messages(query="is:unread", max_results=50)
gmail.get_message(message_id, format="full")
gmail.list_labels()
gmail.modify_message(message_id, remove_label_ids=["INBOX"])
gmail.trash_message(message_id)
gmail.send_message(raw_base64url, idempotency_key=stable_command_id)
```

Only these fixed operations are admitted. Hard deletion and arbitrary URLs or
HTTP methods are unavailable. `send_message` is never retried by Core and
requires an Application-owned idempotency key; the Application must suppress a
duplicate command before issuing a second provider call.

`begin_connection` returns an authorization URL for a browser `openUrl` action
with `withAuth=false`. The callback is
`/api/providers/google/gmail/oauth/callback` on the configured local API base.
OAuth state is single-use and expires after ten minutes.

## Node configuration

Configure a Google OAuth client through either environment variables:

```text
ADAOS_GOOGLE_OAUTH_CLIENT_ID
ADAOS_GOOGLE_OAUTH_CLIENT_SECRET
```

or node-vault records:

```text
provider:google.oauth:client_id
provider:google.oauth:client_secret
```

`ADAOS_SELF_BASE_URL` may override the callback base. Otherwise Core uses its
configured local API URL and finally `http://127.0.0.1:8777`. The Google client
must admit the resulting exact callback URI.

Applications store only redacted connected-account metadata and revisions.
The provider credential key is scoped by provider, user subject, and logical
account rather than by Application, which permits later controlled reuse. The
second-Application attach/consent flow is deliberately not implemented by this
stage-one boundary.

## Setup and Builder

Trial placement compiles the setup contract from immutable component manifests
and the release permission profile. A required Gmail declaration therefore
appears automatically as a connected-account requirement in Applications;
tokens never enter that projection.

Builder Automation receives the exact Gmail implementation contract only when
the accepted task mentions Gmail, `google.gmail`, or
`mail.messages.manage`. Package tests mock the typed facade and perform no
external network calls.
