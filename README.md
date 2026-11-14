# Smart Factory ETL Pipeline

Python ETL pipeline that cleans and integrates machine telemetry and QC inspection
logs into a SQLite database (`smart_factory.db`).

## Layout
```
etl_pipeline.py              # the ETL pipeline (Extract -> Transform -> Load)
data/iot_telemetry_data.csv  # source 1 (UTF-8)
data/qc_inspection_logs.csv  # source 2 (ISO-8859-1)
smart_factory.db             # output database (3 tables)
report/ETL_Engineering_Report.pdf
report/build_report.py       # regenerates the report figures + PDF from the database
```

## Run
```bash
pip install -r requirements.txt
python etl_pipeline.py                 # rebuilds smart_factory.db
python report/build_report.py          # optional: rebuild the report
```
Options: `--data-dir <folder>` and `--db <file>`.

## Output tables
| table | rows | key |
|---|---|---|
| sensor_readings | 6,971 | record_id (SHA-1 based) |
| quality_checks | 1,463 | check_id AUTOINCREMENT |
| hourly_summary | 3,801 | summary_id AUTOINCREMENT |
