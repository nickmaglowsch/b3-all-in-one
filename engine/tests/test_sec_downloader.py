"""Guards on the EDGAR client. No network — these test the refusals.

The two that matter are both about failing loudly instead of quietly: EDGAR
answers 403 without a declaring User-Agent, and answers 200 with an HTML error
page when it throttles, so neither a missing header nor a successful status code
can be taken at face value.
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from us_pipeline import sec_downloader as sec


def test_a_missing_user_agent_is_refused_before_the_request(monkeypatch):
    """EDGAR requires a contact address and answers 403 without one. There is no
    default on purpose: a made-up address would work right up until it got the
    host blocked, which is a worse failure and a later one."""
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(RuntimeError, match="SEC_USER_AGENT"):
        sec.user_agent()


def test_a_blank_user_agent_counts_as_missing(monkeypatch):
    """Compose interpolates an unset variable to the empty string."""
    monkeypatch.setenv("SEC_USER_AGENT", "   ")
    with pytest.raises(RuntimeError, match="SEC_USER_AGENT"):
        sec.user_agent()


def test_a_declared_user_agent_is_used(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Quantax ingest contato@exemplo.com.br")
    assert "contato@exemplo.com.br" in sec.user_agent()


def test_a_non_zip_download_is_rejected(tmp_path, monkeypatch):
    """EDGAR answers 200 with an HTML error page when it throttles, so a
    successful status code is not evidence the bytes are an archive. Left
    unchecked it would surface much later as an unreadable member."""
    monkeypatch.setenv("SEC_USER_AGENT", "x y@z.com")

    class _Resp:
        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size=0):
            yield b"<html>rate limited</html>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(sec.requests, "get", lambda *a, **k: _Resp())
    dest = str(tmp_path / "facts.zip")
    with pytest.raises(RuntimeError, match="not a zip"):
        sec.download_bulk_facts(dest)


def _zip(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, payload in members.items():
            zf.writestr(name, payload)
    return buf.getvalue()


def test_the_bulk_archive_is_streamed_member_by_member(tmp_path):
    """It expands to tens of gigabytes; reading it whole is not an option on the
    box this runs on."""
    path = tmp_path / "facts.zip"
    path.write_bytes(_zip({
        "CIK0000320193.json": json.dumps({"cik": 320193, "facts": {}}),
        "CIK0000789019.json": json.dumps({"cik": 789019, "facts": {}}),
    }))
    got = dict(sec.iter_bulk_facts(str(path)))
    assert set(got) == {"0000320193", "0000789019"}


def test_one_unreadable_member_does_not_cost_the_others(tmp_path):
    path = tmp_path / "facts.zip"
    path.write_bytes(_zip({
        "CIK0000320193.json": json.dumps({"cik": 320193, "facts": {}}),
        "CIK0000000001.json": "{not json",
    }))
    assert list(dict(sec.iter_bulk_facts(str(path)))) == ["0000320193"]


def test_non_json_members_are_ignored(tmp_path):
    path = tmp_path / "facts.zip"
    path.write_bytes(_zip({"README.txt": "hello",
                           "CIK0000320193.json": json.dumps({"cik": 320193})}))
    assert list(dict(sec.iter_bulk_facts(str(path)))) == ["0000320193"]
