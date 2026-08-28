# 睡眠→翌日パフォーマンス予測 LINE アドバイザー 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 夜22:30に「今夜◯時までに寝れば明日はベスト比 -◯%以内」を、朝8:00に「前夜の睡眠に基づく時間帯別の低下カーブとリカバリー行動」を LINE で送る AWS システムを構築する。

**Architecture:** Two-Process Model（純粋関数）が全ての数値を決定論的に算出し、OpenAI API はその確定値を日本語に整形するだけ。LLM が落ちてもテンプレート文で数値は届く。AWS Lambda×3 + EventBridge Scheduler×2 + DynamoDB×1 を CDK で構築する。

**Tech Stack:** Python 3.12 / pytest / boto3 / AWS CDK (Python) / Google Health API v4 / OpenAI Chat Completions API / LINE Messaging API

## Global Constraints

これらは全タスクの要件に暗黙的に含まれる。

- **Lambda の実行時依存は boto3 + Python 標準ライブラリのみ。** `requests` / `openai` / `numpy` などサードパーティを `src/` 配下で import してはならない。HTTP は `urllib.request` を使う
- **`src/sleeptech/model/` 配下は純粋関数のみ。** boto3・環境変数・現在時刻の取得（`datetime.now`）を含めてはならない。時刻は必ず引数で受け取る
- **数値・時刻を LLM に生成させてはならない。** LLM への入力は確定値であり、出力は文章化のみ
- **すべての日付 `<date>` は「その夜の起点日（＝夜メッセージの送信日）」**。翌朝のメッセージも同じ `<date>` を使う。タイムゾーンは JST 固定
- **時刻の内部表現は「起点日 0:00 からの経過時間（float, 単位=時）」**。翌日 7:00 は `31.0`、翌日 0:30 は `24.5`。この単位を `_h` サフィックスで表す
- テストフレームワークは pytest。全テストは `python -m pytest` で実行できること
- コメント・docstring は日本語で書く
- 仕様書: `docs/superpowers/specs/2026-08-26-sleep-performance-line-advisor-design.md`
- 転用元コードは同リポジトリの `src/night_ramen/` に残っている（Task 1 で削除するので、参照が必要なら `git show HEAD~1:src/night_ramen/...` で読む）

---

## File Structure

| ファイル | 責務 | タスク |
|---|---|---|
| `src/sleeptech/model/constants.py` | モデル定数の集約 | T2 |
| `src/sleeptech/model/two_process.py` | Process S/C・覚醒度・%変換・推奨就寝時刻 | T2,T3,T5 |
| `src/sleeptech/model/baseline.py` | 直近履歴から個人ベースライン算出 | T4 |
| `src/sleeptech/model/recovery.py` | カフェイン・光曝露の時刻算出 | T6 |
| `src/sleeptech/memory/schemas.py` | データクラス定義 | T2,T7 |
| `src/sleeptech/memory/dynamo_backend.py` | DynamoDB の put/get/範囲クエリ | T8 |
| `src/sleeptech/memory/client.py` | エンティティ単位の保存・取得 | T8 |
| `src/sleeptech/guardrail/validator.py` | 文面検証（数値照合を含む） | T9 |
| `src/sleeptech/guardrail/fallback.py` | LLM 不使用のテンプレート文 | T9 |
| `src/sleeptech/llm/client.py` | OpenAI 呼び出し＋リトライ | T10 |
| `src/sleeptech/llm/prompts.py` | システムプロンプトと入力整形 | T10 |
| `src/sleeptech/notifier/line.py` | LINE push・クイックリプライ・冪等 | T11 |
| `src/sleeptech/collectors/google_health_api.py` | Google Health API クライアント | T12 |
| `src/sleeptech/collectors/google_auth_helper.py` | OAuth アクセストークン取得 | T12 |
| `src/sleeptech/workflow/night.py` | 夜フロー | T13 |
| `src/sleeptech/workflow/morning.py` | 朝フロー（冪等を内包） | T14 |
| `src/handlers/*.py` | Lambda エントリ | T15 |
| `infra/stacks/sleeptech_stack.py` | CDK スタック | T16 |

---

### Task 1: プロジェクト骨格

**Files:**
- Delete: `src/night_ramen/`, `src/handlers/`, `tests/`, `infra/stacks/night_ramen_stack.py`, `specs/`, `spec-v4.md`, `handoff-2026-06-15.md`, `scripts/`
- Create: `src/sleeptech/__init__.py` および各サブパッケージの `__init__.py`
- Modify: `pyproject.toml`, `README.md`
- Test: `tests/unit/test_smoke.py`

**Interfaces:**
- Consumes: なし
- Produces: パッケージ `sleeptech` が import 可能になる

- [ ] **Step 1: 移植するファイルを退避してから転用元コードを削除する**

`collectors/` と `observability.py` はそのまま使うので、先に新しい場所へ移す。
これを先にやることで、Task 12 と Task 13 で削除済みコミットを探す必要がなくなる。

```bash
mkdir -p src/sleeptech/collectors
git mv src/night_ramen/collectors/google_health_api.py src/sleeptech/collectors/google_health_api.py
git mv src/night_ramen/collectors/google_auth_helper.py src/sleeptech/collectors/google_auth_helper.py
git mv src/night_ramen/collectors/__init__.py src/sleeptech/collectors/__init__.py
git mv src/night_ramen/observability.py src/sleeptech/observability.py

git rm -r --quiet src/night_ramen src/handlers tests infra/stacks/night_ramen_stack.py specs scripts
git rm --quiet spec-v4.md handoff-2026-06-15.md

# import 文のパッケージ名を書き換える
sed -i 's/night_ramen/sleeptech/g' src/sleeptech/collectors/*.py src/sleeptech/observability.py
```

`src/sleeptech/collectors/self_report.py` と `google_health.py` はラーメン申告専用なので移さない。

- [ ] **Step 2: パッケージ骨格を作る**

`src/__init__.py` も作ること。Lambda ハンドラを `src.handlers.<module>` として import するため、
`src` 自体がパッケージである必要がある。

```bash
mkdir -p src/sleeptech/{collectors,memory,model,llm,guardrail,notifier,workflow} src/handlers infra/stacks tests/unit tests/integration
for d in src src/sleeptech src/sleeptech/memory src/sleeptech/model src/sleeptech/llm src/sleeptech/guardrail src/sleeptech/notifier src/sleeptech/workflow src/handlers infra/stacks; do
  [ -f "$d/__init__.py" ] || printf '' > "$d/__init__.py"
done
```

`src/sleeptech/collectors/__init__.py` は Step 1 で退避済みなので上書きしない
（`ConsentRevokedError` の定義が入っている）。

- [ ] **Step 3: pyproject.toml を書く**

```toml
[project]
name = "sleeptech"
version = "1.0.0"
description = "睡眠→翌日パフォーマンス予測 LINE アドバイザー"
requires-python = ">=3.12"
dependencies = ["boto3>=1.34"]

[project.optional-dependencies]
dev = ["pytest>=8.0", "aws-cdk-lib>=2.140", "constructs>=10.0"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]

[tool.setuptools.packages.find]
where = ["src"]
```

- [ ] **Step 4: スモークテストを書く**

`tests/unit/test_smoke.py`:

```python
"""パッケージが import できることの確認。"""


def test_package_importable():
    import sleeptech

    assert sleeptech is not None
```

- [ ] **Step 5: テストを実行して通ることを確認**

Run: `python -m pytest tests/unit/test_smoke.py -v`
Expected: PASS（1 passed）

- [ ] **Step 6: README を書き換える**

```markdown
# SleepTech

睡眠データから翌日のパフォーマンス低下を予測し、LINE で1日2回アドバイスする。

- **夜 22:30**: 推奨就寝時刻を1つ提示
- **朝 8:00**（「おはよう」で前倒し可）: 時間帯別の低下カーブとリカバリー行動

## アーキテクチャ

| 層 | 技術 |
|----|------|
| インフラ | AWS CDK (Python) — Lambda×3, EventBridge Scheduler×2, DynamoDB, Secrets Manager |
| 予測 | Two-Process Model（純粋関数・LLM 不使用） |
| 文章化 | OpenAI Chat Completions API |
| 通知 | LINE Messaging API |
| データ源 | Google Health API v4（OAuth 2.0） |

## テスト

```bash
python -m pytest
```

仕様: `docs/superpowers/specs/2026-08-26-sleep-performance-line-advisor-design.md`
計画: `docs/superpowers/plans/2026-08-26-sleep-performance-line-advisor.md`
```

- [ ] **Step 7: コミット**

```bash
git add -A
git commit -m "chore: SleepTech のプロジェクト骨格を作成し転用元コードを削除"
```

---

### Task 2: モデル定数と Process S / Process C

**Files:**
- Create: `src/sleeptech/model/constants.py`
- Create: `src/sleeptech/model/two_process.py`
- Create: `src/sleeptech/memory/schemas.py`
- Test: `tests/unit/test_two_process_processes.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `constants.TAU_RISE, TAU_DECAY, CIRCADIAN_AMPLITUDE, HARMONIC_RATIO, PERF_STEEPNESS, PERF_THRESHOLD, W_REM, S_WAKE_BASE, DEBT_TO_S0, DEFAULT_WAKE_H, DEFAULT_SLEEP_H, TARGET_DROP_PCT, SCAN_STEP_MIN, MIN_SLEEP_BEFORE_WAKE_H`
  - `schemas.SleepInput(bedtime_h: float, wake_h: float, rem_min: float|None, deep_min: float|None)` — プロパティ `duration_h -> float`
  - `two_process.pressure_awake(s0: float, hours: float) -> float`
  - `two_process.pressure_asleep(s0: float, hours: float) -> float`
  - `two_process.circadian(t_h: float, wake_h: float) -> float`

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/test_two_process_processes.py`:

```python
"""Process S / Process C の基本性質。"""

import math

from sleeptech.model import constants as C
from sleeptech.model import two_process as tp


def test_pressure_awake_increases_monotonically():
    """覚醒中の睡眠圧は時間とともに単調増加する。"""
    s0 = 0.2
    values = [tp.pressure_awake(s0, h) for h in (0.0, 4.0, 8.0, 16.0)]
    assert values[0] == 0.2
    assert values == sorted(values)
    assert values[-1] < 1.0


def test_pressure_asleep_decays_monotonically():
    """睡眠中の睡眠圧は時間とともに単調減少し、0 に漸近する。"""
    s0 = 0.7
    values = [tp.pressure_asleep(s0, h) for h in (0.0, 2.0, 4.0, 8.0)]
    assert values[0] == 0.7
    assert values == sorted(values, reverse=True)
    assert values[-1] > 0.0


def test_pressure_awake_matches_formula():
    """既知の入力に対する期待値。"""
    got = tp.pressure_awake(0.2, 16.5)
    want = 1.0 - (1.0 - 0.2) * math.exp(-16.5 / C.TAU_RISE)
    assert got == round(want, 6)


def test_circadian_minimum_is_two_hours_before_wake():
    """概日リズムの底は起床の2時間前にある。"""
    wake_h = 31.0  # 翌日 7:00
    t_min = wake_h - 2.0
    at_min = tp.circadian(t_min, wake_h)
    around = [tp.circadian(t_min + d, wake_h) for d in (-1.5, -0.5, 0.5, 1.5)]
    assert all(v > at_min for v in around)


def test_circadian_has_afternoon_dip():
    """起床8時間後（14時台）に副次的な谷がある。"""
    wake_h = 31.0  # 翌日 7:00 → 14:00 は 38.0
    at_dip = tp.circadian(39.0, wake_h)  # 起床+8h = 15:00
    at_morning = tp.circadian(34.0, wake_h)  # 起床+3h = 10:00
    at_evening = tp.circadian(42.0, wake_h)  # 起床+11h = 18:00
    assert at_dip < at_evening
    assert at_dip < at_morning + 0.25  # 谷が主成分の上昇に埋もれていない
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `python -m pytest tests/unit/test_two_process_processes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sleeptech.model.constants'`

- [ ] **Step 3: constants.py を書く**

`src/sleeptech/model/constants.py`:

```python
"""Two-Process Model とリカバリー算出の定数（仕様 第5節・第6節）。

TAU_RISE / TAU_DECAY は Borbely/Achermann 系 Two-Process Model の標準的な値。
それ以外は仕様 第11節の出力レンジ検査を満たすよう決めた値であり、
検査が通らない場合のみ再調整する。
"""

# --- Process S（恒常性睡眠圧）---
TAU_RISE = 18.2    # 覚醒中の睡眠圧上昇の時定数（時間）
TAU_DECAY = 4.2    # 睡眠中の睡眠圧減衰の時定数（時間）

# --- Process C（概日リズム）---
CIRCADIAN_AMPLITUDE = 0.25  # 振幅 A
HARMONIC_RATIO = 0.5        # 午後の谷の相対的な深さ H
PHASE_MIN_OFFSET_H = -2.0   # 覚醒度の底 = 起床時刻 + このオフセット
PHASE_DIP_OFFSET_H = 8.0    # 午後の谷 = 起床時刻 + このオフセット

# --- 覚醒度 → パフォーマンスのシグモイド変換 ---
PERF_STEEPNESS = 6.0   # 傾き K
PERF_THRESHOLD = -0.3  # 中心 THETA

# --- 気分カーブ ---
W_REM = 0.08  # REM 不足の重み

# --- 睡眠負債 ---
S_WAKE_BASE = 0.15   # 負債ゼロのときの起床時睡眠圧
DEBT_TO_S0 = 0.03    # 負債1時間あたりの起床時睡眠圧への上乗せ
DEBT_DECAY = 0.5     # 睡眠負債の日次減衰係数（直近ほど重い）
DEBT_LOOKBACK_DAYS = 7
S0_MAX = 0.9         # 起床時睡眠圧の上限（クランプ）

# --- ベースライン ---
BASELINE_LOOKBACK_DAYS = 14
BASELINE_MIN_DAYS = 3       # これ未満なら既定値にフォールバック
DEFAULT_WAKE_H = 31.0       # 翌日 7:00
DEFAULT_SLEEP_H = 7.5

# --- 推奨就寝時刻の探索 ---
TARGET_DROP_PCT = 10.0        # 主要時間帯の平均低下率の目標上限
SCAN_STEP_MIN = 5             # 探索の刻み（分）
MIN_SLEEP_BEFORE_WAKE_H = 4.0  # 探索区間の終端（起床の何時間前まで許すか）
MAIN_WINDOW_START_H = 2.0     # 主要時間帯の開始（起床からの経過時間）
MAIN_WINDOW_END_H = 10.0      # 主要時間帯の終了（同上）
CURVE_HOURS = 16              # 予測カーブを何時間分出すか

# --- カフェイン ---
CAFFEINE_HALF_LIFE_H = 5.0
CAFFEINE_BEDTIME_RATIO = 0.25   # 就寝時に残ってよい割合の上限
CAFFEINE_SPLIT_THRESHOLD_H = 8.0  # 起床〜最終摂取可能時刻がこれを超えたら2回に分ける
CAFFEINE_FIRST_OFFSET_H = 1.0     # 1回目 = 起床 + このオフセット
CAFFEINE_PRE_DIP_H = 1.5          # 2回目 = 午後の谷 - このオフセット

# --- 光曝露 ---
LIGHT_MORNING_WITHIN_MIN = 30   # 起床後この分数以内に屋外光
LIGHT_MORNING_DURATION_MIN = 10
LIGHT_AVOID_BEFORE_BED_H = 3.0  # 就寝の何時間前から避光するか
```

- [ ] **Step 4: schemas.py に SleepInput を書く**

`src/sleeptech/memory/schemas.py`:

```python
"""記憶エンティティと予測入出力のスキーマ（仕様 第9節）。

時刻はすべて「起点日 0:00 からの経過時間（float, 単位=時）」で表す。
翌日 7:00 は 31.0、翌日 0:30 は 24.5。この単位のフィールドは _h で終わる。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SleepInput:
    """予測モデルに渡す1晩分の睡眠。"""

    bedtime_h: float
    wake_h: float
    rem_min: float | None = None
    deep_min: float | None = None

    @property
    def duration_h(self) -> float:
        """睡眠時間（時間）。"""
        return self.wake_h - self.bedtime_h
```

- [ ] **Step 5: two_process.py に Process S / C を書く**

`src/sleeptech/model/two_process.py`:

```python
"""Two-Process Model による翌日パフォーマンス予測（仕様 第5節）。

このモジュールは純粋関数のみで構成する。boto3・環境変数・現在時刻の取得を
含めてはならない。時刻はすべて引数で受け取る。
"""

from __future__ import annotations

import math

from sleeptech.model import constants as C


def pressure_awake(s0: float, hours: float) -> float:
    """覚醒を hours 時間続けた後の睡眠圧。s0 は開始時点の睡眠圧。"""
    return round(1.0 - (1.0 - s0) * math.exp(-hours / C.TAU_RISE), 6)


def pressure_asleep(s0: float, hours: float) -> float:
    """睡眠を hours 時間続けた後の睡眠圧。s0 は入眠時点の睡眠圧。"""
    return round(s0 * math.exp(-hours / C.TAU_DECAY), 6)


def circadian(t_h: float, wake_h: float) -> float:
    """時刻 t_h における概日リズム成分。

    第1項は起床2時間前に最小値をとる24時間周期。
    第2項は起床8時間後に谷を作る12時間周期（post-lunch dip）。
    """
    t_min = wake_h + C.PHASE_MIN_OFFSET_H
    t_dip = wake_h + C.PHASE_DIP_OFFSET_H
    main = -C.CIRCADIAN_AMPLITUDE * math.cos(2 * math.pi * (t_h - t_min) / 24.0)
    dip = -C.CIRCADIAN_AMPLITUDE * C.HARMONIC_RATIO * math.cos(
        2 * math.pi * (t_h - t_dip) / 12.0
    )
    return round(main + dip, 6)
```

- [ ] **Step 6: テストを実行して通ることを確認**

Run: `python -m pytest tests/unit/test_two_process_processes.py -v`
Expected: PASS（5 passed）

- [ ] **Step 7: コミット**

```bash
git add src/sleeptech/model/constants.py src/sleeptech/model/two_process.py src/sleeptech/memory/schemas.py tests/unit/test_two_process_processes.py
git commit -m "feat(model): Process S / Process C とモデル定数を実装"
```

---

### Task 3: 覚醒度・パフォーマンス変換・予測カーブ

**Files:**
- Modify: `src/sleeptech/model/two_process.py`
- Modify: `src/sleeptech/memory/schemas.py`
- Test: `tests/unit/test_two_process_curve.py`

**Interfaces:**
- Consumes: `two_process.pressure_awake/pressure_asleep/circadian`, `schemas.SleepInput`
- Produces:
  - `schemas.Baseline(wake_h: float, sleep_h: float, rem_min: float|None, deep_min: float|None, days_used: int, is_default: bool)`
  - `schemas.HourlyDrop(hour: int, focus_pct: int, mood_pct: int)` — `hour` は起床日の 0-23 時
  - `two_process.performance(alert: float) -> float`
  - `two_process.sleep_debt_h(recent: list[SleepInput], baseline: Baseline) -> float`
  - `two_process.predict_curve(sleep: SleepInput, baseline: Baseline, debt_h: float) -> list[HourlyDrop]`
  - `two_process.mean_drop_in_main_window(curve: list[HourlyDrop], wake_h: float) -> float`

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/test_two_process_curve.py`:

```python
"""覚醒度→%変換と予測カーブの性質（仕様 第5節・第11節）。"""

import pytest

from sleeptech.memory.schemas import Baseline, SleepInput
from sleeptech.model import two_process as tp

WAKE_H = 31.0  # 翌日 7:00
BASE = Baseline(
    wake_h=WAKE_H, sleep_h=7.5, rem_min=90.0, deep_min=60.0, days_used=14, is_default=False
)


def _sleep(hours: float, rem_min: float = 90.0) -> SleepInput:
    """起床時刻を固定し、睡眠時間だけ変えた入力を作る。"""
    return SleepInput(
        bedtime_h=WAKE_H - hours, wake_h=WAKE_H, rem_min=rem_min, deep_min=60.0
    )


def _at(curve, hour: int) -> int:
    return next(d.focus_pct for d in curve if d.hour == hour)


def test_performance_is_monotonic_in_alertness():
    """覚醒度が高いほどパフォーマンスが高い。"""
    values = [tp.performance(a) for a in (-1.0, -0.5, 0.0, 0.5)]
    assert values == sorted(values)
    assert all(0.0 < v < 1.0 for v in values)


def test_baseline_sleep_yields_zero_drop():
    """平常時と同じ睡眠時間なら低下率は全時間帯 0%。"""
    curve = tp.predict_curve(_sleep(7.5), BASE, debt_h=0.0)
    assert all(d.focus_pct == 0 for d in curve)
    assert all(d.mood_pct == 0 for d in curve)


def test_longer_than_baseline_clamps_to_zero():
    """平常時より長く寝ても低下率は負にならず 0 に張り付く。"""
    curve = tp.predict_curve(_sleep(9.0), BASE, debt_h=0.0)
    assert all(d.focus_pct == 0 for d in curve)


