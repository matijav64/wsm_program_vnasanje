# File: wsm/ui/main_menu.py
"""Simple main menu for the WSM application."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from wsm.ui.common import select_invoice, open_invoice_gui


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
        messagebox.showinfo(
            "Spremljaj cene",
            "Funkcija še ni implementirana.",
        )

    btn_invoice = tk.Button(root, text="Unesi račun", width=20, command=_enter_invoice)
    btn_invoice.pack(pady=20)

    btn_prices = tk.Button(root, text="Spremljaj cene", width=20, command=_watch_prices)
    btn_prices.pack(pady=10)

    root.mainloop()

