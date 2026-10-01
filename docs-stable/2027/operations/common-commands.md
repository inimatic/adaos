# Common Commands

Status: public current-state command guide.

## Everyday Commands

```bash
adaos --help
adaos where
adaos install
adaos update
adaos skill list
adaos scenario list
adaos node status
adaos node reliability
adaos autostart status
```

## One-Line Bootstrap Variants

Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/linux/init.sh | bash -s -- --zone ru
```

Windows PowerShell:

```powershell
& ([scriptblock]::Create((iwr -UseBasicParsing https://raw.githubusercontent.com/inimatic/adaos/rev2026/tools/init/windows/init.ps1).Content)) -ZoneId ru
```

Useful options:

```bash
--join-code CODE
--node-name "Kitchen Member"
--role hub
--install-service auto
--no-core-update
--use-git-from https://github.com/<you>/adaos.git --rev my-branch
```

Windows uses corresponding PowerShell names such as `-JoinCode`, `-NodeName`,
`-Role`, `-InstallService`, and `-NoCoreUpdate`.