def test_shorter_sleep_drops_more():
    """睡眠が短いほど低下率が大きい（14時で比較）。"""
    d5 = _at(tp.predict_curve(_sleep(5.0), BASE, 0.0), 14)
    d6 = _at(tp.predict_curve(_sleep(6.0), BASE, 0.0), 14)
    d7 = _at(tp.predict_curve(_sleep(7.0), BASE, 0.0), 14)
    assert d5 > d6 > d7


def test_afternoon_dip_is_worse_than_morning():
    """午後の谷（14時）は午前（10時）より落ち込みが大きい。"""
    curve = tp.predict_curve(_sleep(5.0), BASE, 0.0)
    assert _at(curve, 14) > _at(curve, 10)


@pytest.mark.parametrize(
    "hours,lo,hi",
    [(5.0, 12, 35), (6.0, 5, 20)],
)
def test_drop_is_within_plausible_range(hours, lo, hi):
    """14時の低下率が常識的なレンジに収まる（仕様 第5節の期待出力表）。"""
    got = _at(tp.predict_curve(_sleep(hours), BASE, 0.0), 14)
    assert lo <= got <= hi


def test_mood_drops_more_than_focus_when_rem_is_short():
    """REM が不足した日は気分カーブの方が大きく落ちる。"""
    curve = tp.predict_curve(_sleep(5.0, rem_min=40.0), BASE, 0.0)
    at14 = next(d for d in curve if d.hour == 14)
    assert at14.mood_pct > at14.focus_pct


def test_mood_equals_focus_when_rem_missing():
    """REM も深睡眠も欠損なら気分カーブは集中力カーブと一致する。"""
    sleep = SleepInput(bedtime_h=WAKE_H - 5.0, wake_h=WAKE_H, rem_min=None, deep_min=None)
    curve = tp.predict_curve(sleep, BASE, 0.0)
    assert all(d.mood_pct == d.focus_pct for d in curve)


def test_sleep_debt_weights_recent_days_more():
    """睡眠負債は直近の日ほど重く効く。"""
    recent_bad = [_sleep(5.0), _sleep(7.5), _sleep(7.5)]
    old_bad = [_sleep(7.5), _sleep(7.5), _sleep(5.0)]
    assert tp.sleep_debt_h(recent_bad, BASE) > tp.sleep_debt_h(old_bad, BASE)


def test_sleep_debt_is_zero_when_well_rested():
    """十分に寝ている日が続けば負債は 0。"""
    assert tp.sleep_debt_h([_sleep(8.0)] * 5, BASE) == 0.0


def test_debt_increases_drop():
    """睡眠負債があると同じ睡眠時間でも低下率が大きくなる。"""
    no_debt = _at(tp.predict_curve(_sleep(6.0), BASE, 0.0), 14)
    with_debt = _at(tp.predict_curve(_sleep(6.0), BASE, 4.0), 14)
    assert with_debt > no_debt


def test_curve_covers_expected_hours():
    """カーブは起床時刻から CURVE_HOURS 時間分を1時間刻みで返す。"""
    curve = tp.predict_curve(_sleep(7.0), BASE, 0.0)
    assert len(curve) == 16
    assert curve[0].hour == 7
    assert curve[-1].hour == 22


def test_mean_drop_in_main_window():
    """主要時間帯（起床+2h〜+10h）の平均低下率を返す。"""
    curve = tp.predict_curve(_sleep(5.0), BASE, 0.0)
    mean = tp.mean_drop_in_main_window(curve, WAKE_H)
    in_window = [d.focus_pct for d in curve if 9 <= d.hour <= 17]
    assert mean == pytest.approx(sum(in_window) / len(in_window))
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `python -m pytest tests/unit/test_two_process_curve.py -v`
Expected: FAIL — `ImportError: cannot import name 'Baseline' from 'sleeptech.memory.schemas'`

- [ ] **Step 3: schemas.py に Baseline と HourlyDrop を追加**

`src/sleeptech/memory/schemas.py` の末尾に追記:

```python
@dataclass(frozen=True)
class Baseline:
    """直近履歴から算出した個人の平常時。

    is_default=True は履歴が不足しており既定値を使ったことを表す。
    このときメッセージに「まだデータが少ない」旨を含める必要がある。
    """

    wake_h: float
    sleep_h: float
    rem_min: float | None = None
    deep_min: float | None = None
    days_used: int = 0
    is_default: bool = False


@dataclass(frozen=True)
class HourlyDrop:
    """ある時刻におけるパフォーマンス低下率（%、0以上の整数）。"""

    hour: int        # 起床日の時刻（0-23）
    focus_pct: int   # 集中力
    mood_pct: int    # 気分
```

- [ ] **Step 4: two_process.py に変換とカーブを追加**

`src/sleeptech/model/two_process.py` の末尾に追記:

```python
def performance(alert: float) -> float:
    """覚醒度をパフォーマンス（0-1）に変換する。

    線形ではなくシグモイドを通す。覚醒度が低い領域（午後の谷）で
    睡眠不足の差が増幅され、「14-16時が谷」という体感を再現できる。
    """
    return 1.0 / (1.0 + math.exp(-C.PERF_STEEPNESS * (alert - C.PERF_THRESHOLD)))


def sleep_debt_h(recent: list, baseline) -> float:
    """直近の睡眠不足を減衰加重で積算した負債（時間）。

    recent は新しい順に並んだ SleepInput のリスト。先頭ほど重みが大きい。
    """
    total = 0.0
    for i, s in enumerate(recent[: C.DEBT_LOOKBACK_DAYS]):
        deficit = max(0.0, baseline.sleep_h - s.duration_h)
        total += deficit * (C.DEBT_DECAY**i)
    return round(total, 3)


def _rem_deficit_ratio(sleep, baseline) -> float:
    """REM 不足の比率（0-1）。REM が無ければ深睡眠で代替、両方無ければ 0。"""
    for actual, base in ((sleep.rem_min, baseline.rem_min), (sleep.deep_min, baseline.deep_min)):
        if actual is not None and base:
            return max(0.0, min(1.0, (base - actual) / base))
    return 0.0


def _wake_pressure(sleep, debt_h: float, baseline) -> float:
    """その晩を寝終えた時点の睡眠圧を求める。

    前日の起床時刻は baseline.wake_h - 24 とみなす（前日も同じリズムだった前提）。
    """
    s_prev_wake = min(C.S0_MAX, C.S_WAKE_BASE + C.DEBT_TO_S0 * debt_h)
    awake_h = max(0.0, sleep.bedtime_h - (baseline.wake_h - 24.0))
    s_bed = pressure_awake(s_prev_wake, awake_h)
    return pressure_asleep(s_bed, max(0.0, sleep.duration_h))


def _curve_points(sleep, baseline, debt_h: float) -> list[tuple[float, float]]:
    """(時刻 t_h, 覚醒度) の列を CURVE_HOURS 時間分返す。"""
    s_wake = _wake_pressure(sleep, debt_h, baseline)
    points = []
    for i in range(C.CURVE_HOURS):
        t_h = sleep.wake_h + i
        s = pressure_awake(s_wake, float(i))
        points.append((t_h, circadian(t_h, sleep.wake_h) - s))
    return points


def predict_curve(sleep, baseline, debt_h: float) -> list:
    """実際の睡眠と平常時を比較し、時間帯別の低下率を返す。

    平常時カーブは「同じ起床時刻で baseline.sleep_h だけ寝た場合」。
    低下率は 0 未満にならないようクランプする（寝過ぎでボーナスは出さない）。
    """
    from sleeptech.memory.schemas import HourlyDrop, SleepInput

    ideal = SleepInput(
        bedtime_h=sleep.wake_h - baseline.sleep_h,
        wake_h=sleep.wake_h,
        rem_min=baseline.rem_min,
        deep_min=baseline.deep_min,
    )
    actual_points = _curve_points(sleep, baseline, debt_h)
    ideal_points = _curve_points(ideal, baseline, 0.0)
    rem_penalty = C.W_REM * _rem_deficit_ratio(sleep, baseline)

    curve = []
    for (t_h, alert_actual), (_, alert_ideal) in zip(actual_points, ideal_points):
        p_ideal = performance(alert_ideal)
        focus = (p_ideal - performance(alert_actual)) / p_ideal * 100.0
        mood = (p_ideal - performance(alert_actual - rem_penalty)) / p_ideal * 100.0
        curve.append(
            HourlyDrop(
                hour=int(t_h) % 24,
                focus_pct=max(0, min(100, round(focus))),
                mood_pct=max(0, min(100, round(mood))),
            )
        )
    return curve


def mean_drop_in_main_window(curve: list, wake_h: float) -> float:
    """主要時間帯（起床+2h 〜 起床+10h）の集中力低下率の平均。"""
    start = int(wake_h + C.MAIN_WINDOW_START_H) % 24
    end = int(wake_h + C.MAIN_WINDOW_END_H) % 24
    picked = [d.focus_pct for d in curve if start <= d.hour <= end]
    if not picked:
        picked = [d.focus_pct for d in curve]
    return sum(picked) / len(picked)
```

- [ ] **Step 5: テストを実行する**

Run: `python -m pytest tests/unit/test_two_process_curve.py -v`
Expected: PASS（14 passed）

`test_drop_is_within_plausible_range` が失敗した場合は、`constants.py` の `CIRCADIAN_AMPLITUDE` `HARMONIC_RATIO` `PERF_STEEPNESS` `PERF_THRESHOLD` の4つのみを調整する。他のテスト（順序関係）を壊さないこと。調整したら定数のコメントに実測値を記録する。

- [ ] **Step 6: コミット**

```bash
git add src/sleeptech/model/two_process.py src/sleeptech/memory/schemas.py tests/unit/test_two_process_curve.py
git commit -m "feat(model): 覚醒度のシグモイド変換と時間帯別予測カーブを実装"
```

---

### Task 4: 個人ベースラインの算出

**Files:**
- Create: `src/sleeptech/model/baseline.py`
- Test: `tests/unit/test_baseline.py`

**Interfaces:**
- Consumes: `schemas.SleepInput`, `schemas.Baseline`, `constants`
- Produces: `baseline.compute(recent: list[SleepInput], override_wake_h: float|None = None) -> Baseline`

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/test_baseline.py`:

```python
"""個人ベースラインの算出（仕様 第10節「履歴が3日未満」）。"""

from sleeptech.memory.schemas import SleepInput
from sleeptech.model import baseline
from sleeptech.model import constants as C


def _night(bedtime_h: float, wake_h: float, rem: float = 90.0) -> SleepInput:
    return SleepInput(bedtime_h=bedtime_h, wake_h=wake_h, rem_min=rem, deep_min=60.0)


def test_uses_defaults_when_history_is_empty():
    """履歴ゼロなら既定値を使い、is_default が立つ。"""
    got = baseline.compute([])
    assert got.wake_h == C.DEFAULT_WAKE_H
    assert got.sleep_h == C.DEFAULT_SLEEP_H
    assert got.is_default is True
    assert got.days_used == 0


def test_uses_defaults_when_history_is_too_short():
    """3日未満なら既定値。"""
    got = baseline.compute([_night(23.0, 31.0), _night(23.5, 31.0)])
    assert got.is_default is True
    assert got.days_used == 2


def test_uses_median_when_history_is_sufficient():
    """3日以上あれば中央値を使い、既定値へは落とさない。"""
    nights = [_night(23.0, 31.0), _night(23.5, 31.5), _night(24.0, 30.5)]
    got = baseline.compute(nights)
    assert got.is_default is False
    assert got.days_used == 3
    assert got.wake_h == 31.0  # 30.5, 31.0, 31.5 の中央値
    assert got.sleep_h == 7.5  # 8.0, 8.0, 6.5 の中央値


def test_ignores_days_beyond_lookback():
    """BASELINE_LOOKBACK_DAYS を超える古い日は無視する。"""
    nights = [_night(23.0, 31.0)] * C.BASELINE_LOOKBACK_DAYS + [_night(20.0, 28.0)] * 5
    got = baseline.compute(nights)
    assert got.days_used == C.BASELINE_LOOKBACK_DAYS
    assert got.wake_h == 31.0


def test_override_wins_over_computed_value():
    """設定による上書きが実績より優先される。"""
    nights = [_night(23.0, 31.0)] * 5
    got = baseline.compute(nights, override_wake_h=30.0)
    assert got.wake_h == 30.0
    assert got.is_default is False


def test_override_applies_even_with_no_history():
    """履歴ゼロでも上書き値は反映される。"""
    got = baseline.compute([], override_wake_h=30.0)
    assert got.wake_h == 30.0
    assert got.sleep_h == C.DEFAULT_SLEEP_H
    assert got.is_default is True


def test_rem_baseline_ignores_missing_values():
    """REM が欠損した日は中央値の計算から除外する。"""
    nights = [_night(23.0, 31.0, rem=100.0), _night(23.0, 31.0, rem=80.0),
              SleepInput(bedtime_h=23.0, wake_h=31.0, rem_min=None, deep_min=None)]
    got = baseline.compute(nights)
    assert got.rem_min == 90.0


def test_rem_baseline_is_none_when_all_missing():
    """全日で REM が欠損なら None。"""
    nights = [SleepInput(bedtime_h=23.0, wake_h=31.0, rem_min=None, deep_min=None)] * 4
    got = baseline.compute(nights)
    assert got.rem_min is None
    assert got.deep_min is None
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `python -m pytest tests/unit/test_baseline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sleeptech.model.baseline'`

- [ ] **Step 3: baseline.py を書く**

`src/sleeptech/model/baseline.py`:

```python
"""直近履歴から個人の平常時を求める（仕様 第10節）。

純粋関数のみ。現在時刻や DynamoDB には触れない。
"""

from __future__ import annotations

import statistics

from sleeptech.memory.schemas import Baseline, SleepInput
from sleeptech.model import constants as C


def _median_or_none(values: list[float]) -> float | None:
    """欠損を除いた中央値。値が1つも無ければ None。"""
    present = [v for v in values if v is not None]
    return round(statistics.median(present), 2) if present else None


def compute(recent: list[SleepInput], override_wake_h: float | None = None) -> Baseline:
    """新しい順に並んだ睡眠履歴からベースラインを算出する。

    履歴が BASELINE_MIN_DAYS 未満なら起床時刻・睡眠時間に既定値を用い、
    is_default=True を立てる。override_wake_h が与えられれば起床時刻はそれで上書きする。
    """
    used = recent[: C.BASELINE_LOOKBACK_DAYS]
    insufficient = len(used) < C.BASELINE_MIN_DAYS

    if insufficient:
        wake_h = C.DEFAULT_WAKE_H
        sleep_h = C.DEFAULT_SLEEP_H
    else:
        wake_h = _median_or_none([s.wake_h for s in used])
        sleep_h = _median_or_none([s.duration_h for s in used])

    return Baseline(
        wake_h=override_wake_h if override_wake_h is not None else wake_h,
        sleep_h=sleep_h,
        rem_min=_median_or_none([s.rem_min for s in used]),
        deep_min=_median_or_none([s.deep_min for s in used]),
        days_used=len(used),
        is_default=insufficient,
    )
```

- [ ] **Step 4: テストを実行する**

Run: `python -m pytest tests/unit/test_baseline.py -v`
Expected: PASS（8 passed）

- [ ] **Step 5: コミット**

```bash
git add src/sleeptech/model/baseline.py tests/unit/test_baseline.py
git commit -m "feat(model): 直近履歴から個人ベースラインを算出"
```

---

### Task 5: 推奨就寝時刻の探索

**Files:**
- Modify: `src/sleeptech/model/two_process.py`
- Modify: `src/sleeptech/memory/schemas.py`
- Test: `tests/unit/test_recommend_bedtime.py`

**Interfaces:**
- Consumes: `two_process.predict_curve`, `two_process.mean_drop_in_main_window`, `schemas.Baseline`
- Produces:
  - `schemas.BedtimeAdvice(bedtime_h: float, mean_drop_pct: int, curve: list[HourlyDrop], status: str)` — `status` は `"ok"` / `"unreachable"` / `"too_late"`
  - `two_process.predict_curve_from_bedtime(bedtime_h: float, baseline: Baseline, debt_h: float, rem_min=None, deep_min=None) -> list[HourlyDrop]`
  - `two_process.recommend_bedtime(now_h: float, baseline: Baseline, debt_h: float, rem_min: float|None = None, deep_min: float|None = None) -> BedtimeAdvice`

**設計メモ:** 二分探索は使わない。Process C の位相との相互作用で低下率が就寝時刻に対して局所的に非単調になりうるため、二分探索は誤った解に収束する。`SCAN_STEP_MIN` 刻みの線形スキャンで全候補を評価する。

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/test_recommend_bedtime.py`:

```python
"""推奨就寝時刻の探索と3つの境界ケース（仕様 第5節「推奨就寝時刻の解法」）。"""

from sleeptech.memory.schemas import Baseline
from sleeptech.model import constants as C
from sleeptech.model import two_process as tp

BASE = Baseline(
    wake_h=31.0, sleep_h=7.5, rem_min=90.0, deep_min=60.0, days_used=14, is_default=False
)


def test_returns_ok_status_in_normal_case():
    """22:30 時点で十分に間に合う場合は status='ok'。"""
    got = tp.recommend_bedtime(now_h=22.5, baseline=BASE, debt_h=0.0)
    assert got.status == "ok"
    assert 22.5 <= got.bedtime_h <= BASE.wake_h - C.MIN_SLEEP_BEFORE_WAKE_H
    assert got.mean_drop_pct <= C.TARGET_DROP_PCT


def test_does_not_recommend_less_than_baseline_sleep_when_possible():
    """全区間で閾値を満たす場合、平常時睡眠時間を確保できる時刻を推奨する。

    最も遅い候補（起床4時間前）に飛びつくと過小睡眠を勧めることになる。
    """
    got = tp.recommend_bedtime(now_h=18.0, baseline=BASE, debt_h=0.0)
    assert got.bedtime_h <= BASE.wake_h - BASE.sleep_h


def test_unreachable_when_debt_is_overwhelming():
    """負債が大きく、どんなに早く寝ても閾値に届かない場合は status='unreachable'。"""
    got = tp.recommend_bedtime(now_h=22.5, baseline=BASE, debt_h=30.0)
    assert got.status == "unreachable"
    assert got.mean_drop_pct > C.TARGET_DROP_PCT


def test_unreachable_still_returns_the_best_available_time():
    """届かない場合でも、区間内で最も低下率が小さい時刻を返す。"""
    got = tp.recommend_bedtime(now_h=22.5, baseline=BASE, debt_h=30.0)
    limit = BASE.wake_h - C.MIN_SLEEP_BEFORE_WAKE_H
    assert 22.5 <= got.bedtime_h <= limit
    later = tp.predict_curve_from_bedtime(got.bedtime_h + 1.0, BASE, 30.0)
    assert got.mean_drop_pct <= tp.mean_drop_in_main_window(later, BASE.wake_h)


def test_too_late_when_now_is_past_the_window():
    """既に探索区間の終端を過ぎていれば status='too_late' で「今すぐ」を返す。"""
    now = BASE.wake_h - 2.0  # 起床2時間前
    got = tp.recommend_bedtime(now_h=now, baseline=BASE, debt_h=0.0)
    assert got.status == "too_late"
    assert got.bedtime_h == now


def test_curve_is_returned_for_the_recommended_time():
    """返り値のカーブは推奨就寝時刻で寝た場合のもの。"""
    got = tp.recommend_bedtime(now_h=22.5, baseline=BASE, debt_h=0.0)
    expected = tp.predict_curve_from_bedtime(got.bedtime_h, BASE, 0.0)
    assert got.curve == expected


def test_scan_is_deterministic():
    """同じ入力からは必ず同じ結果が出る。"""
    a = tp.recommend_bedtime(now_h=22.5, baseline=BASE, debt_h=1.5)
    b = tp.recommend_bedtime(now_h=22.5, baseline=BASE, debt_h=1.5)
    assert a == b
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `python -m pytest tests/unit/test_recommend_bedtime.py -v`
Expected: FAIL — `AttributeError: module 'sleeptech.model.two_process' has no attribute 'recommend_bedtime'`

- [ ] **Step 3: schemas.py に BedtimeAdvice を追加**

`src/sleeptech/memory/schemas.py` の末尾に追記:

```python
@dataclass(frozen=True)
class BedtimeAdvice:
    """夜フローが算出する推奨就寝時刻とその根拠。

    status:
      "ok"          — 閾値を満たす就寝時刻が見つかった
      "unreachable" — どんなに早く寝ても閾値に届かない（最善時刻を返す）
      "too_late"    — 既に探索区間の終端を過ぎている（今すぐ寝るべき）
    """

    bedtime_h: float
    mean_drop_pct: int
    curve: list
    status: str
```

- [ ] **Step 4: two_process.py に探索を追加**

`src/sleeptech/model/two_process.py` の末尾に追記:

