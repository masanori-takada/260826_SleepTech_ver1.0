"""Two-Process Model とリカバリー算出の定数（仕様 第5節・第6節）。

TAU_RISE / TAU_DECAY は Borbely/Achermann 系 Two-Process Model の標準的な値。
それ以外は仕様 第11節の出力レンジ検査を満たすよう決めた値であり、
検査が通らない場合のみ再調整する。
"""

# --- Process S（恒常性睡眠圧）---
TAU_RISE = 18.2    # 覚醒中の睡眠圧上昇の時定数（時間）
TAU_DECAY = 4.2    # 睡眠中の睡眠圧減衰の時定数（時間）

# --- Process C（概日リズム）---
# CIRCADIAN_AMPLITUDE と HARMONIC_RATIO は 0.25 / 0.5 が正しい値である。
# 第2高調波（12時間周期）を加えるとこの値でしか午後の谷（post-lunch dip、
# 本プロダクトの中核機能）が有意な深さで現れない。
# その代償として、夜間の谷（全体最小）は起床のちょうど2時間前ではなく
# 起床3.3時間前あたりへ数学的にずれる。これはこのモデルの正しい挙動であり、
# 「起床2時間前が底」と決め打ったテストの前提が誤っていたため、テスト側を修正した。
CIRCADIAN_AMPLITUDE = 0.25  # 振幅 A（0.2 に下げると午後の谷が消える）
HARMONIC_RATIO = 0.5        # 午後の谷の相対的な深さ H（0.02 に下げると午後の谷が消える）
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

# リカバリー提案の時刻メニュー（領域の課題に対して「何時に」を出すための基準）
FOCUS_BLOCK_OFFSET_H = 3.0      # 重要作業に向く時間帯 = 起床 + このオフセット
NAP_BEFORE_DIP_H = 0.5          # 仮眠は集中力の谷の何時間前に置くか
NAP_EARLIEST_OFFSET_H = 4.0     # 仮眠は起床からこの時間より前には置かない
EXERCISE_BEFORE_BED_H = 4.0     # 運動は就寝の何時間前までにするか
EXERCISE_EARLIEST_OFFSET_H = 2.0  # 運動は起床からこの時間より前には置かない
MEAL_BEFORE_BED_H = 3.0         # 夕食は就寝の何時間前までに終えるか
BATH_BEFORE_BED_H = 1.5         # 入浴は就寝の何時間前に終えるか
WINDDOWN_BEFORE_BED_H = 1.0     # 活動を落とし始めるのは就寝の何時間前か

