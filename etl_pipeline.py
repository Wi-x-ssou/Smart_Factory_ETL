"""
Smart Factory ETL Pipeline
==========================

Extracts machine telemetry and QC inspection logs from CSV, cleans and
integrates them, and loads the result into a SQLite database.

    python etl_pipeline.py                      # defaults: ./data/*.csv -> ./smart_factory.db
    python etl_pipeline.py --data-dir data --db smart_factory.db

Pipeline
--------
EXTRACT    extract_sensor_data()   -> last 7 days of telemetry (relative to max timestamp)
           extract_qc_data()       -> ISO-8859-1 QC log, INSPECTION_STATUS == 'Completed'
TRANSFORM  standardize_sensor_schema() / standardize_qc_schema()
           clean_sensor_data()     -> error codes -> NaN, bounds check, ffill, data_quality
           add_record_id()
           join_sensor_quality()   -> LEFT JOIN on (timestamp, line_id, machine_id)
           aggregate_hourly()
LOAD       load_to_sqlite()        -> sensor_readings, quality_checks, hourly_summary
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = BASE_DIR / "data"
DEFAULT_DB_PATH = BASE_DIR / "smart_factory.db"

SENSOR_FILE = "iot_telemetry_data.csv"
QC_FILE = "qc_inspection_logs.csv"
SENSOR_ENCODING = "utf-8"
QC_ENCODING = "ISO-8859-1"

LOOKBACK_DAYS = 7
ERROR_CODES = [-999.0, -1.0]
KEY_COLS = ["timestamp", "line_id", "machine_id"]
TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S"

# Raw telemetry column -> standard name
SENSOR_COLUMN_MAP = {
    "timestamp": "timestamp",
    "line_id": "line_id",
    "machine_id": "machine_id",
    "temp_c": "temperature",
    "pressure_bar": "pressure",
    "vibration_mms": "vibration",
    "power_kw": "power",
}

# Raw QC column -> telemetry-compatible name
QC_COLUMN_MAP = {
    "INSPECTION_TIME": "timestamp",
    "LINE_CODE": "line_id",
    "EQUIPMENT_TAG": "machine_id",
    "INSPECTION_STATUS": "inspection_status",
    "QC_RESULT": "qc_result",
    "DEFECT_TYPE": "defect_type",
}

# Valid operating bounds (inclusive)
OPERATING_BOUNDS = {
    "temperature": (0.0, 150.0),   # deg C
    "pressure": (0.0, 10.0),       # bar
    "vibration": (0.0, 100.0),     # mm/s
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("etl")


class ETLError(Exception):
    """Raised when a pipeline stage cannot continue."""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def normalize_column_name(name: str) -> str:
    """'Line ID' -> 'line_id', 'Temp_C' -> 'temp_c'."""
    return "_".join(str(name).strip().lower().replace("-", " ").split())


def _read_csv(path: Path, encoding: str) -> pd.DataFrame:
    """Read a CSV with consistent error handling."""
    try:
        df = pd.read_csv(path, encoding=encoding, keep_default_na=True)
    except FileNotFoundError as exc:
        raise ETLError(f"Source file not found: {path}") from exc
    except UnicodeDecodeError as exc:
        raise ETLError(f"Could not decode {path.name} as {encoding}: {exc}") from exc
    except pd.errors.EmptyDataError as exc:
        raise ETLError(f"Source file is empty: {path}") from exc
    except pd.errors.ParserError as exc:
        raise ETLError(f"Malformed CSV {path.name}: {exc}") from exc
    if df.empty:
        raise ETLError(f"No rows in {path}")
    return df


# --------------------------------------------------------------------------- #
# Phase 1: EXTRACT
# --------------------------------------------------------------------------- #
def extract_sensor_data(path: Path, lookback_days: int = LOOKBACK_DAYS) -> pd.DataFrame:
    """Read telemetry and keep records within `lookback_days` of the latest timestamp."""
    log.info("EXTRACT  sensor data  <- %s", path.name)
    try:
        df = _read_csv(path, SENSOR_ENCODING)
        df["_source_row"] = np.arange(len(df))           # stable lineage for record_id
        ts = pd.to_datetime(df["Timestamp"], errors="coerce")
        bad_ts = ts.isna().sum()
        if bad_ts:
            log.warning("         %d sensor rows have unparseable timestamps (dropped)", bad_ts)
        max_ts = ts.max()
        cutoff = max_ts - pd.Timedelta(days=lookback_days)
        mask = ts >= cutoff
        out = df.loc[mask].reset_index(drop=True)
    except KeyError as exc:
        raise ETLError(f"Telemetry file is missing expected column {exc}") from exc
    log.info("         %d rows read, window %s .. %s, %d rows kept",
             len(df), cutoff, max_ts, len(out))
    return out


def extract_qc_data(path: Path) -> pd.DataFrame:
    """Read the ISO-8859-1 QC log and keep only completed inspections."""
    log.info("EXTRACT  QC data      <- %s", path.name)
    try:
        df = _read_csv(path, QC_ENCODING)
        status = df["INSPECTION_STATUS"].astype(str).str.strip()
        out = df.loc[status == "Completed"].reset_index(drop=True)
    except KeyError as exc:
        raise ETLError(f"QC file is missing expected column {exc}") from exc
    log.info("         %d rows read, %d with INSPECTION_STATUS == 'Completed'", len(df), len(out))
    return out


# --------------------------------------------------------------------------- #
# Phase 2: TRANSFORM
# --------------------------------------------------------------------------- #
def standardize_sensor_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Lower-case/underscore column names, map to standard names, parse timestamps."""
    df = df.copy()
    df.columns = [c if c.startswith("_") else normalize_column_name(c) for c in df.columns]
    df = df.rename(columns=SENSOR_COLUMN_MAP)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"])
    for col in ("line_id", "machine_id"):
        df[col] = df[col].astype(str).str.strip()
    for col in ("temperature", "pressure", "vibration", "power"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def standardize_qc_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Rename QC columns to the telemetry schema and parse timestamps."""
    df = df.rename(columns=QC_COLUMN_MAP)
    df.columns = [normalize_column_name(c) for c in df.columns]
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"])
    for col in ("line_id", "machine_id", "qc_result"):
        df[col] = df[col].astype(str).str.strip()
    df["qc_result"] = df["qc_result"].str.upper()
    # The source writes the literal 'None' for "no defect"; store it as NULL.
    df["defect_type"] = df["defect_type"].replace({"None": np.nan, "": np.nan})
    return df


def clean_sensor_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    1. Replace error codes (-999.0, -1.0) and nulls with NaN.
    2. Values outside the operating bounds are invalid too.
    3. Invalid readings are forward-filled from the previous valid reading of the
       *same machine* (rows sorted by time within (line_id, machine_id)).
    4. data_quality = 'estimated' if any value in the row was filled, else 'good'.
    """
    df = df.sort_values(["line_id", "machine_id", "timestamp", "_source_row"]).reset_index(drop=True)
    measured = list(OPERATING_BOUNDS)

    df[measured] = df[measured].replace(ERROR_CODES, np.nan)
    error_or_null = df[measured].isna()

    out_of_range = pd.DataFrame(False, index=df.index, columns=measured)
    for col, (lo, hi) in OPERATING_BOUNDS.items():
        out_of_range[col] = df[col].notna() & ~df[col].between(lo, hi)
        df.loc[out_of_range[col], col] = np.nan

    invalid = error_or_null | out_of_range
    df[measured] = df.groupby(["line_id", "machine_id"], sort=False)[measured].ffill()

    df["data_quality"] = np.where(invalid.any(axis=1), "estimated", "good")

    unfilled = df[measured].isna().sum()
    log.info("TRANSFORM clean: error codes/nulls=%d, out-of-range=%d, rows estimated=%d",
             int(error_or_null.values.sum()), int(out_of_range.values.sum()),
             int((df["data_quality"] == "estimated").sum()))
    for col, (lo, hi) in OPERATING_BOUNDS.items():
        log.info("          %-11s [%g, %g]  error/null=%4d  out-of-range=%4d",
                 col, lo, hi, int(error_or_null[col].sum()), int(out_of_range[col].sum()))
    if unfilled.any():
        log.warning("          %d values left NaN (no earlier valid reading for that machine)",
                    int(unfilled.sum()))
    return df


def add_record_id(df: pd.DataFrame) -> pd.DataFrame:
    """
    Deterministic record_id = SHA-1 of (timestamp, line_id, machine_id, source row).
    The source row is included because the raw file contains repeated
    (timestamp, line_id, machine_id) readings.
    """
    df = df.copy()
    keys = (df["timestamp"].dt.strftime(TIMESTAMP_FMT) + "|" + df["line_id"] + "|"
            + df["machine_id"] + "|" + df["_source_row"].astype(str))
    df["record_id"] = [hashlib.sha1(k.encode("utf-8")).hexdigest()[:16] for k in keys]
    if df["record_id"].duplicated().any():
        raise ETLError("record_id collision detected")
    return df


def join_sensor_quality(sensor: pd.DataFrame, qc: pd.DataFrame) -> pd.DataFrame:
    """
    LEFT JOIN sensor <- QC on (timestamp, line_id, machine_id).
    The QC log can contain several completed inspections for the same key; to
    keep exactly one output row per sensor reading, the side used for the join is
    de-duplicated, and a FAIL takes precedence over a PASS (conservative).
    """
    qc_side = (qc.assign(_fail_first=(qc["qc_result"] != "FAIL").astype(int))
                 .sort_values(KEY_COLS + ["_fail_first"])
                 .drop_duplicates(subset=KEY_COLS, keep="first")
                 [KEY_COLS + ["qc_result", "defect_type"]])
    dropped = len(qc) - len(qc_side)
    if dropped:
        log.info("TRANSFORM join: %d duplicate QC keys collapsed before join", dropped)

    joined = sensor.merge(qc_side, on=KEY_COLS, how="left", validate="many_to_one")
    joined["quality_status"] = joined["qc_result"].where(
        joined["qc_result"].isin(["PASS", "FAIL"]), "NOT_CHECKED")

    if len(joined) != len(sensor):
        raise ETLError("Left join changed the sensor row count")
    counts = joined["quality_status"].value_counts().to_dict()
    log.info("TRANSFORM join: %d sensor rows -> %s", len(joined), counts)
    return joined


def aggregate_hourly(joined: pd.DataFrame) -> pd.DataFrame:
    """Hourly metrics per (hour, line_id, machine_id)."""
    df = joined.assign(
        hour=joined["timestamp"].dt.floor("h"),
        _checked=(joined["quality_status"] != "NOT_CHECKED").astype(int),
        _failed=(joined["quality_status"] == "FAIL").astype(int),
    )
    agg = (df.groupby(["hour", "line_id", "machine_id"], as_index=False)
             .agg(avg_temperature=("temperature", "mean"),
                  min_temperature=("temperature", "min"),
                  max_temperature=("temperature", "max"),
                  avg_pressure=("pressure", "mean"),
                  min_pressure=("pressure", "min"),
                  max_pressure=("pressure", "max"),
                  avg_vibration=("vibration", "mean"),
                  min_vibration=("vibration", "min"),
                  max_vibration=("vibration", "max"),
                  total_checks=("_checked", "sum"),
                  defect_count=("_failed", "sum")))
    # defect_rate is undefined (NULL) for hours with no QC check.
    agg["defect_rate"] = np.where(
        agg["total_checks"] > 0,
        agg["defect_count"] / agg["total_checks"].where(agg["total_checks"] > 0) * 100,
        np.nan)
    float_cols = [c for c in agg.columns if c.startswith(("avg_", "min_", "max_"))] + ["defect_rate"]
    agg[float_cols] = agg[float_cols].round(2)
    agg = agg.sort_values(["hour", "line_id", "machine_id"]).reset_index(drop=True)
    log.info("TRANSFORM aggregate: %d hourly rows", len(agg))
    return agg


# --------------------------------------------------------------------------- #
# Phase 3: LOAD
# --------------------------------------------------------------------------- #
SCHEMA_SQL = """
DROP TABLE IF EXISTS sensor_readings;
DROP TABLE IF EXISTS quality_checks;
DROP TABLE IF EXISTS hourly_summary;

CREATE TABLE sensor_readings (
    record_id     TEXT PRIMARY KEY,
    timestamp     TEXT NOT NULL,
    line_id       TEXT NOT NULL,
    machine_id    TEXT NOT NULL,
    temperature   REAL,
    pressure      REAL,
    vibration     REAL,
    power         REAL,
    data_quality  TEXT NOT NULL CHECK (data_quality IN ('good', 'estimated'))
);

CREATE TABLE quality_checks (
    check_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT NOT NULL,
    line_id       TEXT NOT NULL,
    machine_id    TEXT NOT NULL,
    qc_result     TEXT NOT NULL CHECK (qc_result IN ('PASS', 'FAIL')),
    defect_type   TEXT
);

CREATE TABLE hourly_summary (
    summary_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    hour             TEXT NOT NULL,
    line_id          TEXT NOT NULL,
    machine_id       TEXT NOT NULL,
    avg_temperature  REAL,
    min_temperature  REAL,
    max_temperature  REAL,
    avg_pressure     REAL,
    min_pressure     REAL,
    max_pressure     REAL,
    avg_vibration    REAL,
    min_vibration    REAL,
    max_vibration    REAL,
    total_checks     INTEGER NOT NULL,
    defect_count     INTEGER NOT NULL,
    defect_rate      REAL,
    UNIQUE (hour, line_id, machine_id)
);

CREATE INDEX idx_sensor_key ON sensor_readings (timestamp, line_id, machine_id);
CREATE INDEX idx_qc_key     ON quality_checks  (timestamp, line_id, machine_id);
"""


def _to_records(df: pd.DataFrame, columns: list[str]) -> list[tuple]:
    """DataFrame -> list of tuples with timestamps as text and NaN as NULL."""
    out = df[columns].copy()
    for col in columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime(TIMESTAMP_FMT)
    out = out.astype(object).where(out.notna(), None)
    return [tuple(r) for r in out.itertuples(index=False, name=None)]


def _insert(conn: sqlite3.Connection, table: str, df: pd.DataFrame, columns: list[str]) -> int:
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})"
    rows = _to_records(df, columns)
    conn.executemany(sql, rows)
    return len(rows)


