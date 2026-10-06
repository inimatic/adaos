"""Migrate first-party consumers to the shared ui.chat ABI, without publishing.

Dry-run by default. --write only updates the explicitly supplied DEV directory.
Voice is intentionally not a migration target. Backups belong under .tmp/.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ('web_desktop', 'builder', 'research_workbench')


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def migrate(document: dict, application: str) -> dict:
    if application not in TARGETS:
        raise ValueError('Unsupported migration target')
    doc = deepcopy(document)
    nodes = list(walk(doc))
    widgets = {item['id']: item for item in nodes if item.get('type') and item.get('id')}
    if application == 'web_desktop':
        chat = widgets.get('desktop-chat')
        if not chat:
            raise ValueError('Management conversation is missing')
        chat['inputs']['conversation'] = {
            'version': 1, 'owner': 'application:web_desktop',
            'agent': {'mode': 'select', 'source': {'kind': 'y', 'path': 'data/dialog', 'observe': 'dataRoot'}},
            'history': {'mode': 'auto', 'allAgents': True, 'defaultAll': True, 'maxMessages': 200},
            'voice': True, 'voiceOutput': 'shell', 'presentation': {'floating': True},
        }
        # Desktop chrome owns audio playback. The embedded/floating renderer
        # is a visual projection and must not synthesize the same reply again.
        chat['inputs'].update(syncDialogSelection=False, autoSpeak=False, openCommand='voice.chat.open')
        chat['inputs'].pop('autoSpeakFrom', None)
        remove = {'chat-channel-selector', 'chat-agent-selector', 'chat-voice-input'}
        for node in nodes:
            for key, value in list(node.items()):
                if isinstance(value, list):
                    node[key] = [item for item in value if not (isinstance(item, dict) and item.get('id') in remove)]
        # Opening a view is not a dialog selection and must not emit a greeting.
        for widget in widgets.values():
            if not isinstance(widget.get('actions'), list):
                continue
            widget['actions'] = [action for action in widget.get('actions', [])
                                 if not (action.get('on') == 'click:chat' and
                                         action.get('params', {}).get('command') == 'dialog.channel.select')]
    else:
        builder = application == 'builder'
        owner = 'builder_skill' if builder else 'research_orchestrator_skill'
        agent = 'builder' if builder else 'researcher'
        for widget in widgets.values():
            if widget.get('type') != 'ui.chat':
                continue
            # Exclude diagnostic transcript modals; migrate actual conversation surfaces.
            if builder and not (str(widget['id']).startswith('design-conversation-') or widget['id'] == 'builder-chat'):
                continue
            if not builder and widget['id'] != 'research-chat':
                continue
            inputs = widget.setdefault('inputs', {})
            informal = str(widget['id']).endswith('-informal')
            surface = {
                'version': 1, 'owner': f'application:{application}',
                'label': '$state.selectedProjectId' if builder else '$state.selectedDirectionId',
                'agent': {'mode': 'fixed', 'id': f'agent:{owner}:{agent}',
                          'label': 'Builder' if builder else 'Researcher', 'icon': 'person-circle-outline'},
                'history': {'mode': 'manual' if informal else 'auto', 'maxMessages': 200},
                'voice': not informal, 'presentation': {'floating': True},
            }
            if builder and not informal:
                params = {'object_type': '$state.selectedProjectKind', 'object_id': '$state.selectedProjectId',
                          '_meta': {'current_scenario': 'builder'}}
                surface['model'] = {
                    'source': {'kind': 'skill', 'name': 'builder_sdk_control_skill.get_llm_options',
                               'params': params, 'invalidationTags': ['builder.project.llm'],
                               'cacheTtlMs': 5000, 'preserveLastValue': True},
                    'valuePath': 'value', 'optionsPath': 'options', 'label': 'Prototype model',
                }
                action = {'on': 'model.change', 'type': 'callSkill',
                          'target': 'builder_sdk_control_skill.set_llm_profile',
                          'params': {**params, 'model': '$event.value'}, 'invalidates': 'builder.project.llm'}
                widget['actions'] = [a for a in widget.get('actions', []) if a.get('on') != 'model.change'] + [action]
            inputs.update(conversation=surface, syncDialogSelection=False, multiline=True, composerRows=2)
            if informal:
                composer_id = widget['id'].replace('design-conversation-', 'design-composer-')
                composer = widgets.get(composer_id)
                if composer:
                    actions = deepcopy(composer.get('actions', []))
                    for action in actions:
                        action['on'] = 'send'
                        action.setdefault('params', {})['text'] = '$event.text'
                    widget['actions'] = actions
                    composer['visibleIf'] = 'false'
    return doc


def validate_surfaces(document):
    schema = json.loads((ROOT / 'src/adaos/abi/webui.v1.schema.json').read_text(encoding='utf-8'))
    validator = Draft202012Validator({'$ref': '#/$defs/conversationSurface', '$defs': schema['$defs']})
    count = 0
    for node in walk(document):
        if node.get('type') == 'ui.chat' and node.get('inputs', {}).get('conversation'):
            validator.validate(node['inputs']['conversation'])
            count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scenarios', type=Path)
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    directory = args.scenarios.resolve(strict=True)
    for application in TARGETS:
        path = directory / application / 'webui.json'
        before = path.read_bytes()
        document = json.loads(before.decode('utf-8-sig'))
        result = migrate(document, application)
        count = validate_surfaces(result)
        changed = result != document
        if args.write and changed:
            backup = ROOT / '.tmp' / 'conversation-surface-migration' / application
            backup.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(before).hexdigest()
            (backup / f'{digest}.json').write_bytes(before)
            path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'application': application, 'surfaces': count, 'changed': changed, 'written': args.write and changed}))


if __name__ == '__main__':
    main()
