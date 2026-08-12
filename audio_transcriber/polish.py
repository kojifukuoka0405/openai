"""文字起こし結果の推敲（読みやすい文章に整える）。

素の文字起こしは「言ったとおり」なので、フィラー（えー/あの）、言い直し、
句読点の欠落、改行なしの長文などが残る。ここでは GPT-5.6 に
**内容を変えずに** 整形させ、読み物として通用する版を作る。

長いテキストは文単位で分割して順に処理し、直前の推敲結果の末尾を文脈として
渡すことで、文体や表記が途中で変わらないようにしている。
"""

from __future__ import annotations

import re
from typing import Callable, List, Optional

# 高性能なものから順に試す
DEFAULT_POLISH_MODEL = "gpt-5.6-sol"
POLISH_FALLBACKS = ["gpt-5.6-sol", "gpt-5.4", "gpt-5.1", "gpt-4.1"]

# 1 回のリクエストで推敲する文字数の目安
CHUNK_CHARS = 3000
# 文脈として渡す直前の推敲結果の末尾
CONTEXT_TAIL_CHARS = 400
# 出力トークンの上限（推論トークンを含む）
MAX_OUTPUT_TOKENS = 12000
REQUEST_TIMEOUT = 600.0

ProgressFn = Callable[[int, int, str], None]

STYLE_PRESETS = {
    "readable": "読みやすい書き言葉",
    "verbatim": "話し言葉のまま最小限の整形",
    "article": "記事・議事録として通用する構成",
}

BASE_INSTRUCTIONS = """あなたは音声の文字起こしを整える、経験豊富な編集者です。
渡された文字起こしを、意味を一切変えずに読みやすく推敲してください。

必ず守ること:
- 入力と同じ言語で出力する（日本語なら日本語のまま）。
- 事実・数値・固有名詞・主張を追加、削除、変更しない。要約もしない。
- 話されていない内容を補わない。推測で言葉を足さない。
- 聞き取れなかった箇所（[不明] など）はそのまま残す。

行うこと:
- フィラー（えー、あのー、まあ、um, uh など）と、意味のない繰り返しを削除する。
- 言い直しは、話し手が最終的に言いたかった形に整える。
- 句読点・かぎ括弧・改行を補い、話題ごとに段落を分ける。
- 明らかな音声認識の誤変換（同音異義語など）を、文脈から自然な表記に直す。
- 表記ゆれ（数字・英数字・カタカナ語）を文書内でそろえる。
- 話者が区別されている場合はその区別を保つ。

出力は推敲後の本文のみ。前置き・見出しの説明・コードブロックは付けない。"""

STYLE_INSTRUCTIONS = {
    "readable": "文体は自然な書き言葉に整えます。段落は 2〜5 文程度を目安にします。",
    "verbatim": (
        "話し言葉のニュアンスは残し、フィラーの削除と句読点・改行の追加だけに"
        "とどめます。語順や語尾は極力変えません。"
    ),
    "article": (
        "読み物として通る構成にします。話題の切れ目には Markdown の見出し"
        "（## 見出し）を付け、箇条書きが自然な箇所はリストにします。"
        "ただし本文の内容は追加も削除もしません。"
    ),
}


class PolishError(RuntimeError):
    """推敲に失敗したときのエラー。"""


def split_for_polish(text: str, chunk_chars: int = CHUNK_CHARS) -> List[str]:
    """推敲用に、文の切れ目でテキストを分割する。"""
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_chars:
        return [text]

    # 文末（。！？.!?）または改行で区切る
    pieces = re.split(r"(?<=[。！？!?\n])", text)
    chunks: List[str] = []
    current = ""
    for piece in pieces:
        if not piece:
            continue
        if current and len(current) + len(piece) > chunk_chars:
            chunks.append(current.strip())
            current = piece
        else:
            current += piece
        # 1 文が極端に長い場合はそのまま出す
        while len(current) > chunk_chars * 2:
            chunks.append(current[:chunk_chars].strip())
            current = current[chunk_chars:]
    if current.strip():
        chunks.append(current.strip())
    return [c for c in chunks if c]


