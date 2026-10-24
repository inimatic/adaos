"""Managed bootstrap must not infer delivery policy from an installed Git binary."""
from pathlib import Path

import pytest


@pytest.mark.parametrize('script', ['bootstrap.sh', 'bootstrap_uv.sh'])
def test_managed_bootstrap_persists_archive_delivery_before_git_probe(script):
    text = (Path(__file__).parents[1] / 'tools' / script).read_text(encoding='utf-8')
    policy = '''if [[ "$ENV_TYPE" != "dev" ]]; then
  # Managed runtime consumes archives/packages; Git in PATH is not delivery policy.
  export ADAOS_GIT_MODE=disabled
  write_env_var "ADAOS_GIT_MODE" "disabled" ".env"
fi'''
    assert policy in text
    assert text.index('export ENV_TYPE="${ENV_TYPE:-prod}"') < text.index(policy)
    assert text.index(policy) < text.index('-m adaos git autodetect')
    assert text.index(policy) < text.index('log "Installing default webspace content')
