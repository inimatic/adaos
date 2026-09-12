"""Regrade a retained request without mutating its artifact, rubric or old grade."""

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from adaos.apps.cli.app import Settings, init_ctx
from adaos.e2e.builder_grading import grade_builder_prototype


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    load_dotenv()
    if os.getenv('ENV_TYPE') != 'dev':
        parser.error('Requires ENV_TYPE=dev')
    root = Path(__file__).resolve().parents[2] / 'e2e' / 'artifacts'
    if root not in args.output.resolve().parents:
        parser.error('Output must stay below e2e/artifacts')
    original = json.loads(args.input.read_text(encoding='utf-8'))
    if original.get('schema') != 'adaos.builder.prototype_grade_input.v1':
        parser.error('Requires a retained prototype_grade_input record')
    payload = json.loads(original['messages'][1]['content'])
    args.output.mkdir(parents=True, exist_ok=False)
    def record(name, value):
        with (args.output / name).open('x', encoding='utf-8') as target:
            json.dump(value, target, ensure_ascii=False, indent=2)
            target.write('\n')
    record('source.json', {'input': str(args.input.resolve()), 'request_digest': original['request_digest'],
                           'artifact_digest': original['artifact_digest'], 'rubric_digest': original['rubric_digest']})
    init_ctx(Settings.from_sources())
    grade, request = grade_builder_prototype(
        artifact=payload['artifact'], user_turns=payload['user_turns'], requirements=payload['rubric'],
        prohibited_assumptions=payload['rubric']['prohibited_assumptions'], locale=payload['locale'],
        model=original['model'], max_output_tokens=original['generation_options']['max_tokens'],
        request_recorder=lambda value: record('input.json', value),
        response_recorder=lambda value: record('response.json', value),
    )
    record('grade.json', grade)
    print(json.dumps({'passed': grade['passed'], 'score': grade['score'],
                      'same_artifact': request['artifact_digest'] == original['artifact_digest'],
                      'same_rubric': request['rubric_digest'] == original['rubric_digest']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
