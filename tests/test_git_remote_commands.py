from adaos.apps.cli.commands import git as git_cmd


def test_convert_github_https_to_ssh() -> None:
    assert git_cmd._convert_github_url("https://github.com/inimatic/adaos.git", "ssh") == (
        "git@github.com:inimatic/adaos.git"
    )


def test_convert_github_ssh_to_https() -> None:
    assert git_cmd._convert_github_url("git@github.com:inimatic/rasa-port.git", "https") == (
        "https://github.com/inimatic/rasa-port.git"
    )


def test_convert_non_github_url_is_unchanged() -> None:
    url = "https://example.com/inimatic/adaos.git"
    assert git_cmd._convert_github_url(url, "ssh") == url
