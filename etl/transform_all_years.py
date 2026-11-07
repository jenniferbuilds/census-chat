"""Build one 2016–2020 time-series RAG document per Census geography.

This reads each SafeGraph archive without unpacking it, aggregates additive ACS
estimates from block groups, derives ratios from summed counts, and estimates
medians from aggregate distributions. One document contains every available year
for a geography, keeping Titan ingestion to roughly one vector per geography.
"""

from __future__ import annotations

import math
import re
import tarfile
from collections import defaultdict
from pathlib import Path

import pandas as pd
from tqdm import tqdm

from transform import HOME_VALUE_BINS, INCOME_BINS, RENT_BINS, STATE_NAMES

ROOT = Path("/Users/jennifer_chen/Documents/projects")
OUTPUT_DIR = Path(__file__).parent / "output" / "documents"
YEARS = (2016, 2017, 2018, 2019, 2020)

TABLE_FILES = {
    "population": "cbg_b01.csv",
    "race": "cbg_b02.csv",
    "ethnicity": "cbg_b03.csv",
    "education": "cbg_b15.csv",
    "poverty": "cbg_b17.csv",
    "income": "cbg_b19.csv",
    "employment": "cbg_b23.csv",
    "housing": "cbg_b25.csv",
}
# Tables that may be absent in earlier SafeGraph releases.
OPTIONAL_TABLES = {"poverty"}
NEEDED_COLUMNS = {
    "population": {
        "B01001e1", "B01001e2",
        *{f"B01001e{i}" for i in (*range(3, 7), *range(20, 26), *range(27, 31), *range(44, 50))},
    },
    "race": {"B02001e1", "B02001e2", "B02001e3", "B02001e5", "B02001e8"},
    "ethnicity": {"B03003e1", "B03003e3"},
    "education": {"B15003e1", *{f"B15003e{i}" for i in range(17, 26)}},
    "poverty": {"B17021e1", "B17021e2"},
    "income": {f"B19001e{i}" for i in range(2, 18)},
    "employment": {"B23025e1", "B23025e2", "B23025e3", "B23025e5"},
    "housing": {
        "B25001e1", "B25002e1", "B25002e2", "B25003e1", "B25003e2",
        *{f"B25075e{i}" for i in range(2, 28)},
        *{f"B25063e{i}" for i in range(3, 27)},
    },
}


def _archive(year: int) -> Path:
    path = ROOT / f"safegraph_open_census_data_{year}.tar.gz"
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def _read_member(year: int, relative_path: str, **kwargs) -> pd.DataFrame:
    member = f"safegraph_open_census_data_{year}/{relative_path}"
    with tarfile.open(_archive(year), "r:gz") as archive:
        handle = archive.extractfile(member)
        if handle is None:
            raise FileNotFoundError(member)
        return pd.read_csv(handle, **kwargs)


def _lookups(year: int) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    frame = _read_member(year, "metadata/cbg_fips_codes.csv", dtype=str)
    frame["state_fips"] = frame["state_fips"].str.zfill(2)
    frame["county_fips"] = frame["county_fips"].str.zfill(3)
    frame["full_fips"] = frame["state_fips"] + frame["county_fips"]
    counties = {
        row.full_fips: (row.county, row.state)
        for row in frame.itertuples()
        if isinstance(row.county, str) and isinstance(row.state, str)
    }
    states = {
        row.state_fips: row.state
        for row in frame.drop_duplicates("state_fips").itertuples()
        if isinstance(row.state, str)
    }
    return counties, states


def _aggregate_table(
    year: int,
    table_name: str,
    national_state_codes: set[str],
) -> dict[str, pd.DataFrame | pd.Series]:
    frame = _read_member(
        year,
        f"data/{TABLE_FILES[table_name]}",
        dtype={"census_block_group": str},
    )
    frame["census_block_group"] = frame["census_block_group"].astype(str).str.zfill(12)
    frame["county_fips"] = frame["census_block_group"].str[:5]
    frame["state_fips"] = frame["census_block_group"].str[:2]

    columns = sorted(NEEDED_COLUMNS[table_name].intersection(frame.columns))
    numeric = frame[columns].apply(pd.to_numeric, errors="coerce")
    numeric = numeric.where(numeric >= 0)
    numeric["county_fips"] = frame["county_fips"]
    numeric["state_fips"] = frame["state_fips"]

    return {
        "county": numeric.groupby("county_fips", sort=False)[columns].sum(min_count=1),
        "state": numeric.groupby("state_fips", sort=False)[columns].sum(min_count=1),
        "national": numeric.loc[numeric["state_fips"].isin(national_state_codes), columns].sum(min_count=1),
    }


def _value(rows: dict[str, pd.Series], table: str, column: str) -> float | None:
    row = rows.get(table)
    if row is None or column not in row.index:
        return None
    value = pd.to_numeric(pd.Series([row[column]]), errors="coerce").iloc[0]
    if pd.isna(value) or float(value) < 0:
        return None
    return float(value)