```python
def predict_curve_from_bedtime(bedtime_h: float, baseline, debt_h: float,
                               rem_min=None, deep_min=None) -> list:
    """就寝時刻を指定して予測カーブを得る（起床時刻は baseline に従う）。"""
    from sleeptech.memory.schemas import SleepInput

    sleep = SleepInput(
        bedtime_h=bedtime_h,
        wake_h=baseline.wake_h,
        rem_min=rem_min if rem_min is not None else baseline.rem_min,
        deep_min=deep_min if deep_min is not None else baseline.deep_min,
    )
    return predict_curve(sleep, baseline, debt_h)


def recommend_bedtime(now_h: float, baseline, debt_h: float,
                      rem_min=None, deep_min=None):
    """閾値を満たす最も遅い就寝時刻を線形スキャンで求める。

    探索区間は [now_h, baseline.wake_h - MIN_SLEEP_BEFORE_WAKE_H]。
    二分探索は使わない（低下率が就寝時刻に対して局所的に非単調になりうるため）。
    """
    from sleeptech.memory.schemas import BedtimeAdvice

    limit_h = baseline.wake_h - C.MIN_SLEEP_BEFORE_WAKE_H

    def _evaluate(t: float):
        curve = predict_curve_from_bedtime(t, baseline, debt_h, rem_min, deep_min)
        return curve, mean_drop_in_main_window(curve, baseline.wake_h)

    # 既に終端を過ぎている
    if now_h >= limit_h:
        curve, mean = _evaluate(now_h)
        return BedtimeAdvice(now_h, round(mean), curve, "too_late")

    step_h = C.SCAN_STEP_MIN / 60.0
    candidates = []
    t = now_h
    while t <= limit_h + 1e-9:
        curve, mean = _evaluate(t)
        candidates.append((t, mean, curve))
        t += step_h

    # 過小睡眠を勧めないため、平常時睡眠時間を確保できる時刻を上限とする
    comfortable_h = baseline.wake_h - baseline.sleep_h
    passing = [c for c in candidates if c[1] <= C.TARGET_DROP_PCT]
    if passing:
        eligible = [c for c in passing if c[0] <= comfortable_h] or passing
        best = max(eligible, key=lambda c: c[0])
        return BedtimeAdvice(round(best[0], 3), round(best[1]), best[2], "ok")

    # 閾値に届かない → 低下率が最小の時刻を返す
    best = min(candidates, key=lambda c: (c[1], c[0]))
    return BedtimeAdvice(round(best[0], 3), round(best[1]), best[2], "unreachable")
```

- [ ] **Step 5: テストを実行する**

Run: `python -m pytest tests/unit/test_recommend_bedtime.py -v`
Expected: PASS（7 passed）

- [ ] **Step 6: モデル層のテストをまとめて実行**

Run: `python -m pytest tests/unit -v`
Expected: PASS（全件）

- [ ] **Step 7: コミット**

```bash
git add src/sleeptech/model/two_process.py src/sleeptech/memory/schemas.py tests/unit/test_recommend_bedtime.py
git commit -m "feat(model): 推奨就寝時刻の線形スキャンと3つの境界ケースを実装"
```

---

### Task 6: リカバリー行動の算出

**Files:**
- Create: `src/sleeptech/model/recovery.py`
- Modify: `src/sleeptech/memory/schemas.py`
- Test: `tests/unit/test_recovery.py`

**Interfaces:**
- Consumes: `schemas.Baseline`, `constants`
- Produces:
  - `schemas.RecoveryPlan(caffeine_times_h: list[float], caffeine_cutoff_h: float, light_morning_h: float, light_avoid_after_h: float)`
  - `recovery.plan(wake_h: float, next_bedtime_h: float) -> RecoveryPlan`
  - `recovery.caffeine_cutoff(next_bedtime_h: float) -> float`

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/test_recovery.py`:

```python
"""カフェインと光曝露の時刻算出（仕様 第6節）。"""

import math

import pytest

from sleeptech.model import constants as C
from sleeptech.model import recovery

WAKE_H = 31.0        # 翌日 7:00
NEXT_BED_H = 47.5    # 翌日 23:30


def test_caffeine_cutoff_leaves_little_in_the_body_at_bedtime():
    """最終摂取可能時刻に飲んだ場合、就寝時の残存率が上限以下になる。"""
    cutoff = recovery.caffeine_cutoff(NEXT_BED_H)
    elapsed = NEXT_BED_H - cutoff
    remaining = 0.5 ** (elapsed / C.CAFFEINE_HALF_LIFE_H)
    assert remaining <= C.CAFFEINE_BEDTIME_RATIO + 1e-9


def test_caffeine_cutoff_is_earlier_for_earlier_bedtime():
    """就寝が早いほど最終摂取可能時刻も早い。"""
    early = recovery.caffeine_cutoff(46.0)
    late = recovery.caffeine_cutoff(48.0)
    assert early < late


def test_two_doses_when_window_is_long():
    """起床〜最終摂取可能時刻が閾値を超える場合は2回に分ける。"""
    got = recovery.plan(wake_h=WAKE_H, next_bedtime_h=NEXT_BED_H)
    assert len(got.caffeine_times_h) == 2
    assert got.caffeine_times_h[0] == WAKE_H + C.CAFFEINE_FIRST_OFFSET_H
    assert got.caffeine_times_h[1] == WAKE_H + C.PHASE_DIP_OFFSET_H - C.CAFFEINE_PRE_DIP_H


def test_single_dose_when_window_is_short():
    """就寝が早く窓が短い場合は1回のみ。"""
    got = recovery.plan(wake_h=WAKE_H, next_bedtime_h=WAKE_H + 9.0)
    assert len(got.caffeine_times_h) == 1
    assert got.caffeine_times_h[0] == WAKE_H + C.CAFFEINE_FIRST_OFFSET_H


def test_doses_never_exceed_the_cutoff():
    """推奨する摂取時刻は必ず最終摂取可能時刻以前。"""
    for bed in (WAKE_H + 8.0, WAKE_H + 12.0, WAKE_H + 16.0):
        got = recovery.plan(wake_h=WAKE_H, next_bedtime_h=bed)
        assert all(t <= got.caffeine_cutoff_h for t in got.caffeine_times_h)


def test_no_doses_when_cutoff_is_before_wake():
    """最終摂取可能時刻が起床より前なら、カフェインは推奨しない。"""
    got = recovery.plan(wake_h=WAKE_H, next_bedtime_h=WAKE_H + 2.0)
    assert got.caffeine_times_h == []


def test_morning_light_is_shortly_after_wake():
    """朝の光曝露は起床直後。"""
    got = recovery.plan(wake_h=WAKE_H, next_bedtime_h=NEXT_BED_H)
    assert got.light_morning_h == pytest.approx(
        WAKE_H + C.LIGHT_MORNING_WITHIN_MIN / 60.0
    )


def test_light_avoidance_starts_before_bedtime():
    """避光の開始は就寝の LIGHT_AVOID_BEFORE_BED_H 時間前。"""
    got = recovery.plan(wake_h=WAKE_H, next_bedtime_h=NEXT_BED_H)
    assert got.light_avoid_after_h == NEXT_BED_H - C.LIGHT_AVOID_BEFORE_BED_H


def test_plan_is_deterministic():
    """同じ入力からは必ず同じ結果が出る。"""
    assert recovery.plan(WAKE_H, NEXT_BED_H) == recovery.plan(WAKE_H, NEXT_BED_H)


def test_cutoff_formula_is_exact():
    """最終摂取可能時刻は半減期の式どおり。"""
    got = recovery.caffeine_cutoff(NEXT_BED_H)
    hours = -C.CAFFEINE_HALF_LIFE_H * math.log2(C.CAFFEINE_BEDTIME_RATIO)
    assert got == round(NEXT_BED_H - hours, 3)
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `python -m pytest tests/unit/test_recovery.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sleeptech.model.recovery'`

- [ ] **Step 3: schemas.py に RecoveryPlan を追加**

`src/sleeptech/memory/schemas.py` の末尾に追記:

```python
@dataclass(frozen=True)
class RecoveryPlan:
    """朝フローが提示するリカバリー行動の時刻。

    caffeine_times_h が空リストのときは「カフェインは勧めない」を意味する。
    """

    caffeine_times_h: list
    caffeine_cutoff_h: float
    light_morning_h: float
    light_avoid_after_h: float
```

- [ ] **Step 4: recovery.py を書く**

`src/sleeptech/model/recovery.py`:

```python
"""カフェインと光曝露の推奨時刻を決定論的に算出する（仕様 第6節）。

純粋関数のみ。現在時刻や外部 I/O には触れない。
"""

from __future__ import annotations

import math

from sleeptech.memory.schemas import RecoveryPlan
from sleeptech.model import constants as C


def caffeine_cutoff(next_bedtime_h: float) -> float:
    """就寝時の残存率が CAFFEINE_BEDTIME_RATIO 以下になる最終摂取時刻。

    残存率 = 0.5 ** (経過時間 / 半減期) を解いた結果。
    """
    hours = -C.CAFFEINE_HALF_LIFE_H * math.log2(C.CAFFEINE_BEDTIME_RATIO)
    return round(next_bedtime_h - hours, 3)


def plan(wake_h: float, next_bedtime_h: float) -> RecoveryPlan:
    """起床時刻と今夜の就寝時刻からリカバリー行動の時刻を求める。"""
    cutoff = caffeine_cutoff(next_bedtime_h)
    first = wake_h + C.CAFFEINE_FIRST_OFFSET_H
    second = wake_h + C.PHASE_DIP_OFFSET_H - C.CAFFEINE_PRE_DIP_H

    times: list[float] = []
    if cutoff >= first:
        times.append(round(first, 3))
        window_h = cutoff - wake_h
        if window_h > C.CAFFEINE_SPLIT_THRESHOLD_H and cutoff >= second:
            times.append(round(second, 3))

    return RecoveryPlan(
        caffeine_times_h=times,
        caffeine_cutoff_h=cutoff,
        light_morning_h=round(wake_h + C.LIGHT_MORNING_WITHIN_MIN / 60.0, 3),
        light_avoid_after_h=round(next_bedtime_h - C.LIGHT_AVOID_BEFORE_BED_H, 3),
    )
```

- [ ] **Step 5: テストを実行する**

Run: `python -m pytest tests/unit/test_recovery.py -v`
Expected: PASS（10 passed）

- [ ] **Step 6: コミット**

```bash
git add src/sleeptech/model/recovery.py src/sleeptech/memory/schemas.py tests/unit/test_recovery.py
git commit -m "feat(model): カフェインと光曝露の推奨時刻を算出"
```

---

### Task 7: 記憶エンティティと時刻フォーマット

**Files:**
- Modify: `src/sleeptech/memory/schemas.py`
- Create: `src/sleeptech/timefmt.py`
- Test: `tests/unit/test_timefmt.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `schemas.DailyVital(date, total_sleep_min, deep_sleep_min, rem_sleep_min, awakenings, bedtime_h, wake_h, hrv, skin_temp_delta, respiration_rate, night_avg_hr, weight_kg, body_fat_pct)` — `to_dict()` / `from_dict()` を持つ
  - `schemas.Prediction(date, slot, bedtime_h, mean_drop_pct, status, curve, recovery)` — `to_dict()` / `from_dict()`
  - `schemas.Feedback(date, score, answered_at)`
  - `timefmt.fmt_hm(t_h: float) -> str` — `23.5 -> "23:30"`、`24.5 -> "翌 0:30"`
  - `timefmt.fmt_hm_plain(t_h: float) -> str` — `24.5 -> "0:30"`（翌日プレフィックスなし）
  - `timefmt.to_h(hour: int, minute: int, next_day: bool = False) -> float`

**設計メモ:** 日付をまたぐ就寝時刻を「24:30」と表記してはならない。仕様の日付規約に従い「翌 0:30」とする。

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/test_timefmt.py`:

```python
"""時刻表記（仕様 第4節「日付の規約」）。"""

import pytest

from sleeptech import timefmt


@pytest.mark.parametrize(
    "value,want",
    [
        (23.5, "23:30"),
        (22.0, "22:00"),
        (7.25, "7:15"),
        (24.5, "翌 0:30"),
        (25.0, "翌 1:00"),
        (31.0, "翌 7:00"),
        (47.5, "翌々 23:30"),
    ],
)
def test_fmt_hm(value, want):
    assert timefmt.fmt_hm(value) == want


@pytest.mark.parametrize("value,want", [(24.5, "0:30"), (31.0, "7:00"), (23.5, "23:30")])
def test_fmt_hm_plain_drops_day_prefix(value, want):
    assert timefmt.fmt_hm_plain(value) == want


def test_never_renders_hour_24_or_greater():
    """24時以降を 24:30 のように表記してはならない。"""
    for v in (24.0, 24.5, 25.75, 31.0, 47.5):
        assert not timefmt.fmt_hm(v).lstrip("翌々 ").startswith("24")


def test_to_h_round_trip():
    assert timefmt.to_h(23, 30) == 23.5
    assert timefmt.to_h(0, 30, next_day=True) == 24.5
    assert timefmt.fmt_hm(timefmt.to_h(0, 30, next_day=True)) == "翌 0:30"


def test_minutes_are_rounded_to_the_nearest_minute():
    assert timefmt.fmt_hm(23.0 + 29.6 / 60.0) == "23:30"
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `python -m pytest tests/unit/test_timefmt.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sleeptech.timefmt'`

- [ ] **Step 3: timefmt.py を書く**

`src/sleeptech/timefmt.py`:

```python
"""起点日 0:00 からの経過時間（float, 単位=時）と表示文字列の相互変換。

日付をまたぐ時刻を「24:30」と書いてはならない（仕様 第4節）。
翌日は「翌 0:30」、翌々日は「翌々 23:30」と表記する。
"""

from __future__ import annotations

_DAY_PREFIX = ("", "翌 ", "翌々 ")


def to_h(hour: int, minute: int = 0, next_day: bool = False) -> float:
    """時分から経過時間へ。next_day=True で 24 時間を加算する。"""
    return hour + minute / 60.0 + (24.0 if next_day else 0.0)


def _split(t_h: float) -> tuple[int, int, int]:
    """経過時間を (日数, 時, 分) に分解する。分は四捨五入。"""
    total_min = int(round(t_h * 60.0))
    days, rem = divmod(total_min, 24 * 60)
    hour, minute = divmod(rem, 60)
    return days, hour, minute


def fmt_hm(t_h: float) -> str:
    """表示用の時刻文字列。日付をまたぐ場合は「翌」を前置する。"""
    days, hour, minute = _split(t_h)
    prefix = _DAY_PREFIX[days] if days < len(_DAY_PREFIX) else f"{days}日後 "
    return f"{prefix}{hour}:{minute:02d}"


def fmt_hm_plain(t_h: float) -> str:
    """日付プレフィックスを付けない時刻文字列。"""
    _, hour, minute = _split(t_h)
    return f"{hour}:{minute:02d}"
```

- [ ] **Step 4: テストを実行する**

Run: `python -m pytest tests/unit/test_timefmt.py -v`
Expected: PASS（16 passed）

- [ ] **Step 5: schemas.py に永続化エンティティを追加**

`src/sleeptech/memory/schemas.py` の末尾に追記:

まず冒頭の import 行を次に差し替える（`asdict` と `field` を追加する）。

```python
from dataclasses import asdict, dataclass, field
```

続けて末尾に追記する。

```python
@dataclass
class DailyVital:
    """ある夜の睡眠とバイタル。date は起点日（＝夜メッセージの送信日）。"""

    date: str
    total_sleep_min: int | None = None
    deep_sleep_min: int | None = None
    rem_sleep_min: int | None = None
    awakenings: int | None = None
    bedtime_h: float | None = None
    wake_h: float | None = None
    hrv: float | None = None
    skin_temp_delta: float | None = None
    respiration_rate: float | None = None
    night_avg_hr: float | None = None
    weight_kg: float | None = None
    body_fat_pct: float | None = None
    ingested_at: str | None = None

    @property
    def has_sleep(self) -> bool:
        """予測に必要な最低限（睡眠時間・起床時刻）が揃っているか。"""
        return self.total_sleep_min is not None and self.wake_h is not None

    def to_sleep_input(self) -> SleepInput | None:
        """予測モデルへの入力に変換する。データ不足なら None。"""
        if not self.has_sleep:
            return None
        bedtime_h = (
            self.bedtime_h
            if self.bedtime_h is not None
            else self.wake_h - self.total_sleep_min / 60.0
        )
        return SleepInput(
            bedtime_h=bedtime_h,
            wake_h=self.wake_h,
            rem_min=float(self.rem_sleep_min) if self.rem_sleep_min is not None else None,
            deep_min=float(self.deep_sleep_min) if self.deep_sleep_min is not None else None,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> DailyVital:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Prediction:
    """夜または朝に算出した予測。slot は "NIGHT" / "MORNING"。"""

    date: str
    slot: str
    bedtime_h: float | None = None
    mean_drop_pct: int = 0
    status: str = "ok"
    curve: list = field(default_factory=list)      # HourlyDrop の dict 表現
    recovery: dict = field(default_factory=dict)   # RecoveryPlan の dict 表現

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Prediction:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Feedback:
    """夜のクイックリプライで得た5段階評価。"""

    date: str
    score: int
    answered_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Feedback:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})
```

- [ ] **Step 6: エンティティのテストを書いて実行**

`tests/unit/test_schemas.py`:

```python
"""永続化エンティティの往復変換。"""

from sleeptech.memory.schemas import DailyVital, Feedback, Prediction


def test_daily_vital_round_trip():
    v = DailyVital(date="2026-08-26", total_sleep_min=330, wake_h=31.0, rem_sleep_min=70)
    assert DailyVital.from_dict(v.to_dict()) == v


def test_daily_vital_ignores_unknown_keys():
    """将来フィールドが増減しても復元が壊れないこと。"""
    got = DailyVital.from_dict({"date": "2026-08-26", "legacy_field": 1})
    assert got.date == "2026-08-26"


def test_to_sleep_input_returns_none_without_sleep():
    assert DailyVital(date="2026-08-26").to_sleep_input() is None


def test_to_sleep_input_derives_bedtime_when_missing():
    """就寝時刻が欠損していれば睡眠時間から逆算する。"""
    got = DailyVital(date="2026-08-26", total_sleep_min=360, wake_h=31.0).to_sleep_input()
    assert got.bedtime_h == 25.0
    assert got.duration_h == 6.0


def test_prediction_round_trip():
    p = Prediction(date="2026-08-26", slot="NIGHT", bedtime_h=23.5, mean_drop_pct=8)
    assert Prediction.from_dict(p.to_dict()) == p


def test_feedback_round_trip():
    f = Feedback(date="2026-08-26", score=4, answered_at="2026-08-26T23:10:00+09:00")
    assert Feedback.from_dict(f.to_dict()) == f
```

Run: `python -m pytest tests/unit/test_schemas.py -v`
Expected: PASS（6 passed）

- [ ] **Step 7: コミット**

```bash
git add src/sleeptech/timefmt.py src/sleeptech/memory/schemas.py tests/unit/test_timefmt.py tests/unit/test_schemas.py
git commit -m "feat(memory): 永続化エンティティと日付をまたぐ時刻表記を実装"
```

---

### Task 8: DynamoDB バックエンドと記憶クライアント

**Files:**
- Create: `src/sleeptech/memory/dynamo_backend.py`
- Create: `src/sleeptech/memory/client.py`
- Create: `src/sleeptech/config.py`
- Test: `tests/unit/test_memory_client.py`

**Interfaces:**
- Consumes: `schemas.DailyVital`, `schemas.Prediction`, `schemas.Feedback`
- Produces:
  - `config.Settings(env, region, table, secret_google, secret_line, secret_openai, llm_model, night_hour_jst, morning_hour_jst, target_drop_pct, override_wake_h)` と `config.load_settings() -> Settings`
  - `memory.dynamo_backend.DynamoBackend(table_name, region, ttl_days=None)` — `put(pk, sk, value)` / `get(pk, sk)` / `query_range(pk, start_sk, end_sk)` / `query_desc(pk, limit)`
  - `memory.client.MemoryClient(backend)` — `save_vital` / `get_vital` / `get_vitals_desc(until_date, days)` / `save_prediction` / `get_prediction` / `save_feedback` / `get_feedback` / `is_notified` / `mark_notified` / `get_config` / `save_config`
  - `memory.client.InMemoryBackend()` — テスト用のインメモリ実装

**設計メモ:** 転用元 `dynamo_backend.py` は `PK="MEM#<namespace>", SK="<key>"` の汎用 KV で、`query(namespace)` が全件返すだけのため日付範囲クエリができない。本タスクでは pk にエンティティ種別、sk に日付を置く。GSI は不要。

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/test_memory_client.py`:

```python
"""記憶クライアントの読み書き（仕様 第9節）。"""

from sleeptech.memory.client import InMemoryBackend, MemoryClient
from sleeptech.memory.schemas import DailyVital, Feedback, Prediction


def _client() -> MemoryClient:
    return MemoryClient(InMemoryBackend())


def test_save_and_get_vital():
    c = _client()
    v = DailyVital(date="2026-08-26", total_sleep_min=330, wake_h=31.0)
    c.save_vital(v)
    assert c.get_vital("2026-08-26") == v