def build_instructions(style: str = "readable", extra: Optional[str] = None) -> str:
    """推敲の指示文を組み立てる。"""
    parts = [BASE_INSTRUCTIONS]
    style_note = STYLE_INSTRUCTIONS.get(style)
    if style_note:
        parts.append(style_note)
    if extra:
        parts.append(f"追加の指示: {extra.strip()}")
    return "\n\n".join(parts)


def _build_input(chunk: str, context: str, position: Optional[str]) -> str:
    parts = []
    if context:
        parts.append(
            "【直前までの推敲済みテキスト（文体をそろえる参考。出力には含めない）】\n"
            + context
        )
    if position:
        parts.append(f"【この部分について】{position}")
    parts.append("【推敲する文字起こし】\n" + chunk)
    return "\n\n".join(parts)


def _responses_call(client, model: str, instructions: str, user_input: str, budget: int):
    response = client.responses.create(
        model=model,
        instructions=instructions,
        input=user_input,
        max_output_tokens=budget,
        reasoning={"effort": "low"},
        timeout=REQUEST_TIMEOUT,
    )
    text = (getattr(response, "output_text", "") or "").strip()
    incomplete = getattr(response, "status", None) == "incomplete"
    return text, incomplete


def _chat_call(client, model: str, instructions: str, user_input: str, budget: int):
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": instructions},
            {"role": "user", "content": user_input},
        ],
        timeout=REQUEST_TIMEOUT,
    )
    choice = response.choices[0]
    text = (choice.message.content or "").strip()
    incomplete = getattr(choice, "finish_reason", None) == "length"
    return text, incomplete


def _is_model_missing(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    return status == 404 or "model_not_found" in text or "does not exist" in text


def polish_chunk(client, model: str, instructions: str, user_input: str) -> str:
    """1 断片を推敲する。Responses API が使えなければ Chat Completions に落とす。"""
    budget = MAX_OUTPUT_TOKENS
    last_error: Optional[Exception] = None
    for _attempt in range(2):
        for call in (_responses_call, _chat_call):
            try:
                text, incomplete = call(client, model, instructions, user_input, budget)
            except Exception as exc:  # noqa: BLE001 - SDK の例外型に依存しない
                if _is_model_missing(exc):
                    raise
                last_error = exc
                continue
            if text and not incomplete:
                return text
            if incomplete:
                last_error = PolishError("出力が途中で終了しました。")
                break            # 予算を増やして最初からやり直す
            last_error = PolishError("推敲結果が空でした。")
        budget *= 2
    raise PolishError(f"推敲に失敗しました: {last_error}")


def polish_text(
    client,
    text: str,
    model: str = DEFAULT_POLISH_MODEL,
    style: str = "readable",
    extra_instructions: Optional[str] = None,
    progress: Optional[ProgressFn] = None,
    allow_fallback: bool = True,
) -> str:
    """文字起こし全体を推敲する。"""
    chunks = split_for_polish(text)
    if not chunks:
        return ""

    instructions = build_instructions(style, extra_instructions)
    candidates = [model]
    if allow_fallback:
        candidates += [m for m in POLISH_FALLBACKS if m != model]

    total = len(chunks)
    active_model = candidates[0]
    tried: List[str] = []
    outputs: List[str] = []

    index = 0
    while index < total:
        chunk = chunks[index]
        context = outputs[-1][-CONTEXT_TAIL_CHARS:] if outputs else ""
        position = None
        if total > 1:
            position = f"全 {total} 分割のうち {index + 1} 番目です。前後は別途つながります。"
        if progress is not None:
            progress(index, total, f"推敲中 [{index + 1}/{total}] ({active_model})")

        try:
            outputs.append(
                polish_chunk(
                    client, active_model, instructions,
                    _build_input(chunk, context, position),
                )
            )
        except Exception as exc:  # noqa: BLE001
            if _is_model_missing(exc):
                tried.append(active_model)
                remaining = [m for m in candidates if m not in tried]
                if remaining:
                    active_model = remaining[0]
                    if progress is not None:
                        progress(index, total, f"{tried[-1]} が使えないため {active_model} に切り替えます")
                    continue
            raise
        index += 1

    if progress is not None:
        progress(total, total, "推敲完了")
    return "\n\n".join(o for o in outputs if o).strip()
