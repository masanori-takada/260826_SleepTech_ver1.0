"""システムプロンプトと入力整形（仕様 第7節）。

LLM の役割は確定値の文章化のみ。数値・時刻を生成させてはならない。
プロンプトに埋め込んだ値以外の数値が本文に現れたら、ガードレールが弾く。
"""

from __future__ import annotations

from sleeptech.memory.schemas import (
    Baseline,
    BedtimeAdvice,
    DailyVital,
    HourlyDrop,
    RecoveryPlan,
)
from sleeptech.timefmt import fmt_hm, fmt_hm_plain

# 利用者の平日。提案がこの中に収まらないと実行されない。
LIFESTYLE = (
    "利用者は平日勤務の会社員です。\n"
    "9:00始業、12:00〜13:00が昼休み、17:30が定時、19:00ごろまで残業。\n"
    "夕食は20:30〜21:00に家族ととる。\n"
    "勤務中にできるのは、席でできる軽い運動（肩甲骨回し、スクワット、"
    "ふくらはぎ伸ばしなど）、深呼吸、短い立ち歩き、コーヒー1杯まで。\n"
    "仮眠が取れるのは昼休みだけ。勤務時間中の仮眠、ジム、長い外出はできない。\n"
    "コーヒーは1日1回まで。\n"
)

_COMMON_RULES = (
    "厳守事項:\n"
    "- 与えられた数値と時刻だけを使うこと。新しい数値・時刻を作ってはならない。\n"
    "- 与えられた数値は省略せず本文に含めること。\n"
    "- 範囲の両端と信頼度を変更・省略しないこと。\n"
    "- 時刻は与えられた表記のまま書くこと。「翌」が付いている時刻から「翌」を"
    "落としてはならない。\n"
    "- 医学的な診断・治療の断定をしないこと。\n"
    "- 「必ず」「確実に」「絶対に」は使わないこと。\n"
    "- 不安を過度に煽らないこと。人格を否定しないこと。\n"
    "- 日本語で、箇条書きを1〜2行使ってよい。\n"
)

NIGHT_SYSTEM = (
    "あなたは睡眠コーチです。計算済みの就寝シナリオ比較を、"
    "短く前向きな日本語に整えてください。確定本文の各行は変更・省略せず1回ずつ含めてください。\n"
    "夜の本文は400文字以内にしてください。\n"
    + _COMMON_RULES
)

MORNING_SYSTEM = (
    "あなたは睡眠コーチです。出力するのは「今日のリカバリー」の節だけです。\n"
    "睡眠実績や領域評価の行は書かないでください。\n"
    "出力形式（この形以外は書かない）:\n"
    "1行目: 今日のリカバリー\n"
    "2行目以降: ・領域名：その領域の課題を解決する行動\n"
    "厳守:\n"
    "- 材料に挙がった領域と同じ数・同じ順番で、1領域につき1行だけ書くこと。\n"
    "- 行頭は「・領域名：」の形にし、領域名は材料の表記をそのまま使うこと。\n"
    "- 各行に、その領域で使ってよいと示された時刻を1つか2つ入れること。"
    "3つ以上並べない。\n"
    "- 時刻は材料の「H:MM」表記のまま写すこと。丸めたり言い換えたりしない。\n"
    "- 1行につき行動は1つにすること。複数の動作を並べない。\n"
    "- 測定値の名前（実睡眠、中途覚醒、睡眠効率、REM、HRV、前日平均心拍、"
    "個人基準、低下見込み、平常時比）を本文に書かないこと。"
    "評価の説明ではなく、これからとる行動だけを書く。\n"
    "- % を書かないこと。材料にない数値・時刻を書かないこと。\n"
    "- 行動のジャンルは問わない（食事、入浴、仮眠、光、カフェイン、"
    "仕事の段取り、環境の調整、体を動かすことなど）。"
    "その領域の課題を実際に解けるものを選ぶこと。\n"
    "- 何をどれだけやるかが読んだだけで分かる粒度にすること。"
    "「リラックスする」のような曖昧な指示にしない。\n"
    "- 所要時間か回数を必ず入れること（例: 15分、5回、1件）。\n"
    "- その時刻を選ぶ理由を短く添えること（例: 谷に入る前に、就寝から逆算して）。\n"
    "- 材料に示された時刻はすべて今より後の時刻。過去の時刻を書いてはならない。\n"
    "- 毎日同じ組み合わせにならないよう、その日の評価に合わせて内容を変えること。\n"
    "\n"
    + LIFESTYLE
    + _COMMON_RULES
)

