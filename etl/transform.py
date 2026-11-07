"""Transform SafeGraph ACS 2020 block-group files into stable RAG documents.

The source contains one row per Census block group. Additive estimates are summed
for counties, states, and the United States. Ratios are calculated from those
summed numerators and denominators. Non-additive medians are estimated from
aggregated ACS distribution tables; block-group medians are never averaged.
"""

from __future__ import annotations

import math
import re
import tarfile
from pathlib import Path

import pandas as pd
from tqdm import tqdm

RAW_DATA_PATH = Path("/Users/jennifer_chen/Documents/projects")
ARCHIVE_2020 = RAW_DATA_PATH / "safegraph_open_census_data_2020.tar.gz"
OUTPUT_DIR = Path(__file__).parent / "output" / "documents"
DATA_YEAR = "2020 ACS 5-Year Estimates"

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico",
}

# Inclusive-looking published ranges are represented by continuous boundaries.
INCOME_BINS = [
    (0, 10_000), (10_000, 15_000), (15_000, 20_000), (20_000, 25_000),
    (25_000, 30_000), (30_000, 35_000), (35_000, 40_000), (40_000, 45_000),
    (45_000, 50_000), (50_000, 60_000), (60_000, 75_000), (75_000, 100_000),
    (100_000, 125_000), (125_000, 150_000), (150_000, 200_000), (200_000, 250_000),
]
HOME_VALUE_BINS = [
    (0, 10_000), (10_000, 15_000), (15_000, 20_000), (20_000, 25_000),
    (25_000, 30_000), (30_000, 35_000), (35_000, 40_000), (40_000, 50_000),
    (50_000, 60_000), (60_000, 70_000), (70_000, 80_000), (80_000, 90_000),
    (90_000, 100_000), (100_000, 125_000), (125_000, 150_000),
    (150_000, 175_000), (175_000, 200_000), (200_000, 250_000),
    (250_000, 300_000), (300_000, 400_000), (400_000, 500_000),
    (500_000, 750_000), (750_000, 1_000_000), (1_000_000, 1_500_000),
    (1_500_000, 2_000_000), (2_000_000, 3_000_000),
]
RENT_BINS = [
    (0, 100), (100, 150), (150, 200), (200, 250), (250, 300), (300, 350),
    (350, 400), (400, 450), (450, 500), (500, 550), (550, 600), (600, 650),
    (650, 700), (700, 750), (750, 800), (800, 900), (900, 1_000),
    (1_000, 1_250), (1_250, 1_500), (1_500, 2_000), (2_000, 2_500),
    (2_500, 3_000), (3_000, 3_500), (3_500, 4_500),
]


def extract_csv_from_tar(tar_path: Path, member_name: str) -> pd.DataFrame:
    """Read one CSV member without unpacking the multi-gigabyte archive."""
    with tarfile.open(tar_path, "r:gz") as archive:
        handle = archive.extractfile(member_name)
        if handle is None:
            raise FileNotFoundError(member_name)
        return pd.read_csv(handle, dtype={"census_block_group": str})


def cbg_to_county(cbg: str) -> str | None:
    text = str(cbg)
    return text[:5] if len(text) >= 5 else None


def safe_agg(series: pd.Series, agg: str = "sum") -> float | None:
    """Aggregate numeric ACS values while excluding suppression sentinels."""
    values = pd.to_numeric(series, errors="coerce")
    values = values[(values >= 0) & values.notna()]
    if values.empty:
        return None
    if agg == "sum":
        return float(values.sum())
    if agg == "mean":
        return float(values.mean())
    if agg == "median":
        return float(values.median())
    raise ValueError(f"Unsupported aggregation: {agg}")


def _sum(frame: pd.DataFrame | None, column: str) -> float | None:
    if frame is None or column not in frame.columns:
        return None
    return safe_agg(frame[column])


