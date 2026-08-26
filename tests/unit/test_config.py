"""設定ローダーのテスト。"""

from sleeptech.config import load_settings


def test_load_settings_uses_spec_defaults(monkeypatch):
    for name in (
        "STC_ENV", "AWS_REGION", "STC_DDB_TABLE", "STC_SECRET_GOOGLE",
        "STC_SECRET_LINE", "STC_SECRET_OPENAI", "LLM_MODEL",
        "STC_NIGHT_HOUR_JST", "STC_MORNING_HOUR_JST", "STC_TARGET_DROP_PCT",
        "STC_OVERRIDE_WAKE_H",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = load_settings()

    assert settings.env == "dev"
    assert settings.region == "us-east-1"
    assert settings.table == "SleepTechTable"
    assert settings.night_hour_jst == 22
    assert settings.morning_hour_jst == 8
    assert settings.target_drop_pct == 10.0
    assert settings.override_wake_h is None


def test_load_settings_reads_override_wake(monkeypatch):
    monkeypatch.setenv("STC_OVERRIDE_WAKE_H", "30.5")
    assert load_settings().override_wake_h == 30.5
