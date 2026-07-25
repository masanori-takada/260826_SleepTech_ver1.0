"""説得メッセージの話題ローテーション（FR-007 / SC-004）。

23:00 の説得メッセージが毎晩「同じ指標・同じ切り口」に偏るのを防ぐため、
直近の説得履歴を見て「まだ触れていない／久しく触れていない」

  - バイタル指標（VITAL_FIELDS 全12種）
  - 語りの切り口（Angle）

を選び直す。少なくとも前夜に扱った指標は必ず外す（十分な候補がある限り）。

指標のメタデータ（表示名・単位・良い方向・検出キーワード）もここに集約し、
本文が実際にどの指標へ言及したかの検出（SC-004）も同じ定義を使う。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_cls

from night_ramen.memory.schemas import (
    GuardrailResult,
    PersuasionMessage,
    VITAL_FIELDS,
)

# 一度も使われていない話題に与える「経過日数」（必ず最優先で選ばれる大きな値）
_NEVER_USED = 10**6


@dataclass(frozen=True)
class MetricMeta:
    """バイタル指標の表示情報。

    higher_is_better: True=大きいほど良い / False=小さいほど良い / None=方向を断定しない
    keywords: 本文中の言及検出に使う語（SC-004）
    """

    field: str
    label: str
    unit: str
    higher_is_better: bool | None
    keywords: tuple[str, ...]


# VITAL_FIELDS の全指標を網羅する（欠けると話題ローテーションから漏れる）。
METRIC_META: dict[str, MetricMeta] = {
    "sleep_score": MetricMeta(
        "sleep_score", "睡眠スコア", "点", True,
        ("睡眠スコア", "睡眠の質", "眠りの質"),
    ),
    "total_sleep_min": MetricMeta(
        "total_sleep_min", "総睡眠時間", "分", True,
        ("総睡眠", "睡眠時間", "眠れた時間", "睡眠の長さ"),
    ),
    "awakenings": MetricMeta(
        "awakenings", "中途覚醒の回数", "回", False,
        ("中途覚醒", "夜中に目が覚め", "目が覚める回数", "覚醒回数"),
    ),
    "deep_sleep_min": MetricMeta(
        "deep_sleep_min", "深い睡眠の時間", "分", True,
        ("深い睡眠", "深睡眠", "浅い眠り", "深い眠り"),
    ),
    "night_avg_hr": MetricMeta(
        "night_avg_hr", "睡眠中の平均心拍数", "bpm", False,
        ("心拍", "脈拍"),
    ),
    "hrv": MetricMeta(
        "hrv", "HRV（自律神経の回復度）", "ms", True,
        ("HRV", "hrv", "自律神経", "心拍変動"),
    ),
    "skin_temp_delta": MetricMeta(
        "skin_temp_delta", "皮膚温の平常時との差", "℃", None,
        ("皮膚温", "体温", "深部体温"),
    ),
    "respiration_rate": MetricMeta(
        "respiration_rate", "睡眠中の呼吸数", "回/分", False,
        ("呼吸数", "呼吸", "いびき"),
    ),
    "weight_kg": MetricMeta(
        "weight_kg", "体重", "kg", False,
        ("体重",),
    ),
    "body_fat_pct": MetricMeta(
        "body_fat_pct", "体脂肪率", "%", False,
        ("体脂肪",),
    ),
    "next_day_steps": MetricMeta(
        "next_day_steps", "翌日の歩数", "歩", True,
        ("歩数", "歩いた"),
    ),
    "next_day_calories": MetricMeta(
        "next_day_calories", "翌日の消費カロリー", "kcal", True,
        ("消費カロリー", "カロリー", "消費エネルギー", "活動量"),
    ),
}


@dataclass(frozen=True)
class Angle:
    """語りの切り口。同じ指標でも「どう語るか」を変えて反復感をなくす。

    requires: この切り口が成立するために必要な指標（いずれか1つでもデータがあればよい）。
              空タプルなら指標データの種類を問わない。
    requires_reports: ラーメンの自己申告（ramen_last_night）が必要か。
    """

    id: str
    label: str
    instruction: str
    requires: tuple[str, ...] = ()
    requires_reports: bool = False


# 切り口のカタログ。順序が「一度も使われていない時」の既定ローテーション順になる。
ANGLES: tuple[Angle, ...] = (
    Angle(
        "last_night",
        "昨夜の実測値そのもの",
        "昨夜〜今朝に実際に記録された数値を1つ挙げ、それが今夜の選択でどう変わるかを語る。",
    ),
    Angle(
        "contrast",
        "ラーメンあり／なしの平均比較",
        "ラーメンを食べた日と食べなかった日の平均差を具体的な数値で示し、差の大きさを実感させる。",
        requires_reports=True,
    ),
    Angle(
        "weekly_trend",
        "直近1週間とそれ以前のトレンド",
        "直近7日の平均と、それ以前の平均を比べ、良くなっている／崩れてきている流れとして語る。",
    ),
    Angle(
        "personal_best",
        "自己ベストとの距離",
        "これまでの最良値を思い出させ、そこまであと少しであること（または戻れること）を語る。",
    ),
    Angle(
        "streak",
        "積み上げた連続記録",
        "ここまで我慢できた連続日数や成功率という「積み上げ」を主役にし、途切れさせない動機づけをする。",
        requires_reports=True,
    ),
    Angle(
        "body_composition",
        "体重・体組成の推移",
        "体重や体脂肪率の推移を主役にし、今夜の一杯が数字にどう表れるかを語る。",
        requires=("weight_kg", "body_fat_pct"),
    ),
    Angle(
        "next_day_cost",
        "翌日のパフォーマンスへの波及",
        "翌日の歩数や消費カロリーへの影響を主役にし、「明日の自分の動きが落ちる」という形で語る。",
        requires=("next_day_steps", "next_day_calories"),
    ),
    Angle(
        "autonomic",
        "自律神経という見えない疲労",
        "HRV・心拍・呼吸数・皮膚温といった自覚しにくい回復指標を主役に、"
        "「気づかないうちに溜まる疲労」として語る。",
        requires=("hrv", "night_avg_hr", "respiration_rate", "skin_temp_delta"),
    ),
    Angle(
        "sleep_architecture",
        "眠りの中身（深さと途切れ）",
        "睡眠時間の長さではなく、深い睡眠の量や中途覚醒の回数という「眠りの中身」を主役に語る。",
        requires=("deep_sleep_min", "awakenings", "total_sleep_min", "sleep_score"),
    ),
    Angle(
        "goal_gap",
        "目標までの残り距離",
        "設定した目標（成功率・減量）に対して今どこにいるか、あと何日分の我慢で届くかを語る。",
    ),
    Angle(
        "good_day_replay",
        "うまくいった日の再現",
        "数値が特に良かった日を取り上げ、その日と同じ選択を今夜も再現しようと促す。",
    ),
)

_ANGLES_BY_ID = {a.id: a for a in ANGLES}


def angle_by_id(angle_id: str) -> Angle | None:
    """id から Angle を引く（履歴の復元用）。"""
    return _ANGLES_BY_ID.get(angle_id)


def detect_metrics(text: str) -> list[str]:
    """本文が言及したバイタル指標を検出する（SC-004 計測用）。"""
    hits: list[str] = []
    for field in VITAL_FIELDS:
        meta = METRIC_META.get(field)
        if meta and any(kw in text for kw in meta.keywords):
            hits.append(field)
    return hits


def available_metrics(vitals: list) -> list[str]:
    """記憶上、値が1件でも存在する指標を VITAL_FIELDS 順で返す。"""
    return [
        f for f in VITAL_FIELDS
        if any(getattr(v, f, None) is not None for v in vitals)
    ]


def _topics_of(msg: PersuasionMessage) -> set[str]:
    """あるメッセージが扱った指標（意図した focus と、本文で実際に触れた指標の和）。"""
    return set(getattr(msg, "focus_metrics", []) or []) | set(msg.referenced_metrics or [])


def _effective_history(
    date: str, recent_messages: list[PersuasionMessage]
) -> list[PersuasionMessage]:
    """ローテーション判断に使う履歴。

    - 当日以降のメッセージは除く（同一日の再実行で自分自身を参照しないため）
    - ガードレール不通過（固定安全文面）は「その話題を使った」とみなさない
    - 新しい順
    """
    usable = [
        m for m in recent_messages
        if m.date < date and m.guardrail_result == GuardrailResult.PASS
    ]
    usable.sort(key=lambda m: m.date, reverse=True)
    return usable


def _days_between(newer: str, older: str) -> int:
    try:
        return (date_cls.fromisoformat(newer) - date_cls.fromisoformat(older)).days
    except ValueError:
        return _NEVER_USED


def _last_used_days(
    date: str, history: list[PersuasionMessage], topics_of
) -> dict[str, int]:
    """話題 -> 最後に使われてからの経過日数。未使用は _NEVER_USED。"""
    last: dict[str, int] = {}
    for msg in history:  # 新しい順なので最初に見つかったものが直近
        for topic in topics_of(msg):
            last.setdefault(topic, _days_between(date, msg.date))
    return last


def _pick(
    date: str, candidates: list[str], last_used: dict[str, int], count: int
) -> list[str]:
    """「久しく使っていない順」に count 件選ぶ。同点は日付でローテーションして崩す。"""
    if not candidates:
        return []
    n = len(candidates)
    seed = date_cls.fromisoformat(date).toordinal()
    ranked = sorted(
        enumerate(candidates),
        key=lambda pair: (-last_used.get(pair[1], _NEVER_USED), (pair[0] + seed) % n),
    )
    return [name for _, name in ranked[:count]]


def select_focus_metrics(
    date: str,
    available: list[str],
    recent_messages: list[PersuasionMessage],
    count: int = 2,
) -> list[str]:
    """今夜の主役にする指標を選ぶ。

    方針:
      1. 前夜に扱った指標は候補から外す（ユーザー要望「前日とは違うデータを見て喋る」）
      2. 残りを「最後に触れてから長い順」に並べ、未使用の指標を最優先で選ぶ
      3. 候補が足りないときだけ制約を緩める（データが1指標しかない場合など）
    """
    if not available:
        return []

    history = _effective_history(date, recent_messages)
    last_used = _last_used_days(date, history, _topics_of)

    yesterday_topics = _topics_of(history[0]) if history else set()
    fresh = [m for m in available if m not in yesterday_topics]
    # 前夜分を除くと候補が足りない場合のみ、除外を解いて全候補から選ぶ
    pool = fresh if len(fresh) >= min(count, len(available)) else list(available)

    return _pick(date, pool, last_used, count)


def select_angle(
    date: str,
    focus_metrics: list[str],
    has_reports: bool,
    recent_messages: list[PersuasionMessage],
) -> Angle:
    """今夜の語りの切り口を選ぶ（最も久しく使っていないもの）。

    候補は「今夜の主役指標と噛み合う切り口」に限る。
    例えば体組成の切り口は、体重・体脂肪率を主役に選んだ夜だけ使う
    （そうしないと「体重の話をしろ」と「睡眠を主役にしろ」が矛盾する）。
    """
    usable = [
        a for a in ANGLES
        if (not a.requires or any(m in focus_metrics for m in a.requires))
        and (not a.requires_reports or has_reports)
    ]
    if not usable:
        # 指標を問わない切り口（あれば）へ退避。それも無ければ先頭。
        usable = [a for a in ANGLES if not a.requires and not a.requires_reports] or [ANGLES[0]]

    history = _effective_history(date, recent_messages)
    last_used = _last_used_days(date, history, lambda m: {getattr(m, "angle", "") or ""})

    ids = [a.id for a in usable]
    chosen = _pick(date, ids, last_used, 1)
    return _ANGLES_BY_ID[chosen[0]] if chosen else usable[0]


def recently_used_metrics(
    date: str, recent_messages: list[PersuasionMessage], days: int = 3
) -> list[str]:
    """直近 days 日で既に扱った指標（プロンプトの「繰り返し禁止」指示に使う）。"""
    history = _effective_history(date, recent_messages)
    used: list[str] = []
    for msg in history:
        if _days_between(date, msg.date) > days:
            continue
        for topic in _topics_of(msg):
            if topic not in used:
                used.append(topic)
    return used
