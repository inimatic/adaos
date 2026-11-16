import codecs
import sqlite3

import pytest

from adaos.services.site_studio.authoring import read_material_object
from adaos.services.site_studio.source_materials import read_source_file, save_source_file


@pytest.mark.parametrize(("media_type", "text", "encoding"), [
    ("text/html", '<META http-equiv="Content-Type" content="text/html; charset=windows-1251"><p>Попечительский совет</p>', "cp1251"),
    ("text/html", "<meta charset='windows-1251'><p>Состав совета: Иван Петров</p>", "cp1251"),
    ("text/html", '<meta charset="utf-8"><p>Совет фонда</p>', "utf-8"),
    ("text/plain", "Исходные данные", "utf-8-sig"),
    ("text/plain", "Текст с маркером кодировки", "utf-16"),
    ("text/plain", "Совет", "utf-32"),
    ("image/svg+xml", '<?xml version="1.0" encoding="windows-1251"?><svg><title>Города</title></svg>', "cp1251"),
])
def test_private_source_read_honors_encoding_and_preserves_original(media_type, text, encoding):
    with sqlite3.connect(":memory:") as db:
        original = text.encode(encoding)
        item = save_source_file(db, project_id="site", title="source", content=original, media_type=media_type)
        result = read_source_file(db, project_id="site", identity=item["id"], expected_revision=1,
                                  expected_digest=item["digest"], offset=1, length=12)
        assert result["text_available"]
        assert result["content"] == text[1:13]
        assert result["total_characters"] == len(text)
        assert result["has_more"] == (13 < len(text))
        assert result["encoding"] == codecs.lookup(encoding).name
        assert read_material_object(db, item["object_ref"])["content"] == original


def test_source_bom_takes_precedence_over_stale_html_declaration():
    text = '<meta charset="windows-1251"><p>Города</p>'
    with sqlite3.connect(":memory:") as db:
        item = save_source_file(db, project_id="site", title="source.html", content=text.encode("utf-8-sig"), media_type="text/html")
        result = read_source_file(db, project_id="site", identity=item["id"], expected_revision=1)
        assert result["content"] == text
        assert result["encoding"] == "utf-8-sig"


@pytest.mark.parametrize("content", [
    b'<meta charset="unknown-charset"><p>text</p>',
    b'<meta charset="base64_codec"><p>text</p>',
    b'<meta charset="utf-8"><p>\xff\xfe</p>',
])
def test_invalid_source_encoding_is_reported_without_lossy_model_text(content):
    with sqlite3.connect(":memory:") as db:
        item = save_source_file(db, project_id="site", title="source.html", content=content, media_type="text/html")
        result = read_source_file(db, project_id="site", identity=item["id"], expected_revision=1)
        assert result["text_available"] is False
        assert result["content"] == ""
        assert "encoding" in result["limitation"]
