from __future__ import annotations

import gzip
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


PORTABLE_RASA_SCHEMA = "adaos.rasa.mobile.v1"
_NO_ENTITY = "O"
_NUMBER_RE = re.compile(r"\b[0-9]+\b", re.UNICODE)
_TOKEN_CLEAN_RE = re.compile(
    r"[^\w#@&]+(?=\s|$)|"
    r"(\s|^)[^\w#@&]+(?=[^0-9\s])|"
    r"(?<=[^0-9\s])[^\w._~:/?#\[\]()@!$&*+,;=-]+(?=[^0-9\s])",
    re.UNICODE,
)


@dataclass(frozen=True)
class PortableToken:
    text: str
    start: int
    end: int
    patterns: Mapping[str, bool]


def load_portable_rasa(path: str | Path) -> "PortableRasaRuntime":
    return PortableRasaRuntime.from_path(path)


class PortableRasaRuntime:
    """Inference-only runtime exported from one canonical Rasa NLU artifact.

    The implementation intentionally uses only the Python standard library. It is
    shared by Android and desktop parity tests; training and export remain desktop
    operations. This is a deployment adapter, not an independently trained model.
    """

    def __init__(self, bundle: Mapping[str, Any]) -> None:
        if bundle.get("schema") != PORTABLE_RASA_SCHEMA:
            raise ValueError("portable_rasa_schema_unsupported")
        self.bundle = dict(bundle)
        self.metadata = dict(bundle.get("metadata") or {})
        self.patterns = [
            dict(item)
            for item in bundle.get("regex_patterns") or []
            if isinstance(item, Mapping)
        ]
        self.vectorizers = [
            dict(item)
            for item in bundle.get("count_vectorizers") or []
            if isinstance(item, Mapping)
        ]
        self.classifier = dict(bundle.get("classifier") or {})
        self.crf = dict(bundle.get("crf") or {})
        self.synonyms = {
            str(key): str(value)
            for key, value in dict(bundle.get("synonyms") or {}).items()
        }
        regex_config = dict(bundle.get("regex") or {})
        regex_flags = re.UNICODE
        if not bool(regex_config.get("case_sensitive", True)):
            regex_flags |= re.IGNORECASE
        self._compiled_patterns = [
            (
                str(item.get("name") or ""),
                re.compile(str(item.get("pattern") or ""), regex_flags),
            )
            for item in self.patterns
            if item.get("name") and item.get("pattern")
        ]
        self._classes = [str(item) for item in self.classifier.get("classes") or []]
        self._coef = [list(map(float, row)) for row in self.classifier.get("coef") or []]
        self._intercept = list(map(float, self.classifier.get("intercept") or []))
        if not self._classes or len(self._coef) != len(self._classes):
            raise ValueError("portable_rasa_classifier_invalid")
        if len(self._intercept) != len(self._classes):
            raise ValueError("portable_rasa_intercept_invalid")
        dimensions = {len(row) for row in self._coef}
        if len(dimensions) != 1:
            raise ValueError("portable_rasa_classifier_dimensions_invalid")
        self._feature_count = dimensions.pop()
        self._crf_labels = [str(item) for item in self.crf.get("labels") or []]
        self._crf_state = {
            (str(attribute), str(label)): float(weight)
            for attribute, label, weight in self.crf.get("state_features") or []
        }
        self._crf_transitions = {
            (str(source), str(target)): float(weight)
            for source, target, weight in self.crf.get("transition_features") or []
        }

    @classmethod
    def from_path(cls, path: str | Path) -> "PortableRasaRuntime":
        source = Path(path)
        opener = gzip.open if source.suffix == ".gz" else open
        with opener(source, "rt", encoding="utf-8") as stream:
            payload = json.load(stream)
        if not isinstance(payload, Mapping):
            raise ValueError("portable_rasa_bundle_invalid")
        return cls(payload)

    def describe(self) -> dict[str, Any]:
        return {
            "schema": PORTABLE_RASA_SCHEMA,
            "model_id": self.metadata.get("model_id"),
            "source_model_sha256": self.metadata.get("source_model_sha256"),
            "rasa_version": self.metadata.get("rasa_version"),
            "trained_at": self.metadata.get("trained_at"),
            "intent_count": len(self._classes),
            "entity_labels": list(self._crf_labels),
            "runtime": "portable_rasa",
        }

    def parse(self, text: str) -> dict[str, Any]:
        value = str(text or "")
        explicit = self._parse_regex_message(value)
        if explicit is not None:
            return explicit
        tokens = self._tokenize(value)
        features = self._sentence_features(tokens)
        if len(features) != self._feature_count:
            raise ValueError(
                f"portable_rasa_feature_mismatch:{len(features)}!={self._feature_count}"
            )
        probabilities = self._predict_probabilities(features)
        ranking = sorted(
            (
                {"name": label, "confidence": probability}
                for label, probability in zip(self._classes, probabilities)
            ),
            key=lambda item: -float(item["confidence"]),
        )
        ranking_length = int(self.classifier.get("ranking_length") or 10)
        if ranking_length > 0:
            ranking = ranking[:ranking_length]
        intent = dict(ranking[0]) if ranking else {"name": None, "confidence": 0.0}
        return {
            "text": value,
            "intent": intent,
            "entities": self._extract_entities(value, tokens),
            "text_tokens": [(token.start, token.end) for token in tokens],
            "intent_ranking": ranking,
        }

    def _tokenize(self, text: str) -> list[PortableToken]:
        cleaned = _TOKEN_CLEAN_RE.sub(" ", text)
        words = cleaned.split()
        if not words and text:
            words = [text]
        offset = 0
        tokens: list[PortableToken] = []
        matches_by_pattern = {
            name: list(pattern.finditer(text)) for name, pattern in self._compiled_patterns
        }
        for word in words:
            try:
                start = text.index(word, offset)
            except ValueError:
                start = text.find(word)
            if start < 0:
                continue
            end = start + len(word)
            offset = end
            pattern_flags = {
                name: any(start < match.end() and end > match.start() for match in matches)
                for name, matches in matches_by_pattern.items()
            }
            tokens.append(PortableToken(word, start, end, pattern_flags))
        return tokens

    def _sentence_features(self, tokens: Sequence[PortableToken]) -> list[float]:
        values: list[float] = []
        source_text = self._join_original(tokens)
        for _, pattern in self._compiled_patterns:
            values.append(1.0 if pattern.search(source_text) else 0.0)
        processed = [
            _NUMBER_RE.sub("__NUMBER__", token.text).lower() for token in tokens
        ]
        sentence = " ".join(processed)
        for vectorizer in self.vectorizers:
            vocabulary = {
                str(key): int(index)
                for key, index in dict(vectorizer.get("vocabulary") or {}).items()
            }
            vector = [0.0] * len(vocabulary)
            analyzer = str(vectorizer.get("analyzer") or "word")
            if analyzer == "word":
                terms: Iterable[str] = processed
            elif analyzer == "char_wb":
                terms = self._char_wb_ngrams(
                    sentence,
                    int(vectorizer.get("min_ngram") or 1),
                    int(vectorizer.get("max_ngram") or 1),
                )
            elif analyzer == "char":
                terms = self._char_ngrams(
                    sentence,
                    int(vectorizer.get("min_ngram") or 1),
                    int(vectorizer.get("max_ngram") or 1),
                )
            else:
                raise ValueError(f"portable_rasa_analyzer_unsupported:{analyzer}")
            for term in terms:
                index = vocabulary.get(term)
                if index is not None:
                    vector[index] += 1.0
            values.extend(vector)
        return values

    @staticmethod
    def _join_original(tokens: Sequence[PortableToken]) -> str:
        if not tokens:
            return ""
        # Regex sentence features are insensitive to tokenization. Joining tokens
        # preserves every current AdaOS lookup value and avoids retaining source text
        # on token objects.
        return " ".join(token.text for token in tokens)

    @staticmethod
    def _char_ngrams(text: str, minimum: int, maximum: int) -> Iterable[str]:
        compact = re.sub(r"\s\s+", " ", text)
        for size in range(minimum, min(maximum, len(compact)) + 1):
            for index in range(0, len(compact) - size + 1):
                yield compact[index : index + size]

    @staticmethod
    def _char_wb_ngrams(text: str, minimum: int, maximum: int) -> Iterable[str]:
        compact = re.sub(r"\s\s+", " ", text)
        for word in compact.split():
            padded = f" {word} "
            for size in range(minimum, maximum + 1):
                offset = 0
                while offset + size < len(padded):
                    yield padded[offset : offset + size]
                    offset += 1
                if offset == 0:
                    yield padded[0:size]
                    break
                yield padded[offset : offset + size]

    def _predict_probabilities(self, features: Sequence[float]) -> list[float]:
        scores = [
            intercept + sum(weight * feature for weight, feature in zip(row, features))
            for row, intercept in zip(self._coef, self._intercept)
        ]
        pivot = max(scores)
        exponentials = [math.exp(score - pivot) for score in scores]
        total = sum(exponentials) or 1.0
        return [value / total for value in exponentials]

    def _extract_entities(
        self, text: str, tokens: Sequence[PortableToken]
    ) -> list[dict[str, Any]]:
        if not tokens or not self._crf_labels:
            return []
        token_features = self._crf_token_features(tokens)
        marginals = self._crf_marginals(token_features)
        tags: list[str] = []
        confidences: list[float] = []
        for probabilities in marginals:
            tag = max(probabilities, key=probabilities.get)
            tags.append(tag)
            entity_name = self._tag_without_prefix(tag)
            confidences.append(
                sum(
                    probability
                    for candidate, probability in probabilities.items()
                    if self._tag_without_prefix(candidate) == entity_name
                )
            )
        return self._entities_from_tags(text, tokens, tags, confidences)

    def _crf_token_features(
        self, tokens: Sequence[PortableToken]
    ) -> list[dict[str, Any]]:
        configured = self.crf.get("features") or [
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
        half = len(configured) // 2
        output: list[dict[str, Any]] = []
        for token_index in range(len(tokens)):
            current: dict[str, Any] = {}
            for configured_index, names in enumerate(configured):
                pointer = configured_index - half
                candidate_index = token_index + pointer
                if candidate_index < 0:
                    current["BOS"] = True
                    continue
                if candidate_index >= len(tokens):
                    current["EOS"] = True
                    continue
                token = tokens[candidate_index]
                prefix = str(pointer)
                for name in names:
                    if name == "pattern":
                        for pattern_name, matched in token.patterns.items():
                            current[f"{prefix}:pattern:{pattern_name}"] = matched
                    else:
                        current[f"{prefix}:{name}"] = self._crf_value(token, str(name))
            output.append(current)
        return output

    @staticmethod
    def _crf_value(token: PortableToken, name: str) -> Any:
        if name == "low":
            return token.text.lower()
        if name == "title":
            return token.text.istitle()
        if name == "upper":
            return token.text.isupper()
        if name == "digit":
            return token.text.isdigit()
        if name == "bias":
            return "bias"
        if name == "prefix5":
            return token.text[:5]
        if name == "prefix2":
            return token.text[:2]
        if name == "suffix5":
            return token.text[-5:]
        if name == "suffix3":
            return token.text[-3:]
        if name == "suffix2":
            return token.text[-2:]
        if name == "suffix1":
            return token.text[-1:]
        return None

    @staticmethod
    def _crfsuite_attributes(features: Mapping[str, Any]) -> Iterable[tuple[str, float]]:
        for name, value in features.items():
            if isinstance(value, bool):
                if value:
                    yield name, 1.0
            elif isinstance(value, str):
                yield f"{name}:{value}", 1.0
            elif isinstance(value, (int, float)):
                yield name, float(value)

    def _crf_marginals(
        self, token_features: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, float]]:
        labels = self._crf_labels
        local_scores: list[list[float]] = []
        for features in token_features:
            attributes = list(self._crfsuite_attributes(features))
            local_scores.append(
                [
                    sum(
                        self._crf_state.get((attribute, label), 0.0) * value
                        for attribute, value in attributes
                    )
                    for label in labels
                ]
            )
        alpha = [list(local_scores[0])]
        for index in range(1, len(local_scores)):
            row = []
            for target_index, target in enumerate(labels):
                row.append(
                    local_scores[index][target_index]
                    + self._logsumexp(
                        alpha[index - 1][source_index]
                        + self._crf_transitions.get((source, target), 0.0)
                        for source_index, source in enumerate(labels)
                    )
                )
            alpha.append(row)
        beta = [[0.0] * len(labels) for _ in token_features]
        for index in range(len(token_features) - 2, -1, -1):
            for source_index, source in enumerate(labels):
                beta[index][source_index] = self._logsumexp(
                    self._crf_transitions.get((source, target), 0.0)
                    + local_scores[index + 1][target_index]
                    + beta[index + 1][target_index]
                    for target_index, target in enumerate(labels)
                )
        partition = self._logsumexp(alpha[-1])
        return [
            {
                label: math.exp(alpha[index][label_index] + beta[index][label_index] - partition)
                for label_index, label in enumerate(labels)
            }
            for index in range(len(token_features))
        ]

    @staticmethod
    def _logsumexp(values: Iterable[float]) -> float:
        materialized = list(values)
        pivot = max(materialized)
        return pivot + math.log(sum(math.exp(value - pivot) for value in materialized))

    @staticmethod
    def _tag_without_prefix(tag: str) -> str:
        return tag[2:] if len(tag) > 2 and tag[1] == "-" and tag[0] in "BILU" else tag

    def _entities_from_tags(
        self,
        text: str,
        tokens: Sequence[PortableToken],
        tags: Sequence[str],
        confidences: Sequence[float],
    ) -> list[dict[str, Any]]:
        entities: list[dict[str, Any]] = []
        index = 0
        while index < len(tokens):
            tag = tags[index]
            entity_type = self._tag_without_prefix(tag)
            if entity_type == _NO_ENTITY:
                index += 1
                continue
            prefix = tag[:1] if len(tag) > 2 and tag[1] == "-" else ""
            end_index = index
            if prefix == "B":
                while end_index + 1 < len(tokens):
                    following = tags[end_index + 1]
                    if self._tag_without_prefix(following) != entity_type:
                        break
                    if not following.startswith(("I-", "L-")):
                        break
                    end_index += 1
                    if following.startswith("L-"):
                        break
            start = tokens[index].start
            end = tokens[end_index].end
            raw_value = text[start:end]
            entities.append(
                {
                    "entity": entity_type,
                    "start": start,
                    "end": end,
                    "confidence_entity": min(confidences[index : end_index + 1]),
                    "value": self.synonyms.get(raw_value, raw_value),
                    "extractor": "CRFEntityExtractor",
                    "processors": ["EntitySynonymMapper"],
                }
            )
            index = end_index + 1
        return entities

    def _parse_regex_message(self, text: str) -> dict[str, Any] | None:
        if not text.startswith("/") or len(text) < 2:
            return None
        body = text[1:]
        match = re.match(r"(?P<intent>[^\s{]+)(?P<entities>\{.*\})?", body)
        if not match:
            return None
        intent_name = match.group("intent")
        entities: list[dict[str, Any]] = []
        encoded = match.group("entities")
        if encoded:
            try:
                values = json.loads(encoded)
            except ValueError:
                values = {}
            if isinstance(values, Mapping):
                entities = [
                    {
                        "entity": str(name),
                        "value": value,
                        "start": 0,
                        "end": len(text),
                        "confidence_entity": 1.0,
                        "extractor": "RegexMessageHandler",
                    }
                    for name, value in values.items()
                ]
        intent = {"name": intent_name, "confidence": 1.0}
        return {
            "text": text,
            "intent": intent,
            "entities": entities,
            "text_tokens": [(0, len(text))],
            "intent_ranking": [intent],
        }


__all__ = [
    "PORTABLE_RASA_SCHEMA",
    "PortableRasaRuntime",
    "PortableToken",
    "load_portable_rasa",
]
