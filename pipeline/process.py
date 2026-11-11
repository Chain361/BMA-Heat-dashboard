"""Stage 2 — Validate + Integrate + Store: data/raw/ → quality report → DuckDB

1) อ่านข้อมูลดิบ 2 แหล่ง (กริด Landsat, ขอบเขตเขต)
2) spatial join กริด → เขต (point-in-polygon)
3) ตรวจคุณภาพทุกข้อแล้วบันทึก report ก่อนเสมอ; มี check ระดับ error ไม่ผ่าน = หยุด ไม่โหลดเข้า warehouse
4) โหลดเข้า DuckDB: districts (dim), cells (fact), scenes, runs (lineage), quality_checks
"""
import json

import duckdb
import numpy as np
import pandas as pd
from matplotlib.path import Path as MplPath

from . import config as C

SCHEMA = """
CREATE TABLE IF NOT EXISTS districts (
  dcode VARCHAR PRIMARY KEY, dname VARCHAR, dname_e VARCHAR, area_km2 DOUBLE);
CREATE TABLE IF NOT EXISTS runs (
  run_id VARCHAR PRIMARY KEY, started_at TIMESTAMP, finished_at TIMESTAMP, status VARCHAR,
  git_commit VARCHAR, inputs JSON, config JSON, n_cells INTEGER);
CREATE TABLE IF NOT EXISTS scenes (
  run_id VARCHAR, scene_id VARCHAR, date DATE, spacecraft VARCHAR,
  wrs_path INTEGER, wrs_row INTEGER, clear_frac DOUBLE);
CREATE TABLE IF NOT EXISTS quality_checks (
  run_id VARCHAR, check_name VARCHAR, category VARCHAR, severity VARCHAR,
  value DOUBLE, threshold VARCHAR, passed BOOLEAN, detail VARCHAR);
CREATE TABLE IF NOT EXISTS cells (
  run_id VARCHAR, lon DOUBLE, lat DOUBLE, dcode VARCHAR REFERENCES districts(dcode),
  lst DOUBLE, ndvi DOUBLE, ndbi DOUBLE, n_obs DOUBLE);
CREATE OR REPLACE VIEW latest_cells AS
  SELECT * FROM cells WHERE run_id = (
    SELECT run_id FROM runs WHERE status = 'success' ORDER BY started_at DESC LIMIT 1);
"""


def load_raw():
    raw = pd.read_csv(C.CELLS_CSV)
    if ".geo" in raw:
        xy = np.array([json.loads(g)["coordinates"] for g in raw[".geo"]])
        raw = raw.drop(columns=".geo").assign(lon=xy[:, 0], lat=xy[:, 1])
    if "n_obs" not in raw:
        raw["n_obs"] = np.nan  # ข้อมูลรุ่นเก่า (export จาก Code Editor) ไม่มีคอลัมน์นี้
    gj = json.loads(C.DISTRICTS_GEOJSON.read_text(encoding="utf-8"))
    meta = json.loads(C.META_JSON.read_text(encoding="utf-8"))
    return raw, gj, meta


def join_districts(df, gj):
    # ponytail: ใช้แค่ขอบนอกของ Polygon (ไฟล์นี้ไม่มีรู/MultiPolygon); เปลี่ยนเป็น geopandas ถ้าเปลี่ยนไฟล์ขอบเขต
    xy = df[["lon", "lat"]].to_numpy()
    dcode = np.full(len(df), None, dtype=object)
    for f in gj["features"]:
        inside = MplPath(f["geometry"]["coordinates"][0]).contains_points(xy)
        dcode[inside & (dcode == None)] = f["properties"]["dcode"]  # noqa: E711
    return df.assign(dcode=dcode)


