from malt.icons import _VIVID_PALETTE, _letter_and_color


def test_fallback_avatar_color_is_deterministic_and_vivid():
    first = _letter_and_color("Project", "stable-id")
    assert first == _letter_and_color("Project", "stable-id")
    assert first[0] == "P"
    assert first[1] in _VIVID_PALETTE
    assert max(first[1]) >= 0.48
