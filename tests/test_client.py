import pytest

from mnemosyne import client


def test_server_url_order(tmp_path, monkeypatch):
    config = tmp_path / "client.toml"
    config.write_text('url = "https://from-file:8444"\n')
    monkeypatch.delenv("MNEMOSYNE_URL", raising=False)
    assert client.server_url(None, config) == "https://from-file:8444"
    monkeypatch.setenv("MNEMOSYNE_URL", "https://from-env:8444")
    assert client.server_url(None, config) == "https://from-env:8444"
    assert client.server_url("https://explicit", config) == "https://explicit"


def test_server_url_missing_or_broken(tmp_path, monkeypatch):
    monkeypatch.delenv("MNEMOSYNE_URL", raising=False)
    with pytest.raises(client.ClientError) as err:
        client.server_url(None, tmp_path / "absent.toml")
    assert (err.value.exit_code, err.value.code) == (2, "no_url")
    broken = tmp_path / "client.toml"
    broken.write_text("url = \n")
    with pytest.raises(client.ClientError) as err:
        client.server_url(None, broken)
    assert err.value.code == "bad_config"
