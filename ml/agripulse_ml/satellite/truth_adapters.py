"""Ground-truth adapters (V2-4): real published files -> validate.py's normalised schema.

Written after reading the real tables (rule 5). data/ground_truth/tomato_district_hsg.csv was extracted from:
  * Horticulture Statistics at a Glance 2018 (NHB), Table 7.5.32, PDF page 344: 2015-16, 2016-17, '000 Ha / '000 MT
  * Horticultural Statistics at a Glance 2021 (DA&FW), Table 7.4.1, PDF page 264 (landscape): 2016-17 .. 2020-21.
    The header says '000 Ha / '000 MT but the values are hectares / tonnes: 2016-17 Kolar 8510 / 481447 matches the
    2018 edition's 8.51 / 481.45 thousand exactly.
  * Horticultural Statistics at a Glance 2024 (DA&FW), Table 7.4.1, PDF page 193: 2020-21 .. 2023-24, Hectare /
    Metric Tonne. Its 2020-21 values equal the 2021 edition's.
District names are mapped to the pilot's names; "2018-19" -> agri_year 2018 (July 2018 - June 2019).
"""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
HSG_FILE = ROOT / "data" / "ground_truth" / "tomato_district_hsg.csv"
DISTRICT_NAMES = {"KOLAR": "Kolar", "CHIKBALLAPUR": "Chikkaballapur", "CHIKKABALLAPUR": "Chikkaballapur"}


def hsg_tomato(path: str | Path = HSG_FILE) -> pd.DataFrame:
    raw = pd.read_csv(path)
    raw["district"] = raw["district_as_printed"].str.upper().map(DISTRICT_NAMES)
    if raw["district"].isna().any():
        raise ValueError(f"unmapped districts: {sorted(raw.loc[raw['district'].isna(), 'district_as_printed'])}")
    raw["agri_year"] = raw["season"].str[:4].astype(int)
    src = raw["edition"] + ", Table " + raw["table"].astype(str) + ", PDF p." + raw["pdf_page"].astype(str)
    rows = []
    for var, col, unit in (("area", "area_ha", "ha"), ("production", "production_t", "t")):
        rows.append(pd.DataFrame({"district": raw["district"], "agri_year": raw["agri_year"], "crop": "Tomato",
                                  "variable": var, "value": raw[col].astype(float), "unit": unit, "source": src}))
    out = pd.concat(rows, ignore_index=True)
    dup = out.duplicated(["district", "agri_year", "variable"])
    if dup.any():
        raise ValueError("duplicate district-years in ground truth")
    return out