def test_get_vital_returns_none_when_absent():
    assert _client().get_vital("2026-08-26") is None


def test_get_vitals_desc_returns_newest_first():
    """新しい順に並べて返す（ベースライン算出がこの順序を前提にする）。"""
    c = _client()
    for d in ("2026-08-20", "2026-08-21", "2026-08-22"):
        c.save_vital(DailyVital(date=d, total_sleep_min=400, wake_h=31.0))
    got = c.get_vitals_desc("2026-08-22", days=14)
    assert [v.date for v in got] == ["2026-08-22", "2026-08-21", "2026-08-20"]


def test_get_vitals_desc_excludes_days_outside_the_window():
    """指定日数より古いレコードは含めない。"""
    c = _client()
    for d in ("2026-08-01", "2026-08-25", "2026-08-26"):
        c.save_vital(DailyVital(date=d, total_sleep_min=400, wake_h=31.0))
    got = c.get_vitals_desc("2026-08-26", days=7)
    assert [v.date for v in got] == ["2026-08-26", "2026-08-25"]


def test_save_and_get_prediction_by_slot():
    """同じ日付でも NIGHT と MORNING は別レコード。"""
    c = _client()
    c.save_prediction(Prediction(date="2026-08-26", slot="NIGHT", mean_drop_pct=8))
    c.save_prediction(Prediction(date="2026-08-26", slot="MORNING", mean_drop_pct=19))
    assert c.get_prediction("2026-08-26", "NIGHT").mean_drop_pct == 8
    assert c.get_prediction("2026-08-26", "MORNING").mean_drop_pct == 19


def test_idempotency_is_per_date_and_slot():
    c = _client()
    assert c.is_notified("2026-08-26", "NIGHT") is False
    c.mark_notified("2026-08-26", "NIGHT")
    assert c.is_notified("2026-08-26", "NIGHT") is True
    assert c.is_notified("2026-08-26", "MORNING") is False
    assert c.is_notified("2026-08-27", "NIGHT") is False


def test_save_and_get_feedback():
    c = _client()
    c.save_feedback(Feedback(date="2026-08-26", score=4))
    assert c.get_feedback("2026-08-26").score == 4


def test_feedback_is_overwritten_by_later_answer():
    """同じ日付に複数回答が来たら最後で上書きする。"""
    c = _client()
    c.save_feedback(Feedback(date="2026-08-26", score=2))
    c.save_feedback(Feedback(date="2026-08-26", score=5))
    assert c.get_feedback("2026-08-26").score == 5


def test_config_defaults_to_empty_dict():
    assert _client().get_config() == {}


def test_config_round_trip():
    c = _client()
    c.save_config({"override_wake_h": 30.0})
    assert c.get_config()["override_wake_h"] == 30.0
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `python -m pytest tests/unit/test_memory_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sleeptech.memory.client'`

- [ ] **Step 3: config.py を書く**

`src/sleeptech/config.py`:

```python
"""環境変数から設定を読む。

LLM_MODEL の既定値は暫定である（仕様 第14節）。実機で 400/404 が返る場合は
この環境変数を差し替えるだけでよく、コード変更は不要。
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    env: str
    region: str
    table: str
    secret_google: str
    secret_line: str
    secret_openai: str
    llm_model: str
    night_hour_jst: int
    morning_hour_jst: int
    target_drop_pct: float
    override_wake_h: float | None


def _env_float_or_none(name: str) -> float | None:
    raw = os.getenv(name)
    return float(raw) if raw and raw.strip() else None


def load_settings() -> Settings:
    return Settings(
        env=os.getenv("STC_ENV", "dev").lower(),
        region=os.getenv("AWS_REGION", "us-east-1"),
        table=os.getenv("STC_DDB_TABLE", "SleepTechTable"),
        secret_google=os.getenv("STC_SECRET_GOOGLE", "sleeptech/google-oauth"),
        secret_line=os.getenv("STC_SECRET_LINE", "sleeptech/line"),
        secret_openai=os.getenv("STC_SECRET_OPENAI", "sleeptech/openai"),
        # 暫定値。正確なモデルIDが判明したら環境変数で上書きする（仕様 第14節）
        llm_model=os.getenv("LLM_MODEL", "gpt-5.6-luna"),
        night_hour_jst=int(os.getenv("STC_NIGHT_HOUR_JST", "22")),
        morning_hour_jst=int(os.getenv("STC_MORNING_HOUR_JST", "8")),
        target_drop_pct=float(os.getenv("STC_TARGET_DROP_PCT", "10")),
        override_wake_h=_env_float_or_none("STC_OVERRIDE_WAKE_H"),
    )
```

- [ ] **Step 4: dynamo_backend.py を書く**

`src/sleeptech/memory/dynamo_backend.py`:

```python
"""DynamoDB バックエンド（仕様 第9節）。

物理キー: pk = エンティティ種別, sk = 日付（または "META"）。
値は json 属性に JSON 文字列として格納し、DynamoDB の Decimal 変換問題を避ける。
この「JSON 文字列でラップする」手法だけは転用元から踏襲している。

日付を sk に置くのは、ベースライン（直近14日）と睡眠負債（直近7日）が
sk 範囲クエリを必要とするため。転用元の汎用 KV 実装では実現できない。
"""

from __future__ import annotations

import json
import time
from typing import Any


class DynamoBackend:
    def __init__(self, table_name: str, region: str, ttl_days: int | None = None) -> None:
        import boto3

        self._table = boto3.resource("dynamodb", region_name=region).Table(table_name)
        self._ttl_days = ttl_days

    def put(self, pk: str, sk: str, value: dict) -> None:
        item: dict[str, Any] = {
            "pk": pk,
            "sk": sk,
            "json": json.dumps(value, ensure_ascii=False, default=str),
        }
        if self._ttl_days:
            item["ttl"] = int(time.time()) + self._ttl_days * 86400
        self._table.put_item(Item=item)

    def get(self, pk: str, sk: str) -> dict | None:
        item = self._table.get_item(Key={"pk": pk, "sk": sk}).get("Item")
        return json.loads(item["json"]) if item and "json" in item else None

    def query_range(self, pk: str, start_sk: str, end_sk: str) -> list[dict]:
        """sk が [start_sk, end_sk] の範囲にある項目を昇順で返す。"""
        from boto3.dynamodb.conditions import Key

        return self._query(
            KeyConditionExpression=Key("pk").eq(pk) & Key("sk").between(start_sk, end_sk)
        )

    def query_desc(self, pk: str, limit: int) -> list[dict]:
        """sk の降順で最大 limit 件返す。"""
        from boto3.dynamodb.conditions import Key

        resp = self._table.query(
            KeyConditionExpression=Key("pk").eq(pk),
            ScanIndexForward=False,
            Limit=limit,
        )
        return [json.loads(it["json"]) for it in resp.get("Items", []) if "json" in it]

    def _query(self, **kwargs) -> list[dict]:
        items: list[dict] = []
        while True:
            resp = self._table.query(**kwargs)
            items.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return [json.loads(it["json"]) for it in items if "json" in it]
```

- [ ] **Step 5: client.py を書く**

`src/sleeptech/memory/client.py`:

```python
"""エンティティ単位の読み書き（仕様 第9節）。

バックエンドは put / get / query_range を持つオブジェクトであればよい。
テストでは InMemoryBackend を注入する。
"""

from __future__ import annotations

from datetime import date as date_cls
from datetime import timedelta

from sleeptech.memory.schemas import DailyVital, Feedback, Prediction

PK_VITAL = "VITAL"
PK_FEEDBACK = "FEEDBACK"
PK_CONFIG = "CONFIG"
SK_META = "META"


def _pk_prediction(slot: str) -> str:
    return f"PREDICT#{slot}"


def _pk_idem(slot: str) -> str:
    return f"IDEM#{slot}"


class InMemoryBackend:
    """テスト用。DynamoBackend と同じインタフェースを持つ。"""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], dict] = {}

    def put(self, pk: str, sk: str, value: dict) -> None:
        self._items[(pk, sk)] = value

    def get(self, pk: str, sk: str) -> dict | None:
        return self._items.get((pk, sk))

    def query_range(self, pk: str, start_sk: str, end_sk: str) -> list[dict]:
        picked = [
            (sk, v) for (p, sk), v in self._items.items() if p == pk and start_sk <= sk <= end_sk
        ]
        return [v for _, v in sorted(picked)]

    def query_desc(self, pk: str, limit: int) -> list[dict]:
        picked = [(sk, v) for (p, sk), v in self._items.items() if p == pk]
        return [v for _, v in sorted(picked, reverse=True)][:limit]


class MemoryClient:
    def __init__(self, backend) -> None:
        self._b = backend

    # --- バイタル ---
    def save_vital(self, vital: DailyVital) -> None:
        self._b.put(PK_VITAL, vital.date, vital.to_dict())

    def get_vital(self, date: str) -> DailyVital | None:
        raw = self._b.get(PK_VITAL, date)
        return DailyVital.from_dict(raw) if raw else None

    def get_vitals_desc(self, until_date: str, days: int) -> list[DailyVital]:
        """until_date から遡って days 日分を新しい順で返す。"""
        start = (date_cls.fromisoformat(until_date) - timedelta(days=days - 1)).isoformat()
        raws = self._b.query_range(PK_VITAL, start, until_date)
        return [DailyVital.from_dict(r) for r in reversed(raws)]

    # --- 予測 ---
    def save_prediction(self, pred: Prediction) -> None:
        self._b.put(_pk_prediction(pred.slot), pred.date, pred.to_dict())

    def get_prediction(self, date: str, slot: str) -> Prediction | None:
        raw = self._b.get(_pk_prediction(slot), date)
        return Prediction.from_dict(raw) if raw else None

    # --- フィードバック ---
    def save_feedback(self, fb: Feedback) -> None:
        self._b.put(PK_FEEDBACK, fb.date, fb.to_dict())

    def get_feedback(self, date: str) -> Feedback | None:
        raw = self._b.get(PK_FEEDBACK, date)
        return Feedback.from_dict(raw) if raw else None

    # --- 冪等 ---
    def is_notified(self, date: str, slot: str) -> bool:
        return self._b.get(_pk_idem(slot), date) is not None

    def mark_notified(self, date: str, slot: str) -> None:
        self._b.put(_pk_idem(slot), date, {"date": date, "slot": slot})

    # --- 設定 ---
    def get_config(self) -> dict:
        return self._b.get(PK_CONFIG, SK_META) or {}

    def save_config(self, cfg: dict) -> None:
        self._b.put(PK_CONFIG, SK_META, cfg)
```

- [ ] **Step 6: テストを実行する**

Run: `python -m pytest tests/unit/test_memory_client.py -v`
Expected: PASS（10 passed）

- [ ] **Step 7: コミット**

```bash
git add src/sleeptech/config.py src/sleeptech/memory/dynamo_backend.py src/sleeptech/memory/client.py tests/unit/test_memory_client.py
git commit -m "feat(memory): 日付範囲クエリ対応の DynamoDB バックエンドと記憶クライアント"
```

---

### Task 9: ガードレールとテンプレート文

**Files:**
- Create: `src/sleeptech/guardrail/validator.py`
- Create: `src/sleeptech/guardrail/fallback.py`
- Test: `tests/unit/test_guardrail.py`

**Interfaces:**
- Consumes: `timefmt`
- Produces:
  - `validator.Facts(times: list[float], percents: list[int])` — 本文に現れるべき確定値
  - `validator.ValidationResult(passed: bool, reason: str)`
  - `validator.validate(text: str, facts: Facts) -> ValidationResult`
  - `validator.normalize(text: str) -> str`
  - `fallback.night_text(advice, baseline, debt_h: float) -> str`
  - `fallback.morning_text(curve, recovery_plan, vital) -> str`

**設計メモ:** 数値照合は単純な文字列一致では表記ゆれ（`23:30` / `23時30分` / `午後11時半`）で誤検知が多発し、常にテンプレートへ落ちて LLM の価値が消える。照合前に正規化する（仕様 第8節）。

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/test_guardrail.py`:

```python
"""ガードレール検証（仕様 第8節）。最優先のテスト群。"""

import pytest

from sleeptech.guardrail import validator as v

FACTS = v.Facts(times=[23.5], percents=[8])
OK_TEXT = "今夜は23:30までに寝れば、明日はベスト比 -8% 以内の見込みです（目安）。"


def test_clean_text_passes():
    assert v.validate(OK_TEXT, FACTS).passed is True


@pytest.mark.parametrize(
    "text",
    [
        "この症状は睡眠時無呼吸症候群と診断できます。23:30までに寝ましょう。-8%",
        "治療のために23:30に就寝してください。-8%",
        "あなたは病気です。23:30に寝れば-8%です。",
    ],
)
def test_medical_assertions_are_rejected(text):
    """医学的断定を弾く。"""
    got = v.validate(text, FACTS)
    assert got.passed is False
    assert got.reason == "medical"


@pytest.mark.parametrize(
    "text",
    [
        "今寝ないと取り返しがつかないことになります。23:30 -8%",
        "このままでは寿命が縮みます。23:30 -8%",
        "手遅れになる前に23:30に寝てください。-8%",
    ],
)
def test_fear_mongering_is_rejected(text):
    """過度な不安煽りを弾く。"""
    got = v.validate(text, FACTS)
    assert got.passed is False
    assert got.reason == "fear"


@pytest.mark.parametrize(
    "text",
    [
        "23:30に寝れば必ず-8%に収まります。",
        "23:30に寝ないと確実に-8%落ちます。",
    ],
)
def test_definitive_claims_are_rejected(text):
    """断定表現（必ず・確実に）を弾く。"""
    got = v.validate(text, FACTS)
    assert got.passed is False
    assert got.reason == "definitive"


def test_personal_attacks_are_rejected():
    """人格否定を弾く。"""
    got = v.validate("だらしないあなたは23:30に寝るべきです。-8%", FACTS)
    assert got.passed is False
    assert got.reason == "attack"


def test_missing_time_is_rejected():
    """確定値の時刻が本文に現れなければ不通過。"""
    got = v.validate("早めに寝ましょう。明日は-8%の見込みです。", FACTS)
    assert got.passed is False
    assert got.reason == "numbers"


def test_missing_percent_is_rejected():
    got = v.validate("今夜は23:30までに寝ましょう。", FACTS)
    assert got.passed is False
    assert got.reason == "numbers"


def test_fabricated_number_is_rejected():
    """指示していない時刻や % が現れたら不通過（捏造）。"""
    got = v.validate("23:30に寝れば-8%。ちなみに25:00なら-40%です。", FACTS)
    assert got.passed is False
    assert got.reason == "numbers"


@pytest.mark.parametrize(
    "text",
    [
        "今夜は23時30分までに寝れば-8%以内の見込みです（目安）。",
        "今夜は２３：３０までに寝れば-8%以内の見込みです（目安）。",
        "今夜は午後11時半までに寝れば-8%以内の見込みです（目安）。",
    ],
)
def test_time_notation_variants_are_accepted(text):
    """表記ゆれを正規化して照合するので通過する。"""
    assert v.validate(text, FACTS).passed is True


def test_normalize_converts_fullwidth_digits():
    assert "23:30" in v.normalize("２３：３０")


def test_empty_text_is_rejected():
    got = v.validate("", FACTS)
    assert got.passed is False


def test_facts_with_no_percent_only_checks_times():
    """% を指示していない場合は時刻だけ検査する。"""
    facts = v.Facts(times=[7.5], percents=[])
    assert v.validate("7:30に屋外光を浴びましょう。", facts).passed is True


def test_allowed_percents_permit_additional_values():
    """必須は谷の1つだけ、他の時間帯の % は許可値として通す（朝のメッセージ）。"""
    facts = v.Facts(times=[], percents=[19], allowed_percents=[6, 19, 11])
    text = "10時台は-6%、14時台は-19%、17時台は-11%の見込みです（目安）。"
    assert v.validate(text, facts).passed is True


def test_allowed_percents_still_reject_unlisted_values():
    """許可リストに無い % は捏造として弾く。"""
    facts = v.Facts(times=[], percents=[19], allowed_percents=[6, 19, 11])
    text = "14時台は-19%、ちなみに深夜は-55%です。"
    assert v.validate(text, facts).passed is False
```

`tests/unit/test_fallback.py`:

```python
"""LLM 障害時のテンプレート文（仕様 第7節）。"""

from sleeptech.guardrail import fallback
from sleeptech.guardrail import validator as v
from sleeptech.memory.schemas import (
    Baseline,
    BedtimeAdvice,
    DailyVital,
    HourlyDrop,
    RecoveryPlan,
)

BASE = Baseline(wake_h=31.0, sleep_h=7.5, rem_min=90.0, deep_min=60.0,
                days_used=14, is_default=False)
CURVE = [HourlyDrop(hour=h, focus_pct=p, mood_pct=p + 2)
         for h, p in ((10, 6), (14, 19), (17, 11))]
ADVICE = BedtimeAdvice(bedtime_h=23.5, mean_drop_pct=8, curve=CURVE, status="ok")
PLAN = RecoveryPlan(caffeine_times_h=[32.0, 37.5], caffeine_cutoff_h=37.5,
                    light_morning_h=31.5, light_avoid_after_h=44.5)
VITAL = DailyVital(date="2026-08-26", total_sleep_min=342, deep_sleep_min=48,
                   rem_sleep_min=70, awakenings=3, wake_h=31.0)


def test_night_text_contains_the_recommended_time():
    got = fallback.night_text(ADVICE, BASE, debt_h=2.3)
    assert "23:30" in got


def test_night_text_passes_its_own_guardrail():
    """テンプレート文自身がガードレールを通ること。"""
    text = fallback.night_text(ADVICE, BASE, debt_h=2.3)
    facts = v.Facts(times=[23.5], percents=[8])
    assert v.validate(text, facts).passed is True


def test_night_text_mentions_it_is_an_estimate():
    assert "目安" in fallback.night_text(ADVICE, BASE, debt_h=0.0)


def test_night_text_warns_when_baseline_is_default():
    """履歴不足のときは精度が低い旨を含める。"""
    default_base = Baseline(wake_h=31.0, sleep_h=7.5, days_used=1, is_default=True)
    got = fallback.night_text(ADVICE, default_base, debt_h=0.0)
    assert "データが少ない" in got


def test_night_text_changes_for_unreachable_status():
    """閾値に届かない場合は文面が切り替わる。"""
    unreachable = BedtimeAdvice(bedtime_h=23.0, mean_drop_pct=28,
                                curve=CURVE, status="unreachable")
    got = fallback.night_text(unreachable, BASE, debt_h=10.0)
    assert "何時に寝ても" in got


def test_night_text_changes_for_too_late_status():
    too_late = BedtimeAdvice(bedtime_h=29.0, mean_drop_pct=25,
                             curve=CURVE, status="too_late")
    got = fallback.night_text(too_late, BASE, debt_h=5.0)
    assert "今すぐ" in got


def test_morning_text_lists_the_curve_and_recovery():
    got = fallback.morning_text(CURVE, PLAN, VITAL)
    assert "14" in got and "19" in got   # 谷の時刻と低下率
    assert "8:00" in got                  # カフェイン1回目 32.0 → fmt_hm_plain で "8:00"
    assert "☕" in got and "☀" in got


def test_morning_text_handles_no_caffeine():
    """カフェインを勧めない場合でも文が壊れない。"""
    plan = RecoveryPlan(caffeine_times_h=[], caffeine_cutoff_h=30.0,
                        light_morning_h=31.5, light_avoid_after_h=44.5)
    got = fallback.morning_text(CURVE, plan, VITAL)
    assert "カフェイン" in got
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `python -m pytest tests/unit/test_guardrail.py tests/unit/test_fallback.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sleeptech.guardrail.validator'`

- [ ] **Step 3: validator.py を書く**

`src/sleeptech/guardrail/validator.py`:

