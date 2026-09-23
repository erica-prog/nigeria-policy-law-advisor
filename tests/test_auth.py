from policy_advisor import auth


def test_load_credentials_returns_empty_usernames_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "CREDENTIALS_PATH", tmp_path / "credentials.yaml")
    assert auth.load_credentials() == {"usernames": {}}


def test_load_credentials_round_trips_a_written_file(tmp_path, monkeypatch):
    path = tmp_path / "credentials.yaml"
    path.write_text(
        "usernames:\n  jdoe:\n    name: Jane Doe\n    password: some-bcrypt-hash\n    email: ''\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(auth, "CREDENTIALS_PATH", path)

    credentials = auth.load_credentials()

    assert credentials["usernames"]["jdoe"]["name"] == "Jane Doe"
    assert credentials["usernames"]["jdoe"]["password"] == "some-bcrypt-hash"