def load_to_sqlite(db_path: Path, sensor: pd.DataFrame, qc: pd.DataFrame,
                   hourly: pd.DataFrame) -> None:
    """Rebuild the three tables inside one transaction."""
    log.info("LOAD     -> %s", db_path)
    sensor_cols = ["record_id", "timestamp", "line_id", "machine_id", "temperature",
                   "pressure", "vibration", "power", "data_quality"]
    qc_cols = ["timestamp", "line_id", "machine_id", "qc_result", "defect_type"]
    hourly_cols = [c for c in hourly.columns]

    conn = None
    try:
        conn = sqlite3.connect(db_path)
        with conn:  # commits on success, rolls back on error
            conn.executescript(SCHEMA_SQL)
            n1 = _insert(conn, "sensor_readings",
                         sensor.sort_values(["timestamp", "line_id", "machine_id"]), sensor_cols)
            n2 = _insert(conn, "quality_checks",
                         qc.sort_values(["timestamp", "line_id", "machine_id"]), qc_cols)
            n3 = _insert(conn, "hourly_summary", hourly, hourly_cols)
    except sqlite3.Error as exc:
        raise ETLError(f"SQLite load failed: {exc}") from exc
    finally:
        if conn is not None:
            conn.close()
    log.info("         sensor_readings=%d  quality_checks=%d  hourly_summary=%d", n1, n2, n3)


