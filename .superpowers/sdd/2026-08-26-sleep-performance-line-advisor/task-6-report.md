# Task 6 実装レポート

## 実装内容

- `RecoveryPlan` データクラスを `schemas.py` に追加。
- `recovery.caffeine_cutoff()` を半減期と就寝時残存率の式で実装。
- `recovery.plan()` を純粋関数として実装し、カフェイン摂取時刻、朝の光曝露時刻、避光開始時刻を算出。
- ブリーフ指定の10テストを `tests/unit/test_recovery.py` に追加。
- `model/` 配下に外部I/O、boto3、環境変数、現在時刻、サードパーティ依存は追加していない。

## TDD 実行結果

### RED

コマンド:

```text
python -m pytest tests/unit/test_recovery.py -v
```

結果: 収集時に `ImportError: cannot import name 'recovery'`。実装未作成による期待どおりの失敗。

### GREEN

実装後に同じコマンドを実行した結果は `8 passed, 2 failed`。

失敗:

- `test_two_doses_when_window_is_long`
- `test_single_dose_when_window_is_short`

## 全スイート

コマンド:

```text
python -m pytest -q
```

結果: `45 passed, 2 failed`。

## コミット

`3fad729 feat(model): カフェインと光曝露の推奨時刻を算出`

## 変更ファイル

- `src/sleeptech/memory/schemas.py`
- `src/sleeptech/model/recovery.py`
- `tests/unit/test_recovery.py`

## セルフレビュー

- ブリーフの実装コードと一致していることを確認。
- 時刻変数は `_h` サフィックスを使用。
- コメント・docstring は日本語。
- `git diff --check` は問題なし。
- 未追跡の `.agents/` と `AGENTS.md` は変更・コミットしていない。

## 懸念

既存定数 `CAFFEINE_HALF_LIFE_H = 5.0`、`CAFFEINE_BEDTIME_RATIO = 0.25`、`CAFFEINE_SPLIT_THRESHOLD_H = 8.0` と、ブリーフのテスト期待値が算術的に矛盾している。

- `NEXT_BED_H = 47.5` では cutoff は `37.5`、起床から `6.5` 時間であり、2回分割条件（8時間超）を満たさない。
- `wake_h + 9.0` では cutoff は `30.0` で、1回目の摂取時刻 `32.0` がcutoffを超えるため、1回推奨と「摂取時刻はcutoff以前」を同時に満たせない。

既存定数やブリーフの正本コードを変更せず、矛盾を未解決の懸念として残した。

## 修正ラウンド1

### 裁定

仕様書第6節の定数・規則、実装コード、`constants.py` は維持し、算術的に誤っていたテストシナリオの入力だけを修正した。

- 長い窓: `next_bedtime_h=WAKE_H + 19.0`（cutoff-wake は9時間）に変更。
- 短い窓: `next_bedtime_h=WAKE_H + 12.0`（cutoff-wake は2時間）に変更。
- テスト名とテスト意図は維持。

### 変更

- `tests/unit/test_recovery.py` の2テストの入力値のみ変更。
- 本レポートに修正ラウンド1の裁定、結果、懸念を追記。
- 実装コードおよび `src/sleeptech/model/constants.py` は変更なし。

### テスト結果

対象10件:

    python -m pytest tests/unit/test_recovery.py -v
    10 passed in 0.18s

全スイート:

    python -m pytest -q
    47 passed in 0.32s

### 修正ラウンド1時点の懸念

裁定によりテスト入力を仕様に整合する値へ修正したため、前回の算術矛盾は解消した。実装・定数の追加懸念はない。

## 修正ラウンド2

### 変更

- 公開Interfaceに合わせ、`RecoveryPlan.caffeine_times_h` を `list` から `list[float]` に修正。
- `typing.get_type_hints()` で公開型を固定する回帰テストを追加。
- runtimeロジック、`constants.py`、既存の計算式は変更なし。

### RED

コマンド:

    python -m pytest tests/unit/test_recovery.py -v

結果: `11 items` 中 `10 passed, 1 failed`。新規テストが `list` と `list[float]` の不一致で失敗。

### GREEN

型注釈を1行修正後のコマンド:

    python -m pytest tests/unit/test_recovery.py -v
    11 passed in 0.18s

### 全スイート

    python -m pytest -q
    48 passed in 0.34s

### 修正ラウンド2時点の懸念

なし。公開Interfaceの型注釈と回帰テストが一致し、実装ロジックと定数は裁定どおり不変。
