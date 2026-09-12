"""Check the configured Root/provider schema support without generating a project."""

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from jsonschema import Draft202012Validator

from adaos.apps.cli.app import Settings, init_ctx
from adaos.sdk.llm.llm_client import submit_response_job, wait_response_job


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv()
    if os.getenv("ENV_TYPE") != "dev" or args.output.exists():
        parser.error("Requires ENV_TYPE=dev and a new evidence directory")
    args.output.mkdir(parents=True)
    init_ctx(Settings.from_sources())
    schema = {
        "type": "object", "additionalProperties": False,
        "required": ["label", "values", "surface"],
        "properties": {
            "label": {"type": "string", "minLength": 2, "maxLength": 4, "pattern": "^[a-z]+$"},
            "values": {"type": "array", "minItems": 2, "maxItems": 3,
                       "items": {"type": "number", "minimum": 0, "maximum": 5}},
            "surface": {"anyOf": [
                {"type": "object", "additionalProperties": False, "required": ["role", "media"],
                 "properties": {"role": {"type": "string", "enum": ["editor"]}, "media": {"type": "null"}}},
                {"type": "object", "additionalProperties": False, "required": ["role", "media"],
                 "properties": {"role": {"type": "string", "enum": ["details"]}, "media": {"type": "string"}}},
            ]},
        },
    }
    messages = [{"role": "user", "content": "Return label ok, values 1 and 2, and an editor surface matching the schema."}]
    options = {"model": "gpt-5", "reasoning": {"effort": "low"}, "max_tokens": 128000,
               "text": {"format": {"type": "json_schema", "name": "adaos_schema_support", "strict": True, "schema": schema}},
               "request_id": f"adaos-schema-support-{args.output.name}", "timeout": 15}
    def retain(name, value):
        with (args.output / name).open("x", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
    retain("request.json", {"messages": messages, "options": options})
    submitted = submit_response_job(messages, **options)
    retain("submission.json", submitted)
    response = wait_response_job(submitted["job_id"], base_url=submitted.get("_client", {}).get("base_url"), timeout_s=180)
    retain("response.json", response)
    if response.get("status") != "succeeded":
        print(json.dumps({"status": response.get("status"), "evidence": str(args.output)}))
        return 1
    candidate = json.loads(response["output_text"])
    Draft202012Validator(schema).validate(candidate)
    print(json.dumps({"status": "passed", "candidate": candidate, "evidence": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
