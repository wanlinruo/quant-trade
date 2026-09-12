#!/usr/bin/env bash
# 单年度付费分钟数据接力：等待下载、暂存、初检、严格隔离、复检。
# 故意不自动发布；发布前仍需人工审阅质量差异并复核分片与 ZIP 哈希。
set -euo pipefail

year="${1:?usage: process_paid_minute_year.sh YEAR}"
project="/home/ubuntu/qsys"
warehouse="$project/warehouse"
zip="/data/qsys/baidupan/A股分钟行情/$year.zip"
download_unit="qsys-paid-minute-download-$year.service"

while systemctl is-active --quiet "$download_unit"; do
  sleep 10
done
test -s "$zip"

cd "$project"
nice -n 10 .venv/bin/python -u scripts/ingest_paid_minute.py stage \
  --zip "$zip" --warehouse "$warehouse" --year "$year" --shard-members 32
nice -n 10 .venv/bin/python -u scripts/ingest_paid_minute.py validate \
  --warehouse "$warehouse" --year "$year"
nice -n 10 .venv/bin/python -u scripts/ingest_paid_minute.py quarantine-placeholders \
  --warehouse "$warehouse" --year "$year"
nice -n 10 .venv/bin/python -u scripts/ingest_paid_minute.py validate \
  --warehouse "$warehouse" --year "$year"
