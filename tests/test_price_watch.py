import json
import pandas as pd
from wsm.ui.price_watch import (
    _load_price_histories,
    clear_price_cache,
    PriceWatch,
)


def test_load_price_histories(tmp_path):
    clear_price_cache()
    links = tmp_path / "links"
    s1 = links / "Sup1"
    s2 = links / "Sup2"
    s1.mkdir(parents=True)
    s2.mkdir(parents=True)

    (s1 / "supplier.json").write_text(json.dumps({"sifra": "S1", "ime": "Sup1"}))
    (s2 / "supplier.json").write_text(json.dumps({"sifra": "S2", "ime": "Sup2"}))

    df1 = pd.DataFrame({
        "key": ["S1_ItemA"],
        "line_netto": [1],
        "unit_price": [pd.NA],
        "time": [pd.Timestamp("2023-01-01")],
    })
    df2 = pd.DataFrame({
        "key": ["S2_ItemB"],
        "line_netto": [2],
        "unit_price": [pd.NA],
        "time": [pd.Timestamp("2023-01-02")],
    })
    df1.to_excel(s1 / "price_history.xlsx", index=False)
    df2.to_excel(s2 / "price_history.xlsx", index=False)

    items = _load_price_histories(links)
    assert set(items.keys()) == {"S1", "S2"}
    assert set(items["S1"].keys()) == {"S1 - ItemA"}
    assert set(items["S2"].keys()) == {"S2 - ItemB"}
    df_loaded = items["S1"]["S1 - ItemA"]
    assert {"line_netto", "unit_price", "enota_norm"}.issubset(df_loaded.columns)


def test_load_price_histories_non_datetime(tmp_path):
    clear_price_cache()
    links = tmp_path / "links"
    s1 = links / "Sup1"
    s1.mkdir(parents=True)

    (s1 / "supplier.json").write_text(json.dumps({"sifra": "S1", "ime": "Sup1"}))

    df = pd.DataFrame(
        {
            "key": ["S1_ItemA", "S1_ItemA"],
            "line_netto": [1, 2],
            "unit_price": [pd.NA, pd.NA],
            "time": [pd.Timestamp("2023-01-01"), "not-a-date"],
        }
    )
    df.to_excel(s1 / "price_history.xlsx", index=False)

    items = _load_price_histories(links)
    item_df = items["S1"]["S1 - ItemA"]
    assert len(item_df) == 1
    assert item_df["time"].iloc[0] == pd.Timestamp("2023-01-01")


def test_load_price_histories_missing_file(tmp_path):
    clear_price_cache()
    links = tmp_path / "links"
    s1 = links / "Sup1"
    s1.mkdir(parents=True)
    (s1 / "supplier.json").write_text(json.dumps({"sifra": "S1", "ime": "Sup1"}))

    items = _load_price_histories(links)
    assert items == {}


def test_load_price_histories_vat_dir(tmp_path):
    clear_price_cache()
    links = tmp_path / "links"
    sup = links / "SI123"
    sup.mkdir(parents=True)
    (sup / "supplier.json").write_text(
        json.dumps({"sifra": "SUP", "ime": "Supplier", "vat": "SI123"})
    )

    df = pd.DataFrame(
        {
            "key": ["SUP_ItemA"],
            "line_netto": [1],
            "unit_price": [pd.NA],
            "time": [pd.Timestamp("2023-01-01")],
        }
    )
    df.to_excel(sup / "price_history.xlsx", index=False)

    items = _load_price_histories(links)
    assert set(items.keys()) == {"SUP"}
    assert set(items["SUP"].keys()) == {"SUP - ItemA"}


