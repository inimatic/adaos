# CLI: Interpreter and Rasa NLU

AdaOS no longer installs upstream `rasa==3.6.x` into the root environment. Rasa NLU is provided by the AdaOS-maintained `rasa-port` package and runs as an optional service-skill in the normal skill runtime slots.

## Install model

`adaos install` prepares the default scenarios/skills, installs `rasa_nlu_service_skill`, stages it into an active skill slot, and trains NLU once by default.

Useful switches:

- `adaos install --no-rasa-nlu` disables Rasa service-skill preparation.
- `adaos install --no-train-nlu` prepares the service-skill but skips post-install training.
- `ADAOS_NLU_RASA=0` disables Rasa in runtime.
- `ADAOS_NLU_AUTOTRAIN=1` enables event-driven retraining after scenario/skill changes. Keep it off in production unless noisy model churn is acceptable.

## Commands

```bash
adaos interpreter status
adaos interpreter sync-nlu
adaos interpreter train --engine rasa
adaos interpreter parse "open modal nlu_teacher_modal"
adaos interpreter intent list
```

The CLI builds the Rasa project from installed skill/scenario training content, then calls `rasa_nlu_service_skill:/train`. The service owns its dependencies and model loading; the hub process stays free of Rasa/TensorFlow dependency conflicts.

## Runtime locations

- Workspace template copy: `.adaos/workspace/skills/rasa_nlu_service_skill`
- Active slot source: `.adaos/workspace/skills/.runtime/rasa_nlu_service_skill/<version>/slots/<A|B>/src/skills/rasa_nlu_service_skill`
- Slot-local service venv: `.adaos/workspace/skills/.runtime/rasa_nlu_service_skill/<version>/slots/<A|B>/venv`
- Generated project: `.adaos/state/interpreter/rasa_project`
- Model artifact: `.adaos/models/interpreter/interpreter_latest.tar.gz`
- Service log: `.adaos/logs/service.rasa_nlu_service_skill.log`

## External dependency

If `src/adaos/integrations/rasa-port` exists, AdaOS installs it into the service venv as editable local source. Otherwise the service-skill uses:

```text
adaos-rasa-nlu @ git+https://github.com/stipot-com/rasa-port.git@main
```

Override with:

```bash
ADAOS_RASA_PORT_PATH=/path/to/rasa-port
ADAOS_RASA_PORT_REQUIREMENT="adaos-rasa-nlu @ git+https://github.com/<fork>/rasa-port.git@branch"
```

## Manual check

```bash
adaos interpreter train --engine rasa
adaos interpreter parse "open modal nlu_teacher_modal"
adaos skill service status rasa_nlu_service_skill --health
```

If Rasa is disabled or confidence is low, the runtime emits `nlp.intent.not_obtained` and the teacher/fallback path can collect the phrase.
