# Browser And Member Connection

Status: public current-state onboarding guide.

AdaOS environments can include a hub, browser access, and member nodes.

## Browser Access

Open the public client:

```text
https://inimatic.com/?intent=auth.login&zone=ru
```

The browser is an endpoint into an AdaOS environment. It should not be treated
as the owner of the runtime or as a replacement for node-side authorization.

## Member Join

Create a join code on the hub:

```bash
adaos hub join-code create
```

Join from another node:

```bash
bash tools/bootstrap.sh --join-code CODE --zone ru --node-name "Kitchen Member"
```

Join codes should be treated as short-lived admission material. Revoke or
rotate them if they are exposed.