def quality_checks(raw, df, gj, meta):
    """คืน list ของผลตรวจ: severity error = ต้องผ่าน, warn = เตือนแต่ไปต่อได้, info = รายงานเฉย ๆ"""
    out = []

    def add(name, cat, sev, value, threshold, passed, detail=""):
        out.append({"check_name": name, "category": cat, "severity": sev,
                    "value": None if value is None else float(value), "threshold": str(threshold),
                    "passed": None if passed is None else bool(passed), "detail": detail})

    # schema
    need = ["LST", "NDVI", "NDBI", "lon", "lat"]
    missing = [c for c in need if c not in raw]
    add("มีคอลัมน์ครบ", "schema", "error", len(missing), "0 คอลัมน์ที่ขาด", not missing, ",".join(missing))
    if missing:
        return out
    non_num = [c for c in need if not pd.api.types.is_numeric_dtype(raw[c])]
    add("คอลัมน์เป็นตัวเลข", "schema", "error", len(non_num), "0", not non_num, ",".join(non_num))
    nulls = int(raw[need].isna().any(axis=1).sum())
    add("ไม่มีค่าว่างในคอลัมน์หลัก", "schema", "error", nulls, "0", nulls == 0)
    x0, y0, x1, y1 = C.BBOX
    out_bbox = int((~raw.lon.between(x0, x1) | ~raw.lat.between(y0, y1)).sum())
    add("พิกัดอยู่ในกรอบ กทม.", "schema", "error", out_bbox, f"0 (bbox {C.BBOX})", out_bbox == 0)
    dup = int(raw.round({"lon": 5, "lat": 5}).duplicated(["lon", "lat"]).sum())
    add("ไม่มีกริดซ้ำ", "schema", "error", dup, "0", dup == 0)

    # ช่วงค่า
    lo, hi = C.LST_RANGE
    bad = int((~raw.LST.between(lo, hi)).sum())
    add("LST อยู่ในช่วงที่เป็นไปได้", "range", "error", bad, f"0 กริดนอก {lo}–{hi}°C", bad == 0)
    bad = int((raw[["NDVI", "NDBI"]].abs() > 1).any(axis=1).sum())
    add("NDVI/NDBI อยู่ใน [-1, 1]", "range", "error", bad, "0", bad == 0)

    # integration
    outside = float(df.dcode.isna().mean())
    add("สัดส่วนกริดที่อยู่นอกเขต (ตัดทิ้ง)", "integration", "info", outside, "-", None,
        "มาจาก buffer รอบ AOI ใน GEE")
    inb = df.dropna(subset=["dcode"])
    nd = inb.dcode.nunique()
    add("ครบทุกเขต", "integration", "error", nd, C.N_DISTRICTS, nd == C.N_DISTRICTS)

    # completeness: กริดที่ได้ / กริดที่ควรได้ ต่อเขต (พื้นที่เขตรวมแหล่งน้ำ จึงเป็นค่าต่ำสุดแบบอนุรักษ์)
    cell_km2 = (C.GRID_M / 1000) ** 2
    area = {f["properties"]["dcode"]: f["properties"]["AREA"] / 1e6 for f in gj["features"]}
    cov = inb.groupby("dcode").size() / pd.Series(area).div(cell_km2)
    low = cov[cov < C.MIN_DISTRICT_COVERAGE]
    names = {f["properties"]["dcode"]: f["properties"]["dname_e"] for f in gj["features"]}
    add("coverage ต่ำสุดรายเขต", "completeness", "warn", cov.min(), f"≥ {C.MIN_DISTRICT_COVERAGE}",
        low.empty, ", ".join(f"{names[d]} {v:.0%}" for d, v in low.items()))
    add("coverage รวม", "completeness", "info", len(inb) / (sum(area.values()) / cell_km2), "-", None)

    # temporal
    dates = sorted({s["DATE_ACQUIRED"] for s in meta["scenes"]})
    add("จำนวนวันที่ถ่ายภาพ (ไม่ซ้ำ)", "temporal", "error", len(dates), f"≥ {C.MIN_DATES}",
        len(dates) >= C.MIN_DATES, ", ".join(dates))
    add("จำนวน scene", "temporal", "info", len(meta["scenes"]), "-", None, "1 วันอาจมีหลาย scene (path/row ติดกัน)")
    if inb.n_obs.notna().any():
        share = float((inb.n_obs >= C.MIN_OBS).mean())
        add(f"สัดส่วนกริดที่มี obs ≥ {C.MIN_OBS}", "temporal", "warn", share, f"≥ {C.MIN_OBS_SHARE}",
            share >= C.MIN_OBS_SHARE)
    else:
        add(f"สัดส่วนกริดที่มี obs ≥ {C.MIN_OBS}", "temporal", "warn", None, f"≥ {C.MIN_OBS_SHARE}", None,
            "ข้อมูลดิบไม่มีคอลัมน์ n_obs — รัน ingest ใหม่")
    return out


def run(run_id, lineage):
    raw, gj, meta = load_raw()
    df = join_districts(raw, gj) if {"lon", "lat"} <= set(raw) else raw
    checks = quality_checks(raw, df, gj, meta)
    failed = [c for c in checks if c["severity"] == "error" and c["passed"] is False]

    # บันทึก report ก่อนตัดสินว่าจะไปต่อหรือไม่ — run ที่ fail ก็ต้องตรวจย้อนได้
    C.REPORTS.mkdir(parents=True, exist_ok=True)
    report = {"run_id": run_id, "status": "failed_quality" if failed else "ok", **lineage, "checks": checks}
    (C.REPORTS / f"{run_id}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for c in checks:
        mark = {True: "ผ่าน", False: "ไม่ผ่าน", None: "-"}[c["passed"]]
        v = "" if c["value"] is None else f"{c['value']:.3g}"
        print(f"  [{mark:^6}] ({c['severity']}) {c['check_name']}: {v} {c['detail']}")

    con = duckdb.connect(str(C.WAREHOUSE))
    con.execute(SCHEMA)
    qc = pd.DataFrame(checks).assign(run_id=run_id)
    con.execute("INSERT INTO quality_checks SELECT run_id, check_name, category, severity, value, threshold, "
                "passed, detail FROM qc")
    if failed:
        con.close()
        raise SystemExit(f"quality gate ไม่ผ่าน: {[c['check_name'] for c in failed]} → ดู {C.REPORTS / run_id}.json")

    dist = pd.DataFrame([{**{k: f["properties"][k] for k in ("dcode", "dname", "dname_e")},
                          "area_km2": f["properties"]["AREA"] / 1e6} for f in gj["features"]])
    con.execute("INSERT OR IGNORE INTO districts SELECT * FROM dist")
    sc = pd.DataFrame([{"scene_id": s.get("id"), "date": s["DATE_ACQUIRED"], "spacecraft": s.get("SPACECRAFT_ID"),
                        "wrs_path": s.get("WRS_PATH"), "wrs_row": s.get("WRS_ROW"),
                        "clear_frac": s.get("clear_frac")} for s in meta["scenes"]]).assign(run_id=run_id)
    con.execute("INSERT INTO scenes SELECT run_id, scene_id, date::DATE, spacecraft, wrs_path, wrs_row, clear_frac "
                "FROM sc")
    cells_df = df.dropna(subset=["dcode"]).assign(run_id=run_id)  # ชื่อห้ามซ้ำกับตาราง cells
    con.execute("INSERT INTO cells SELECT run_id, lon, lat, dcode, LST, NDVI, NDBI, n_obs FROM cells_df")
    con.close()
    print(f"โหลดเข้า warehouse: {len(cells_df):,} กริด, {len(sc)} scenes → {C.WAREHOUSE.name}")
    return len(cells_df)
