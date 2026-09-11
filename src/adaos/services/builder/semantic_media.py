"""Lower explicit, domain-neutral media bindings into Client capabilities."""

from collections.abc import Mapping

from .workflow import BuilderWorkflowError


SAMPLE_MEDIA = {
    "sample://image": "/assets/prototype/sample-image.jpg",
    "sample://video": "/assets/prototype/sample-video.mp4",
    "sample://unavailable": "/assets/prototype/unavailable-media.mp4",
}


def compile_media(document: Mapping, webui: dict, resources: list[dict], source_map: dict) -> None:
    widgets = webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    by_id = {widget["id"]: widget for widget in widgets}
    records = {resource["resource_ref"]: resource["records"] for resource in resources}
    fields = {resource["id"]: {field["id"]: field for field in resource["fields"]} for resource in document["resources"]}
    for view in document["views"]:
        media = view.get("media")
        if not media:
            continue
        if view["role"] not in {"collection", "details"}:
            raise BuilderWorkflowError("Media binding requires a collection or details view")
        for ref in media.values():
            if ref and ref not in fields[view["resource_ref"]]:
                raise BuilderWorkflowError(f"Media binding references an unknown field: {ref}")
        widget = by_id[view["id"]]
        inputs = widget["inputs"]
        if view["role"] == "details":
            inputs.update({"presentation": "section", "mediaKey": media["source_field_ref"],
                           "mediaKindKey": media.get("kind_field_ref"), "mediaPosterKey": media.get("poster_field_ref")})
        for ref in (media["source_field_ref"], media.get("poster_field_ref")):
            if not ref:
                continue
            source_map.setdefault(f"field:{ref}", []).append(f"ui.application.desktop.pageSchema.widgets.@{view['id']}.inputs")
            for record in records[view["resource_ref"]]:
                value = record.get(ref)
                if isinstance(value, str) and value.startswith("sample://"):
                    if value not in SAMPLE_MEDIA:
                        raise BuilderWorkflowError(f"Unknown built-in media sample: {value}")
                    record[ref] = SAMPLE_MEDIA[value]
        if view["role"] == "collection":
            cover_key = f"_adaos_cover_{view['id']}"
            if cover_key in fields[view["resource_ref"]]:
                raise BuilderWorkflowError("Media cover key collides with an authored field")
            for record in records[view["resource_ref"]]:
                source = record.get(media["source_field_ref"])
                poster = record.get(media.get("poster_field_ref"))
                kind = str(record.get(media.get("kind_field_ref")) or "").lower()
                sample_video = source in (SAMPLE_MEDIA["sample://video"], SAMPLE_MEDIA["sample://unavailable"])
                if poster:
                    record[cover_key] = poster
                elif sample_video:
                    record[cover_key] = SAMPLE_MEDIA["sample://image"]
                elif kind in {"video", "audio"}:
                    record[cover_key] = None
                else:
                    record[cover_key] = source
            if widget["type"] == "ui.list":
                inputs["imageKey"] = cover_key
            else:
                visible_ref = media.get("poster_field_ref") or media["source_field_ref"]
                column = next((item for item in inputs["columns"] if item["key"] == visible_ref), None)
                if column is None:
                    raise BuilderWorkflowError("A table cover field must be one of its visible columns")
                column.update(kind="image", key=cover_key)
