"""Build the bundled demo database.

Every measurement table produced here is SYNTHETIC. The generator encodes
plausible, documented patterns for the Indian EEZ (southwest-monsoon
upwelling off Kerala, warm El Nino years, a lagged oil-sardine response to
upwelling, DHW-driven coral bleaching) so the agents have something real to
reason about, but the numbers are not observations. The catalog marks each
dataset ``synthetic=1`` and every answer that cites one says so.

Swap in real data with ``python -m orca.data.ingest`` (see README).

Usage:  python -m orca.data.generate [--out data/orca_demo.sqlite]
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
from pathlib import Path

from orca.config import get_settings

SEED = 176
YEARS = list(range(2004, 2025))

REGIONS = {
    # name: (base SST, 12 monthly SST offsets Jan..Dec, lat, lon)
    "SE Arabian Sea": (28.4, [-0.6, -0.1, 0.8, 1.6, 1.3, -0.6, -1.9, -2.2, -1.5, -0.2, 0.3, -0.2], 9.5, 75.5),
    "NE Arabian Sea": (27.4, [-2.6, -2.3, -0.9, 0.9, 2.1, 1.7, 0.4, -0.4, 0.1, 1.0, 0.1, -1.6], 20.0, 69.5),
    "SW Bay of Bengal": (28.6, [-1.4, -0.9, 0.3, 1.5, 1.6, 0.8, 0.0, -0.2, -0.1, 0.1, -0.4, -1.2], 12.0, 81.0),
    "NW Bay of Bengal": (28.2, [-3.0, -2.1, 0.0, 1.8, 2.3, 1.7, 1.0, 0.8, 0.9, 0.6, -0.9, -2.4], 19.5, 87.5),
    "Andaman Sea": (28.9, [-1.0, -0.8, 0.0, 1.0, 1.2, 0.4, 0.0, -0.1, -0.2, 0.1, 0.0, -0.6], 11.5, 93.0),
    "Lakshadweep Sea": (28.8, [-0.5, 0.0, 0.7, 1.3, 1.0, -0.2, -0.9, -1.0, -0.6, -0.1, 0.2, -0.1], 10.5, 72.6),
}

# Interannual SST anomalies (degC) applied to Mar-Jun, loosely echoing strong
# Indian Ocean warm years (2010, 2015-16, 2019-20, 2024).
WARM_YEARS = {2010: 1.25, 2015: 0.45, 2016: 1.35, 2019: 0.55, 2020: 0.8, 2024: 1.2}

# Relative strength of the SE Arabian Sea monsoon upwelling bloom per year.
# Weak in warm years; draws for the rest come from the seeded RNG.
WEAK_UPWELLING = {2015: 0.45, 2016: 0.6, 2010: 0.75, 2020: 0.7, 2024: 0.8}


def _salinity(region: str, month: int, rng: random.Random) -> float:
    base = {
        "SE Arabian Sea": 35.0,
        "NE Arabian Sea": 36.2,
        "SW Bay of Bengal": 33.6,
        "NW Bay of Bengal": 32.4,
        "Andaman Sea": 32.6,
        "Lakshadweep Sea": 34.8,
    }[region]
    fresh = 0.0
    if region in ("NW Bay of Bengal", "SW Bay of Bengal", "Andaman Sea") and month in (8, 9, 10, 11):
        fresh = {8: 0.8, 9: 1.9, 10: 2.3, 11: 1.4}[month] * (1.6 if region == "NW Bay of Bengal" else 0.7)
    if region == "SE Arabian Sea" and month in (6, 7, 8, 9):
        fresh = 0.6
    return round(base - fresh + rng.gauss(0, 0.12), 2)


def _chlorophyll(region: str, month: int, upwelling: float, year: int, rng: random.Random) -> float:
    if region == "SE Arabian Sea":
        bloom = {6: 1.2, 7: 3.6, 8: 4.2, 9: 2.4, 10: 0.8}.get(month, 0.0) * upwelling
        base = 0.32
    elif region == "NE Arabian Sea":
        bloom = {12: 0.6, 1: 1.1, 2: 1.4, 3: 0.9}.get(month, 0.0)
        base = 0.45
    elif region == "NW Bay of Bengal":
        bloom = {8: 0.5, 9: 0.6, 10: 0.4}.get(month, 0.0)
        base = 0.55
    elif region == "SW Bay of Bengal":
        bloom = {11: 0.3, 12: 0.35}.get(month, 0.0)
        base = 0.3
    elif region == "Andaman Sea":
        bloom, base = 0.0, 0.2
    else:
        bloom, base = 0.0, 0.14
    decline = 1 - 0.004 * (year - YEARS[0])
    return round(max(0.05, (base + bloom) * decline * math.exp(rng.gauss(0, 0.12))), 3)


def build_ocean(rng: random.Random) -> tuple[list[tuple], dict[int, float]]:
    rows: list[tuple] = []
    upwelling: dict[int, float] = {}
    for y in YEARS:
        upwelling[y] = WEAK_UPWELLING.get(y, round(min(1.45, max(0.7, rng.gauss(1.08, 0.18))), 3))
    for region, (base, offsets, _lat, _lon) in REGIONS.items():
        for y in YEARS:
            for m in range(1, 13):
                anomaly = WARM_YEARS.get(y, 0.0) if m in (3, 4, 5, 6) else WARM_YEARS.get(y, 0.0) * 0.3
                if region in ("NE Arabian Sea",):
                    anomaly *= 0.6
                sst = base + offsets[m - 1] + 0.019 * (y - YEARS[0]) + anomaly + rng.gauss(0, 0.15)
                up = upwelling[y] if region == "SE Arabian Sea" else 1.0
                chl = _chlorophyll(region, m, up, y, rng)
                sal = _salinity(region, m, rng)
                # Surface DO falls with temperature (solubility) and slowly over time.
                do = 8.35 - 0.075 * sst - 0.0045 * (y - YEARS[0]) + rng.gauss(0, 0.05)
                rows.append((region, y, m, round(sst, 2), chl, sal, round(do, 2)))
    return rows, upwelling


def build_landings(rng: random.Random, upwelling: dict[int, float]) -> list[tuple]:
    rows: list[tuple] = []
    stocks = [
        # species, common name, state, base tonnes, driver
        ("Sardinella longiceps", "Indian oil sardine", "Kerala", 210_000, "upwelling_lag1"),
        ("Sardinella longiceps", "Indian oil sardine", "Karnataka", 95_000, "upwelling_lag1"),
        ("Rastrelliger kanagurta", "Indian mackerel", "Kerala", 48_000, "upwelling_lag0"),
        ("Rastrelliger kanagurta", "Indian mackerel", "Karnataka", 62_000, "upwelling_lag0"),
        ("Rastrelliger kanagurta", "Indian mackerel", "Tamil Nadu", 35_000, "none"),
        ("Harpadon nehereus", "Bombay duck", "Gujarat", 78_000, "decline"),
        ("Harpadon nehereus", "Bombay duck", "Maharashtra", 52_000, "decline"),
        ("Tenualosa ilisha", "Hilsa shad", "West Bengal", 45_000, "freshwater"),
        ("Tenualosa ilisha", "Hilsa shad", "Odisha", 18_000, "freshwater"),
        ("Thunnus albacares", "Yellowfin tuna", "Tamil Nadu", 16_000, "increase"),
        ("Thunnus albacares", "Yellowfin tuna", "Kerala", 11_000, "increase"),
        ("Penaeidae", "Penaeid shrimps", "Gujarat", 58_000, "none"),
        ("Penaeidae", "Penaeid shrimps", "Andhra Pradesh", 41_000, "increase"),
        ("Penaeidae", "Penaeid shrimps", "Kerala", 37_000, "none"),
    ]
    freshwater = {y: rng.gauss(1.0, 0.15) for y in YEARS}
    for species, common, state, base, driver in stocks:
        for y in YEARS:
            f = 1.0
            if driver == "upwelling_lag1":
                prev = upwelling.get(y - 1, 1.0)
                f = (prev / 1.05) ** 1.6 * (upwelling[y] / 1.05) ** 0.4
            elif driver == "upwelling_lag0":
                f = (upwelling[y] / 1.05) ** 0.7
            elif driver == "decline":
                f = 1 - 0.018 * (y - YEARS[0])
            elif driver == "freshwater":
                f = freshwater[y]
            elif driver == "increase":
                f = 1 + 0.03 * (y - YEARS[0])
            tonnes = base * f * math.exp(rng.gauss(0, 0.1))
            rows.append((species, common, state, y, int(round(tonnes, -1))))
    return rows


REF_SPECIES = [
    # scientific, common, family (current WoRMS), group, habitat,
    # IUCN (None = not recorded here), WoRMS AphiaID (checked against api.obis.org)
    ("Sardinella longiceps", "Indian oil sardine", "Dorosomatidae", "fish", "coastal pelagic", "LC", 212269),
    ("Rastrelliger kanagurta", "Indian mackerel", "Scombridae", "fish", "coastal pelagic", None, 127020),
    ("Harpadon nehereus", "Bombay duck", "Synodontidae", "fish", "estuarine/coastal demersal", None, 217661),
    ("Tenualosa ilisha", "Hilsa shad", "Dorosomatidae", "fish", "anadromous, estuarine", "LC", 278568),
    ("Thunnus albacares", "Yellowfin tuna", "Scombridae", "fish", "oceanic pelagic", "LC", 127027),
    ("Penaeidae", "Penaeid shrimps", "Penaeidae", "crustacean", "coastal benthic", None, 106727),
    ("Dugong dugon", "Dugong", "Dugongidae", "marine mammal", "seagrass meadows", "VU", 220227),
    ("Lepidochelys olivacea", "Olive ridley turtle", "Cheloniidae", "reptile", "oceanic; mass-nesting beaches", "VU", 220293),
    ("Rhincodon typus", "Whale shark", "Rhincodontidae", "fish", "oceanic and coastal pelagic", "EN", 105847),
    ("Tursiops aduncus", "Indo-Pacific bottlenose dolphin", "Delphinidae", "marine mammal", "coastal", "NT", 254983),
    ("Acropora", "Staghorn/table corals (genus)", "Acroporidae", "coral", "shallow reef", None, 205469),
]


def build_occurrences(rng: random.Random) -> list[tuple]:
    rows: list[tuple] = []
    plans = [
        # species, region, method, years, mean count, yearly trend (fraction)
        ("Dugong dugon", "SW Bay of Bengal", "aerial/boat survey", range(2010, 2025), 38, -0.025),
        ("Dugong dugon", "Andaman Sea", "aerial/boat survey", range(2010, 2025), 21, -0.02),
        ("Lepidochelys olivacea", "NW Bay of Bengal", "nesting beach count", range(2008, 2025), 310_000, 0.02),
        ("Rhincodon typus", "NE Arabian Sea", "fisher-reported sighting", range(2012, 2025), 24, 0.04),
        ("Tursiops aduncus", "SE Arabian Sea", "boat line-transect", range(2012, 2025), 140, 0.0),
        ("Tursiops aduncus", "SW Bay of Bengal", "boat line-transect", range(2012, 2025), 95, -0.01),
        ("Sardinella longiceps", "SE Arabian Sea", "eDNA", range(2018, 2025), 0, 0.0),
        ("Rastrelliger kanagurta", "SE Arabian Sea", "eDNA", range(2018, 2025), 0, 0.0),
        ("Thunnus albacares", "Lakshadweep Sea", "eDNA", range(2018, 2025), 0, 0.0),
        ("Acropora", "Lakshadweep Sea", "eDNA", range(2018, 2025), 0, 0.0),
    ]
    rid = 0
    for species, region, method, years, mean, trend in plans:
        _, _, lat, lon = REGIONS[region]
        for y in years:
            n_rec = 1 if method == "nesting beach count" else rng.randint(2, 4)
            for _ in range(n_rec):
                rid += 1
                if method == "eDNA":
                    count = None
                    reads = int(rng.lognormvariate(7.5, 0.8))
                else:
                    base = mean * (1 + trend) ** (y - years[0])
                    count = max(0, int(round(base * rng.lognormvariate(0, 0.25) / (1 if n_rec == 1 else n_rec))))
                    reads = None
                rows.append((
                    f"OCC-{rid:05d}", species, region, y,
                    round(lat + rng.uniform(-1.2, 1.2), 3), round(lon + rng.uniform(-1.2, 1.2), 3),
                    count, reads, method,
                ))
    return rows


def build_coral(ocean_rows: list[tuple], rng: random.Random) -> list[tuple]:
    sites = [
        ("Kavaratti", "Lakshadweep", "Lakshadweep Sea", 46.0),
        ("Agatti", "Lakshadweep", "Lakshadweep Sea", 52.0),
        ("Swaraj Dweep", "Andaman Islands", "Andaman Sea", 48.0),
        ("North Bay", "Andaman Islands", "Andaman Sea", 41.0),
        ("Kurusadai", "Gulf of Mannar", "SW Bay of Bengal", 33.0),
        ("Pirotan", "Gulf of Kutch", "NE Arabian Sea", 22.0),
    ]
    sst = {(r[0], r[1], r[2]): r[3] for r in ocean_rows}
    rows: list[tuple] = []
    for site, area, region, cover0 in sites:
        clim = [
            sum(sst[(region, y, m)] for y in range(2004, 2013)) / 9 for m in range(1, 13)
        ]
        mmm = max(clim)  # maximum monthly mean climatology
        cover = cover0
        for y in YEARS:
            hotspots = [max(0.0, sst[(region, y, m)] - mmm) for m in range(1, 13)]
            # Monthly approximation of NOAA's DHW: sum hotspots >= 1 degC over a
            # rolling 3-month window, scaled from months to weeks.
            dhw = 0.0
            for m in range(12):
                window = [h for h in hotspots[max(0, m - 2): m + 1] if h >= 1.0]
                dhw = max(dhw, sum(window) * 4.35)
            dhw += max(0.0, rng.gauss(0.4, 0.4))
            bleached = 100 / (1 + math.exp(-(dhw - 7.0) / 1.8))
            bleached = min(100.0, max(0.0, bleached + rng.gauss(0, 3)))
            mortality = bleached * (0.35 if dhw >= 8 else 0.12) / 100
            if y >= 2008:
                cover = cover * (1 - mortality) + 1.6 * (1 - cover / 65)
            rows.append((site, area, region, y, round(cover, 1), round(bleached, 1), round(dhw, 1)))
    return rows


CATALOG = [
    {
        "dataset_id": "D-OCEAN",
        "table_name": "ocean_monthly",
        "title": "Monthly sea-surface conditions by region, Indian EEZ, 2004-2024",
        "description": (
            "Region-averaged monthly sea surface temperature, chlorophyll-a, "
            "salinity and surface dissolved oxygen for six regions around India. "
            "Encodes southwest-monsoon upwelling off Kerala (Jun-Sep chlorophyll "
            "bloom in 'SE Arabian Sea') and warm years such as 2010, 2016, 2024."
        ),
        "columns": {
            "region": "one of: SE Arabian Sea, NE Arabian Sea, SW Bay of Bengal, NW Bay of Bengal, Andaman Sea, Lakshadweep Sea",
            "year": "integer 2004-2024",
            "month": "integer 1-12",
            "sst_c": "sea surface temperature, degC",
            "chlorophyll_mg_m3": "surface chlorophyll-a, mg/m^3",
            "salinity_psu": "sea surface salinity, PSU",
            "dissolved_oxygen_mg_l": "surface dissolved oxygen, mg/L",
        },
    },
    {
        "dataset_id": "D-FISH",
        "table_name": "fish_landings",
        "title": "Annual marine fish landings by state and species, 2004-2024",
        "description": (
            "Annual landings (tonnes) for key species by Indian coastal state. "
            "Oil sardine landings respond to the previous year's upwelling strength."
        ),
        "columns": {
            "scientific_name": "species or taxon (e.g. Sardinella longiceps, Penaeidae)",
            "common_name": "common name (e.g. Indian oil sardine)",
            "state": "Kerala, Karnataka, Tamil Nadu, Gujarat, Maharashtra, West Bengal, Odisha, Andhra Pradesh",
            "year": "integer 2004-2024",
            "landings_tonnes": "landings in tonnes",
        },
    },
    {
        "dataset_id": "D-OCC",
        "table_name": "species_occurrence",
        "title": "Survey occurrence and eDNA records for protected and key species, 2008-2024",
        "description": (
            "Survey counts (dugong, olive ridley nesting, whale shark sightings, "
            "dolphin transects) and eDNA read counts by region and year."
        ),
        "columns": {
            "record_id": "unique record id",
            "scientific_name": "species or genus",
            "region": "ocean region (same names as ocean_monthly)",
            "year": "integer",
            "latitude": "decimal degrees",
            "longitude": "decimal degrees",
            "individual_count": "individuals counted (NULL for eDNA)",
            "edna_reads": "eDNA sequence reads assigned to the taxon (NULL for surveys)",
            "method": "survey method",
        },
    },
    {
        "dataset_id": "D-CORAL",
        "table_name": "coral_reef_monitoring",
        "title": "Annual coral reef monitoring at six Indian reef sites, 2004-2024",
        "description": (
            "Live coral cover, percent of colonies bleached and annual maximum "
            "Degree Heating Weeks (DHW, degC-weeks) per site."
        ),
        "columns": {
            "site": "reef site name",
            "reef_area": "Lakshadweep, Andaman Islands, Gulf of Mannar, Gulf of Kutch",
            "region": "ocean region used to join with ocean_monthly",
            "year": "integer 2004-2024",
            "live_coral_cover_pct": "percent live hard-coral cover",
            "bleached_pct": "percent of colonies bleached",
            "max_dhw": "annual maximum Degree Heating Weeks, degC-weeks",
        },
    },
    {
        "dataset_id": "D-SPECIES",
        "table_name": "species_reference",
        "title": "Species reference table (taxonomy, habitat, IUCN category)",
        "description": (
            "Hand-compiled reference for the taxa in this database. IUCN "
            "category given only where recorded; NULL means not recorded here."
        ),
        "columns": {
            "scientific_name": "species or higher taxon",
            "common_name": "common name",
            "family": "taxonomic family",
            "taxon_group": "fish, crustacean, marine mammal, reptile, coral",
            "habitat": "typical habitat",
            "iucn_category": "IUCN Red List category (LC, NT, VU, EN, CR) or NULL",
            "aphia_id": "WoRMS AphiaID (marinespecies.org)",
        },
        "synthetic": 0,
        "publisher": "ORCA curated reference",
        "citation": (
            "ORCA curated species reference (hand-compiled from WoRMS and IUCN Red List "
            "categories; verify current status at marinespecies.org and iucnredlist.org)."
        ),
        "license": "CC-BY-4.0",
    },
]


def _synthetic_citation(entry: dict) -> str:
    return (
        f"ORCA synthetic demo dataset '{entry['title']}', generated by "
        f"orca/data/generate.py (seed {SEED}). Not real observations."
    )


SCHEMA = """
CREATE TABLE _catalog (
    dataset_id TEXT PRIMARY KEY, table_name TEXT NOT NULL, title TEXT NOT NULL,
    description TEXT, columns_json TEXT NOT NULL, publisher TEXT, citation TEXT NOT NULL,
    license TEXT, url TEXT, synthetic INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE ocean_monthly (
    region TEXT, year INTEGER, month INTEGER, sst_c REAL, chlorophyll_mg_m3 REAL,
    salinity_psu REAL, dissolved_oxygen_mg_l REAL
);
CREATE TABLE fish_landings (
    scientific_name TEXT, common_name TEXT, state TEXT, year INTEGER, landings_tonnes INTEGER
);
CREATE TABLE species_occurrence (
    record_id TEXT PRIMARY KEY, scientific_name TEXT, region TEXT, year INTEGER,
    latitude REAL, longitude REAL, individual_count INTEGER, edna_reads INTEGER, method TEXT
);
CREATE TABLE coral_reef_monitoring (
    site TEXT, reef_area TEXT, region TEXT, year INTEGER, live_coral_cover_pct REAL,
    bleached_pct REAL, max_dhw REAL
);
CREATE TABLE species_reference (
    scientific_name TEXT PRIMARY KEY, common_name TEXT, family TEXT, taxon_group TEXT,
    habitat TEXT, iucn_category TEXT, aphia_id INTEGER
);
CREATE INDEX ix_ocean ON ocean_monthly(region, year, month);
CREATE INDEX ix_fish ON fish_landings(scientific_name, state, year);
"""


def build(out: Path) -> Path:
    rng = random.Random(SEED)  # noqa: S311 - reproducible demo data, not security
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(SCHEMA)
        ocean, upwelling = build_ocean(rng)
        conn.executemany("INSERT INTO ocean_monthly VALUES (?,?,?,?,?,?,?)", ocean)
        conn.executemany("INSERT INTO fish_landings VALUES (?,?,?,?,?)", build_landings(rng, upwelling))
        conn.executemany("INSERT INTO species_occurrence VALUES (?,?,?,?,?,?,?,?,?)", build_occurrences(rng))
        conn.executemany("INSERT INTO coral_reef_monitoring VALUES (?,?,?,?,?,?,?)", build_coral(ocean, rng))
        conn.executemany("INSERT INTO species_reference VALUES (?,?,?,?,?,?,?)", REF_SPECIES)
        for entry in CATALOG:
            synthetic = entry.get("synthetic", 1)
            conn.execute(
                "INSERT INTO _catalog VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    entry["dataset_id"], entry["table_name"], entry["title"], entry["description"],
                    json.dumps(entry["columns"]),
                    entry.get("publisher", "ORCA demo (synthetic)"),
                    entry.get("citation") or _synthetic_citation(entry),
                    entry.get("license", "CC0-1.0"), entry.get("url"), synthetic,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    tmp.replace(out)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, default=get_settings().data_db)
    args = parser.parse_args()
    path = build(args.out)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