def verify_load(db_path: Path) -> None:
    """Print row counts and a sample query as a post-load sanity check."""
    with sqlite3.connect(db_path) as conn:
        for table in ("sensor_readings", "quality_checks", "hourly_summary"):
            (n,) = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            log.info("VERIFY   %-16s %6d rows", table, n)
        top = conn.execute("""
            SELECT line_id, machine_id, SUM(total_checks), SUM(defect_count),
                   ROUND(100.0 * SUM(defect_count) / NULLIF(SUM(total_checks), 0), 2)
            FROM hourly_summary GROUP BY line_id, machine_id
            ORDER BY 5 DESC LIMIT 3""").fetchall()
        for row in top:
            log.info("VERIFY   worst defect rate: %s %s checks=%s fails=%s rate=%s%%", *row)
    conn.close()


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run_pipeline(data_dir: Path, db_path: Path) -> None:
    log.info("=" * 64)
    log.info("Smart Factory ETL started")

    # EXTRACT
    raw_sensor = extract_sensor_data(data_dir / SENSOR_FILE)
    raw_qc = extract_qc_data(data_dir / QC_FILE)

    # TRANSFORM
    sensor = standardize_sensor_schema(raw_sensor)
    qc = standardize_qc_schema(raw_qc)
    sensor = clean_sensor_data(sensor)
    sensor = add_record_id(sensor)
    joined = join_sensor_quality(sensor, qc)
    hourly = aggregate_hourly(joined)

    # LOAD
    load_to_sqlite(db_path, sensor, qc, hourly)
    verify_load(db_path)
    log.info("Smart Factory ETL finished successfully")
    log.info("=" * 64)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Smart Factory ETL pipeline")
    p.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR,
                   help="folder containing the two source CSV files")
    p.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="output SQLite file")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        run_pipeline(args.data_dir, args.db)
    except ETLError as exc:
        log.error("ETL failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
