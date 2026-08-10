from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA = "adaos.rasa.mobile.v1"
DEFAULT_CRF_FEATURES = [
    ["low", "title", "upper"],
    [
        "low",
        "bias",
        "prefix5",
        "prefix2",
        "suffix5",
        "suffix3",
        "suffix2",
        "upper",
        "title",
        "digit",
        "pattern",
    ],
    ["low", "title", "upper"],
]


def _safe_extract(archive: tarfile.TarFile, destination: Path) -> None:
    root = destination.resolve()
    for member in archive.getmembers():
        name = member.name.lstrip("/\\")
        if not name:
            continue
        target = (destination / name).resolve()
        if root not in target.parents and target != root:
            raise ValueError(f"rasa_model_member_unsafe:{member.name}")
        member.name = name
        archive.extract(member, destination)


def _resource_for(metadata: Mapping[str, Any], component_suffix: str) -> tuple[Path, dict[str, Any]]:
    nodes = ((metadata.get("predict_schema") or {}).get("nodes") or {})
    for node in nodes.values():
        if not isinstance(node, Mapping):
            continue
        if not str(node.get("uses") or "").endswith(component_suffix):
            continue
        resource = node.get("resource") or {}
        name = str(resource.get("name") or "")
        if name:
            return Path("components") / name, dict(node.get("config") or {})
    raise ValueError(f"rasa_model_component_missing:{component_suffix}")


def _crf_value(token: Mapping[str, Any], name: str) -> Any:
    text = str(token.get("text") or "")
    if name == "low":
        return text.lower()
    if name == "title":
        return text.istitle()
    if name == "upper":
        return text.isupper()
    if name == "digit":
        return text.isdigit()
    if name == "bias":
        return "bias"
    if name == "prefix5":
        return text[:5]
    if name == "prefix2":
        return text[:2]
    if name == "suffix5":
        return text[-5:]
    if name == "suffix3":
        return text[-3:]
    if name == "suffix2":
        return text[-2:]
    if name == "suffix1":
        return text[-1:]
    return None


def _crf_features(
    sentence: Sequence[Mapping[str, Any]],
    configured: Sequence[Sequence[str]],
    *,
    include_entity: bool = False,
) -> list[dict[str, Any]]:
    half = len(configured) // 2
    output: list[dict[str, Any]] = []
    for token_index in range(len(sentence)):
        current: dict[str, Any] = {}
        for configured_index, names_value in enumerate(configured):
            names = list(names_value)
            pointer = configured_index - half
            candidate_index = token_index + pointer
            if candidate_index < 0:
                current["BOS"] = True
                continue
            if candidate_index >= len(sentence):
                current["EOS"] = True
                continue
            token = sentence[candidate_index]
            prefix = str(pointer)
            if include_entity:
                names.append("entity")
            for name in names:
                if name == "pattern":
                    for pattern_name, matched in dict(token.get("pattern") or {}).items():
                        current[f"{prefix}:pattern:{pattern_name}"] = bool(matched)
                elif name == "entity":
                    current[f"{prefix}:entity"] = str(token.get("entity_tag") or "O")
                else:
                    current[f"{prefix}:{name}"] = _crf_value(token, name)
        output.append(current)
    return output