JUDGE_SYSTEM = (
    "あなたは睡眠コーチの提案を採点する評価者です。甘く付けないでください。\n"
    "出力はJSONオブジェクト1個だけ。前置き・説明・コードフェンスを書かないこと。\n"
    '形式: {"relevance":0-5,"feasibility":0-5,"timing":0-5,"reason":"40字以内"}\n'
    "各軸は整数。提案が複数あるときは、最も弱い提案に合わせて採点すること。\n"
    "\n"
    + LIFESTYLE
    + "\n"
    "relevance（その領域の困りごとに直接効くか）\n"
    "0: 領域と無関係、または状態を悪化させる\n"
    "1: 話題は近いが課題の中心を外している\n"
    "2: 睡眠一般には良いが、その領域固有の課題には効かない\n"
    "3: 課題の一部に効く。主要因ではない側面を扱っている\n"
    "4: 課題の中心に効き、仕組みとして妥当\n"
    "5: 課題の中心に効き、なぜ今日のその評価に効くのかが本文から読み取れる\n"
    "\n"
    "feasibility（その時刻の生活状況で、実際にその場でできるか）\n"
    "0: その時刻の予定と両立しない。"
    "勤務時間中の仮眠、会議中の外出、夕食時間に別の予定を入れる、"
    "コーヒーを1日2回以上、退勤前にジムへ行く、など\n"
    "1: 理屈の上ではできるが、平日にその時間を確保するのは現実的でない\n"
    "2: できるが「何をどれだけ」が曖昧で、本人が解釈で補う必要がある\n"
    "3: その時刻の状況でできる。ただし場所や周囲の目で気まずさが残る\n"
    "4: 席や自宅でそのまま始められ、5〜20分以内に終わる。"
    "所要時間か回数が書かれている\n"
    "5: 4に加え、通勤・昼休み・夕食・入浴など既にある予定に重ねられ、"
    "新しく時間を作らなくてよい\n"
    "\n"
    "timing（その時刻に行う生理的な意味があるか）\n"
    "0: 現在時刻より前を指している、その時刻に行うと有害"
    "（就寝直前のカフェインや強い運動、夜の強い光など）、"
    "または提案どうしの時刻が矛盾している\n"
    "1: 実行はできるが、時刻と行動が噛み合わず効果が期待できない\n"
    "2: 不自然ではないが、その時刻である必然性が読み取れない\n"
    "3: おおむね妥当な時間帯で、今から無理なく実行できる\n"
    "4: その行動の効果が最も出る時間帯で、今夜の就寝を妨げない\n"
    "5: 4に加え、なぜその時刻なのかが本文の言葉で示されている\n"
    "\n"
    "feasibility が最も重い軸です。次の順に確認してください。\n"
    "1) すべての時刻が現在時刻より後か（1つでも過去なら timing は0）\n"
    "2) その時刻の予定の中で本当に実行できるか（できないなら feasibility は0）\n"
    "3) その行動をその時間帯に行う生理的な意味があるか\n"
)


def morning_recovery_user(recovery_material: str) -> str:
    """リカバリー材料をLLMへ渡す。LLMは数値計算を行わない。"""
    return recovery_material


def judge_user(
    domain_lines: tuple[str, ...], section: str, now_h: float | None = None
) -> str:
    """採点対象と、その根拠になる領域評価、そして現在時刻を渡す。

    睡眠実績や生成時の指示は渡さない。採点が睡眠の良し悪しや
    自分の書いた指示に引きずられるのを避けるため。
    現在時刻だけは渡す。これが無いと「すでに過ぎた時刻の提案」を
    妥当と誤判定するため（実機で朝9:30に5:50の提案を通してしまった）。
    """
    lines = []
    if now_h is not None:
        lines.append(f"現在時刻: {fmt_hm_plain(now_h)}")
        lines.append("")
    lines.append("対象の領域と評価:")
    lines.extend(f"- {line}" for line in domain_lines)
    lines.extend(["", "採点対象:", section])
    return "\n".join(lines)


def night_scenarios_user(canonical_body: str) -> str:
    """計算済みの夜シナリオ本文をLLMへ渡す。"""
    return canonical_body


