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
        "cena": [1],
        "time": [pd.Timestamp("2023-01-01")],
    })
    df2 = pd.DataFrame({
        "key": ["S2_ItemB"],
        "cena": [2],
        "time": [pd.Timestamp("2023-01-02")],
    })
    df1.to_excel(s1 / "price_history.xlsx", index=False)
    df2.to_excel(s2 / "price_history.xlsx", index=False)

    items = _load_price_histories(links)
    assert set(items.keys()) == {"S1", "S2"}
    assert set(items["S1"].keys()) == {"S1 - ItemA"}
    assert set(items["S2"].keys()) == {"S2 - ItemB"}


def test_load_price_histories_non_datetime(tmp_path):
    clear_price_cache()
    links = tmp_path / "links"
    s1 = links / "Sup1"
    s1.mkdir(parents=True)

    (s1 / "supplier.json").write_text(json.dumps({"sifra": "S1", "ime": "Sup1"}))

    df = pd.DataFrame(
        {
            "key": ["S1_ItemA", "S1_ItemA"],
            "cena": [1, 2],
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
            "cena": [1],
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
            "cena": [1, 2, 3],
        }
    )

    import types, sys

    xticks_capture = {}

    class FakeFig:
        def __init__(self):
            self.autofmt_called = False

        def autofmt_xdate(self):
            self.autofmt_called = True

    class FakeAx:
        def __init__(self):
            self.xticks = None

        def plot(self, *a, **k):
            pass

        def set_xlabel(self, *a):
            pass

        def set_ylabel(self, *a):
            pass

        def grid(self, *a, **k):
            pass

        def set_xticks(self, ticks):
            self.xticks = list(ticks)

        def margins(self, *a, **k):
            pass

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
    fake_matplotlib = types.ModuleType("matplotlib")
    fake_matplotlib.pyplot = fake_plt
    fake_matplotlib.backends = fake_backends
    monkeypatch.setitem(sys.modules, "matplotlib", fake_matplotlib)
    monkeypatch.setitem(sys.modules, "matplotlib.pyplot", fake_plt)
    monkeypatch.setitem(sys.modules, "matplotlib.backends", fake_backends)
    monkeypatch.setitem(sys.modules, "matplotlib.backends.backend_tkagg", fake_backend)

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

    expected_ticks = pd.to_datetime(df["time"]).tolist()
    assert xticks_capture["ax"].xticks == expected_ticks
    assert xticks_capture["fig"].autofmt_called


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

    df = pd.DataFrame({"cena": [1], "time": [pd.Timestamp("2023-01-01")]})

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


def test_show_graph_with_real_matplotlib(monkeypatch):
    import matplotlib
    matplotlib.use("Agg")

    df = pd.DataFrame(
        {
            "time": [pd.Timestamp("2023-01-01"), pd.Timestamp("2023-01-02")],
            "cena": [1, 2],
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

    monkeypatch.setattr(
        "matplotlib.backends.backend_tkagg.FigureCanvasTkAgg", FakeCanvas
    )
    monkeypatch.setattr("wsm.ui.price_watch.tk.Toplevel", FakeTop)
    monkeypatch.setattr("wsm.ui.price_watch.ttk.Button", FakeButton)
    monkeypatch.setattr("wsm.ui.price_watch.tk.BOTH", "both", raising=False)
    monkeypatch.setattr("wsm.ui.price_watch.messagebox.showerror", lambda *a, **k: None)

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