```python
"""生成文の検証（仕様 第8節）。

検査は2種類ある。
1. 語句検査 — 医学的断定・不安煽り・断定表現・人格否定を正規表現で弾く
2. 数値照合 — 指示した時刻と % が過不足なく本文に現れているかを検査する

数値照合の前に表記を正規化する。単純な文字列一致では「23時30分」「午後11時半」を
取り違え、常にテンプレートへ落ちて LLM 文章化の価値が失われるため。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_MEDICAL = re.compile(r"診断(でき|し)|治療|症候群|病気です|処方")
_FEAR = re.compile(r"取り返しがつかない|寿命が縮|手遅れ|命に関わ|危険な状態")
_DEFINITIVE = re.compile(r"必ず|確実に|絶対に")
_ATTACK = re.compile(r"だらしない|意志が弱い|情けない|怠惰|ダメな人")

_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")
_PCT_RE = re.compile(r"-?(\d{1,3})%")

_FULLWIDTH = str.maketrans("０１２３４５６７８９：％－", "0123456789:%-")


@dataclass(frozen=True)
class Facts:
    """本文に現れるべき確定値と、現れてもよい確定値。

    times は起点日 0:00 からの経過時間（時）。percents は低下率（正の整数）。

    allowed_* は「本文に出てきても捏造とみなさない」値。省略すると必須値と同じになる。
    朝のメッセージは時間帯別の低下率を複数並べるため、必須は谷の1つだけにして
    allowed_percents に全時間帯の値を渡す。これを分けないと、LLM が全時間帯を
    列挙しただけで捏造と判定され、常にテンプレートへ落ちてしまう。
    """

    times: list = field(default_factory=list)
    percents: list = field(default_factory=list)
    allowed_times: list | None = None
    allowed_percents: list | None = None


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    reason: str = ""


def normalize(text: str) -> str:
    """時刻表記を HH:MM に寄せる。

    順序が重要。「午後N時半」を先に処理しないと「N時半」が先に食われる。
    """
    s = text.translate(_FULLWIDTH)
    s = re.sub(r"午後(\d{1,2})時半", lambda m: f"{int(m.group(1)) + 12}:30", s)
    s = re.sub(
        r"午後(\d{1,2})時(\d{1,2})分", lambda m: f"{int(m.group(1)) + 12}:{int(m.group(2)):02d}", s
    )
    s = re.sub(r"午後(\d{1,2})時", lambda m: f"{int(m.group(1)) + 12}:00", s)
    s = re.sub(r"午前(\d{1,2})時半", lambda m: f"{int(m.group(1))}:30", s)
    s = re.sub(r"午前(\d{1,2})時", lambda m: f"{int(m.group(1))}:00", s)
    s = re.sub(r"(\d{1,2})時半", lambda m: f"{int(m.group(1))}:30", s)
    s = re.sub(r"(\d{1,2})時(\d{1,2})分", lambda m: f"{int(m.group(1))}:{int(m.group(2)):02d}", s)
    s = re.sub(r"(\d{1,2})時(?!間)", lambda m: f"{int(m.group(1))}:00", s)
    return s


def _time_key(t_h: float) -> str:
    """経過時間を照合キー（24時間内の HH:MM）に変換する。"""
    total = int(round(t_h * 60.0)) % (24 * 60)
    return f"{total // 60}:{total % 60:02d}"


def _extract(text: str) -> tuple[set[str], set[int]]:
    norm = normalize(text)
    times = {f"{int(h)}:{m}" for h, m in _TIME_RE.findall(norm)}
    percents = {int(p) for p in _PCT_RE.findall(norm)}
    return times, percents


def validate(text: str, facts: Facts) -> ValidationResult:
    """語句検査と数値照合を行う。最初に見つかった違反の理由を返す。"""
    if not text or not text.strip():
        return ValidationResult(False, "empty")

    for pattern, reason in (
        (_MEDICAL, "medical"),
        (_FEAR, "fear"),
        (_DEFINITIVE, "definitive"),
        (_ATTACK, "attack"),
    ):
        if pattern.search(text):
            return ValidationResult(False, reason)

    found_times, found_pcts = _extract(text)
    want_times = {_time_key(t) for t in facts.times}
    want_pcts = {abs(int(p)) for p in facts.percents}
    ok_times = (
        {_time_key(t) for t in facts.allowed_times}
        if facts.allowed_times is not None
        else want_times
    )
    ok_pcts = (
        {abs(int(p)) for p in facts.allowed_percents}
        if facts.allowed_percents is not None
        else want_pcts
    )

    if not want_times.issubset(found_times):
        return ValidationResult(False, "numbers")
    if not want_pcts.issubset(found_pcts):
        return ValidationResult(False, "numbers")
    # 許可していない数値の捏造を弾く
    if found_times - ok_times or found_pcts - ok_pcts:
        return ValidationResult(False, "numbers")

    return ValidationResult(True)
```

- [ ] **Step 4: fallback.py を書く**

`src/sleeptech/guardrail/fallback.py`:

```python
"""LLM を使わずに数値だけで組むテンプレート文（仕様 第7節）。

LLM が落ちても推奨就寝時刻とリカバリー時刻は必ず届く。この経路は必須である。
ここで生成する文は validator.validate を必ず通ること（テストで担保する）。
"""

from __future__ import annotations

from sleeptech.timefmt import fmt_hm_plain

_ESTIMATE_NOTE = "※あなたの平常時を100%とした推定の目安です"
_LOW_DATA_NOTE = "※まだデータが少ないため精度は低めです"


def night_text(advice, baseline, debt_h: float) -> str:
    """夜のテンプレート文。"""
    time_s = fmt_hm_plain(advice.bedtime_h)
    lines: list[str] = []

    if debt_h > 0.5:
        lines.append(f"睡眠負債が{debt_h:.1f}時間分たまっています。")

    if advice.status == "too_late":
        lines.append(f"今すぐ寝てください（{time_s}）。")
        lines.append(f"それでも明日はベスト比 -{advice.mean_drop_pct}% 程度の見込みです。")
    elif advice.status == "unreachable":
        lines.append(f"今夜は何時に寝てもベスト比 -{advice.mean_drop_pct}% 程度の見込みです。")
        lines.append(f"できるだけ早めに、遅くとも{time_s}には横になってください。")
    else:
        lines.append(f"▶ 今夜は {time_s} までに就寝")
        lines.append(f"守れれば明日は一日ベスト比 -{advice.mean_drop_pct}% 以内の見込みです。")

    lines.append(_ESTIMATE_NOTE)
    if baseline.is_default:
        lines.append(_LOW_DATA_NOTE)
    return "\n".join(lines)


def morning_text(curve, recovery_plan, vital) -> str:
    """朝のテンプレート文。"""
    lines: list[str] = []

    if vital.total_sleep_min is not None:
        h, m = divmod(int(vital.total_sleep_min), 60)
        extras = []
        if vital.deep_sleep_min is not None:
            extras.append(f"深い睡眠{vital.deep_sleep_min}分")
        if vital.awakenings is not None:
            extras.append(f"中途覚醒{vital.awakenings}回")
        head = f"昨夜は {h}時間{m}分"
        lines.append(f"{head}（{'・'.join(extras)}）" if extras else head)

    lines.append("")
    lines.append("今日の見込み")
    worst = max(curve, key=lambda d: d.focus_pct)
    for d in curve:
        mark = "  ← 谷" if d is worst and d.focus_pct > 0 else ""
        lines.append(f"  {d.hour}時台  -{d.focus_pct}%{mark}")

    lines.append("")
    lines.append("リカバリー")
    if recovery_plan.caffeine_times_h:
        times = "と".join(fmt_hm_plain(t) for t in recovery_plan.caffeine_times_h)
        cutoff = fmt_hm_plain(recovery_plan.caffeine_cutoff_h)
        lines.append(f"☕ カフェインは{times}に。{cutoff}以降は今夜の入眠を邪魔するので避けて。")
    else:
        lines.append("☕ カフェインは今日は控えめに。今夜の入眠に響きます。")
    light = fmt_hm_plain(recovery_plan.light_morning_h)
    avoid = fmt_hm_plain(recovery_plan.light_avoid_after_h)
    lines.append(f"☀ {light}までに屋外光を10分。{avoid}以降は画面を暗くすると巻き返せます。")

    lines.append("")
    lines.append(_ESTIMATE_NOTE)
    return "\n".join(lines)
```

- [ ] **Step 5: テストを実行する**

Run: `python -m pytest tests/unit/test_guardrail.py tests/unit/test_fallback.py -v`
Expected: PASS（全件）

- [ ] **Step 6: コミット**

```bash
git add src/sleeptech/guardrail tests/unit/test_guardrail.py tests/unit/test_fallback.py
git commit -m "feat(guardrail): 語句検査・数値照合の検証とテンプレート文を実装"
```

---

### Task 10: OpenAI クライアントとプロンプト

**Files:**
- Create: `src/sleeptech/llm/client.py`
- Create: `src/sleeptech/llm/prompts.py`
- Test: `tests/unit/test_llm_client.py`

**Interfaces:**
- Consumes: `config.Settings`
- Produces:
  - `client.LlmError(Exception)`
  - `client.OpenAiClient(api_key, model, http_post=None, sleep=None)` — `complete(system: str, user: str) -> str`
  - `client.NullClient()` — 常に `LlmError` を投げる。フォールバック経路のテスト用
  - `prompts.NIGHT_SYSTEM`, `prompts.MORNING_SYSTEM`
  - `prompts.night_user(advice, baseline, debt_h) -> str`
  - `prompts.morning_user(curve, recovery_plan, vital) -> str`

**設計メモ:** OpenAI SDK は使わない（Global Constraints）。`urllib.request` で叩く。リトライは2回、指数バックオフ。`http_post` と `sleep` を注入可能にし、テストで実 HTTP を発生させない。

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/test_llm_client.py`:

```python
"""OpenAI クライアントのリトライとエラー処理（仕様 第7節）。"""

import pytest

from sleeptech.llm import client as llm


def _ok_response(text: str = "生成された文章") -> dict:
    return {"choices": [{"message": {"content": text}}]}


def test_complete_returns_generated_text():
    calls = []

    def fake_post(url, headers, body):
        calls.append((url, headers, body))
        return _ok_response()

    c = llm.OpenAiClient("sk-test", "gpt-x", http_post=fake_post, sleep=lambda _: None)
    assert c.complete("システム", "ユーザー") == "生成された文章"
    assert len(calls) == 1


def test_request_shape_is_chat_completions():
    captured = {}

    def fake_post(url, headers, body):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = body
        return _ok_response()

    llm.OpenAiClient("sk-test", "gpt-x", http_post=fake_post, sleep=lambda _: None).complete(
        "SYS", "USR"
    )
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["body"]["model"] == "gpt-x"
    assert captured["body"]["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USR"},
    ]


def test_retries_twice_then_succeeds():
    attempts = {"n": 0}

    def flaky_post(url, headers, body):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise OSError("timeout")
        return _ok_response()

    c = llm.OpenAiClient("sk", "gpt-x", http_post=flaky_post, sleep=lambda _: None)
    assert c.complete("s", "u") == "生成された文章"
    assert attempts["n"] == 3


def test_raises_llm_error_after_exhausting_retries():
    def always_fail(url, headers, body):
        raise OSError("connection refused")

    c = llm.OpenAiClient("sk", "gpt-x", http_post=always_fail, sleep=lambda _: None)
    with pytest.raises(llm.LlmError):
        c.complete("s", "u")


def test_attempts_exactly_three_times():
    """初回 + リトライ2回 = 3回で打ち切る。"""
    attempts = {"n": 0}

    def always_fail(url, headers, body):
        attempts["n"] += 1
        raise OSError("boom")

    c = llm.OpenAiClient("sk", "gpt-x", http_post=always_fail, sleep=lambda _: None)
    with pytest.raises(llm.LlmError):
        c.complete("s", "u")
    assert attempts["n"] == 3


def test_malformed_response_raises_llm_error():
    """レスポンス形状が想定外でも例外型は LlmError に揃える。"""
    c = llm.OpenAiClient("sk", "gpt-x", http_post=lambda *a: {"unexpected": 1},
                         sleep=lambda _: None)
    with pytest.raises(llm.LlmError):
        c.complete("s", "u")


def test_empty_content_raises_llm_error():
    c = llm.OpenAiClient("sk", "gpt-x", http_post=lambda *a: _ok_response("  "),
                         sleep=lambda _: None)
    with pytest.raises(llm.LlmError):
        c.complete("s", "u")


def test_null_client_always_raises():
    with pytest.raises(llm.LlmError):
        llm.NullClient().complete("s", "u")
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `python -m pytest tests/unit/test_llm_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sleeptech.llm.client'`

- [ ] **Step 3: client.py を書く**

`src/sleeptech/llm/client.py`:

```python
"""OpenAI Chat Completions の呼び出し（仕様 第7節）。

OpenAI SDK は使わない。Lambda の実行時依存を boto3 + 標準ライブラリのみに
保つため、urllib.request で叩く。

モデルIDは config.Settings.llm_model（環境変数 LLM_MODEL）から渡される。
既定値は暫定であり、400/404 が返る場合は環境変数を差し替えればよい。
"""

from __future__ import annotations

from typing import Callable

API_URL = "https://api.openai.com/v1/chat/completions"
MAX_ATTEMPTS = 3       # 初回 + リトライ2回
TIMEOUT_SEC = 25
TEMPERATURE = 0.7
MAX_TOKENS = 700


class LlmError(Exception):
    """LLM 呼び出しが最終的に失敗したことを表す。呼び出し側はテンプレート文に切り替える。"""


class OpenAiClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        http_post: Callable[[str, dict, dict], dict] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._key = api_key
        self._model = model
        self._post = http_post or _default_http_post
        self._sleep = sleep or _default_sleep

    def complete(self, system: str, user: str) -> str:
        """system / user プロンプトを渡し、生成テキストを返す。

        失敗時は指数バックオフで最大 MAX_ATTEMPTS 回まで試み、それでも駄目なら
        LlmError を送出する。
        """
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
        }

        last: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                resp = self._post(API_URL, headers, body)
                text = resp["choices"][0]["message"]["content"]
                if not text or not text.strip():
                    raise ValueError("空の生成結果")
                return text.strip()
            except Exception as exc:  # noqa: BLE001 — 全失敗を LlmError に統一する
                last = exc
                if attempt < MAX_ATTEMPTS - 1:
                    self._sleep(2.0**attempt)
        raise LlmError(f"LLM 呼び出しに失敗しました: {last}") from last


class NullClient:
    """常に失敗するクライアント。フォールバック経路の検証に使う。"""

    def complete(self, system: str, user: str) -> str:
        raise LlmError("LLM は無効化されています")


def _default_http_post(url: str, headers: dict, body: dict) -> dict:
    import json
    import urllib.request

    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as r:  # noqa: S310 — 固定端点
        return json.loads(r.read().decode("utf-8"))


def _default_sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)
```

- [ ] **Step 4: prompts.py を書く**

`src/sleeptech/llm/prompts.py`:

```python
"""システムプロンプトと入力整形（仕様 第7節）。

LLM の役割は確定値の文章化のみ。数値・時刻を生成させてはならない。
プロンプトに埋め込んだ値以外の数値が本文に現れたら、ガードレールが弾く。
"""

from __future__ import annotations

from sleeptech.timefmt import fmt_hm_plain

_COMMON_RULES = (
    "厳守事項:\n"
    "- 与えられた数値と時刻だけを使うこと。新しい数値・時刻を作ってはならない。\n"
    "- 与えられた数値は必ず本文に含めること。\n"
    "- % は「あなたの平常時を100%とした推定の目安」である旨を必ず添えること。\n"
    "- 医学的な診断・治療の断定をしないこと。\n"
    "- 「必ず」「確実に」「絶対に」は使わないこと。\n"
    "- 不安を過度に煽らないこと。人格を否定しないこと。\n"
    "- 日本語で、200文字以内。箇条書きを1〜2行使ってよい。\n"
)

NIGHT_SYSTEM = (
    "あなたは睡眠コーチです。与えられた推奨就寝時刻と予測低下率を、"
    "短く前向きな日本語のメッセージに整形してください。\n" + _COMMON_RULES
)

MORNING_SYSTEM = (
    "あなたは睡眠コーチです。与えられた前夜の睡眠実績・時間帯別の予測低下率・"
    "リカバリー行動の時刻を、短く実行しやすい日本語のメッセージに整形してください。\n"
    + _COMMON_RULES
)


def night_user(advice, baseline, debt_h: float) -> str:
    """夜フローのユーザープロンプト。確定値のみを列挙する。"""
    status_note = {
        "ok": "この時刻までに寝れば目標を満たせる",
        "unreachable": "何時に寝ても目標には届かない。早めの就寝を促す",
        "too_late": "既に手遅れの時間帯。今すぐ寝るよう促す",
    }[advice.status]

    lines = [
        f"推奨就寝時刻: {fmt_hm_plain(advice.bedtime_h)}",
        f"明日の主要時間帯の平均低下率: {advice.mean_drop_pct}%",
        f"状況: {status_note}",
        f"睡眠負債: {debt_h:.1f}時間",
    ]
    if baseline.is_default:
        lines.append("注意: 履歴が少なく精度が低いことを一言添えること")
    return "\n".join(lines)


def morning_user(curve, recovery_plan, vital) -> str:
    """朝フローのユーザープロンプト。確定値のみを列挙する。"""
    lines: list[str] = []

    if vital.total_sleep_min is not None:
        h, m = divmod(int(vital.total_sleep_min), 60)
        lines.append(f"昨夜の睡眠: {h}時間{m}分")
    if vital.deep_sleep_min is not None:
        lines.append(f"深い睡眠: {vital.deep_sleep_min}分")
    if vital.awakenings is not None:
        lines.append(f"中途覚醒: {vital.awakenings}回")

    lines.append("今日の時間帯別の低下率:")
    for d in curve:
        lines.append(f"  {d.hour}時台: 集中力 -{d.focus_pct}% / 気分 -{d.mood_pct}%")

    if recovery_plan.caffeine_times_h:
        times = "、".join(fmt_hm_plain(t) for t in recovery_plan.caffeine_times_h)
        lines.append(f"カフェイン推奨時刻: {times}")
        lines.append(f"カフェイン最終摂取時刻: {fmt_hm_plain(recovery_plan.caffeine_cutoff_h)}")
    else:
        lines.append("カフェインは今日は勧めない")

    lines.append(f"朝の屋外光: {fmt_hm_plain(recovery_plan.light_morning_h)}までに10分")
    lines.append(f"避光の開始: {fmt_hm_plain(recovery_plan.light_avoid_after_h)}")
    return "\n".join(lines)
```

- [ ] **Step 5: テストを実行する**

Run: `python -m pytest tests/unit/test_llm_client.py -v`
Expected: PASS（8 passed）

- [ ] **Step 6: コミット**

```bash
git add src/sleeptech/llm tests/unit/test_llm_client.py
git commit -m "feat(llm): OpenAI クライアントとリトライ、数値生成を禁じるプロンプト"
```

---

### Task 11: LINE 通知とクイックリプライ

**Files:**
- Create: `src/sleeptech/notifier/line.py`
- Test: `tests/unit/test_line.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `line.FEEDBACK_PREFIX = "評価:"`
  - `line.FEEDBACK_LABELS: list[tuple[str, int]]` — 表示ラベルとスコアの対
  - `line.build_message(text: str, with_feedback: bool) -> dict` — LINE の messages 要素
  - `line.parse_feedback(text: str) -> int | None`
  - `line.LinePushClient(channel_access_token, user_id, http_post=None)` — `push(message: dict) -> None`
  - `line.FakePushClient()` — `sent: list[dict]` に貯めるテスト用

**設計メモ:** クイックリプライの回答は通常のテキストとして Webhook に届く。自然文と衝突しない固定形式 `評価:N` を使い、前方一致だけで判定する（LLM 分類器は使わない）。

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/test_line.py`:

```python
"""LINE メッセージ構築とフィードバック判定（仕様 第9節「フィードバックの取り込み」）。"""

import pytest

from sleeptech.notifier import line


def test_plain_message_has_no_quick_reply():
    msg = line.build_message("本文", with_feedback=False)
    assert msg["type"] == "text"
    assert msg["text"] == "本文"
    assert "quickReply" not in msg


def test_feedback_message_has_five_options():
    msg = line.build_message("本文", with_feedback=True)
    items = msg["quickReply"]["items"]
    assert len(items) == 5


def test_feedback_actions_use_the_fixed_prefix():
    """回答テキストは 評価:1〜評価:5 の固定形式。"""
    items = line.build_message("本文", with_feedback=True)["quickReply"]["items"]
    texts = [i["action"]["text"] for i in items]
    assert texts == [f"{line.FEEDBACK_PREFIX}{n}" for n in (1, 2, 3, 4, 5)]


def test_feedback_prompt_is_appended_to_the_body():
    msg = line.build_message("本文", with_feedback=True)
    assert "本文" in msg["text"]
    assert "調子" in msg["text"]


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5])
def test_parse_feedback_accepts_valid_scores(n):
    assert line.parse_feedback(f"{line.FEEDBACK_PREFIX}{n}") == n


def test_parse_feedback_tolerates_surrounding_whitespace():
    assert line.parse_feedback(f"  {line.FEEDBACK_PREFIX}3  ") == 3


@pytest.mark.parametrize(
    "text",
    ["3", "評価", "評価:0", "評価:6", "評価:abc", "おはよう", "", "今日の評価:3くらい"],
)
def test_parse_feedback_rejects_everything_else(text):
    """自然文や範囲外を誤って取り込まない。"""
    assert line.parse_feedback(text) is None


def test_push_client_posts_expected_payload():
    captured = {}

    def fake_post(url, headers, body):
        captured.update({"url": url, "headers": headers, "body": body})

    c = line.LinePushClient("token", "U123", http_post=fake_post)
    c.push(line.build_message("本文", with_feedback=False))

    assert captured["url"] == line.PUSH_URL
    assert captured["headers"]["Authorization"] == "Bearer token"
    assert captured["body"]["to"] == "U123"
    assert captured["body"]["messages"][0]["text"] == "本文"


def test_fake_push_client_records_messages():
    c = line.FakePushClient()
    c.push(line.build_message("A", with_feedback=False))
    c.push(line.build_message("B", with_feedback=True))
    assert len(c.sent) == 2
    assert c.sent[0]["text"] == "A"
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `python -m pytest tests/unit/test_line.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sleeptech.notifier.line'`

