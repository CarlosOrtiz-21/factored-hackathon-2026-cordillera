"""Deployment bootstrap: data comes from a private dataset only when it is missing."""

import pytest

from cardagent import bootstrap


@pytest.fixture
def targets(tmp_path, monkeypatch):
    paths = {"bank.db": tmp_path / "data" / "bank.db", "intent_v1.joblib": tmp_path / "m.joblib"}
    monkeypatch.setattr(bootstrap, "files", lambda: paths)
    return paths


def test_existing_files_are_kept_and_nothing_is_downloaded(targets, monkeypatch):
    for path in targets.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("local")
    monkeypatch.delenv("HF_DATA_REPO", raising=False)
    bootstrap.main()  # no repo needed when everything is present
    assert all(p.read_text() == "local" for p in targets.values())


def test_missing_data_without_a_repo_stops_the_start(targets, monkeypatch):
    monkeypatch.delenv("HF_DATA_REPO", raising=False)
    with pytest.raises(SystemExit, match="HF_DATA_REPO"):
        bootstrap.main()


def test_missing_data_is_downloaded_from_the_private_dataset(targets, monkeypatch, tmp_path):
    calls = []

    def fake_download(repo_id, filename, repo_type, token):
        calls.append((repo_id, filename, repo_type, token))
        src = tmp_path / f"cache-{filename}"
        src.write_text(filename)
        return str(src)

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_download)
    monkeypatch.setenv("HF_DATA_REPO", "someone/private-data")
    monkeypatch.setenv("HF_TOKEN", "read-token")
    bootstrap.main()
    assert {c[1] for c in calls} == {"bank.db", "intent_v1.joblib"}
    assert all(c[2] == "dataset" and c[3] == "read-token" for c in calls)
    assert targets["bank.db"].read_text() == "bank.db"
