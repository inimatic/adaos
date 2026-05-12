from adaos.services.semver import bump_version
from adaos.services.skill.version_policy import effective_skill_bump


def test_bump_version_minor_resets_patch():
    assert bump_version("1.2.3", 1) == "1.3.0"


def test_bump_version_patch_increments_patch():
    assert bump_version("1.2.3", 2) == "1.2.4"


def test_bump_version_handles_prefix_and_missing_parts():
    assert bump_version("v1.2", 1) == "1.3.0"


def test_bump_version_defaults_from_none():
    assert bump_version(None, 1) == "0.1.0"


def test_bump_version_clamps_index():
    assert bump_version("1.2.3", -10) == "2.0.0"
    assert bump_version("1.2.3", 99) == "1.2.4"


def test_effective_skill_bump_promotes_data_migration_patch_to_minor():
    manifest = {"version": "1.2.3", "data_migration": {"tool": "migrate"}}

    assert effective_skill_bump(manifest, "patch") == "minor"
    assert effective_skill_bump(manifest, "major") == "major"


def test_effective_skill_bump_keeps_plain_skill_patch():
    assert effective_skill_bump({"version": "1.2.3"}, "patch") == "patch"