def test_show_graph_sets_xticks(monkeypatch):
    df = pd.DataFrame(
        {
            "time": [
                pd.Timestamp("2023-01-01"),
                pd.Timestamp("2023-01-02"),
                pd.Timestamp("2023-01-03"),
            ],
            "unit_price": [1, 2, 3],
        }
    )

    import types, sys

    xticks_capture = {}

    class FakeFig:
        def __init__(self):
            self.autofmt_called = False

        def autofmt_xdate(self, *a, **k):
            self.autofmt_called = True

    class FakeAx:
        def __init__(self):
            self.xticks = None
            self.ylim = None
            self.locator = None
            self.formatter = None
            self.xaxis = self
            self.yaxis = self
            self.lines = []

        def plot(self, *a, **k):
            self.lines.append("line")

        def set_xlabel(self, *a):
            pass

        def set_ylabel(self, *a):
            pass

        def grid(self, *a, **k):
            pass

        def set_xticks(self, ticks):
            self.xticks = list(ticks)

        def set_major_locator(self, loc):
            self.locator = loc

        def set_major_formatter(self, fmt):
            self.formatter = fmt

        def set_ylim(self, ymin, ymax):
            self.ylim = (ymin, ymax)

        def margins(self, *a, **k):
            pass

        def get_lines(self):
            return self.lines

    def fake_subplots(*args, **kwargs):
        fig = FakeFig()
        ax = FakeAx()
        xticks_capture["fig"] = fig
        xticks_capture["ax"] = ax
        return fig, ax

    class FakeCanvas:
        def __init__(self, fig, master=None):
            pass

        def draw(self):
            pass

        def get_tk_widget(self):
            class W:
                def pack(self, *a, **k):
                    pass

            return W()

    fake_plt = types.SimpleNamespace(subplots=fake_subplots)
    fake_backend = types.SimpleNamespace(FigureCanvasTkAgg=FakeCanvas)
    fake_backends = types.SimpleNamespace(**{"backend_tkagg": fake_backend})
    fake_dates = types.SimpleNamespace(
        AutoDateLocator=lambda: "LOC",
        ConciseDateFormatter=lambda loc: f"FMT-{loc}",
    )
    fake_ticker = types.SimpleNamespace(FuncFormatter=lambda func: ("FF", func))
    fake_mplcursors = types.SimpleNamespace(
        cursor=lambda *a, **k: types.SimpleNamespace(connect=lambda *a, **k: None)
    )
    fake_matplotlib = types.ModuleType("matplotlib")
    fake_matplotlib.pyplot = fake_plt
    fake_matplotlib.backends = fake_backends
    fake_matplotlib.dates = fake_dates
    fake_matplotlib.ticker = fake_ticker
    monkeypatch.setitem(sys.modules, "matplotlib", fake_matplotlib)
    monkeypatch.setitem(sys.modules, "matplotlib.pyplot", fake_plt)
    monkeypatch.setitem(sys.modules, "matplotlib.backends", fake_backends)
    monkeypatch.setitem(sys.modules, "matplotlib.backends.backend_tkagg", fake_backend)
    monkeypatch.setitem(sys.modules, "matplotlib.dates", fake_dates)
    monkeypatch.setitem(sys.modules, "matplotlib.ticker", fake_ticker)
    monkeypatch.setitem(sys.modules, "mplcursors", fake_mplcursors)

    class FakeTop:
        def __init__(self, master=None):
            pass

        def title(self, t):
            pass

        def bind(self, *a, **k):
            pass

        def destroy(self):
            pass

    class FakeButton:
        def __init__(self, master, text=None, command=None):
            pass

        def pack(self, *a, **k):
            pass

    monkeypatch.setattr("wsm.ui.price_watch.tk.Toplevel", FakeTop)
    monkeypatch.setattr("wsm.ui.price_watch.ttk.Button", FakeButton)
    monkeypatch.setattr("wsm.ui.price_watch.tk.BOTH", "both", raising=False)
    monkeypatch.setattr("wsm.ui.price_watch.messagebox.showerror", lambda *a, **k: None)

    pw = PriceWatch.__new__(PriceWatch)
    pw._show_graph("Item", df)

    assert xticks_capture["ax"].locator is not None
    assert xticks_capture["ax"].formatter is not None
    assert xticks_capture["fig"].autofmt_called
    expected_pad = (df["unit_price"].max() - df["unit_price"].min()) * 0.10
    assert xticks_capture["ax"].ylim == (
        df["unit_price"].min() - expected_pad,
        df["unit_price"].max() + expected_pad,
    )


