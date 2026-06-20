"""Unit tests for caption composition (stat line + summary card)."""

from __future__ import annotations

from cerebellum_cua.tutorial.captions import (
    build_drawtext_filter,
    compose_caption,
    summary_card,
)


def test_compose_caption_plain_is_just_the_caption() -> None:
    assert compose_caption({"caption": "hello"}) == "hello"


def test_compose_caption_omits_token_line_for_zero_cost_steps() -> None:
    # A pause (no tokens, no shot cost) shows only its caption, not "~0 tok".
    text = compose_caption({"caption": "intro", "tokens": 0, "full_tokens": 0})
    assert text == "intro"


def test_compose_caption_includes_perceived_and_three_way_tokens() -> None:
    text = compose_caption({
        "caption": "Click Open", "perceived": "BUTTON 'Open'",
        "tokens": 420, "shot_tokens": 6, "full_tokens": 1365,
    })
    assert "Click Open" in text
    assert "BUTTON 'Open'" in text
    assert "420" in text and "1365" in text and "6" in text


def test_summary_card_shows_totals_and_ratio() -> None:
    card = summary_card({"a11y_tokens": 1240, "shot_tokens": 720,
                         "full_tokens": 5460})
    assert "1240" in card and "5460" in card
    assert "x" in card.lower()  # ratio like "4.4x"


def test_build_filter_uses_composed_caption_text() -> None:
    # A stat-bearing entry must render its perceived line into the drawtext chain.
    flt = build_drawtext_filter([
        {"caption": "Click Open", "start": 0.0, "end": 2.0,
         "perceived": "BUTTON 'Open'", "tokens": 420, "full_tokens": 1365},
    ])
    assert "perceived" in flt


def test_drawtext_folds_apostrophes_and_keeps_newlines() -> None:
    from cerebellum_cua.tutorial.captions import build_drawtext_filter
    flt = build_drawtext_filter([
        {"caption": "perceived: BUTTON 'Menu'\nmatrix ~88 tok",
         "start": 0.0, "end": 2.0},
    ])
    assert "'Menu'" not in flt        # no raw ASCII apostrophes (would break ffmpeg)
    assert "’Menu’" in flt            # folded to typographic
    assert "\\:" in flt               # colon escaped (renders clean)
