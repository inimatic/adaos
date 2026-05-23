# Browser and Member Connection

AdaOS has two common ways to attach an endpoint to a subnet:

- a **browser** connects through the hosted web client and receives a browser
  access session
- a **member node** joins with a short one-time join-code and then maintains a
  member-to-hub runtime link

Both flows eventually appear in the device access model described in
[Device Access and Browsers](../architecture/device-access-and-browsers.md).

## Connect a browser

1. Start or verify the hub runtime:

   ```bash
   adaos node status
   adaos node reliability
   ```

2. Open the client:

   ```text
   https://inimatic.com/?zone=ru&mode=login
   ```

3. For diagnostic or local development sessions, use:

   ```text
   http://127.0.0.1:4200/?zone=lo&boot_debug=1
   https://inimatic.com/?zone=ru&boot_debug=1
   ```

4. If the hub is local, keep the API on `8777` or `8778` when you want the
   browser to auto-discover it. Use a non-discoverable port such as `8779` when
   you want the hosted client to stay routed through Root.

Browser pair links use the `pair_code` URL parameter:

```text
https://inimatic.com/?pair_code=PAIRCODE&zone=ru
```

The full client URL parameter reference lives in
`src/adaos/integrations/adaos-client/README.md#client-url-parameters`.

## Connect a member node

Create a short join-code on the hub:

```bash
adaos hub join-code create
```

Bootstrap the member:

```bash
bash tools/bootstrap.sh --join-code CODE --zone ru --node-name "Kitchen Member"
```

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File tools/bootstrap.ps1 -JoinCode CODE -ZoneId ru
```

For local/LAN-only onboarding without Root:

```bash
adaos hub join-code create --local
bash tools/bootstrap.sh --join-code CODE --root-url http://<HUB_HOST>:8777
```

Verify the member:

```bash
adaos node status --json
adaos node reliability
```

More details are in [Member node onboarding](member-node-phase1.md) and
[Member-Hub Connectivity](../architecture/member-hub-connectivity.md).