def _train_crf_export(
    dataset: Sequence[Sequence[Mapping[str, Any]]],
    order: Sequence[str],
    configured: Sequence[Sequence[str]],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    import sklearn_crfsuite

    state_features: list[list[Any]] = []
    transition_features: list[list[Any]] = []
    labels: list[str] = []
    # Current AdaOS data has only the entity dimension. Keeping the loop makes the
    # bundle format forward-compatible with role/group CRFs.
    taggers: dict[str, Any] = {}
    for tag_name in order:
        include_entity = tag_name != "entity"
        features = [
            _crf_features(sentence, configured, include_entity=include_entity)
            for sentence in dataset
        ]
        targets = [
            [str(token.get(f"{tag_name}_tag") or "O") for token in sentence]
            for sentence in dataset
        ]
        tagger = sklearn_crfsuite.CRF(
            algorithm="lbfgs",
            c1=float(config.get("L1_c", 0.1)),
            c2=float(config.get("L2_c", 0.1)),
            max_iterations=int(config.get("max_iterations", 50)),
            all_possible_transitions=True,
        )
        tagger.fit(features, targets)
        taggers[tag_name] = tagger
        if tag_name == "entity":
            labels = [str(item) for item in tagger.classes_]
            state_features = [
                [str(attribute), str(label), float(weight)]
                for (attribute, label), weight in sorted(tagger.state_features_.items())
            ]
            transition_features = [
                [str(source), str(target), float(weight)]
                for (source, target), weight in sorted(tagger.transition_features_.items())
            ]
    if order and not labels:
        raise ValueError("rasa_mobile_entity_crf_missing")
    return {
        "order": [str(item) for item in order],
        "features": [list(map(str, row)) for row in configured],
        "labels": labels,
        "state_features": state_features,
        "transition_features": transition_features,
        "bilou": bool(config.get("BILOU_flag", True)),
        "split_entities_by_comma": bool(config.get("split_entities_by_comma", True)),
    }


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def export_bundle(model_path: Path, output_path: Path) -> dict[str, Any]:
    import skops.io as sio

    model_path = model_path.resolve()
    source_bytes = model_path.read_bytes()
    with tempfile.TemporaryDirectory(prefix="adaos-rasa-mobile-export-") as temporary:
        extracted = Path(temporary)
        with tarfile.open(model_path, "r:gz") as archive:
            _safe_extract(archive, extracted)
        metadata = _load_json(extracted / "metadata.json")

        regex_resource, regex_config = _resource_for(metadata, ".RegexFeaturizer")
        word_resources: list[tuple[Path, dict[str, Any]]] = []
        nodes = ((metadata.get("predict_schema") or {}).get("nodes") or {})
        for node in nodes.values():
            if not isinstance(node, Mapping):
                continue
            if not str(node.get("uses") or "").endswith(".CountVectorsFeaturizer"):
                continue
            resource = node.get("resource") or {}
            name = str(resource.get("name") or "")
            if name:
                word_resources.append(
                    (Path("components") / name, dict(node.get("config") or {}))
                )
        crf_resource, crf_config = _resource_for(metadata, ".CRFEntityExtractor")
        synonym_resource, _ = _resource_for(metadata, ".EntitySynonymMapper")
        classifier_resource, classifier_config = _resource_for(
            metadata, ".LogisticRegressionClassifier"
        )

        classifier_file = extracted / classifier_resource / f"{classifier_resource.name}.skops"
        unknown = sio.get_untrusted_types(file=classifier_file)
        if unknown:
            raise ValueError(f"rasa_mobile_classifier_untrusted:{unknown}")
        classifier = sio.load(classifier_file, trusted=[])

        vectorizers = []
        for resource, config in word_resources:
            config = {
                "analyzer": "word",
                "min_ngram": 1,
                "max_ngram": 1,
                "lowercase": True,
                **config,
            }
            vocabularies = _load_json(extracted / resource / "vocabularies.json")
            vocabulary = dict(vocabularies.get("text") or {})
            vectorizers.append(
                {
                    "analyzer": str(config.get("analyzer") or "word"),
                    "min_ngram": int(config.get("min_ngram") or 1),
                    "max_ngram": int(config.get("max_ngram") or 1),
                    "lowercase": bool(config.get("lowercase", True)),
                    "vocabulary": vocabulary,
                }
            )

        patterns = _load_json(extracted / regex_resource / "patterns.json")
        dataset = _load_json(extracted / crf_resource / "crf_dataset.json")
        crf_order = _load_json(extracted / crf_resource / "crf_order.json")
        resolved_crf_config = {
            "BILOU_flag": True,
            "split_entities_by_comma": True,
            "max_iterations": 50,
            "L1_c": 0.1,
            "L2_c": 0.1,
            **crf_config,
        }
        configured_features = resolved_crf_config.get("features") or DEFAULT_CRF_FEATURES
        crf_export = _train_crf_export(
            dataset,
            crf_order,
            configured_features,
            resolved_crf_config,
        )
        synonyms = _load_json(extracted / synonym_resource / "synonyms.json")

        expected_features = len(patterns) + sum(
            len(item["vocabulary"]) for item in vectorizers
        )
        actual_features = int(classifier.n_features_in_)
        if expected_features != actual_features:
            raise ValueError(
                f"rasa_mobile_feature_mismatch:{expected_features}!={actual_features}"
            )
        bundle = {
            "schema": SCHEMA,
            "metadata": {
                "model_id": metadata.get("model_id"),
                "trained_at": metadata.get("trained_at"),
                "rasa_version": metadata.get("rasa_open_source_version"),
                "language": metadata.get("language"),
                "source_model_sha256": hashlib.sha256(source_bytes).hexdigest(),
                "source_model_name": model_path.name,
                "deployment": "inference_only",
                "training_location": "off_device",
            },
            "regex": {"case_sensitive": bool(regex_config.get("case_sensitive", True))},
            "regex_patterns": patterns,
            "count_vectorizers": vectorizers,
            "classifier": {
                "classes": [str(item) for item in classifier.classes_.tolist()],
                "coef": classifier.coef_.tolist(),
                "intercept": classifier.intercept_.tolist(),
                "ranking_length": int(classifier_config.get("ranking_length") or 10),
            },
            "crf": crf_export,
            "synonyms": synonyms,
        }

    encoded = json.dumps(
        bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output_path.with_suffix(output_path.suffix + ".tmp")
    with temporary_output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            compressed.write(encoded)
    temporary_output.replace(output_path)
    return {
        "ok": True,
        "output": str(output_path),
        "size": output_path.stat().st_size,
        "source_model_sha256": bundle["metadata"]["source_model_sha256"],
        "model_id": bundle["metadata"]["model_id"],
        "intent_count": len(bundle["classifier"]["classes"]),
        "feature_count": expected_features,
        "entity_labels": bundle["crf"]["labels"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export a canonical AdaOS Rasa artifact for the stdlib-only mobile runtime."
    )
    parser.add_argument("model", type=Path)
    parser.add_argument("output", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    print(json.dumps(export_bundle(args.model, args.output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
