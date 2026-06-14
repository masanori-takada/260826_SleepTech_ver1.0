"""起床トリガー収集の統合テスト（Feature 003 / FR-003）。

LINE で「おはよう」等を送ると Webhook が当日 run_morning を実行して結果を返信する。
自己申告（食べた/我慢した）の従来動作が壊れていないことも確認する。
"""

from __future__ import annotations

import json

import handlers.morning_handler as mh
import handlers.webhook_handler as wh
from night_ramen.collectors import ConsentRevokedError
from night_ramen.collectors.self_report import StaticSelfReportSource
from night_ramen.memory.client import MemoryClient


class _FakeHealth:
    """run_morning に注入するフェイクの HealthDataSource。"""

    def __init__(self, raw):
        self._raw = raw

    def fetch_raw(self, date):
        if self._raw == "revoked":
            raise ConsentRevokedError("revoked")
        return self._raw


def _event(text: str) -> dict:
    body = json.dumps(
        {
            "events": [
                {
                    "type": "message",
                    "message": {"type": "text", "text": text},
                    "replyToken": "rt",
                }
            ]
        }
    )
    return {"body": body, "headers": {}}


def _patch(monkeypatch, sent: list, *, deps=None, report_puts=None):
    """webhook_handler の外部依存（Secrets/DDB/LINE）をフェイクに差し替える。"""
    # 署名検証は channel_secret 未設定でスキップされる
    monkeypatch.setattr(wh, "_line_secret", lambda s: {"channel_access_token": "tok"})

    class _FakeReport:
        def __init__(self, *a, **k):
            pass

        def put(self, date, ramen):
            if report_puts is not None:
                report_puts.append((date, ramen))

    monkeypatch.setattr(wh, "DynamoSelfReportSource", _FakeReport)
    monkeypatch.setattr(wh, "reply_message", lambda rt, text, tok: sent.append(text))

    if deps is not None:
        monkeypatch.setattr(mh, "build_morning_dependencies", lambda s: deps)
    else:
        # 起床分岐に入ったら明確に失敗させる（自己申告回帰テスト用）
        def _boom(s):
            raise AssertionError("収集分岐に入ってはいけない")

        monkeypatch.setattr(mh, "build_morning_dependencies", _boom)


def test_wakeup_collects_and_replies(monkeypatch):
    sent: list[str] = []
    memory = MemoryClient()
    health = _FakeHealth(
        {
            "sleep_score": 75,
            "deep_sleep_min": 80,
            "awakenings": 2,
            "night_avg_hr": 58,
            "hrv": 48.0,
        }
    )
    deps = (memory, health, StaticSelfReportSource())
    _patch(monkeypatch, sent, deps=deps)

    resp = wh.handler(_event("おはよう"))

    assert resp["statusCode"] == 200
    # 当日バイタルが保存されている
    saved = memory.get_vitals(wh._today_jst(), wh._today_jst())
    assert len(saved) == 1
    assert saved[0].sleep_score == 75
    # 返信に主要指標が含まれる
    assert len(sent) == 1
    assert "今朝のデータ取れたよ" in sent[0]
    assert "睡眠スコア: 75" in sent[0]
    assert "HRV: 48" in sent[0]


def test_self_report_still_works(monkeypatch):
    sent: list[str] = []
    report_puts: list = []
    _patch(monkeypatch, sent, deps=None, report_puts=report_puts)

    resp = wh.handler(_event("食べた"))

    assert resp["statusCode"] == 200
    # 自己申告が保存され、収集分裂に入っていない（_boom が呼ばれていない）
    assert report_puts and report_puts[0][1] is True
    assert sent and "記録しました" in sent[0]


def test_wakeup_consent_revoked(monkeypatch):
    sent: list[str] = []
    deps = (MemoryClient(), _FakeHealth("revoked"), StaticSelfReportSource())
    _patch(monkeypatch, sent, deps=deps)

    resp = wh.handler(_event("おはよう"))

    assert resp["statusCode"] == 200
    assert sent and "連携が切れている" in sent[0]


def test_wakeup_all_missing(monkeypatch):
    sent: list[str] = []
    deps = (MemoryClient(), _FakeHealth({}), StaticSelfReportSource())
    _patch(monkeypatch, sent, deps=deps)

    wh.handler(_event("データ更新"))

    assert sent and "まだ同期されてない" in sent[0]
