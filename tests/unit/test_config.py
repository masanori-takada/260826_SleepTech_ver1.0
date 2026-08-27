"""設定ローダーのテスト。"""

import pytest

from sleeptech.config import load_settings


def test_load_settings_uses_spec_defaults(monkeypatch):
    for name in (
        "STC_ENV", "AWS_REGION", "STC_DDB_TABLE", "STC_SECRET_GOOGLE",
        "STC_SECRET_LINE", "STC_SECRET_OPENAI", "LLM_MODEL",
        "STC_TARGET_DROP_PCT", "STC_OVERRIDE_WAKE_H", "STC_MIN_TARGET_SLEEP_H",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = load_settings()

    assert settings.env == "dev"
    assert settings.region == "us-east-1"
    assert settings.table == "SleepTechTable"
    assert settings.target_drop_pct == 10.0
    assert settings.override_wake_h is None


def test_load_settings_reads_override_wake(monkeypatch):
    monkeypatch.setenv("STC_OVERRIDE_WAKE_H", "30.5")
    assert load_settings().override_wake_h == 30.5


def test_min_target_sleep_defaults_to_seven_hours(monkeypatch):
    """最低目標睡眠時間は未指定なら7時間になる。"""
    monkeypatch.delenv("STC_MIN_TARGET_SLEEP_H", raising=False)
    assert load_settings().min_target_sleep_h == 7.0


@pytest.mark.parametrize("raw", ["6.9", "9.1", "abc"])
def test_min_target_sleep_rejects_out_of_range(monkeypatch, raw):
    """最低目標睡眠時間は範囲外または数値でない設定を拒否する。"""
    monkeypatch.setenv("STC_MIN_TARGET_SLEEP_H", raw)
    with pytest.raises(ValueError):
        load_settings()
