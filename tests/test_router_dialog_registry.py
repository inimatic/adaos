from types import SimpleNamespace

from adaos.services.router import dialog_registry as registry


def test_builder_channel_reads_only_its_owner_and_detects_manifest_updates(tmp_path, monkeypatch):
    builder = tmp_path / 'builder_skill'
    other = tmp_path / 'other_skill'
    builder.mkdir()
    other.mkdir()
    manifest = builder / 'skill.yaml'
    manifest.write_text('name: builder_skill\nconversation:\n  dialog_channel:\n    id: builder\n    default_tool: builder_skill.chat\n', encoding='utf-8')
    (other / 'skill.yaml').write_text('name: other_skill\nconversation:\n  dialog_channel:\n    id: helper\n', encoding='utf-8')
    monkeypatch.setattr(registry, 'get_ctx', lambda: SimpleNamespace(paths=SimpleNamespace(skills_workspace_dir=lambda: tmp_path)))
    read = registry._read_skill_manifest
    seen = []
    def track(path):
        seen.append(path)
        return read(path)
    monkeypatch.setattr(registry, '_read_skill_manifest', track)
    first = registry._conversation_manifest_channel_records('host', owner_skill_id='builder_skill')
    assert first[0]['default_tool'] == 'chat' and all(path.name == 'builder_skill' for path in seen)
    seen.clear()
    manifest.write_text(manifest.read_text(encoding='utf-8').replace('builder_skill.chat', 'builder_skill.chat_changed'), encoding='utf-8')
    second = registry._conversation_manifest_channel_records('host', owner_skill_id='builder_skill')
    assert second[0]['default_tool'] == 'chat_changed'
    assert all(path.name == 'builder_skill' for path in seen)
    assert any(row['id'] == 'helper' for row in registry._conversation_manifest_channel_records('host'))
