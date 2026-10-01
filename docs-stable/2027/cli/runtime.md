# Runtime Operations

Status: public current-state runtime guide.

## Development Runtime

```bash
adaos dev serve --host 127.0.0.1 --port 8777
```

Use this mode when developing or inspecting AdaOS locally.

## API Debug Runtime

```bash
adaos api serve --host 127.0.0.1 --port 8779
```

Use this only for lower-level HTTP API debugging. It does not manage
production-style runtime slots.

## Autostart Runtime

```bash
adaos autostart status
adaos autostart update-status
```

Autostart-managed runtimes are the production-style local path. When a command
requires the active slot environment, enter the slot shell first.

