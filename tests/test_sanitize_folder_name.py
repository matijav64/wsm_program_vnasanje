import pytest
from wsm.utils import sanitize_folder_name


def test_invalid_chars_replaced():
    assert sanitize_folder_name("A<B>:C?") == "A_B__C_"

def test_trailing_dot_and_space_removed():
    assert sanitize_folder_name("Supplier. ") == "Supplier"

def test_non_string_raises_type_error():
    with pytest.raises(TypeError):
        sanitize_folder_name(None)