def night_user(advice: BedtimeAdvice, baseline: Baseline, debt_h: float,
               range_low=None, range_high=None, confidence=None, feedback_count=None) -> str:
    """夜フローのユーザープロンプト。確定値のみを列挙する。"""
    status_note = {
        "ok": "指定睡眠時間を確保できる時刻までに就寝する",
        "unreachable": "指定睡眠時間を確保できる時刻に届かないため早めに就寝する",
        "too_late": "指定睡眠時間の確保が難しい時間帯のため今すぐ就寝する",
    }[advice.status]

    lines = [f"推奨就寝時刻: {fmt_hm(advice.bedtime_h)}"]
    lines.extend([
        f"起床目安: {fmt_hm_plain(baseline.wake_h)}",
        f"目標睡眠時間: {baseline.effective_target_sleep_h:.1f}時間",
    ])
    if advice.status == "too_late" and advice.available_sleep_h is not None:
        hours, minutes = divmod(round(advice.available_sleep_h * 60), 60)
        lines.extend([
            "目標時刻は過ぎています。今すぐ寝る",
            f"今から確保できる見込み: {hours}時間{minutes:02d}分",
        ])
    # I4対応: 低下率0%のときは % を本文に書かせない（「0%」の明記を必須にすると
    # 平常な晩ほど不自然な文章を強いることになり、ガードレールにも落ちやすくなる）。
    if range_low is not None and range_high is not None:
        lines.append(f"明日の主要時間帯の低下見込み: {range_low}〜{range_high}%")
        label = {"low": "低", "medium": "中", "high": "高"}.get(confidence, confidence)
        lines.append(f"信頼度: {label}（評価データ{feedback_count}件）")
    elif advice.mean_drop_pct != 0:
        lines.append(f"明日の主要時間帯の平均低下率: -{advice.mean_drop_pct}%")
    else:
        lines.append("明日の主要時間帯の平均低下率: ほぼ低下なし（%の数値は本文に書かないこと）")
    lines.append(f"状況: {status_note}")
    lines.append(f"睡眠負債: {debt_h:.1f}時間")
    if baseline.is_default:
        lines.append("注意: 履歴が少なく精度が低いことを一言添えること")
    if range_low is not None and range_high is not None:
        lines.extend([
            "※睡眠モデルと主観評価による推定の目安（範囲）です",
            "実測の仕事能力ではありません",
            "中途覚醒・HRV・心拍数は現行の%計算には使っていません",
        ])
    return "\n".join(lines)


def morning_user(curve: list[HourlyDrop], recovery_plan: RecoveryPlan, vital: DailyVital,
                 ranges=None, confidence=None, feedback_count=None) -> str:
    """朝フローのユーザープロンプト。確定値のみを列挙する。"""
    lines: list[str] = []

    if vital.total_sleep_min is not None:
        h, m = divmod(int(vital.total_sleep_min), 60)
        lines.append(f"昨夜の睡眠: {h}時間{m}分")
    if vital.deep_sleep_min is not None:
        lines.append(f"深い睡眠: {vital.deep_sleep_min}分")
    if vital.awakenings is not None:
        lines.append(f"中途覚醒: {vital.awakenings}回")

    lines.append("今日の見込み:")
    for i, d in enumerate(curve):
        if ranges is None:
            lines.append(f"  {d.hour}時台: 集中力 -{d.focus_pct}% / 気分 -{d.mood_pct}%")
        else:
            low, high = ranges[i]
            lines.append(f"{d.hour}時台 低下見込み{low}〜{high}%")
    if ranges is not None:
        label = {"low": "低", "medium": "中", "high": "高"}.get(confidence, confidence)
        lines.append(f"信頼度: {label}（評価データ{feedback_count}件）")
        lines.extend([
            "実測の仕事能力ではありません",
            "中途覚醒・HRV・心拍数は現行の%計算には使っていません",
        ])

    if recovery_plan.caffeine_times_h:
        times = "、".join(fmt_hm_plain(t) for t in recovery_plan.caffeine_times_h)
        lines.append(f"カフェイン推奨時刻: {times}")
        lines.append(f"カフェイン最終摂取時刻: {fmt_hm_plain(recovery_plan.caffeine_cutoff_h)}")
    else:
        lines.append("カフェインは今日は勧めない")

    lines.append(f"朝の屋外光: {fmt_hm_plain(recovery_plan.light_morning_h)}までに10分")
    lines.append(f"避光の開始: {fmt_hm_plain(recovery_plan.light_avoid_after_h)}")
    return "\n".join(lines)
