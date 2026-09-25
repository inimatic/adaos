# Google Gmail provider

Status: bounded beta provider for the Gmail Application E2E.

AdaOS Core owns the OAuth exchange, PKCE verifier, access and refresh tokens,
token refresh, and all HTTP calls to `gmail.googleapis.com`. Application skills
use the typed `adaos.sdk.providers.gmail` facade. They must not use a generic
HTTP SDK, credential slots, or receive OAuth tokens.

This boundary is intentionally narrower than a general email abstraction. The
installed CBS inventory supplies `capability:mail.messages.manage@1.0.0` and a
Google Gmail binding. A second Application can reuse the Core-owned Gmail
credential after an explicit per-Application attach; it never receives or
copies OAuth tokens.

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
gmail.reusable_connections()
gmail.attach_reusable_connection()
gmail.connection_status()
gmail.list_messages(query="is:unread", max_results=50)
gmail.get_message(message_id, format="full")
gmail.list_labels()
gmail.modify_message(message_id, remove_label_ids=["INBOX"])
gmail.trash_message(message_id)
gmail.send_message(raw_base64url, idempotency_key=stable_command_id)
```

Provider failures use the public `gmail.GmailProviderError` with stable `code`,
`status_code`, and `retryable` fields plus a secret-free `to_dict()` envelope.
Successful mail operations return
`{ok, provider_id, operation, result}`; the owning skill validates the Gmail
result and projects only the fields required by its bounded tool output. A
missing account raises `gmail_account_not_connected`, which maps to the
setup-required UI state rather than a fabricated empty mailbox.

Only these fixed operations are admitted. Hard deletion and arbitrary URLs or
HTTP methods are unavailable. `send_message` is never retried by Core and
requires an Application-owned idempotency key; the Application must suppress a
duplicate command before issuing a second provider call.

`begin_connection` returns an authorization URL for a browser `openUrl` action
with `withAuth=false`. The callback is
`/api/providers/google/gmail/oauth/callback` on the configured local API base.
OAuth state is single-use and expires after ten minutes.

`reusable_connections` returns only a redacted account projection for the
resolved owner. `attach_reusable_connection` creates the second Application's
connected-account authorization record after its immutable permission profile
has passed the same provider, destination, account-mode, and scope checks. The
provider/user/account vault key does not change, so no credential is copied.
Builder-generated UI must present this as an explicit user choice; discovery
alone must not attach the account.

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

The Core ingress broker materializes the callback from the selected
EnvironmentProfile. Applications, skills and the provider adapter do not
construct a callback URL. For `environment-profile:local-development@1` the
Google Web Application client must admit exactly:

```text
http://127.0.0.1:8777/api/providers/google/gmail/oauth/callback
```

Do not use a Google OAuth client of type **Desktop application** for this flow;
create one of type **Web application** so the exact redirect URI can be
registered.

For `environment-profile:public-connected@1`, follow the
[public callback operator procedure](../operations/public-google-oauth-callback.md)
and additionally register the canonical `integrations.inimatic.com` URI. Keep
the loopback URI during the migration window.

## Google Cloud setup for the beta

1. In the same Google Cloud project enable **Gmail API**
   (`gmail.googleapis.com`). Allow a few minutes for a newly enabled API to
   propagate.
2. In Google Auth Platform configure **Branding** and **Audience**. For an
   External app in Testing, add the Gmail address under **Test users**. Google
   Testing refresh tokens normally expire after seven days.
3. Under **Data Access** add the restricted scope
   `https://www.googleapis.com/auth/gmail.modify`. A personal/dev test can
   continue through Google's unverified-app warning; public production use
   requires the applicable Google verification.
4. Create an OAuth client of type **Web application** and register the exact
   redirect URI shown above. Scheme, IP, port, path, and lack of trailing slash
   must match.
5. In AdaOS Applications Settings save its **Google OAuth client ID** and
   **Google OAuth client secret**. AdaOS stores both in the local credential
   vault; never put them in chat, source, fixtures, logs, or telemetry.
6. Open the Application, choose **Connect Gmail**, select the test account, and
   approve access. After the `Gmail connected` callback page appears, close it
   and return to AdaOS.

Applications store only redacted connected-account metadata and revisions.
The provider credential key is scoped by provider, user subject, and logical
account rather than by Application. Each consuming Application still has its
own redacted, revisioned connected-account authorization record.

## Progressive message lists

Use `gmail.list_message_summaries(...)` for a first-paint message collection.
Gmail's ordinary list endpoint returns identifiers only; the Core provider
therefore resolves up to 20 metadata summaries with one Gmail HTTP batch after
the list request. The consumer receives a normal typed result plus
`nextPageToken`, `fetch_strategy="gmail_http_batch"`, and a bounded network
round-trip count. It must render that page immediately and request the next
page progressively instead of issuing one `get_message` call per row.

`gmail.list_messages(...)` remains the identifier-only primitive. Use
`gmail.get_message(...)` when the user opens a single message and the reader
needs its body. Message content remains transient across both paths.

## Setup and Builder

Trial placement compiles the setup contract from immutable component manifests
and the release permission profile. A required Gmail declaration therefore
appears automatically as a connected-account requirement in Applications;
tokens never enter that projection.

Builder Automation receives the exact Gmail implementation contract only when
the accepted task mentions Gmail, `google.gmail`, or
`mail.messages.manage`. Package tests mock the typed facade and perform no
external network calls.
