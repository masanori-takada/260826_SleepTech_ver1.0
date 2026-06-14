# Contract: 内部エージェント／モジュール インターフェース

**Date**: 2026-06-14 | **Branch**: `001-night-ramen-breakdown`

外部 REST API は持たない（LINE/Google/Bedrock は外部サービス利用）。本コントラクトは内部モジュール間の
関数シグネチャ契約を定義し、ユニットテストの境界となる。型は Python の型ヒント表記。

## C1. collectors

```python
# collectors/google_health.py
def fetch_daily_vitals(date: str) -> DailyVitalRecord:
    """指定日の前夜バイタルを取得。欠損は None、異常値は除外し missing_fields に記録。
    同意失効時は ConsentRevokedError を送出（FR-012）。"""

# collectors/self_report.py
def get_ramen_self_report(date: str) -> bool | None:
    """夜食ラーメン有無の自己申告。未申告は None（FR-002）。"""
```

## C2. memory

```python
# memory/client.py
def save_vital(record: DailyVitalRecord) -> None: ...
def get_vitals(since: str, until: str) -> list[DailyVitalRecord]: ...
def save_analysis(result: AnalysisResult) -> None: ...
def get_current_strategy() -> PersuasionStrategy: ...
def save_strategy(strategy: PersuasionStrategy) -> None:
    """guardrail_passed=True のみ許可。False は ValueError（FR-010 不変条件）。"""
def save_message(msg: PersuasionMessage) -> None: ...
def get_recent_messages(limit: int) -> list[PersuasionMessage]: ...
```

## C3. goals

```python
# goals/evaluator.py
def evaluate(goal: Goal, vitals: list[DailyVitalRecord]) -> GoalProgress:
    """成功率・減量の達成率を算出（FR-005）。"""

def choose_tone(progress: GoalProgress) -> Tone:
    """達成率→強度トーン（praise/neutral/warning/strong_warning）を決定（FR-006）。
    目標未設定時は既定 85%/2kg を用いる（FR-017）。"""
```

## C4. agents

```python
# agents/analyzer.py
def analyze(vitals: list[DailyVitalRecord]) -> AnalysisResult:
    """ラーメンが各指標へ与えた悪影響を推論・数値化（FR-004）。
    使用モデルは config 経由（既定 Haiku / 本番 Opus 4.8）。"""

# agents/persuader.py
def generate_message(
    strategy: PersuasionStrategy, analysis: AnalysisResult, tone: Tone
) -> str:
    """説得メッセージ本文を生成（FR-007）。モデルは config（本番 Sonnet 4.6）。"""

def update_strategy(
    current: PersuasionStrategy,
    recent_messages: list[PersuasionMessage],
    recent_vitals: list[DailyVitalRecord],
) -> PersuasionStrategy:
    """説得履歴と結果から戦略を自律更新（FR-010）。返り値は未検証。保存前に guardrail 必須。"""
```

## C5. guardrail（NON-NEGOTIABLE / 原則II）

```python
# guardrail/validator.py
@dataclass
class GuardrailVerdict:
    passed: bool
    reasons: list[str]   # 不合格理由（差別/逸脱/正論の暴力 等）

def validate(text: str, context: GuardrailContext) -> GuardrailVerdict:
    """送信前検証。3観点 (a)差別/不適切 (b)目標/仕様逸脱 (c)正論の暴力 を検査。
    判定不能・例外時は passed=False（fail-closed, FR-008）。"""

def generate_safe_message(
    generator: Callable[[], str], context: GuardrailContext, max_retries: int = 2
) -> tuple[str, GuardrailResult]:
    """生成→検証を最大2回再試行。全不合格なら固定安全文面を返す（FR-009）。
    返り値: (本文, pass|fallback)。"""

# guardrail/fallback.py
SAFE_FALLBACK_MESSAGE: str  # 人格否定を含まない固定の励まし文面
```

### guardrail テスト契約（tests/unit/test_guardrail.py — 必須）

- 差別的表現を含む入力 → `passed=False`、reason に「差別」を含む。
- 目標から逸脱する指示を含む入力 → `passed=False`。
- 人格否定・過激な罵倒（正論の暴力）→ `passed=False`。
- 健全な個別化メッセージ → `passed=True`。
- `validate` 内部例外 → `passed=False`（fail-closed）。
- `generate_safe_message`: 3回連続不合格 → `SAFE_FALLBACK_MESSAGE` を返し result=`fallback`。

## C6. notifier

```python
# notifier/line.py
def send_if_not_notified(date: str, text: str) -> bool:
    """当夜未通知のときのみ LINE push 送信し True。送信済みなら False（FR-014 冪等）。"""
```

## C7. workflow（strands）

```python
# workflow/morning.py
def run_morning(date: str) -> None:
    """収集→記憶（US2）。各ステップに相関IDログ（FR-013）。"""

# workflow/night.py
def run_night(date: str) -> None:
    """分析→目標評価→トーン決定→生成→guardrail→送信→戦略更新（US1,3,4）。
    送信は guardrail 通過＋未通知時のみ（原則II/FR-014）。"""
```