def test_show_graph_single_value(monkeypatch):
    df = pd.DataFrame({"time": [pd.Timestamp("2023-01-01")], "unit_price": [5]})

    import types, sys

    cap = {}

    class FakeFig:
        def __init__(self):
            pass

        def autofmt_xdate(self, *a, **k):
            cap["auto"] = True

    class FakeAx:
        def __init__(self):
            self.ylim = None
            self.locator = None
            self.formatter = None
            self.xaxis = self
            self.yaxis = self
            self.lines = []

        def plot(self, *a, **k):
            self.lines.append("line")

        def set_xlabel(self, *a):
            pass

        def set_ylabel(self, *a):
            pass

        def grid(self, *a, **k):
            pass

        def set_xticks(self, ticks):
            cap["ticks"] = list(ticks)

        def set_ylim(self, ymin, ymax):
            self.ylim = (ymin, ymax)

        def set_major_locator(self, loc):
            self.locator = loc

        def set_major_formatter(self, fmt):
            self.formatter = fmt

        def margins(self, *a, **k):
            pass

        def get_lines(self):
            return self.lines

    def fake_subplots(*args, **kwargs):
        fig = FakeFig()
        ax = FakeAx()
        cap["ax"] = ax
        return fig, ax

    class FakeCanvas:
        def __init__(self, fig, master=None):
            pass

        def draw(self):
            pass

        def get_tk_widget(self):
            class W:
                def pack(self, *a, **k):
                    pass

            return W()

    fake_plt = types.SimpleNamespace(subplots=fake_subplots)
    fake_backend = types.SimpleNamespace(FigureCanvasTkAgg=FakeCanvas)
    fake_backends = types.SimpleNamespace(**{"backend_tkagg": fake_backend})
    fake_dates = types.SimpleNamespace(
        AutoDateLocator=lambda: "LOC",
        ConciseDateFormatter=lambda loc: f"FMT-{loc}",
    )
    fake_ticker = types.SimpleNamespace(FuncFormatter=lambda func: ("FF", func))
    fake_mplcursors = types.SimpleNamespace(
        cursor=lambda *a, **k: types.SimpleNamespace(connect=lambda *a, **k: None)
    )
    fake_matplotlib = types.ModuleType("matplotlib")
    fake_matplotlib.pyplot = fake_plt
    fake_matplotlib.backends = fake_backends
    fake_matplotlib.dates = fake_dates
    fake_matplotlib.ticker = fake_ticker
    monkeypatch.setitem(sys.modules, "matplotlib", fake_matplotlib)
    monkeypatch.setitem(sys.modules, "matplotlib.pyplot", fake_plt)
    monkeypatch.setitem(sys.modules, "matplotlib.backends", fake_backends)
    monkeypatch.setitem(sys.modules, "matplotlib.backends.backend_tkagg", fake_backend)
    monkeypatch.setitem(sys.modules, "matplotlib.dates", fake_dates)
    monkeypatch.setitem(sys.modules, "matplotlib.ticker", fake_ticker)
    monkeypatch.setitem(sys.modules, "mplcursors", fake_mplcursors)

    class FakeTop:
        def __init__(self, master=None):
            pass

        def title(self, t):
            pass

        def bind(self, *a, **k):
            pass

        def destroy(self):
            pass

    class FakeButton:
        def __init__(self, master, text=None, command=None):
            pass

        def pack(self, *a, **k):
            pass

    monkeypatch.setattr("wsm.ui.price_watch.tk.Toplevel", FakeTop)
    monkeypatch.setattr("wsm.ui.price_watch.ttk.Button", FakeButton)
    monkeypatch.setattr("wsm.ui.price_watch.tk.BOTH", "both", raising=False)
    monkeypatch.setattr("wsm.ui.price_watch.messagebox.showerror", lambda *a, **k: None)

    pw = PriceWatch.__new__(PriceWatch)
    pw._show_graph("Item", df)

    ax = cap["ax"]
    pad = abs(float(df["unit_price"].iloc[0])) * 0.03
    if pad == 0:
        pad = 0.10
    assert ax.ylim == (float(df["unit_price"].iloc[0]) - pad, float(df["unit_price"].iloc[0]) + pad)


