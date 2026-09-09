"""관리용 비밀번호 입력에서 평문 노출과 잘못된 확인 입력을 막는지 검사한다."""

import getpass
import warnings

import pytest

from backend.app.repositories import InvalidInput
from scripts import manage_accounts


def test_password_requires_interactive_terminal(monkeypatch):
    monkeypatch.setattr(manage_accounts.sys.stdin, "isatty", lambda: False)
    with pytest.raises(InvalidInput, match="대화형 터미널"):
        manage_accounts.read_password()


@pytest.mark.parametrize("confirmation", ["different-password", ""])
def test_password_confirmation_mismatch(monkeypatch, confirmation):
    monkeypatch.setattr(manage_accounts.sys.stdin, "isatty", lambda: True)
    entries = iter(["a-valid-test-password", confirmation])
    monkeypatch.setattr(getpass, "getpass", lambda _: next(entries))
    with pytest.raises(InvalidInput, match="서로 다릅니다"):
        manage_accounts.read_password()


def test_password_never_falls_back_to_echoed_input(monkeypatch):
    monkeypatch.setattr(manage_accounts.sys.stdin, "isatty", lambda: True)

    def unavailable(_):
        warnings.warn("터미널 숨김 입력 불가", getpass.GetPassWarning, stacklevel=2)
        pytest.fail("평문 입력으로 계속 진행하면 안 된다.")

    monkeypatch.setattr(getpass, "getpass", unavailable)
    with pytest.raises(InvalidInput, match="숨김 입력"):
        manage_accounts.read_password()


@pytest.mark.parametrize("length, valid", [(7, False), (8, True), (32, True), (33, False)])
def test_password_setup_uses_signup_length_policy(monkeypatch, length, valid):
    monkeypatch.setattr(manage_accounts.sys.stdin, "isatty", lambda: True)
    password = "x" * length
    monkeypatch.setattr(getpass, "getpass", lambda _: password)
    if valid:
        assert manage_accounts.read_password() == password
    else:
        with pytest.raises(InvalidInput, match="8자 이상 32자 이하"):
            manage_accounts.read_password()
