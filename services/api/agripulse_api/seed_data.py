"""Reference data loaded by `python -m agripulse_api.seed`.

Mandi names are exactly as they appear in live data.gov.in Agmarknet responses for
Karnataka tomato (checked against a real pull dated 25/09/2026). Coordinates are
approximate town centroids typed in by hand -> coords_verified=False until someone
confirms the market-yard location on a map (Admin > Mandis). Mandis first seen in an
ingestion run are created automatically with no coordinates.
"""

KARNATAKA_TOMATO_MANDIS = [
    # (agmarknet market name, district, lat, lon)
    ("Binny Mill (FF&V) Bengaluru APMC", "Bengaluru", 12.975, 77.565),
    ("Kolar APMC", "Kolar", 13.137, 78.129),  # not in the 25/09 pull; name follows the pattern, verify
    ("Chintamani APMC", "Chikkaballapur", 13.400, 78.057),
    ("Bagepalli APMC", "Chikkaballapur", 13.785, 77.793),
    ("Gauribidanur APMC", "Chikkaballapur", 13.611, 77.517),
    ("Doddaballapur APMC", "Bengaluru Rural", 13.292, 77.538),
    ("Ramanagara APMC", "Bengaluru South", 12.722, 77.281),
    ("Channapatna APMC", "Bengaluru South", 12.651, 77.209),
    ("Bangarpet APMC", "Kolar", 12.992, 78.178),
    ("Mysuru APMC", "Mysuru", 12.310, 76.652),
    ("Nanjangud APMC", "Mysuru", 12.119, 76.683),
    ("Gundlupet APMC", "Chamarajanagar", 11.808, 76.690),
    ("Davangere APMC", "Davangere", 14.464, 75.921),
    ("Ranebennur APMC", "Haveri", 14.622, 75.632),
    ("Shimoga APMC", "Shivamogga", 13.929, 75.568),
    ("Kadur APMC", "Chikkamagaluru", 13.553, 76.012),
    ("Kalaburagi APMC", "Kalaburagi", 17.329, 76.834),
    ("Belgaum APMC", "Belagavi", 15.850, 74.498),
]
