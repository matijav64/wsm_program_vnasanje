import importlib
import os

import pandas as pd

import wsm.ui.review.gui as rl


def test_header_line_filter():
    df = pd.DataFrame(
        {
            "naziv": ["Racun 123", "Racun 456"],
            "kolicina_norm": [0, 1],
            "vrednost": [0, 0],
        }
    )
    mask = rl._mask_header_like_rows(df)
    assert mask.tolist() == [True, False]
    df_filtered = df.loc[~mask]
    assert df_filtered["naziv"].tolist() == ["Racun 456"]


def test_header_line_filter_diacritics():
    df = pd.DataFrame(
        {
            "naziv": ["Račun 123", "Racun 456"],
            "kolicina_norm": [0, 1],
            "vrednost": [0, 0],
        }
    )
    mask = rl._mask_header_like_rows(df)
    assert mask.tolist() == [True, False]


def test_header_line_filter_storno():
    df = pd.DataFrame(
        {
            "naziv": ["Storno 1", "Artikel"],
            "kolicina_norm": [0, 1],
            "vrednost": [0, 0],
        }
    )
    mask = rl._mask_header_like_rows(df)
    assert mask.tolist() == [True, False]


def test_header_line_filter_bremepis():
    df = pd.DataFrame(
        {
            "naziv": ["Bremepis 7", "Artikel"],
            "kolicina_norm": [0, 1],
            "vrednost": [0, 0],
        }
    )
    mask = rl._mask_header_like_rows(df)
    assert mask.tolist() == [True, False]


def test_header_line_filter_toggle_off(monkeypatch):
    df = pd.DataFrame(
        {
            "naziv": ["Racun 123"],
            "kolicina_norm": [0],
            "vrednost": [0],
        }
    )
    mask = rl._mask_header_like_rows(df)
    monkeypatch.setenv("WSM_HIDE_HEADER_LINES", "0")
    # simulate STEP0.5 behaviour
    if os.environ.get("WSM_HIDE_HEADER_LINES", "1") != "0":
        df_filtered = df.loc[~mask]
    else:
        df_filtered = df
    assert df_filtered.equals(df)


def test_header_line_filter_custom_prefix(monkeypatch):
    df = pd.DataFrame(
        {
            "naziv": ["Dobavnica 1", "Racun 2"],
            "kolicina_norm": [0, 0],
            "vrednost": [0, 0],
        }
    )
    monkeypatch.setenv("WSM_HEADER_PREFIX", r"(?i)^\s*Dobavnica")
    importlib.reload(rl)
    try:
        mask = rl._mask_header_like_rows(df)
        assert mask.tolist() == [True, False]
    finally:
        monkeypatch.delenv("WSM_HEADER_PREFIX", raising=False)
        importlib.reload(rl)
