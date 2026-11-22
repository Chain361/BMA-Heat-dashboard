"""รัน pipeline ทั้งหมดด้วยคำสั่งเดียว

  python run_pipeline.py               # ingest (GEE) → process → analyze
  python run_pipeline.py --skip-ingest # ใช้ข้อมูลดิบใน data/raw/ ที่มีอยู่ (ไม่ต้องมีบัญชี GEE)

ทุก run ได้ run_id และถูกบันทึกในตาราง runs (สถานะ, git commit, sha256 ของไฟล์ input, config)
"""
import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone

import duckdb

from pipeline import config as C
from pipeline import analyze, process
from pipeline.process import SCHEMA


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_commit():
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=C.ROOT, capture_output=True,
                             text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=C.ROOT, capture_output=True,
                               text=True).stdout.strip()
        return sha + ("-dirty" if dirty else "")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-ingest", action="store_true", help="ไม่ดึงข้อมูลใหม่จาก GEE")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # ภาษาไทยบน Windows console

    now = datetime.now(timezone.utc)
    run_id = now.strftime("%Y%m%dT%H%M%SZ")
    print(f"== run {run_id} ==")

    if not args.skip_ingest:
        print("[1/3] ingest")
        from pipeline import ingest  # import ตอนใช้ — ไม่ต้องติดตั้ง/ยืนยันตัวตน GEE ถ้า --skip-ingest
        ingest.run()
    else:
        print("[1/3] ingest — ข้าม (ใช้ data/raw/ ที่มีอยู่)")

    inputs = {p.name: sha256(p) for p in (C.CELLS_CSV, C.META_JSON, C.DISTRICTS_GEOJSON)}
    cfg = {k: v for k, v in vars(C).items() if k.isupper() and not hasattr(v, "exists")}
    lineage = {"git_commit": git_commit(), "inputs": inputs}

    con = duckdb.connect(str(C.WAREHOUSE))
    con.execute(SCHEMA)
    con.execute("INSERT INTO runs VALUES (?, ?, NULL, 'running', ?, ?, ?, NULL)",
                [run_id, now, lineage["git_commit"], json.dumps(inputs), json.dumps(cfg, default=str)])
    con.close()

    def finish(status, n=None):
        with duckdb.connect(str(C.WAREHOUSE)) as c:
            c.execute("UPDATE runs SET status = ?, finished_at = now(), n_cells = ? WHERE run_id = ?",
                      [status, n, run_id])

    try:
        print("[2/3] validate + integrate + store")
        n = process.run(run_id, lineage)
        print("[3/3] analyze + publish")
        analyze.run(run_id)
    except SystemExit:
        finish("failed_quality")
        raise
    except Exception:
        finish("failed")
        raise
    finish("success", n)
    print(f"== เสร็จ: run {run_id} ==")


if __name__ == "__main__":
    main()
