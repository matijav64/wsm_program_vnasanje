import pytest
from wsm.utils import sanitize_folder_name


def test_invalid_chars_replaced():
    assert sanitize_folder_name("A<B>:C?") == "A_B__C_"

def test_trailing_dot_and_space_removed():
    assert sanitize_folder_name("Supplier. ") == "Supplier"

def test_non_string_raises_type_error():
    with pytest.raises(TypeError):
        sanitize_folder_name(None)

def test_reserved_name_modified():
    assert sanitize_folder_name("CON") == "CON_"


def test_control_chars_replaced():
    assert sanitize_folder_name("bad\x05name") == "bad_name"


def test_empty_returns_unknown():
    assert sanitize_folder_name("") == "unknown"