def test_refresh_table_empty(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "wsm.ui.price_watch.messagebox.showinfo",
        lambda *a, **k: calls.append(a),
    )

    class DummyVar:
        def __init__(self, value=""):
            self.val = value

        def get(self):
            return self.val

    class DummyTree:
        def __init__(self):
            self.inserted = []

        def get_children(self):
            return []

        def delete(self, *a):
            pass

        def insert(self, parent, index, values):
            self.inserted.append(values)

    df = pd.DataFrame({"line_netto": [1], "time": [pd.Timestamp("2023-01-01")]})

    pw = PriceWatch.__new__(PriceWatch)
    pw.tree = DummyTree()
    pw.supplier_codes = {"S1 - Sup": "S1"}
    pw.sup_var = DummyVar("S1 - Sup")
    pw.search_var = DummyVar("missing")
    pw.items_by_supplier = {"S1": {"Item": df}}
    pw._sort_col = None
    pw._sort_reverse = False

    pw._refresh_table()

    assert calls and calls[0][0] == "Ni podatkov"
    assert pw.tree.inserted == []


def test_refresh_table_with_data(monkeypatch):
    class DummyVar:
        def __init__(self, value=""):
            self.val = value

        def get(self):
            return self.val

    class DummyTree:
        def __init__(self):
            self.inserted = []

        def get_children(self):
            return []

        def delete(self, *a):
            pass

        def insert(self, parent, index, values):
            self.inserted.append(values)

    df = pd.DataFrame(
        {
            "line_netto": [1, 2],
            "unit_price": [0.5, 0.6],
            "time": [pd.Timestamp("2023-01-01"), pd.Timestamp("2023-01-02")],
        }
    )

    pw = PriceWatch.__new__(PriceWatch)
    pw.tree = DummyTree()
    pw.supplier_codes = {"S1 - Sup": "S1"}
    pw.sup_var = DummyVar("S1 - Sup")
    pw.search_var = DummyVar("")
    pw.items_by_supplier = {"S1": {"Item": df}}
    pw._sort_col = None
    pw._sort_reverse = False

    pw._refresh_table()

    assert pw.tree.inserted
    row = pw.tree.inserted[0]
    assert float(row[1]) == 2.0
    assert float(row[2]) == 0.6


def test_refresh_table_with_non_contiguous_index(monkeypatch):
    class DummyVar:
        def __init__(self, value=""):
            self.val = value

        def get(self):
            return self.val

    class DummyTree:
        def __init__(self):
            self.inserted = []

        def get_children(self):
            return []

        def delete(self, *a):
            pass

        def insert(self, parent, index, values):
            self.inserted.append(values)

    df = pd.DataFrame(
        {
            "line_netto": [1, 2],
            "unit_price": [0.5, 0.6],
            "time": [pd.Timestamp("2023-01-01"), pd.Timestamp("2023-01-02")],
        }
    )
    df.index = [2, 5]

    pw = PriceWatch.__new__(PriceWatch)
    pw.tree = DummyTree()
    pw.supplier_codes = {"S1 - Sup": "S1"}
    pw.sup_var = DummyVar("S1 - Sup")
    pw.search_var = DummyVar("")
    pw.items_by_supplier = {"S1": {"Item": df}}
    pw._sort_col = None
    pw._sort_reverse = False

    pw._refresh_table()

    assert pw.tree.inserted
    row = pw.tree.inserted[0]
    assert float(row[1]) == 2.0
    assert float(row[2]) == 0.6