def _sum_columns(rows: dict[str, pd.Series], table: str, columns) -> float | None:
    values = [_value(rows, table, column) for column in columns]
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _percentage(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return round(100 * numerator / denominator, 1)


def _median_from_bins(
    rows: dict[str, pd.Series],
    table: str,
    columns: list[str],
    bins: list[tuple[float, float]],
) -> float | None:
    counts = [_value(rows, table, column) or 0.0 for column in columns]
    total = sum(counts)
    if total <= 0:
        return None
    target, cumulative = total / 2, 0.0
    for count, (lower, upper) in zip(counts, bins):
        if cumulative + count >= target:
            fraction = 0 if count == 0 else (target - cumulative) / count
            return round(lower + fraction * (upper - lower))
        cumulative += count
    return round(bins[-1][1])


def _profile(rows: dict[str, pd.Series], identity: dict[str, str]) -> dict[str, object]:
    population = _value(rows, "population", "B01001e1")
    race_total = _value(rows, "race", "B02001e1") or population
    ethnicity_total = _value(rows, "ethnicity", "B03003e1") or population
    poverty_total = _value(rows, "poverty", "B17021e1")
    below_poverty = _value(rows, "poverty", "B17021e2")
    housing_total = _value(rows, "housing", "B25002e1") or _value(rows, "housing", "B25001e1")
    occupied = _value(rows, "housing", "B25002e2")
    tenure_total = _value(rows, "housing", "B25003e1") or occupied
    civilian_labor = _value(rows, "employment", "B23025e3")
    population_25 = _value(rows, "education", "B15003e1")

    return {
        **identity,
        "population": population,
        "under_18": _percentage(
            _sum_columns(rows, "population", [f"B01001e{i}" for i in (*range(3, 7), *range(27, 31))]), population
        ),
        "age_65_plus": _percentage(
            _sum_columns(rows, "population", [f"B01001e{i}" for i in (*range(20, 26), *range(44, 50))]), population
        ),
        "white_alone": _percentage(_value(rows, "race", "B02001e2"), race_total),
        "black_alone": _percentage(_value(rows, "race", "B02001e3"), race_total),
        "asian_alone": _percentage(_value(rows, "race", "B02001e5"), race_total),
        "hispanic_latino": _percentage(_value(rows, "ethnicity", "B03003e3"), ethnicity_total),
        "median_household_income": _median_from_bins(
            rows, "income", [f"B19001e{i}" for i in range(2, 18)], INCOME_BINS
        ),
        "poverty_rate": _percentage(below_poverty, poverty_total),
        "homeownership_rate": _percentage(_value(rows, "housing", "B25003e2"), tenure_total),
        "median_home_value": _median_from_bins(
            rows, "housing", [f"B25075e{i}" for i in range(2, 28)], HOME_VALUE_BINS
        ),
        "median_gross_rent": _median_from_bins(
            rows, "housing", [f"B25063e{i}" for i in range(3, 27)], RENT_BINS
        ),
        "unemployment_rate": _percentage(_value(rows, "employment", "B23025e5"), civilian_labor),
        "bachelors_plus": _percentage(
            _sum_columns(rows, "education", [f"B15003e{i}" for i in range(22, 26)]), population_25
        ),
    }


def build_year(year: int) -> dict[tuple[str, str], dict[str, object]]:
    print(f"\n=== {year} ===", flush=True)
    counties, states = _lookups(year)
    national_codes = {
        code for code, abbreviation in states.items()
        if abbreviation in STATE_NAMES and abbreviation != "PR"
    }
    aggregates = {}
    for table in TABLE_FILES:
        try:
            print(f"  aggregating {table}", flush=True)
            aggregates[table] = _aggregate_table(year, table, national_codes)
        except (FileNotFoundError, KeyError) as error:
            if table not in OPTIONAL_TABLES:
                raise
            print(f"  ⚠️  {table} not found for {year}, skipping", flush=True)

    result: dict[tuple[str, str], dict[str, object]] = {}
    county_codes = aggregates["population"]["county"].index
    for code in county_codes:
        if code not in counties:
            continue
        name, abbreviation = counties[code]
        rows = {
            table: values["county"].loc[code]
            for table, values in aggregates.items()
            if code in values["county"].index
        }
        result[("County", code)] = _profile(rows, {
            "level": "County", "code": code, "name": name, "state": abbreviation,
        })

    for code in aggregates["population"]["state"].index:
        abbreviation = states.get(code)
        if not abbreviation:
            continue
        rows = {
            table: values["state"].loc[code]
            for table, values in aggregates.items()
            if code in values["state"].index
        }
        result[("State", code)] = _profile(rows, {
            "level": "State", "code": code,
            "name": STATE_NAMES.get(abbreviation, abbreviation), "state": abbreviation,
        })

    result[("National", "US")] = _profile(
        {table: values["national"] for table, values in aggregates.items()},
        {"level": "National", "code": "US", "name": "United States", "state": "US"},
    )
    print(f"  built {len(result)} geographies", flush=True)
    return result


def _present(value: object) -> bool:
    return value is not None and not (isinstance(value, float) and math.isnan(value))


def _number(value: object, prefix: str = "") -> str:
    return f"{prefix}{float(value):,.0f}"


def _metrics(profile: dict[str, object]) -> list[str]:
    specs = [
        ("population", "population", "number"),
        ("under_18", "under 18", "percent"),
        ("age_65_plus", "age 65+", "percent"),
        ("white_alone", "White alone", "percent"),
        ("black_alone", "Black alone", "percent"),
        ("asian_alone", "Asian alone", "percent"),
        ("hispanic_latino", "Hispanic or Latino", "percent"),
        ("median_household_income", "estimated median household income", "currency"),
        ("poverty_rate", "poverty rate", "percent"),
        ("homeownership_rate", "homeownership", "percent"),
        ("median_home_value", "estimated median home value", "currency"),
        ("median_gross_rent", "estimated median gross rent", "currency"),
        ("unemployment_rate", "unemployment", "percent"),
        ("bachelors_plus", "bachelor's degree or higher", "percent"),
    ]
    rendered = []
    for field, label, kind in specs:
        value = profile.get(field)
        if not _present(value):
            continue
        if kind == "percent": text = f"{float(value):.1f}%"
        elif kind == "currency": text = _number(value, "$")
        else: text = _number(value)
        rendered.append(f"{label}: {text}")
    return rendered


def single_year_document(year: int, profile: dict[str, object]) -> str:
    level, code, name, state = profile["level"], profile["code"], profile["name"], profile["state"]
    geography = f"{name}, {state}" if level == "County" else (f"{name} ({state})" if level == "State" else name)
    lines = [
        f"GEOGRAPHY: {geography}",
        f"CODE: {code} | LEVEL: {level} | YEAR: {year}",
        "SOURCE: SafeGraph ACS 5-year estimates.",
    ]
    for metric in _metrics(profile):
        lines.append(f"  {metric}")
    return "\n".join(str(line) for line in lines)


def combined_document(years: dict[int, dict[str, object]]) -> str:
    """Ultra-compact multi-year document under 2048 bytes for S3 Vectors."""
    latest = years[max(years)]
    level, code, name = latest["level"], latest["code"], latest["name"]
    state = latest.get("state", "")
    
    # Ultra-short geography name
    geo = f"{name}, {state}" if level == "County" else (f"{name} ({state})" if level == "State" else name)
    if len(geo) > 40:
        geo = geo[:37] + "..."
    
    lines = [f"GEO: {geo}", f"CODE: {code} | LVL: {level}"]
    
    # Extract only 5 key metrics to fit under 2KB
    key_metrics = ["population", "median household income", "poverty rate", "homeownership", "unemployment"]
    
    for metric_name in key_metrics:
        values = []
        for year in sorted(years.keys()):
            profile = years[year]
            found = False
            for full_text in _metrics(profile):
                lower = full_text.lower()
                if metric_name in lower:
                    # Extract just the number/percentage
                    value = full_text.split(":", 1)[1].strip() if ":" in full_text else ""
                    # Shorten: remove commas, units
                    value = value.replace(",", "").replace(" ", "")
                    values.append(value)
                    found = True
                    break
            if not found:
                values.append("—")
        
        # Ultra-short metric names
        short = {
            "population": "pop",
            "median household income": "medinc",
            "poverty rate": "pov",
            "homeownership": "own",
            "unemployment": "unemp"
        }[metric_name]
        
        lines.append(f"{short}: " + "|".join(values))
    
    return "\n".join(lines)


def filename(profile: dict[str, object], year: int | None = None) -> str:
    clean = re.sub(r"[^A-Za-z0-9_-]+", "_", str(profile["name"])).strip("_")
    year_suffix = f"_{year}" if year else ""
    if profile["level"] == "County":
        return f"{profile['state']}_{clean}_{profile['code']}{year_suffix}.txt"
    if profile["level"] == "State":
        return f"STATE_{profile['state']}_{clean}_{profile['code']}{year_suffix}.txt"
    return f"US_United_States{year_suffix}.txt"


def run() -> int:
    by_geography: dict[tuple[str, str], dict[int, dict[str, object]]] = defaultdict(dict)
    for year in YEARS:
        for key, profile in build_year(year).items():
            by_geography[key][year] = profile

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in OUTPUT_DIR.glob("*.txt"):
        path.unlink()

    count = 0
    for yearly_profiles in tqdm(by_geography.values(), desc="Writing documents"):
        latest = yearly_profiles[max(yearly_profiles)]
        
        # One compact combined document (< 2048 bytes for S3 Vectors metadata)
        (OUTPUT_DIR / filename(latest)).write_text(
            combined_document(yearly_profiles), encoding="utf-8"
        )
        count += 1
        
        # One document per year for specific-year questions
        for year, profile in yearly_profiles.items():
            (OUTPUT_DIR / filename(profile, year)).write_text(
                single_year_document(year, profile), encoding="utf-8"
            )
            count += 1

    print(f"Wrote {count} documents (combined + year-specific) to {OUTPUT_DIR}")
    return count


if __name__ == "__main__":
    run()