- [ ] **Step 3: line.py を書く**

`src/sleeptech/notifier/line.py`:

```python
"""LINE Messaging API への送信（仕様 第9節）。

標準ライブラリのみで実装する（Lambda に外部依存を持ち込まない）。
クイックリプライの回答テキストは自然文と衝突しない固定形式 評価:N とし、
Webhook 側は前方一致だけで判定する。LLM 分類器は使わない。
"""

from __future__ import annotations

import re
from typing import Callable

PUSH_URL = "https://api.line.me/v2/bot/message/push"
FEEDBACK_PREFIX = "評価:"
FEEDBACK_QUESTION = "今日の調子はどうだった？"
FEEDBACK_LABELS: list[tuple[str, int]] = [
    ("1 最悪", 1),
    ("2", 2),
    ("3 ふつう", 3),
    ("4", 4),
    ("5 絶好調", 5),
]

_FEEDBACK_RE = re.compile(rf"^{re.escape(FEEDBACK_PREFIX)}([1-5])$")


def build_message(text: str, with_feedback: bool) -> dict:
    """LINE の messages 要素を組み立てる。

    with_feedback=True のとき、本文末尾に質問を足し5段階のクイックリプライを付ける。
    LLM 障害時のテンプレート文でも必ず付けること（データ欠落を防ぐため）。
    """
    if not with_feedback:
        return {"type": "text", "text": text}

    return {
        "type": "text",
        "text": f"{text}\n\n{FEEDBACK_QUESTION}",
        "quickReply": {
            "items": [
                {
                    "type": "action",
                    "action": {
                        "type": "message",
                        "label": label,
                        "text": f"{FEEDBACK_PREFIX}{score}",
                    },
                }
                for label, score in FEEDBACK_LABELS
            ]
        },
    }


def parse_feedback(text: str) -> int | None:
    """クイックリプライの回答なら 1-5 を返す。それ以外は None。"""
    m = _FEEDBACK_RE.match((text or "").strip())
    return int(m.group(1)) if m else None


class LinePushClient:
    def __init__(
        self,
        channel_access_token: str,
        user_id: str,
        http_post: Callable[[str, dict, dict], None] | None = None,
    ) -> None:
        self._token = channel_access_token
        self._user_id = user_id
        self._post = http_post or _default_http_post

    def push(self, message: dict) -> None:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        self._post(PUSH_URL, headers, {"to": self._user_id, "messages": [message]})


class FakePushClient:
    """テスト用。送信内容を sent に貯める。"""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def push(self, message: dict) -> None:
        self.sent.append(message)


def _default_http_post(url: str, headers: dict, body: dict) -> None:
    import json
    import urllib.request

    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 — 固定の LINE 端点
        r.read()
```

- [ ] **Step 4: テストを実行する**

Run: `python -m pytest tests/unit/test_line.py -v`
Expected: PASS（16 passed）

- [ ] **Step 5: コミット**

```bash
git add src/sleeptech/notifier/line.py tests/unit/test_line.py
git commit -m "feat(notifier): LINE 送信とクイックリプライによる5段階フィードバック"
```

---

### Task 12: Google Health コレクタの移植

**Files:**
- Create: `src/sleeptech/collectors/__init__.py`（`ConsentRevokedError` の定義）
- Create: `src/sleeptech/collectors/google_auth_helper.py`
- Create: `src/sleeptech/collectors/google_health_api.py`
- Create: `src/sleeptech/collectors/vitals.py`
- Test: `tests/unit/test_collectors.py`

**Interfaces:**
- Consumes: `schemas.DailyVital`, `timefmt`
- Produces:
  - `collectors.ConsentRevokedError(Exception)`
  - `google_auth_helper.get_access_token(secret: dict, http_post=None) -> str`
  - `google_health_api.GoogleHealthApiSource(access_token_provider, http_post=None, http_get=None)` — `fetch_raw(wake_date: str) -> dict`
  - `vitals.to_daily_vital(raw: dict, wake_date: str) -> DailyVital` — **`date` は `wake_date - 1日`**

**設計メモ:** 転用元 `src/night_ramen/collectors/` をベースにする。`git show HEAD~N:src/night_ramen/collectors/google_health_api.py` で取得できる（N は Task 1 のコミットまでの距離）。変更点は3つ。

1. `_derive_sleep_metrics` に `rem_sleep_min` を追加する（STAGES の `type == "REM"` の合計）
2. `sleep.interval` の `startTime` / `endTime` から `bedtime_h` / `wake_h` を導出する（**これが無いと推奨就寝時刻を逆算できない**）
3. `_pick_sleep_for_date` は起床日で選ぶ。本プロジェクトの `date` は起点日なので、**保存時に1日引く**

- [ ] **Step 1: 退避済みファイルを確認する**

Task 1 Step 1 で `src/sleeptech/collectors/` へ移動済み。`night_ramen` の import が
残っていないことを確認する。

```bash
ls src/sleeptech/collectors/
grep -rn "night_ramen" src/ || echo "残存なし"
```
Expected: `google_health_api.py` / `google_auth_helper.py` / `__init__.py` が存在し、`残存なし`

- [ ] **Step 2: 失敗するテストを書く**

`tests/unit/test_collectors.py`:

```python
"""睡眠ステージの派生と日付変換（仕様 第4節「睡眠レコードから追加で取り出す項目」）。"""

from sleeptech.collectors import vitals
from sleeptech.collectors.google_health_api import _derive_sleep_metrics


def _stage(stage_type: str, start: str, end: str) -> dict:
    return {"type": stage_type, "startTime": start, "endTime": end}


SLEEP_BLOCK = {
    "interval": {
        "startTime": "2026-08-26T14:30:00Z",   # JST 23:30
        "startUtcOffset": "32400s",
        "endTime": "2026-08-26T22:00:00Z",     # JST 翌 7:00
        "endUtcOffset": "32400s",
    },
    "type": "STAGES",
    "stages": [
        _stage("LIGHT", "2026-08-26T14:30:00Z", "2026-08-26T16:00:00Z"),  # 90分
        _stage("DEEP", "2026-08-26T16:00:00Z", "2026-08-26T17:00:00Z"),   # 60分
        _stage("REM", "2026-08-26T17:00:00Z", "2026-08-26T18:10:00Z"),    # 70分
        _stage("AWAKE", "2026-08-26T18:10:00Z", "2026-08-26T18:20:00Z"),  # 中途覚醒
        _stage("LIGHT", "2026-08-26T18:20:00Z", "2026-08-26T22:00:00Z"),  # 220分
    ],
}


def test_derives_rem_sleep_minutes():
    """REM ステージの合計を出す（転用元には無かった項目）。"""
    got = _derive_sleep_metrics(SLEEP_BLOCK)
    assert got["rem_sleep_min"] == 70


def test_derives_total_and_deep_sleep():
    got = _derive_sleep_metrics(SLEEP_BLOCK)
    assert got["total_sleep_min"] == 440   # 90+60+70+220
    assert got["deep_sleep_min"] == 60


def test_counts_awakenings_excluding_the_first_stage():
    got = _derive_sleep_metrics(SLEEP_BLOCK)
    assert got["awakenings"] == 1


def test_derives_bedtime_and_wake_hours():
    """就寝・起床時刻を起点日 0:00 基準の経過時間で返す。"""
    got = _derive_sleep_metrics(SLEEP_BLOCK)
    assert got["bedtime_h"] == 23.5   # JST 23:30
    assert got["wake_h"] == 31.0      # 翌 7:00


def test_empty_stages_yield_all_none():
    got = _derive_sleep_metrics({"interval": {}, "stages": []})
    assert got["total_sleep_min"] is None
    assert got["rem_sleep_min"] is None
    assert got["bedtime_h"] is None


def test_to_daily_vital_shifts_date_back_by_one_day():
    """Google Health は起床日で睡眠を返す。本プロジェクトの date は起点日なので1日引く。"""
    raw = {"total_sleep_min": 440, "wake_h": 31.0, "bedtime_h": 23.5}
    got = vitals.to_daily_vital(raw, wake_date="2026-08-27")
    assert got.date == "2026-08-26"


def test_to_daily_vital_copies_known_fields_only():
    raw = {"total_sleep_min": 440, "hrv": 42.5, "unknown_field": 1}
    got = vitals.to_daily_vital(raw, wake_date="2026-08-27")
    assert got.total_sleep_min == 440
    assert got.hrv == 42.5


def test_to_daily_vital_sets_ingested_at():
    got = vitals.to_daily_vital({}, wake_date="2026-08-27")
    assert got.ingested_at is not None
```

- [ ] **Step 3: テストを実行して失敗を確認**

Run: `python -m pytest tests/unit/test_collectors.py -v`
Expected: FAIL — `KeyError: 'rem_sleep_min'`（`_derive_sleep_metrics` が未対応）

- [ ] **Step 4: `_derive_sleep_metrics` を書き換える**

`src/sleeptech/collectors/google_health_api.py` の `_derive_sleep_metrics` を次で置き換える。あわせて `_fetch_sleep` の `empty` 辞書のキーも同じ集合に揃えること。

```python
def _derive_sleep_metrics(sleep_block: dict) -> dict:
    """STAGES 配列から睡眠指標を派生する。

    転用元からの変更点:
      - REM ステージの合計を rem_sleep_min として追加した（気分カーブに使う）
      - interval から就寝時刻・起床時刻を導出した（推奨就寝時刻の逆算に必須）

    総睡眠時間 = AWAKE 以外（LIGHT/DEEP/REM 等）のステージ時間の合計。
    時刻は起点日 0:00 からの経過時間（時）。起床は翌日なので 24 以上になる。
    """
    empty = {
        "total_sleep_min": None, "deep_sleep_min": None, "rem_sleep_min": None,
        "awakenings": None, "bedtime_h": None, "wake_h": None,
    }
    stages = sleep_block.get("stages") or []
    if not stages:
        return empty

    total_min = 0.0
    deep_min = 0.0
    rem_min = 0.0
    awakenings = 0
    for i, s in enumerate(stages):
        stype = (s.get("type") or "").upper()
        start, end = s.get("startTime"), s.get("endTime")
        if not (start and end):
            continue
        dur = _minutes_between(start, end)
        if stype == "AWAKE":
            if i > 0:  # 先頭の AWAKE は入眠前なので中途覚醒に数えない
                awakenings += 1
            continue
        total_min += dur
        if stype == "DEEP":
            deep_min += dur
        elif stype == "REM":
            rem_min += dur

    interval = sleep_block.get("interval") or {}
    bedtime_h = _local_hours(interval.get("startTime"), interval.get("startUtcOffset"))
    wake_h = _local_hours(interval.get("endTime"), interval.get("endUtcOffset"))
    # 起床は就寝の翌日なので 24 を足して連続値にする
    if bedtime_h is not None and wake_h is not None and wake_h <= bedtime_h:
        wake_h += 24.0

    return {
        "total_sleep_min": int(round(total_min)),
        "deep_sleep_min": int(round(deep_min)),
        "rem_sleep_min": int(round(rem_min)),
        "awakenings": awakenings,
        "bedtime_h": bedtime_h,
        "wake_h": wake_h,
    }


def _local_hours(iso_utc: str | None, offset_str: str | None) -> float | None:
    """UTC ISO 時刻 + UTCオフセット秒 → その日の 0:00 からの経過時間（時）。"""
    from datetime import datetime, timedelta

    if not iso_utc:
        return None
    try:
        t = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
        off = int(str(offset_str).rstrip("s")) if offset_str else 0
        local = t + timedelta(seconds=off)
        return round(local.hour + local.minute / 60.0 + local.second / 3600.0, 4)
    except (ValueError, TypeError):
        return None
```

`sleep_score` は STAGES データ型に含まれないため、返り値から削除した。参照している箇所があれば削除すること。

- [ ] **Step 5: vitals.py を書く**

`src/sleeptech/collectors/vitals.py`:

```python
"""収集した生データを DailyVital に変換する。

Google Health の睡眠レコードは起床日で選択される（_pick_sleep_for_date）。
本プロジェクトの date は起点日（夜メッセージの送信日）なので、ここで1日引く。
取り違えると全レコードが1日ずれる。
"""

from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime, timedelta, timezone

from sleeptech.memory.schemas import DailyVital

JST = timezone(timedelta(hours=9))


def to_daily_vital(raw: dict, wake_date: str) -> DailyVital:
    """生 dict と起床日から DailyVital を組み立てる。date は起床日 − 1日。"""
    origin = (date_cls.fromisoformat(wake_date) - timedelta(days=1)).isoformat()
    known = set(DailyVital.__dataclass_fields__)
    fields = {k: v for k, v in raw.items() if k in known and k != "date"}
    return DailyVital(
        date=origin,
        ingested_at=datetime.now(JST).isoformat(timespec="seconds"),
        **fields,
    )
```

- [ ] **Step 6: テストを実行する**

Run: `python -m pytest tests/unit/test_collectors.py -v`
Expected: PASS（8 passed）

- [ ] **Step 7: 全テストを実行する**

Run: `python -m pytest -q`
Expected: 全件 PASS

- [ ] **Step 8: コミット**

```bash
git add src/sleeptech/collectors tests/unit/test_collectors.py
git commit -m "feat(collectors): Google Health 収集を移植し REM・就寝/起床時刻の抽出を追加"
```

---

### Task 13: 夜フロー

**Files:**
- Create: `src/sleeptech/workflow/night.py`
- Create: `src/sleeptech/observability.py`
- Test: `tests/integration/test_night_flow.py`

**Interfaces:**
- Consumes: `baseline.compute`, `two_process.recommend_bedtime`, `two_process.sleep_debt_h`, `llm`, `validator`, `fallback`, `line`, `MemoryClient`
- Produces:
  - `night.NightDeps(settings, memory, llm_client, push_client, max_regen=2)`
  - `night.run_night(date: str, now_h: float, deps: NightDeps) -> dict`

- [ ] **Step 1: observability.py が使えることを確認する**

Task 1 Step 1 で移植済み。`StructuredLogger` が import できることを確認する。

```bash
python -c "from sleeptech.observability import StructuredLogger; StructuredLogger().log('ok', x=1)"
```
Expected: 構造化ログが1行出力される

- [ ] **Step 2: 失敗するテストを書く**

`tests/integration/test_night_flow.py`:

```python
"""夜フローの統合（仕様 第4節「データフロー（夜）」）。"""

from dataclasses import replace

import pytest

from sleeptech.config import load_settings
from sleeptech.llm.client import LlmError, NullClient
from sleeptech.memory.client import InMemoryBackend, MemoryClient
from sleeptech.memory.schemas import DailyVital
from sleeptech.notifier.line import FakePushClient, parse_feedback
from sleeptech.workflow import night

NOW_H = 22.5  # 22:30
DATE = "2026-08-26"


class StubLlm:
    """プロンプトの確定値をそのまま含む文章を返す偽 LLM。"""

    def __init__(self, template: str = None) -> None:
        self.template = template
        self.calls = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if self.template is not None:
            return self.template
        time_s = next(l.split(": ")[1] for l in user.splitlines() if l.startswith("推奨就寝時刻"))
        pct = next(l.split(": ")[1] for l in user.splitlines() if l.startswith("明日の主要"))
        return f"今夜は{time_s}までに寝れば、明日はベスト比 -{pct} 以内の見込みです（目安）。"


def _memory(days: int = 14) -> MemoryClient:
    m = MemoryClient(InMemoryBackend())
    from datetime import date as d, timedelta

    base = d.fromisoformat(DATE)
    for i in range(1, days + 1):
        day = (base - timedelta(days=i)).isoformat()
        m.save_vital(DailyVital(date=day, total_sleep_min=450, bedtime_h=23.5, wake_h=31.0,
                                rem_sleep_min=90, deep_sleep_min=60))
    return m


def _deps(memory=None, llm=None, push=None):
    return night.NightDeps(
        settings=load_settings(),
        memory=memory or _memory(),
        llm_client=llm or StubLlm(),
        push_client=push or FakePushClient(),
    )


def test_sends_one_message_with_quick_reply():
    push = FakePushClient()
    night.run_night(DATE, NOW_H, _deps(push=push))
    assert len(push.sent) == 1
    assert "quickReply" in push.sent[0]


def test_quick_reply_answers_are_parseable():
    """送ったクイックリプライの回答が parse_feedback で読める。"""
    push = FakePushClient()
    night.run_night(DATE, NOW_H, _deps(push=push))
    texts = [i["action"]["text"] for i in push.sent[0]["quickReply"]["items"]]
    assert [parse_feedback(t) for t in texts] == [1, 2, 3, 4, 5]


def test_saves_prediction_and_marks_notified():
    memory = _memory()
    night.run_night(DATE, NOW_H, _deps(memory=memory))
    pred = memory.get_prediction(DATE, "NIGHT")
    assert pred is not None
    assert pred.bedtime_h is not None
    assert memory.is_notified(DATE, "NIGHT") is True


def test_second_run_does_not_send_again():
    """冪等: 同じ日付で2回実行しても送信は1回。"""
    memory = _memory()
    push = FakePushClient()
    night.run_night(DATE, NOW_H, _deps(memory=memory, push=push))
    night.run_night(DATE, NOW_H, _deps(memory=memory, push=push))
    assert len(push.sent) == 1


def test_falls_back_to_template_when_llm_fails():
    """LLM 障害時もテンプレート文で送信し、クイックリプライも付く。"""
    push = FakePushClient()
    result = night.run_night(DATE, NOW_H, _deps(llm=NullClient(), push=push))
    assert len(push.sent) == 1
    assert "quickReply" in push.sent[0]
    assert result["used_fallback"] is True


def test_falls_back_when_llm_output_fails_guardrail():
    """ガードレールを通らない生成が続いたらテンプレートに落ちる。"""
    push = FakePushClient()
    bad = StubLlm(template="必ず-99%落ちます。手遅れです。")
    result = night.run_night(DATE, NOW_H, _deps(llm=bad, push=push))
    assert result["used_fallback"] is True
    assert len(bad.calls) == 3  # 初回 + 再生成2回


def test_message_contains_the_recommended_time():
    push = FakePushClient()
    memory = _memory()
    night.run_night(DATE, NOW_H, _deps(memory=memory, push=push))
    pred = memory.get_prediction(DATE, "NIGHT")
    from sleeptech.timefmt import fmt_hm_plain

    assert fmt_hm_plain(pred.bedtime_h) in push.sent[0]["text"]


def test_works_with_no_history_using_defaults():
    """履歴ゼロでも予測を出し、精度が低い旨を含める。"""
    push = FakePushClient()
    empty = MemoryClient(InMemoryBackend())
    night.run_night(DATE, NOW_H, _deps(memory=empty, push=push))
    assert len(push.sent) == 1


def test_is_deterministic():
    """同じ入力からは同じ推奨就寝時刻が出る。"""
    a = night.run_night(DATE, NOW_H, _deps())
    b = night.run_night(DATE, NOW_H, _deps())
    assert a["bedtime_h"] == b["bedtime_h"]
```

- [ ] **Step 3: テストを実行して失敗を確認**

Run: `python -m pytest tests/integration/test_night_flow.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sleeptech.workflow.night'`

- [ ] **Step 4: night.py を書く**

`src/sleeptech/workflow/night.py`:

