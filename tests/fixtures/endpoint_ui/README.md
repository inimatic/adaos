# Endpoint UI conformance input

`media_center_controls.webui.json` is a read-only test snapshot of the existing
Media Center `media-mobile-transport` widget. Its provenance is recorded in
`provenance.json`. It is not an additional authoring source or product UI.
The product continues to own its original `webui.json`.

The context declares a fictional endpoint and a pinned experimental profile
digest. It supplies no credentials or access grant. These fixtures do not claim
that the Java or Objective-C agents implement this receiver profile. Run a
preparation directly against the current Media Center source to qualify a new
revision; fixture success alone does not qualify the currently installed app.

All action descriptors remain on the node. A receiver emits an action reference
with surface/state/generation and projection identity. Resolution returns the
original descriptor; the normal action pipeline still owns policy, idempotency,
approval and dispatch. No command or audio is executed by this fixture.
