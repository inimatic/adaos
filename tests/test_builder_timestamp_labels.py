import json
from pathlib import Path
import shutil
import subprocess

import pytest

from adaos.services.builder.timestamp_labels import classify_caption, caption_grammar


CASES = ['Дата создания копии', 'Когда создана', 'Создано', 'Время создания резервной копии',
         'Дата создания:', 'Creation date', 'Created at', 'Created', 'Backup creation date',
         'Date of creation of the backup', 'When was created?', 'Дата проверки копии', 'Проверено',
         'Backup verification date', 'When verified', 'Не дата создания', 'Дата создания или проверки',
         'Дата', 'Момент появления', 'Date not created', 'Created after verification', 'Creation completed',
         'Created tomorrow', 'Дата созданиякопии', 'пересоздана', 'Дата непроверки']


def test_browser_uses_the_same_compositional_grammar_as_core_not_a_separate_caption_list():
    executable = shutil.which('node')
    if not executable:
        pytest.skip('Node is required for cross-language oracle policy parity')
    module = (Path(__file__).resolve().parents[1] / 'e2e/stand/browser/timestamp-caption-grammar.mjs').as_uri()
    script = f'import {{classifyTimestampCaption}} from {json.dumps(module)}; ' + (
        'let raw="";for await(const c of process.stdin)raw+=c;const p=JSON.parse(raw);'
        'process.stdout.write(JSON.stringify(p.labels.map(s=>classifyTimestampCaption(s,p.grammar))));')
    result = subprocess.run([executable, '--input-type=module', '-e', script],
        input=json.dumps({'labels':CASES,'grammar':caption_grammar()}),capture_output=True,text=True,check=True)
    assert json.loads(result.stdout) == [classify_caption(label) for label in CASES]