def _percentage(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return round(100 * numerator / denominator, 1)


def _estimated_median(
    frame: pd.DataFrame | None,
    columns: list[str],
    boundaries: list[tuple[float, float]],
) -> float | None:
    """Estimate a median with linear interpolation through aggregated ACS bins."""
    counts = [_sum(frame, column) or 0.0 for column in columns]
    total = sum(counts)
    if total <= 0:
        return None
    target = total / 2
    cumulative = 0.0
    for count, (lower, upper) in zip(counts, boundaries):
        if cumulative + count >= target:
            if count <= 0:
                return float(lower)
            fraction = (target - cumulative) / count
            return round(lower + fraction * (upper - lower))
        cumulative += count
    return float(boundaries[-1][1])


def load_census_tables() -> dict[str, pd.DataFrame]:
    prefix = "safegraph_open_census_data_2020"
    members = {
        "population": f"{prefix}/data/cbg_b01.csv",
        "race": f"{prefix}/data/cbg_b02.csv",
        "ethnicity": f"{prefix}/data/cbg_b03.csv",
        "education": f"{prefix}/data/cbg_b15.csv",
        "poverty": f"{prefix}/data/cbg_b17.csv",
        "income": f"{prefix}/data/cbg_b19.csv",
        "employment": f"{prefix}/data/cbg_b23.csv",
        "housing": f"{prefix}/data/cbg_b25.csv",
        "fips": f"{prefix}/metadata/cbg_fips_codes.csv",
    }
    tables = {}
    for name, member in members.items():
        print(f"Loading {name}...", flush=True)
        tables[name] = extract_csv_from_tar(ARCHIVE_2020, member)
    for name, frame in tables.items():
        if name == "fips":
            continue
        frame["county_fips"] = frame["census_block_group"].str[:5]
        frame["state_fips"] = frame["census_block_group"].str[:2]
    return tables


def _fips_lookups(fips: pd.DataFrame):
    records = fips.copy()
    records["state_fips"] = records["state_fips"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(2)
    records["county_fips"] = records["county_fips"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(3)
    records["full_fips"] = records["state_fips"] + records["county_fips"]
    county = {
        row.full_fips: (str(row.county), str(row.state))
        for row in records.itertuples()
        if pd.notna(row.county) and pd.notna(row.state)
    }
    state = {
        row.state_fips: str(row.state)
        for row in records.drop_duplicates("state_fips").itertuples()
        if pd.notna(row.state)
    }
    return county, state


def _profile(frames: dict[str, pd.DataFrame], identity: dict[str, str]) -> dict:
    pop, race, eth = frames.get("population"), frames.get("race"), frames.get("ethnicity")
    inc, pov = frames.get("income"), frames.get("poverty")
    house, emp, edu = frames.get("housing"), frames.get("employment"), frames.get("education")

    total_population = _sum(pop, "B01001e1")
    male = _sum(pop, "B01001e2")
    under_18_columns = [f"B01001e{i}" for i in (*range(3, 7), *range(27, 31))]
    over_65_columns = [f"B01001e{i}" for i in (*range(20, 26), *range(44, 50))]
    under_18 = sum(_sum(pop, column) or 0 for column in under_18_columns)
    over_65 = sum(_sum(pop, column) or 0 for column in over_65_columns)

    race_total = _sum(race, "B02001e1") or total_population
    ethnicity_total = _sum(eth, "B03003e1") or total_population
    poverty_total = _sum(pov, "B17021e1")
    below_poverty = _sum(pov, "B17021e2")
    labor_force = _sum(emp, "B23025e2")
    civilian_labor_force = _sum(emp, "B23025e3")
    occupied_housing = _sum(house, "B25002e2")
    housing_total = _sum(house, "B25002e1") or _sum(house, "B25001e1")
    occupied_tenure = _sum(house, "B25003e1") or occupied_housing
    population_25_plus = _sum(edu, "B15003e1")

    result = {
        **identity,
        "total_population": total_population,
        "pct_male": _percentage(male, total_population),
        "pct_under_18": _percentage(under_18, total_population),
        "pct_over_65": _percentage(over_65, total_population),
        "pct_white_alone": _percentage(_sum(race, "B02001e2"), race_total),
        "pct_black_alone": _percentage(_sum(race, "B02001e3"), race_total),
        "pct_asian_alone": _percentage(_sum(race, "B02001e5"), race_total),
        "pct_two_or_more_races": _percentage(_sum(race, "B02001e8"), race_total),
        "pct_hispanic_or_latino": _percentage(_sum(eth, "B03003e3"), ethnicity_total),
        "median_household_income": _estimated_median(
            inc, [f"B19001e{i}" for i in range(2, 18)], INCOME_BINS
        ),
        "poverty_rate": _percentage(below_poverty, poverty_total),
        "population_below_poverty": below_poverty,
        "total_housing_units": housing_total,
        "pct_occupied": _percentage(occupied_housing, housing_total),
        "homeownership_rate": _percentage(_sum(house, "B25003e2"), occupied_tenure),
        "median_home_value": _estimated_median(
            house, [f"B25075e{i}" for i in range(2, 28)], HOME_VALUE_BINS
        ),
        "median_gross_rent": _estimated_median(
            house, [f"B25063e{i}" for i in range(3, 27)], RENT_BINS
        ),
        "unemployment_rate": _percentage(_sum(emp, "B23025e5"), civilian_labor_force),
        "labor_force_participation_rate": _percentage(labor_force, _sum(emp, "B23025e1")),
        "pct_bachelors_or_higher": _percentage(
            sum(_sum(edu, f"B15003e{i}") or 0 for i in range(22, 26)), population_25_plus
        ),
        "pct_hs_or_higher": _percentage(
            sum(_sum(edu, f"B15003e{i}") or 0 for i in range(17, 26)), population_25_plus
        ),
    }
    return result


def build_geography_profiles(tables: dict[str, pd.DataFrame]) -> list[dict]:
    county_lookup, state_lookup = _fips_lookups(tables["fips"])
    data_names = [name for name in tables if name != "fips"]
    profiles: list[dict] = []

    county_keys = sorted(tables["population"]["county_fips"].dropna().unique())
    print(f"Building {len(county_keys)} county profiles...", flush=True)
    for code in tqdm(county_keys):
        if code not in county_lookup:
            continue
        county_name, state_abbr = county_lookup[code]
        frames = {name: tables[name][tables[name]["county_fips"] == code] for name in data_names}
        profiles.append(_profile(frames, {
            "geo_level": "County", "geo_code": code, "name": county_name,
            "state_abbr": state_abbr,
        }))

    state_keys = sorted(tables["population"]["state_fips"].dropna().unique())
    print(f"Building {len(state_keys)} state/territory profiles...", flush=True)
    for code in tqdm(state_keys):
        state_abbr = state_lookup.get(code)
        if not state_abbr:
            continue
        frames = {name: tables[name][tables[name]["state_fips"] == code] for name in data_names}
        profiles.append(_profile(frames, {
            "geo_level": "State", "geo_code": code,
            "name": STATE_NAMES.get(state_abbr, state_abbr), "state_abbr": state_abbr,
        }))

    national_state_codes = {
        code for code, abbreviation in state_lookup.items()
        if abbreviation in STATE_NAMES and abbreviation != "PR"
    }
    profiles.append(_profile(
        {
            name: tables[name][tables[name]["state_fips"].isin(national_state_codes)]
            for name in data_names
        },
        {"geo_level": "National", "geo_code": "US", "name": "United States", "state_abbr": "US"},
    ))
    return profiles


def _present(value) -> bool:
    return value is not None and not (isinstance(value, float) and math.isnan(value))


def format_number(value, prefix: str = "", suffix: str = "") -> str:
    if not _present(value):
        return "N/A"
    return f"{prefix}{int(round(float(value))):,}{suffix}"


def county_to_document(row: pd.Series | dict) -> str:
    """Format any county/state/national profile as a retrieval document."""
    get = row.get
    level = get("geo_level", "County")
    name = get("name") or get("county_name", "Unknown geography")
    state_abbr = get("state_abbr", "")
    if level == "County":
        geography = f"{name}, {state_abbr}"
    elif level == "State":
        geography = f"{name} ({state_abbr})"
    else:
        geography = str(name)

    lines = [
        f"GEOGRAPHY: {geography}",
        f"GEOGRAPHIC CODE: {get('geo_code', get('county_fips', 'N/A'))} | LEVEL: {level} | DATA SOURCE: {DATA_YEAR}",
        "POPULATION & DEMOGRAPHICS:",
    ]
    metrics = [
        ("total_population", "Total Population", "number"),
        ("pct_male", "Male", "percent"),
        ("pct_under_18", "Under 18", "percent"),
        ("pct_over_65", "65 and older", "percent"),
        ("pct_white_alone", "White alone", "percent"),
        ("pct_black_alone", "Black or African American alone", "percent"),
        ("pct_asian_alone", "Asian alone", "percent"),
        ("pct_two_or_more_races", "Two or more races", "percent"),
        ("pct_hispanic_or_latino", "Hispanic or Latino", "percent"),
    ]
    for field, label, kind in metrics:
        value = get(field)
        if _present(value):
            rendered = format_number(value) if kind == "number" else f"{float(value):.1f}%"
            lines.append(f"  {label}: {rendered}")

    lines.append("INCOME & POVERTY:")
    if _present(get("median_household_income")):
        lines.append(f"  Estimated Median Household Income: {format_number(get('median_household_income'), '$')}")
    if _present(get("poverty_rate")):
        lines.append(f"  Poverty Rate: {float(get('poverty_rate')):.1f}%")
    if _present(get("population_below_poverty")):
        lines.append(f"  Population Below Poverty: {format_number(get('population_below_poverty'))}")

    lines.append("HOUSING:")
    housing = [
        ("total_housing_units", "Total Housing Units", "number"),
        ("pct_occupied", "Occupied", "percent"),
        ("homeownership_rate", "Homeownership Rate", "percent"),
        ("median_home_value", "Estimated Median Home Value", "currency"),
        ("median_gross_rent", "Estimated Median Gross Rent", "rent"),
    ]
    for field, label, kind in housing:
        value = get(field)
        if not _present(value):
            continue
        if kind == "percent": rendered = f"{float(value):.1f}%"
        elif kind == "currency": rendered = format_number(value, "$")
        elif kind == "rent": rendered = format_number(value, "$", "/month")
        else: rendered = format_number(value)
        lines.append(f"  {label}: {rendered}")

    lines.append("EMPLOYMENT:")
    for field, label in [
        ("unemployment_rate", "Unemployment Rate"),
        ("labor_force_participation_rate", "Labor Force Participation Rate"),
    ]:
        if _present(get(field)):
            lines.append(f"  {label}: {float(get(field)):.1f}%")

    lines.append("EDUCATION (population 25+):")
    for field, label in [
        ("pct_hs_or_higher", "High School Diploma or Higher"),
        ("pct_bachelors_or_higher", "Bachelor's Degree or Higher"),
    ]:
        if _present(get(field)):
            lines.append(f"  {label}: {float(get(field)):.1f}%")

    lines.append("NOTE: Median income, home value, and rent are estimates derived from aggregated ACS distribution bins.")
    return "\n".join(lines)


def _filename(profile: dict) -> str:
    clean_name = re.sub(r"[^A-Za-z0-9_-]+", "_", profile["name"]).strip("_")
    if profile["geo_level"] == "County":
        return f"{profile['state_abbr']}_{clean_name}_{profile['geo_code']}.txt"
    if profile["geo_level"] == "State":
        return f"STATE_{profile['state_abbr']}_{clean_name}_{profile['geo_code']}.txt"
    return "US_United_States.txt"


def run() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for previous in OUTPUT_DIR.glob("*.txt"):
        previous.unlink()

    print("Census Chat ETL: SafeGraph ACS 2020 -> stable RAG documents")
    tables = load_census_tables()
    profiles = build_geography_profiles(tables)
    for profile in tqdm(profiles, desc="Writing documents"):
        (OUTPUT_DIR / _filename(profile)).write_text(
            county_to_document(profile), encoding="utf-8"
        )
    print(f"Wrote {len(profiles)} documents to {OUTPUT_DIR}")
    return len(profiles)


if __name__ == "__main__":
    run()
