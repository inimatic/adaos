# Notification source and management examples

`handlers/main.py` uses only public SDK ports. Admission requires an installed
Application's `notifications.send` and `storage.relational` permissions, the
selected exact component/release, and the compiled category disclosure. The
manifest does not grant these permissions or select external destinations.

1. Admit the category declaration and source data migration through Application
   lifecycle. `initialize()` is the migration example, not a normal tool handler.
2. Call `begin_task` once at verified ingress and persist the captured publisher.
3. Commit a completed outcome with `complete_task`, then run the bounded
   `reconcile_notifications` continuation under the original owned component.
4. A revised result uses the next source revision and a new source event, which
   replaces the category's card. An exact event retry reuses its persisted JSON.
5. If Core admission fails or the process crashes, replay only the source outbox.
   A retired/expired publisher requires an explicit lifecycle/reauthorization
   decision; never replace it with the currently focused user or a new timestamp.
6. `retract_task_notice` withdraws unsent disclosure, preserving the source fact.

The separately permissioned `../notification-manager/controls.py` demonstrates
read-through revision, snooze and category mute. The producer has no management
capability. No example reads a provider credential or implements a delivery loop.
Use `adaos.sdk.testing.notification_story` for provider-free policy stories.