def test_show_graph_with_real_matplotlib(monkeypatch):
    import matplotlib
    import matplotlib.dates as mdates
    import types, sys
    matplotlib.use("Agg")

    df = pd.DataFrame(
        {
            "time": [pd.Timestamp("2023-01-01"), pd.Timestamp("2023-01-02")],
            "unit_price": [1, 2],
        }
    )

    captured = {}

    class FakeCanvas:
        def __init__(self, fig, master=None):
            captured["fig"] = fig

        def draw(self):
            pass

        def get_tk_widget(self):
            class W:
                def pack(self, *a, **k):
                    pass

            return W()

    class FakeTop:
        def __init__(self, master=None):
            pass

        def title(self, t):
            pass

        def bind(self, *a, **k):
            pass

        def destroy(self):
            pass

    class FakeButton:
        def __init__(self, master=None, text=None, command=None):
            pass

        def pack(self, *a, **k):
            pass

    cursor_info = {}

    class FakeCursor:
        def connect(self, event, func):
            cursor_info["event"] = event
            cursor_info["func"] = func

    def fake_cursor(lines, hover=False):
        cursor_info["lines"] = lines
        cursor_info["hover"] = hover
        return FakeCursor()

    monkeypatch.setattr(
        "matplotlib.backends.backend_tkagg.FigureCanvasTkAgg", FakeCanvas
    )
    monkeypatch.setattr("wsm.ui.price_watch.tk.Toplevel", FakeTop)
    monkeypatch.setattr("wsm.ui.price_watch.ttk.Button", FakeButton)
    monkeypatch.setattr("wsm.ui.price_watch.tk.BOTH", "both", raising=False)
    monkeypatch.setattr("wsm.ui.price_watch.messagebox.showerror", lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "mplcursors", types.SimpleNamespace(cursor=fake_cursor))

    pw = PriceWatch.__new__(PriceWatch)
    pw._show_graph("Item", df)

    fig = captured.get("fig")
    assert fig is not None
    ax = fig.axes[0]
    line = ax.lines[0]
    assert len(line.get_xdata()) == len(df)
    assert len(line.get_ydata()) == len(df)
    assert ax.get_xlabel() == "Datum"
    assert ax.get_ylabel() == "Cena"

    # verify cursor was attached and annotation formatting works
    assert cursor_info.get("lines") == ax.get_lines()
    assert cursor_info.get("hover") is True
    func = cursor_info.get("func")
    assert callable(func)
    ann_text = []

    class Ann:
        def set_text(self, t):
            ann_text.append(t)

    first_x = line.get_xdata()[0]
    first_y = line.get_ydata()[0]
    func(
        types.SimpleNamespace(
            target=(mdates.date2num(first_x), first_y), annotation=Ann()
        )
    )
    assert ann_text and "2023-01-01" in ann_text[0]


def test_show_graph_skips_zero_prices(monkeypatch):
    import matplotlib
    matplotlib.use("Agg")
    import types, sys

    df = pd.DataFrame(
        {
            "time": [
                pd.Timestamp("2023-01-01"),
                pd.Timestamp("2023-01-02"),
                pd.Timestamp("2023-01-03"),
            ],
            "unit_price": [1, 0, 2],
        }
    )

    captured = {}

    class FakeCanvas:
        def __init__(self, fig, master=None):
            captured["fig"] = fig

        def draw(self):
            pass

        def get_tk_widget(self):
            class W:
                def pack(self, *a, **k):
                    pass

            return W()

    class FakeTop:
        def __init__(self, master=None):
            pass

        def title(self, t):
            pass

        def bind(self, *a, **k):
            pass

        def destroy(self):
            pass

    class FakeButton:
        def __init__(self, master=None, text=None, command=None):
            pass

        def pack(self, *a, **k):
            pass

    cursor_info = {}

    class FakeCursor:
        def connect(self, event, func):
            cursor_info["event"] = event
            cursor_info["func"] = func

    def fake_cursor(lines, hover=False):
        cursor_info["lines"] = lines
        cursor_info["hover"] = hover
        return FakeCursor()

    monkeypatch.setattr(
        "matplotlib.backends.backend_tkagg.FigureCanvasTkAgg", FakeCanvas
    )
    monkeypatch.setattr("wsm.ui.price_watch.tk.Toplevel", FakeTop)
    monkeypatch.setattr("wsm.ui.price_watch.ttk.Button", FakeButton)
    monkeypatch.setattr("wsm.ui.price_watch.tk.BOTH", "both", raising=False)
    monkeypatch.setattr(
        "wsm.ui.price_watch.messagebox.showerror",
        lambda *a, **k: None,
    )
    monkeypatch.setitem(sys.modules, "mplcursors", types.SimpleNamespace(cursor=fake_cursor))

    pw = PriceWatch.__new__(PriceWatch)
    pw._show_graph("Item", df)

    fig = captured.get("fig")
    assert fig is not None
    ax = fig.axes[0]
    line = ax.lines[0]
    ydata = list(line.get_ydata())
    assert 0 not in ydata
    assert len(ydata) == 2


def test_close_calls_destroy_and_quit():
    calls = []

    pw = PriceWatch.__new__(PriceWatch)

    def fake_destroy():
        calls.append("destroy")

    def fake_quit():
        calls.append("quit")

    pw.destroy = fake_destroy
    pw.quit = fake_quit

    PriceWatch._close(pw)

    assert calls == ["destroy", "quit"]