```python
"""夜フロー（仕様 第4節「データフロー（夜）」）。

直近履歴 → ベースライン → 推奨就寝時刻 → 文章化 → 検証 → 送信。
数値はすべてモデル層が決め、LLM は文章化だけを担う。
LLM が落ちてもテンプレート文で数値は届く。
"""

from __future__ import annotations

from dataclasses import dataclass

from sleeptech.guardrail import fallback
from sleeptech.guardrail import validator as gv
from sleeptech.llm import prompts
from sleeptech.memory.schemas import Prediction
from sleeptech.model import baseline as bl
from sleeptech.model import constants as C
from sleeptech.model import two_process as tp
from sleeptech.notifier.line import build_message
from sleeptech.observability import StructuredLogger

SLOT = "NIGHT"


@dataclass
class NightDeps:
    settings: object
    memory: object
    llm_client: object
    push_client: object
    max_regen: int = 2


def run_night(date: str, now_h: float, deps: NightDeps) -> dict:
    """夜フローを実行する。now_h は起点日 0:00 からの経過時間（22:30 なら 22.5）。"""
    log = StructuredLogger()
    log.log("night_start", date=date, now_h=now_h)

    if deps.memory.is_notified(date, SLOT):
        log.log("night_skipped", reason="already_notified")
        return {"delivered": False, "skipped": True}

    # 1-2. 履歴からベースラインと睡眠負債
    vitals = deps.memory.get_vitals_desc(date, C.BASELINE_LOOKBACK_DAYS)
    sleeps = [s for s in (v.to_sleep_input() for v in vitals) if s is not None]
    override = deps.memory.get_config().get("override_wake_h", deps.settings.override_wake_h)
    baseline = bl.compute(sleeps, override_wake_h=override)
    debt_h = tp.sleep_debt_h(sleeps, baseline)
    log.log("baseline", days=baseline.days_used, default=baseline.is_default, debt=debt_h)

    # 3. 推奨就寝時刻
    advice = tp.recommend_bedtime(now_h, baseline, debt_h)
    log.log("advice", bedtime_h=advice.bedtime_h, drop=advice.mean_drop_pct,
            status=advice.status)

    # 4-5. 文章化と検証
    facts = gv.Facts(times=[advice.bedtime_h], percents=[advice.mean_drop_pct])
    body, used_fallback = _compose(deps, advice, baseline, debt_h, facts, log)

    # 6. 送信
    deps.push_client.push(build_message(body, with_feedback=True))
    deps.memory.mark_notified(date, SLOT)
    log.log("night_sent", fallback=used_fallback)

    # 7. 保存
    deps.memory.save_prediction(
        Prediction(
            date=date,
            slot=SLOT,
            bedtime_h=advice.bedtime_h,
            mean_drop_pct=advice.mean_drop_pct,
            status=advice.status,
            curve=[d.__dict__ for d in advice.curve],
        )
    )
    return {
        "delivered": True,
        "skipped": False,
        "bedtime_h": advice.bedtime_h,
        "mean_drop_pct": advice.mean_drop_pct,
        "used_fallback": used_fallback,
    }


def _compose(deps, advice, baseline, debt_h, facts, log) -> tuple[str, bool]:
    """LLM で文章化し検証する。通らなければテンプレート文を返す。"""
    user = prompts.night_user(advice, baseline, debt_h)
    for attempt in range(deps.max_regen + 1):
        try:
            text = deps.llm_client.complete(prompts.NIGHT_SYSTEM, user)
        except Exception as exc:  # noqa: BLE001 — LLM 障害は必ずフォールバックへ
            log.log("llm_failed", error=str(exc))
            break
        result = gv.validate(text, facts)
        if result.passed:
            return text, False
        log.log("guardrail_rejected", attempt=attempt, reason=result.reason)
    return fallback.night_text(advice, baseline, debt_h), True
```

- [ ] **Step 5: テストを実行する**

Run: `python -m pytest tests/integration/test_night_flow.py -v`
Expected: PASS（9 passed）

- [ ] **Step 6: コミット**

```bash
git add src/sleeptech/workflow/night.py src/sleeptech/observability.py tests/integration/test_night_flow.py
git commit -m "feat(workflow): 夜フローを実装（推奨就寝時刻の送信とフォールバック）"
```

---

### Task 14: 朝フロー

**Files:**
- Create: `src/sleeptech/workflow/morning.py`
- Test: `tests/integration/test_morning_flow.py`

**Interfaces:**
- Consumes: `collectors`, `baseline.compute`, `two_process.predict_curve`, `recovery.plan`, `llm`, `validator`, `fallback`, `line`, `MemoryClient`
- Produces:
  - `morning.MorningDeps(settings, memory, health_source, llm_client, push_client, max_regen=2)`
  - `morning.run_morning(date: str, deps: MorningDeps) -> dict`

**設計メモ:** 冪等チェックを関数の冒頭に、フラグ設定を末尾に置く。`MorningFn`（8:00 定時）と `WebhookFn`（「おはよう」検出）の両方がこの関数を呼ぶので、ここに閉じ込めないと二重送信する。転用元は webhook 側で収集のみを行い、送信は無条件だった。

- [ ] **Step 1: 失敗するテストを書く**

`tests/integration/test_morning_flow.py`:

```python
"""朝フローの統合と冪等（仕様 第4節「データフロー（朝）」）。"""

from sleeptech.collectors import ConsentRevokedError
from sleeptech.config import load_settings
from sleeptech.llm.client import NullClient
from sleeptech.memory.client import InMemoryBackend, MemoryClient
from sleeptech.memory.schemas import DailyVital
from sleeptech.notifier.line import FakePushClient
from sleeptech.workflow import morning

DATE = "2026-08-26"       # 起点日
WAKE_DATE = "2026-08-27"  # 起床日


class FakeHealthSource:
    """fetch_raw(wake_date) を返す偽ソース。"""

    def __init__(self, raw: dict | None = None, error: Exception | None = None) -> None:
        self.raw = raw if raw is not None else {
            "total_sleep_min": 342, "deep_sleep_min": 48, "rem_sleep_min": 70,
            "awakenings": 3, "bedtime_h": 25.3, "wake_h": 31.0, "hrv": 38.0,
        }
        self.error = error
        self.calls = []

    def fetch_raw(self, wake_date: str) -> dict:
        self.calls.append(wake_date)
        if self.error:
            raise self.error
        return self.raw


class StubLlm:
    def __init__(self) -> None:
        self.calls = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return "不合格な文面"  # 検証に落ちる → テンプレートへ


def _memory() -> MemoryClient:
    m = MemoryClient(InMemoryBackend())
    from datetime import date as d, timedelta

    base = d.fromisoformat(DATE)
    for i in range(1, 15):
        day = (base - timedelta(days=i)).isoformat()
        m.save_vital(DailyVital(date=day, total_sleep_min=450, bedtime_h=23.5,
                                wake_h=31.0, rem_sleep_min=90, deep_sleep_min=60))
    return m


def _deps(memory=None, source=None, llm=None, push=None):
    return morning.MorningDeps(
        settings=load_settings(),
        memory=memory or _memory(),
        health_source=source or FakeHealthSource(),
        llm_client=llm or NullClient(),
        push_client=push or FakePushClient(),
    )


def test_collects_using_the_wake_date():
    """収集は起点日ではなく起床日（date + 1日）で問い合わせる。"""
    source = FakeHealthSource()
    morning.run_morning(DATE, _deps(source=source))
    assert source.calls == [WAKE_DATE]


def test_saves_vital_under_the_origin_date():
    """保存される date は起点日。1日ずれてはいけない。"""
    memory = _memory()
    morning.run_morning(DATE, _deps(memory=memory))
    assert memory.get_vital(DATE) is not None
    assert memory.get_vital(WAKE_DATE) is None


def test_sends_one_message_without_quick_reply():
    """朝はフィードバックを聞かない（夜に1問だけ）。"""
    push = FakePushClient()
    morning.run_morning(DATE, _deps(push=push))
    assert len(push.sent) == 1
    assert "quickReply" not in push.sent[0]


def test_second_run_does_not_send_again():
    """冪等: 定時実行と「おはよう」前倒しが重なっても1回だけ。"""
    memory = _memory()
    push = FakePushClient()
    morning.run_morning(DATE, _deps(memory=memory, push=push))
    morning.run_morning(DATE, _deps(memory=memory, push=push))
    assert len(push.sent) == 1


def test_second_run_reports_skipped():
    memory = _memory()
    morning.run_morning(DATE, _deps(memory=memory))
    got = morning.run_morning(DATE, _deps(memory=memory))
    assert got["skipped"] is True


def test_saves_prediction_with_curve():
    memory = _memory()
    morning.run_morning(DATE, _deps(memory=memory))
    pred = memory.get_prediction(DATE, "MORNING")
    assert pred is not None
    assert len(pred.curve) > 0
    assert pred.recovery["caffeine_cutoff_h"] is not None


def test_no_percentages_when_sleep_data_is_missing():
    """睡眠データが取れないときは予測 % を一切出さない。"""
    push = FakePushClient()
    source = FakeHealthSource(raw={})
    got = morning.run_morning(DATE, _deps(source=source, push=push))
    assert got["has_sleep"] is False
    assert "%" not in push.sent[0]["text"]


def test_consent_revoked_stops_and_notifies():
    """同意失効時は収集を止め、再認証を促す通知を送る。"""
    push = FakePushClient()
    source = FakeHealthSource(error=ConsentRevokedError("token expired"))
    got = morning.run_morning(DATE, _deps(source=source, push=push))
    assert got["consent_revoked"] is True
    assert "再連携" in push.sent[0]["text"] or "認証" in push.sent[0]["text"]


def test_falls_back_to_template_when_llm_fails():
    push = FakePushClient()
    got = morning.run_morning(DATE, _deps(llm=NullClient(), push=push))
    assert got["used_fallback"] is True
    assert "☕" in push.sent[0]["text"]


def test_afternoon_is_the_worst_hour_after_short_sleep():
    """5時間台の睡眠なら午後が谷になる。"""
    memory = _memory()
    morning.run_morning(DATE, _deps(memory=memory))
    pred = memory.get_prediction(DATE, "MORNING")
    worst = max(pred.curve, key=lambda d: d["focus_pct"])
    assert 13 <= worst["hour"] <= 17
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `python -m pytest tests/integration/test_morning_flow.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sleeptech.workflow.morning'`

- [ ] **Step 3: morning.py を書く**

`src/sleeptech/workflow/morning.py`:

```python
"""朝フロー（仕様 第4節「データフロー（朝）」）。

冪等チェックを冒頭に、フラグ設定を末尾に置く。この関数は MorningFn（8:00 定時）と
WebhookFn（「おはよう」検出）の両方から呼ばれるため、ここに閉じ込めないと二重送信する。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_cls
from datetime import timedelta

from sleeptech.collectors import ConsentRevokedError
from sleeptech.collectors import vitals as vt
from sleeptech.guardrail import fallback
from sleeptech.guardrail import validator as gv
from sleeptech.llm import prompts
from sleeptech.memory.schemas import Prediction
from sleeptech.model import baseline as bl
from sleeptech.model import constants as C
from sleeptech.model import recovery as rc
from sleeptech.model import two_process as tp
from sleeptech.notifier.line import build_message
from sleeptech.observability import StructuredLogger

SLOT = "MORNING"

_NO_DATA_TEXT = (
    "おはようございます。今朝は睡眠データを取得できませんでした。\n"
    "予測は出せませんが、起床後30分以内に屋外光を浴びておくと今日の立ち上がりが変わります。"
)
_CONSENT_TEXT = (
    "睡眠データにアクセスできなくなりました。Google Health の再連携（再認証）をお願いします。"
)


@dataclass
class MorningDeps:
    settings: object
    memory: object
    health_source: object
    llm_client: object
    push_client: object
    max_regen: int = 2


def run_morning(date: str, deps: MorningDeps) -> dict:
    """朝フローを実行する。date は起点日（前夜の夜メッセージと同じ日付）。"""
    log = StructuredLogger()
    log.log("morning_start", date=date)

    # 1. 冪等
    if deps.memory.is_notified(date, SLOT):
        log.log("morning_skipped", reason="already_notified")
        return {"delivered": False, "skipped": True}

    # 2-3. 収集と保存（Google Health は起床日で睡眠を返す）
    wake_date = (date_cls.fromisoformat(date) + timedelta(days=1)).isoformat()
    try:
        raw = deps.health_source.fetch_raw(wake_date)
    except ConsentRevokedError as exc:
        log.log("consent_revoked", error=str(exc))
        deps.push_client.push(build_message(_CONSENT_TEXT, with_feedback=False))
        deps.memory.mark_notified(date, SLOT)
        return {"delivered": True, "skipped": False, "consent_revoked": True,
                "has_sleep": False, "used_fallback": True}

    vital = vt.to_daily_vital(raw, wake_date)
    deps.memory.save_vital(vital)
    log.log("vital_saved", date=vital.date, has_sleep=vital.has_sleep)

    # 睡眠が取れていなければ予測を出さない
    if not vital.has_sleep:
        deps.push_client.push(build_message(_NO_DATA_TEXT, with_feedback=False))
        deps.memory.mark_notified(date, SLOT)
        return {"delivered": True, "skipped": False, "consent_revoked": False,
                "has_sleep": False, "used_fallback": True}

    # 4. 予測カーブ
    history = deps.memory.get_vitals_desc(date, C.BASELINE_LOOKBACK_DAYS)
    sleeps = [s for s in (v.to_sleep_input() for v in history) if s is not None]
    override = deps.memory.get_config().get("override_wake_h", deps.settings.override_wake_h)
    baseline = bl.compute(sleeps, override_wake_h=override)
    debt_h = tp.sleep_debt_h(sleeps, baseline)
    curve = tp.predict_curve(vital.to_sleep_input(), baseline, debt_h)

    # 5. リカバリー（今夜の就寝は「起床 + 平常時の覚醒時間」と見なす）
    next_bedtime_h = vital.wake_h + (24.0 - baseline.sleep_h)
    plan = rc.plan(vital.wake_h, next_bedtime_h)
    log.log("recovery", caffeine=len(plan.caffeine_times_h))

    # 6. 文章化・検証・送信
    # 必須は谷の低下率1つ。他の時間帯の値は許可リストに入れる（列挙しても捏造扱いしない）
    all_pcts = [d.focus_pct for d in curve] + [d.mood_pct for d in curve]
    worst = max(curve, key=lambda d: d.focus_pct)
    times = list(plan.caffeine_times_h) + [
        plan.caffeine_cutoff_h, plan.light_morning_h, plan.light_avoid_after_h
    ]
    facts = gv.Facts(
        times=times,
        percents=[worst.focus_pct] if worst.focus_pct > 0 else [],
        allowed_times=times,
        allowed_percents=all_pcts,
    )
    body, used_fallback = _compose(deps, curve, plan, vital, facts, log)
    deps.push_client.push(build_message(body, with_feedback=False))
    deps.memory.mark_notified(date, SLOT)

    deps.memory.save_prediction(
        Prediction(
            date=date,
            slot=SLOT,
            mean_drop_pct=round(tp.mean_drop_in_main_window(curve, vital.wake_h)),
            curve=[d.__dict__ for d in curve],
            recovery=plan.__dict__,
        )
    )
    log.log("morning_sent", fallback=used_fallback)
    return {"delivered": True, "skipped": False, "consent_revoked": False,
            "has_sleep": True, "used_fallback": used_fallback}


def _compose(deps, curve, plan, vital, facts, log) -> tuple[str, bool]:
    """LLM で文章化し検証する。通らなければテンプレート文を返す。"""
    user = prompts.morning_user(curve, plan, vital)
    for attempt in range(deps.max_regen + 1):
        try:
            text = deps.llm_client.complete(prompts.MORNING_SYSTEM, user)
        except Exception as exc:  # noqa: BLE001 — LLM 障害は必ずフォールバックへ
            log.log("llm_failed", error=str(exc))
            break
        result = gv.validate(text, facts)
        if result.passed:
            return text, False
        log.log("guardrail_rejected", attempt=attempt, reason=result.reason)
    return fallback.morning_text(curve, plan, vital), True
```

- [ ] **Step 4: テストを実行する**

Run: `python -m pytest tests/integration/test_morning_flow.py -v`
Expected: PASS（10 passed）

`test_afternoon_is_the_worst_hour_after_short_sleep` が失敗する場合、`FakeHealthSource` の既定睡眠（342分＝5時間42分）に対して谷が 13-17 時に来ていないことを意味する。`constants.py` の `PHASE_DIP_OFFSET_H` を疑う前に、`vital.wake_h`（31.0＝翌7:00）から谷が 8 時間後＝15 時になっているかを確認すること。

- [ ] **Step 5: 全テストを実行**

Run: `python -m pytest -q`
Expected: 全件 PASS

- [ ] **Step 6: コミット**

```bash
git add src/sleeptech/workflow/morning.py tests/integration/test_morning_flow.py
git commit -m "feat(workflow): 朝フローを実装（冪等を関数内に内包し二重送信を防止）"
```

---

### Task 15: Lambda ハンドラ

**Files:**
- Create: `src/handlers/deps.py`
- Create: `src/handlers/night_handler.py`
- Create: `src/handlers/morning_handler.py`
- Create: `src/handlers/webhook_handler.py`
- Test: `tests/integration/test_webhook.py`

**Interfaces:**
- Consumes: `night.run_night`, `morning.run_morning`, `line.parse_feedback`, `config.load_settings`
- Produces:
  - `deps.jst_now() -> datetime`, `deps.origin_date(now) -> str`, `deps.now_h(now) -> float`
  - `deps.build_night_deps(settings)`, `deps.build_morning_deps(settings)`
  - `night_handler.handler(event, context=None) -> dict`
  - `morning_handler.handler(event, context=None) -> dict`
  - `webhook_handler.handler(event, context=None) -> dict`
  - `webhook_handler.is_wakeup_trigger(text: str) -> bool`
  - `webhook_handler.resolve_feedback_date(memory, today: str) -> str | None`

**設計メモ:** 朝メッセージは起点日（前夜）の日付で送る。8:00 に実行されるときの起点日は「今日の日付 − 1日」である。ここを間違えると夜と朝で別の日付になり、予測が紐付かない。

- [ ] **Step 1: 失敗するテストを書く**

`tests/integration/test_webhook.py`:

```python
"""Webhook の起床トリガーとフィードバック取り込み（仕様 第9節）。"""

from datetime import datetime, timedelta, timezone

import pytest

from sleeptech.memory.client import InMemoryBackend, MemoryClient
from sleeptech.memory.schemas import Prediction
from src.handlers import deps as hd
from src.handlers import webhook_handler as wh

JST = timezone(timedelta(hours=9))


@pytest.mark.parametrize(
    "text", ["おはよう", "おはようございます", "起きた", "データ更新", "同期", "計測"]
)
def test_wakeup_triggers_are_detected(text):
    assert wh.is_wakeup_trigger(text) is True


@pytest.mark.parametrize("text", ["こんばんは", "評価:3", "ラーメン食べた", "", "おはようの意味は？"])
def test_non_triggers_are_ignored(text):
    assert wh.is_wakeup_trigger(text) is False


def test_origin_date_at_night_is_today():
    """22:30 の起点日は今日。"""
    now = datetime(2026, 8, 26, 22, 30, tzinfo=JST)
    assert hd.origin_date(now) == "2026-08-26"


def test_origin_date_in_the_morning_is_yesterday():
    """8:00 の起点日は前日（前夜のメッセージと同じ日付にするため）。"""
    now = datetime(2026, 8, 27, 8, 0, tzinfo=JST)
    assert hd.origin_date(now, morning=True) == "2026-08-26"


def test_now_h_converts_to_elapsed_hours():
    now = datetime(2026, 8, 26, 22, 30, tzinfo=JST)
    assert hd.now_h(now) == 22.5


def test_feedback_binds_to_the_latest_unanswered_night_prediction():
    """回答は直近の未回答 PREDICT#NIGHT に紐付ける。"""
    m = MemoryClient(InMemoryBackend())
    m.save_prediction(Prediction(date="2026-08-25", slot="NIGHT"))
    m.save_prediction(Prediction(date="2026-08-26", slot="NIGHT"))
    assert wh.resolve_feedback_date(m, today="2026-08-26") == "2026-08-26"


def test_feedback_falls_back_to_previous_day():
    """当日の夜メッセージがまだ無ければ前日に遡る。"""
    m = MemoryClient(InMemoryBackend())
    m.save_prediction(Prediction(date="2026-08-25", slot="NIGHT"))
    assert wh.resolve_feedback_date(m, today="2026-08-26") == "2026-08-25"


def test_feedback_does_not_go_back_more_than_two_days():
    """2日より前には遡らない。"""
    m = MemoryClient(InMemoryBackend())
    m.save_prediction(Prediction(date="2026-08-20", slot="NIGHT"))
    assert wh.resolve_feedback_date(m, today="2026-08-26") is None


def test_feedback_skips_already_answered_days():
    """既に回答済みの日には紐付けない。"""
    from sleeptech.memory.schemas import Feedback

    m = MemoryClient(InMemoryBackend())
    m.save_prediction(Prediction(date="2026-08-25", slot="NIGHT"))
    m.save_prediction(Prediction(date="2026-08-26", slot="NIGHT"))
    m.save_feedback(Feedback(date="2026-08-26", score=3))
    assert wh.resolve_feedback_date(m, today="2026-08-26") == "2026-08-25"


def test_feedback_returns_none_when_no_prediction_exists():
    m = MemoryClient(InMemoryBackend())
    assert wh.resolve_feedback_date(m, today="2026-08-26") is None
```

- [ ] **Step 2: テストを実行して失敗を確認**

Run: `python -m pytest tests/integration/test_webhook.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.handlers'`

`pyproject.toml` の `pythonpath` に `"."` を追加して `src.handlers` を解決できるようにすること。

```toml
[tool.pytest.ini_options]
pythonpath = ["src", "."]
```

- [ ] **Step 3: deps.py を書く**

`src/handlers/deps.py`:

