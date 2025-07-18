# File: wsm/ui/main_menu.py
"""Simple main menu for the WSM application."""
from __future__ import annotations

import tkinter as tk

from wsm.ui.common import select_invoice, open_invoice_gui
from wsm.ui.price_watch import launch_price_watch


def launch_main_menu() -> None:
    """Launch the main menu window."""
    root = tk.Tk()
    root.title("WSM")
    root.geometry("300x200")

    def _enter_invoice() -> None:
        root.withdraw()
        path = select_invoice()
        root.deiconify()
        if path:
            open_invoice_gui(path)

    def _watch_prices() -> None:
        root.withdraw()
        launch_price_watch(root)
        root.deiconify()

    btn_invoice = tk.Button(
        root,
        text="Vnesi račun",
        width=20,
        command=_enter_invoice,
        bg="brown",
        fg="white",
    )
    btn_invoice.pack(pady=20)

    btn_prices = tk.Button(
        root,
        text="Spremljaj cene",
        width=20,
        command=_watch_prices,
        bg="brown",
        fg="white",
    )
    btn_prices.pack(pady=10)

    root.mainloop()
