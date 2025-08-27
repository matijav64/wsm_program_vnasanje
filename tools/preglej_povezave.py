import pandas as pd
from pathlib import Path

root = Path(r"\\PisarnaNAS\wsm_program_vnasanje_povezave\links\SI55102697")
main = root / "SI55102697_povezane.xlsx"

df = pd.read_excel(main)
need = df[
    df["wsm_sifra"].isna() | (df["wsm_sifra"].astype(str).str.strip() == "")
]
print(f"Skupaj vrstic: {len(df)}, brez wsm_sifra: {len(need)}")
print(
    need[["naziv_ckey", "enota_norm", "wsm_sifra", "wsm_naziv"]].to_string(
        index=False
    )
)
