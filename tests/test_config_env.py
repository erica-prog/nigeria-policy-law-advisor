"""Settings precedence: an exported-but-empty variable must not shadow the
value written in .env (docs/17 "Claude key not detected?")."""

from policy_advisor.config import Settings


def _write_env(tmp_path, key: str):
    env_file = tmp_path / ".env"
    env_file.write_text(f"ANTHROPIC_API_KEY={key}\nAUTH_COOKIE_KEY=file-cookie-key\n")
    return env_file


def test_empty_exported_variable_does_not_override_env_file(tmp_path, monkeypatch):
    env_file = _write_env(tmp_path, "sk-ant-from-file")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    settings = Settings(_env_file=env_file)

    assert settings.anthropic_api_key.get_secret_value() == "sk-ant-from-file"
    assert settings.llm_configured() is True


def test_non_empty_exported_variable_still_wins_over_env_file(tmp_path, monkeypatch):
    env_file = _write_env(tmp_path, "sk-ant-from-file")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-shell")

    settings = Settings(_env_file=env_file)

    assert settings.anthropic_api_key.get_secret_value() == "sk-ant-from-shell"


def test_missing_key_everywhere_reports_not_configured(tmp_path, monkeypatch):
    env_file = _write_env(tmp_path, "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    settings = Settings(_env_file=env_file)

    assert settings.llm_configured() is False
