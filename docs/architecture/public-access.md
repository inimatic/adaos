# Public Access Grants

## Purpose

AdaOS public access is modeled as a root-visible grant, not as an authenticated
desktop session. A public URL names a zone and a public token. The zone selects
the root server. The root server resolves the public token to the target
subnet, node, skill, public face, resource, and hub verification token.

This keeps public links target-addressed: a link may expose one resource inside
a skill, such as one AdaOS Drive file or folder, without implying access to the
whole skill or subnet.

## Contract

The shared grant shape is `adaos.public_grant.v1`.

- `grant_kind`: domain of the grant, for example `drive.files`
- `face_id`: public UI/service face, for example `adaos_drive.files.public`
- `resource.kind`: `file`, `folder`, or a future skill-specific kind
- `capabilities`: readonly verbs such as `read`, `list`, `preview`, `download`
- `readonly`: public grants are readonly unless an explicit future grant says
  otherwise
- `status` and `expires_at`: root and hub both enforce revocation and expiry

The root record stores the routing secret needed to reach the subnet. The hub
stores the same public token plus a hashed hub token and the local resource
root. Public clients never receive `subnet_id`, `node_id`, or the hub token in
public metadata.

## Drive Public Face

AdaOS Drive is the first public face. Owner mode can publish either a file or a
folder. Folder grants allow browsing descendants relative to that folder only.
Public Drive supports:

- tree/list browsing
- file preview for supported browser-safe formats
- direct file downloads
- owner-side listing and revoke of public shares

Public Drive does not support upload, rename, delete, or copy.

The generated user-facing URL uses the app origin:

```text
https://inimatic.com/?intent=drive.view&zone=ru&public_token=<token>
```

The client renders a readonly public Drive screen without entering the normal
authenticated Webspace/YJS desktop flow. Direct downloads still use:

```text
https://<zone>.api.inimatic.com/v1/drive/public-links/<token>/content?download=1
```

For a file under a folder grant, callers add `path=<relative-path>`.

## Guest Device Identity

The public client issues and persists `adaos_public_guest_device_id`. Requests
include it as `guest_device_id` so root/hub logs can distinguish recipients
without requiring login. This is not an authorization secret.

## Performance Direction

Public faces should be able to approach static-site performance:

- public shell loads from the app CDN
- root resolves public metadata from a small cached grant record
- list and content calls are delegated to the hub only when live resource data
  is needed
- future folder grants may cache immutable or short-TTL listings at root

Guest public faces do not need a per-user YJS document by default. If a future
public scenario needs live variables, use a broadcast/readonly subscription
model first; reserve per-user YJS state for interactive guest workflows that
actually mutate user-specific state.
