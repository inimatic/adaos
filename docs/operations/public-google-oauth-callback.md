# Public Google OAuth Callback

Status: operator procedure for the EIG3 migration window.

## Prerequisites

- DNS `integrations.inimatic.com` resolves to the selected Root ingress.
- The isolated ingress vhost has a valid TLS certificate and exposes only
  `/v1/oauth/callback/cbp_*` plus its health probe.
- Root and the selected Core have a healthy outbound hub route.
- Core uses `environment-profile:public-connected@1`; this is environment
  materialization, not a Gmail/provider flag.

## Google registration

In the existing Google Cloud **Web application** OAuth client retain the local
development URI during migration and add this exact URI:

```text
https://integrations.inimatic.com/v1/oauth/callback/cbp_google_oauth_primary
```

Do not create an Application-specific URI. This callback profile represents
the Google authorization-server/client registration and may serve separately
admitted Gmail, Drive or Calendar bindings.

Before activation verify DNS/TLS, neutral CSP response, Root registration,
outbound-only delivery and a successful local acknowledgement. Then complete a
real consent flow and confirm that the refresh/access tokens exist only in the
Core credential vault.

## Rollback

Select `environment-profile:local-development@1` and use the retained URI:

```text
http://127.0.0.1:8777/api/providers/google/gmail/oauth/callback
```

Changing materialization must not change the Application requirement,
CapabilityContract, BindingDefinition or existing provider credential. Pending
public attempts expire and are not redirected to another Core.

## Incident rules

- Cordon the public endpoint revision; do not rotate a callback URI silently.
- Delete pending hashed rendezvous after their TTL. Never export raw state,
  authorization codes, encrypted envelopes or provider errors into tickets.
- A missing local acknowledgement is a failed attempt, not permission to route
  to a different subnet/node or exchange the code at Root.