```python
"""Lambda 共通の依存組み立てと時刻ユーティリティ。

起点日の決め方に注意する。夜 22:30 の起点日は今日だが、
朝 8:00 の起点日は前日である（前夜のメッセージと同じ日付にするため）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))


def jst_now() -> datetime:
    return datetime.now(JST)


def origin_date(now: datetime, morning: bool = False) -> str:
    """起点日を返す。morning=True のときは前日。"""
    d = now.date() - timedelta(days=1) if morning else now.date()
    return d.isoformat()


def now_h(now: datetime) -> float:
    """起点日 0:00 からの経過時間（時）。"""
    return round(now.hour + now.minute / 60.0, 4)


def _secret(name: str, region: str) -> dict:
    import boto3

    sm = boto3.client("secretsmanager", region_name=region)
    return json.loads(sm.get_secret_value(SecretId=name)["SecretString"])


def _memory(settings):
    from sleeptech.memory.client import MemoryClient
    from sleeptech.memory.dynamo_backend import DynamoBackend

    return MemoryClient(DynamoBackend(settings.table, settings.region))


def _llm(settings):
    from sleeptech.llm.client import OpenAiClient

    key = _secret(settings.secret_openai, settings.region).get("api_key", "")
    return OpenAiClient(key, settings.llm_model)


def _push(settings):
    from sleeptech.notifier.line import LinePushClient

    s = _secret(settings.secret_line, settings.region)
    return LinePushClient(s.get("channel_access_token", ""), s.get("user_id", ""))


def _health_source(settings):
    from sleeptech.collectors.google_auth_helper import get_access_token
    from sleeptech.collectors.google_health_api import GoogleHealthApiSource

    def provider() -> str:
        return get_access_token(_secret(settings.secret_google, settings.region))

    return GoogleHealthApiSource(provider)


def build_night_deps(settings):
    from sleeptech.workflow.night import NightDeps

    return NightDeps(
        settings=settings,
        memory=_memory(settings),
        llm_client=_llm(settings),
        push_client=_push(settings),
    )


def build_morning_deps(settings):
    from sleeptech.workflow.morning import MorningDeps

    return MorningDeps(
        settings=settings,
        memory=_memory(settings),
        health_source=_health_source(settings),
        llm_client=_llm(settings),
        push_client=_push(settings),
    )
```

- [ ] **Step 4: night_handler.py と morning_handler.py を書く**

`src/handlers/night_handler.py`:

```python
"""夜 Lambda（EventBridge 22:30 JST 起動）。

payload 例: {"date": "2026-08-26", "now_h": 22.5}（省略時は現在時刻から決定）
"""

from __future__ import annotations

from sleeptech.config import load_settings
from sleeptech.workflow.night import run_night
from src.handlers import deps as hd


def handler(event, context=None) -> dict:
    event = event or {}
    settings = load_settings()
    now = hd.jst_now()
    date = event.get("date") or hd.origin_date(now)
    current_h = event.get("now_h")
    if current_h is None:
        current_h = hd.now_h(now)
    return run_night(date, float(current_h), hd.build_night_deps(settings))
```

`src/handlers/morning_handler.py`:

```python
"""朝 Lambda（EventBridge 8:00 JST 起動）。

起点日は前日である点に注意（前夜の夜メッセージと同じ日付を使う）。
送信の冪等は run_morning が内部で担保するため、ここでは判定しない。
"""

from __future__ import annotations

from sleeptech.config import load_settings
from sleeptech.workflow.morning import run_morning
from src.handlers import deps as hd


def handler(event, context=None) -> dict:
    event = event or {}
    settings = load_settings()
    date = event.get("date") or hd.origin_date(hd.jst_now(), morning=True)
    return run_morning(date, hd.build_morning_deps(settings))
```

- [ ] **Step 5: webhook_handler.py を書く**

`src/handlers/webhook_handler.py`:

```python
"""LINE Webhook Lambda。

役割は2つ。
1. 起床トリガー（「おはよう」等）を検出したら run_morning を前倒し実行する
   （収集だけでなく送信まで行う。冪等は run_morning が担保する）
2. クイックリプライの回答（評価:N）を直近の未回答の夜予測に紐付けて保存する

どちらでもないメッセージは何もしない（ver1.0 に会話機能は無い）。
"""

from __future__ import annotations

import json
import re
from datetime import date as date_cls
from datetime import timedelta

from sleeptech.config import load_settings
from sleeptech.memory.schemas import Feedback
from sleeptech.notifier.line import parse_feedback
from sleeptech.observability import StructuredLogger
from sleeptech.workflow.morning import run_morning
from src.handlers import deps as hd

_WAKEUP_RE = re.compile(r"^(おはよう(ございます)?|起きた|おきた|データ更新|同期|計測)$")
FEEDBACK_LOOKBACK_DAYS = 2


def is_wakeup_trigger(text: str) -> bool:
    """起床トリガー語か判定する。完全一致のみ（質問文を誤検知しないため）。"""
    return bool(_WAKEUP_RE.match((text or "").strip()))


def resolve_feedback_date(memory, today: str) -> str | None:
    """回答を紐付ける日付を返す。直近の未回答の夜予測を最大2日分遡って探す。"""
    base = date_cls.fromisoformat(today)
    for i in range(FEEDBACK_LOOKBACK_DAYS):
        d = (base - timedelta(days=i)).isoformat()
        if memory.get_prediction(d, "NIGHT") and memory.get_feedback(d) is None:
            return d
    return None


def _texts(event) -> list[str]:
    """Webhook イベントからテキストメッセージ本文を取り出す。"""
    try:
        body = json.loads(event.get("body") or "{}")
    except (TypeError, ValueError):
        return []
    out = []
    for ev in body.get("events", []):
        msg = ev.get("message") or {}
        if ev.get("type") == "message" and msg.get("type") == "text":
            out.append(msg.get("text", ""))
    return out


def handler(event, context=None) -> dict:
    log = StructuredLogger()
    settings = load_settings()
    now = hd.jst_now()
    handled = []

    for text in _texts(event or {}):
        score = parse_feedback(text)
        if score is not None:
            memory = hd._memory(settings)
            target = resolve_feedback_date(memory, hd.origin_date(now))
            if target:
                memory.save_feedback(
                    Feedback(date=target, score=score,
                             answered_at=now.isoformat(timespec="seconds"))
                )
                log.log("feedback_saved", date=target, score=score)
                handled.append("feedback")
            else:
                log.log("feedback_unmatched", score=score)
            continue

        if is_wakeup_trigger(text):
            date = hd.origin_date(now, morning=True)
            run_morning(date, hd.build_morning_deps(settings))
            log.log("wakeup_triggered", date=date)
            handled.append("wakeup")

    return {"statusCode": 200, "body": json.dumps({"handled": handled})}
```

- [ ] **Step 6: テストを実行する**

Run: `python -m pytest tests/integration/test_webhook.py -v`
Expected: PASS（19 passed）

- [ ] **Step 7: 全テストを実行**

Run: `python -m pytest -q`
Expected: 全件 PASS

- [ ] **Step 8: コミット**

```bash
git add src/handlers pyproject.toml tests/integration/test_webhook.py
git commit -m "feat(handlers): 夜・朝・Webhook の Lambda エントリを実装"
```

---

### Task 16: CDK インフラ

**Files:**
- Create: `infra/app.py`
- Create: `infra/stacks/sleeptech_stack.py`
- Create: `infra/cdk.json`
- Test: `tests/unit/test_infra_synth.py`

**Interfaces:**
- Consumes: なし
- Produces: `SleepTechStack` — Lambda×3、EventBridge Scheduler×2、DynamoDB×1

**設計メモ:** DynamoDB のキーは `pk`（パーティション）/ `sk`（ソート）。転用元は `PK`/`SK`（大文字）だったので、そのまま流用してはならない。TTL 属性は `ttl`。

- [ ] **Step 1: 失敗するテストを書く**

`tests/unit/test_infra_synth.py`:

```python
"""CDK スタックの構成検査。cdk synth 相当をテストで行う。"""

import pytest

aws_cdk = pytest.importorskip("aws_cdk")
from aws_cdk import App  # noqa: E402
from aws_cdk.assertions import Match, Template  # noqa: E402

from infra.stacks.sleeptech_stack import SleepTechStack  # noqa: E402


@pytest.fixture(scope="module")
def template() -> Template:
    return Template.from_stack(SleepTechStack(App(), "TestStack"))


def test_creates_three_lambdas(template):
    template.resource_count_is("AWS::Lambda::Function", 3)


def test_creates_two_schedules(template):
    template.resource_count_is("AWS::Scheduler::Schedule", 2)


def test_table_uses_lowercase_pk_sk(template):
    """転用元は PK/SK（大文字）だった。本プロジェクトは pk/sk。"""
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "KeySchema": [
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ]
        },
    )


def test_table_has_ttl_enabled(template):
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {"TimeToLiveSpecification": {"AttributeName": "ttl", "Enabled": True}},
    )


def test_schedules_use_jst(template):
    template.has_resource_properties(
        "AWS::Scheduler::Schedule", {"ScheduleExpressionTimezone": "Asia/Tokyo"}
    )


def test_night_schedule_is_at_2230(template):
    template.has_resource_properties(
        "AWS::Scheduler::Schedule", {"ScheduleExpression": "cron(30 22 * * ? *)"}
    )


def test_morning_schedule_is_at_0800(template):
    template.has_resource_properties(
        "AWS::Scheduler::Schedule", {"ScheduleExpression": "cron(0 8 * * ? *)"}
    )


def test_lambdas_receive_the_llm_model_env(template):
    """モデルIDは環境変数で差し替えられること（仕様 第14節）。"""
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {"Environment": {"Variables": Match.object_like({"LLM_MODEL": Match.any_value()})}},
    )


def test_lambdas_have_pythonpath_for_the_src_layout(template):
    """sleeptech パッケージは src/ 配下にあるため PYTHONPATH が要る。"""
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {"Environment": {"Variables": Match.object_like({"PYTHONPATH": "/var/task/src"})}},
    )


def test_webhook_has_a_function_url(template):
    template.resource_count_is("AWS::Lambda::Url", 1)
```

- [ ] **Step 2: CDK 依存を入れてテストが失敗することを確認**

```bash
pip install "aws-cdk-lib>=2.140" "constructs>=10.0"
```

Run: `python -m pytest tests/unit/test_infra_synth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'infra.stacks.sleeptech_stack'`

- [ ] **Step 3: sleeptech_stack.py を書く**

`infra/stacks/sleeptech_stack.py`:

```python
"""SleepTech の AWS 構成（仕様 第4節「AWS リソース」）。

Lambda の依存は boto3 + 標準ライブラリのみなので、Layer もバンドルも不要。
src/ をそのまま Code.from_asset で配る。
"""

from __future__ import annotations

import os

from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_scheduler as scheduler
from constructs import Construct

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "src")

SECRET_GOOGLE = "sleeptech/google-oauth"
SECRET_LINE = "sleeptech/line"
SECRET_OPENAI = "sleeptech/openai"


class SleepTechStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        table = ddb.Table(
            self,
            "SleepTechTable",
            table_name="SleepTechTable",
            partition_key=ddb.Attribute(name="pk", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="sk", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            removal_policy=RemovalPolicy.RETAIN,
        )

        env = {
            "STC_ENV": "prod",
            "STC_DDB_TABLE": table.table_name,
            "STC_SECRET_GOOGLE": SECRET_GOOGLE,
            "STC_SECRET_LINE": SECRET_LINE,
            "STC_SECRET_OPENAI": SECRET_OPENAI,
            # 暫定値。正しいモデルIDが判明したらこの値を差し替えて再デプロイする
            "LLM_MODEL": "gpt-5.6-luna",
            # ハンドラは src.handlers.* として解決されるが、sleeptech パッケージは
            # src/ 配下にあるため PYTHONPATH を通さないと import できない
            "PYTHONPATH": "/var/task/src",
        }

        night_fn = self._function("NightFn", "src.handlers.night_handler.handler", env)
        morning_fn = self._function("MorningFn", "src.handlers.morning_handler.handler", env)
        webhook_fn = self._function("WebhookFn", "src.handlers.webhook_handler.handler", env)

        for fn in (night_fn, morning_fn, webhook_fn):
            table.grant_read_write_data(fn)
            fn.add_to_role_policy(
                iam.PolicyStatement(
                    actions=["secretsmanager:GetSecretValue"],
                    resources=[
                        f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:{name}-*"
                        for name in (SECRET_GOOGLE, SECRET_LINE, SECRET_OPENAI)
                    ],
                )
            )

        webhook_fn.add_function_url(auth_type=lambda_.FunctionUrlAuthType.NONE)

        self._schedule("NightSchedule", "cron(30 22 * * ? *)", night_fn)
        self._schedule("MorningSchedule", "cron(0 8 * * ? *)", morning_fn)

    def _function(self, name: str, handler: str, env: dict) -> lambda_.Function:
        return lambda_.Function(
            self,
            name,
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler=handler,
            code=lambda_.Code.from_asset(os.path.abspath(os.path.join(SRC_DIR, ".."))),
            timeout=Duration.seconds(60),
            memory_size=512,
            environment=env,
        )

    def _schedule(self, name: str, expression: str, fn: lambda_.Function) -> None:
        role = iam.Role(
            self,
            f"{name}Role",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
        )
        fn.grant_invoke(role)
        scheduler.CfnSchedule(
            self,
            name,
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            schedule_expression=expression,
            schedule_expression_timezone="Asia/Tokyo",
            target=scheduler.CfnSchedule.TargetProperty(
                arn=fn.function_arn, role_arn=role.role_arn
            ),
        )
```

`Code.from_asset` はリポジトリルートを配るため、`handler` は `src.handlers.<module>.handler` の形になる。`.gitignore` されたファイルや `docs/` も含まれてしまう場合は `exclude` を指定すること。

- [ ] **Step 4: app.py と cdk.json を書く**

`infra/app.py`:

```python
#!/usr/bin/env python3
"""CDK エントリポイント。"""

import aws_cdk as cdk

from infra.stacks.sleeptech_stack import SleepTechStack

app = cdk.App()
SleepTechStack(app, "SleepTechStack")
app.synth()
```

`infra/cdk.json`:

```json
{
  "app": "python app.py",
  "context": {
    "@aws-cdk/core:newStyleStackSynthesis": true
  }
}
```

- [ ] **Step 5: テストを実行する**

Run: `python -m pytest tests/unit/test_infra_synth.py -v`
Expected: PASS（9 passed）

- [ ] **Step 6: cdk synth が通ることを確認**

Run: `cd infra && npx cdk synth SleepTechStack > /dev/null && echo OK`
Expected: `OK`（エラーなし）

- [ ] **Step 7: コミット**

```bash
git add infra tests/unit/test_infra_synth.py
git commit -m "feat(infra): CDK スタック（Lambda×3・Scheduler×2・DynamoDB）を定義"
```

---

### Task 17: デプロイと実機確認

**Files:**
- Create: `docs/deploy.md`
- Modify: `src/handlers/webhook_handler.py`, `tests/unit/test_handlers.py`

**Interfaces:**
- Consumes: Task 16 の `SleepTechStack`
- Produces: 稼働中の本番環境

**このタスクは利用者の手作業に依存する。** 以下の4点が揃うまでデプロイできない。

1. LINE Developers で SleepTech 用 Messaging API チャネルを作成し、チャネルアクセストークンと userId を取得
2. LINEチャネルシークレットを取得
3. OpenAI の API キー
4. **「GPT5.6 luna」の正確なモデルID文字列**（判明していなければ暫定値のまま進め、実機で 400/404 を確認してから差し替える）

- [ ] **Step 1: 全テストが通ることを確認**

Run: `python -m pytest -q`
Expected: 全件 PASS。1件でも失敗していればデプロイしない。

- [ ] **Step 2: Secrets を登録する**

Google、LINE、OpenAI の Secret は AWS Console の Secrets Manager で作成・入力する。
秘密値をCLI引数、変数、ファイル、ログ、チャットへ出さず、既存Secretの値も取得・表示しない。
既存Secretを移行する必要がある場合は、権限管理されたConsole操作または `asm-exec` を使い、
値をこのエージェントへ渡さない。

- [ ] **Step 3: WebhookのLINE署名検証を実装し、テストする**

`src/handlers/webhook_handler.py`で、LINEから受け取ったraw bodyと`x-line-signature`を検証する。チャネルシークレットはSecrets Managerの`sleeptech/line`に保存し、署名検証にはHMAC-SHA256とconstant-time compareを使う。raw bodyの改変、JSON再シリアライズ、通常の文字列比較による検証は禁止する。

検証要件:

- 署名ヘッダー欠落は401を返す。
- 署名不一致は403を返す。
- 正しい署名は既存Webhook処理へ進む。
- 欠落・不一致・成功の各ケースを`tests/unit/test_handlers.py`でテストし、全件成功すること。

**この署名検証テストが成功するまで、CDK diff・deployへ進まない。**

- [ ] **Step 4: 差分を確認してからデプロイする**

Run: `cd infra && npx cdk diff SleepTechStack`
Expected: 全リソースが新規作成（`+`）であること。既存スタックではないので削除（`-`）は現れない。

```bash
cd infra
npx cdk synth SleepTechStack
npx cdk deploy SleepTechStack --require-approval never
```

`python -m pytest -q`と署名検証テストが成功した後にのみdeployする。

- [ ] **Step 5: Webhook URL を LINE に設定する**

デプロイ出力の Function URL を LINE Developers の Webhook URL に設定し、「Webhookの利用」を ON にする。**この作業は利用者が行う。**

- [ ] **Step 6: 既存 Night Ramen を停止する**

利用者の確認を取ってから実行すること。停止は EventBridge Scheduler の無効化で行い、スタックは削除しない（データを残すため）。

```bash
aws scheduler list-schedules --query "Schedules[?contains(Name, 'NightRamen')].Name" --output text
aws scheduler update-schedule --name <取得した名前> --state DISABLED --schedule-expression "<既存の式>" --flexible-time-window '{"Mode":"OFF"}' --target '<既存のターゲットJSON>'
```

- [ ] **Step 7: 夜フローを手動実行して確認**

```bash
aws lambda invoke --function-name <NightFn の物理名> --payload '{"date":"2026-08-26","now_h":22.5}' --cli-binary-format raw-in-base64-out /tmp/night.json
cat /tmp/night.json
```

確認すること:
- LINE に推奨就寝時刻を含むメッセージが届く
- 5段階のクイックリプライが表示される
- `used_fallback` が `false`（`true` ならモデルIDが誤っている可能性。CloudWatch Logs の `llm_failed` を確認する）

- [ ] **Step 8: モデルIDを確認して必要なら差し替える**

`used_fallback` が `true` で、ログに 404 または `model_not_found` が出ている場合、`LLM_MODEL` の値が誤っている。正しい値を確認して差し替える。

Lambda の環境変数をCLIで全置換せず、`infra/stacks/sleeptech_stack.py` の `LLM_MODEL` を
変更して `cdk synth --strict`、`cdk diff`、`cdk deploy` を順に実行する。これにより既存の
Secret参照やその他の環境変数を保持したまま変更を管理できる。

- [ ] **Step 9: 朝フローを手動実行して確認**

```bash
aws lambda invoke --function-name <MorningFn の物理名> --payload '{"date":"2026-08-26"}' --cli-binary-format raw-in-base64-out /tmp/morning.json
cat /tmp/morning.json
```

確認すること:
- `has_sleep` が `true`（`false` なら Google Health のスコープか日付変換を疑う）
- LINE に時間帯別の低下率とカフェイン・光曝露の時刻が届く
- クイックリプライは付いていない（朝は聞かない）

- [ ] **Step 10: 実機で LINE から確認**

- 「おはよう」と送る → その場で朝のアドバイスが届く
- もう一度「おはよう」と送る → **2通目は届かない**（冪等が効いている）
- 夜のクイックリプライで「3 ふつう」をタップ → 何も返らないが DynamoDB に `FEEDBACK` が保存される

```bash
aws dynamodb get-item --table-name SleepTechTable --key '{"pk":{"S":"FEEDBACK"},"sk":{"S":"2026-08-26"}}'
```

- [ ] **Step 11: deploy.md に手順を残す**

上記 Step 2〜10 の内容を `docs/deploy.md` にまとめる。Function URL、Lambda の物理名、実行したコマンドの実際の値を記録すること。

- [ ] **Step 12: コミット**

```bash
git add docs/deploy.md
git commit -m "docs: デプロイ手順と実機確認の結果を記録"
```

---

## 完了条件

- `python -m pytest -q` が全件 PASS
- 署名欠落・署名不一致・正しい署名のWebhookテストが全件 PASS
- `cd infra && npx cdk diff SleepTechStack` の差分がゼロ
- LINE で夜・朝の両方のメッセージを実際に受信できている
- 「おはよう」の二重送信が起きない
- クイックリプライの回答が DynamoDB に保存される
- `used_fallback` が `false`（LLM 経路が生きている）

---

## 補足: 実装中に判断が必要になったら

| 状況 | 判断 |
|---|---|
| モデル定数を調整した | `constants.py` のコメントに実測値と調整理由を残す。仕様書 第5節の期待出力表も更新する |
| `test_drop_is_within_plausible_range` が通らない | 調整してよいのは `CIRCADIAN_AMPLITUDE` `HARMONIC_RATIO` `PERF_STEEPNESS` `PERF_THRESHOLD` の4つのみ。順序関係のテストを壊さないこと |
| Google Health が REM を返さない | `_derive_sleep_metrics` は `rem_sleep_min=0` を返す。`_rem_deficit_ratio` が深睡眠にフォールバックするので実装変更は不要 |
| LINEのWebhook署名検証 | 公開前必須。`sleeptech/line`へ`channel_secret`を登録し、raw bodyに対するHMAC-SHA256とconstant-time compareを実装する。署名欠落は401、不一致は403とし、検証テスト成功後のみdeployする |
