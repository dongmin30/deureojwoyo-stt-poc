import pytest

from app.metrics import cer, normalize_for_cer


def test_normalize_removes_space_and_punct():
    assert normalize_for_cer(" 밥 뭇나? 응, 먹었다! ") == "밥뭇나응먹었다"


def test_cer_identical_ignoring_space_punct():
    assert cer("밥 뭇나?", "밥뭇나") == 0.0


def test_cer_one_substitution():
    assert cer("밥 뭇나", "밥 묻나") == pytest.approx(1 / 3, abs=1e-3)


def test_cer_empty_reference_is_none():
    assert cer("", "아무거나") is None
    assert cer(None, "아무거나") is None
    assert cer(" ?! ", "아무거나") is None


def test_cer_empty_hypothesis_is_one():
    assert cer("밥 뭇나", "") == 1.0
    assert cer("밥 뭇나", None) == 1.0
