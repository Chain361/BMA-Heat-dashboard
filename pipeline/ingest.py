"""Stage 1 — Ingest: Landsat 8/9 (Google Earth Engine) → data/raw/

ประมวลผลภาพฝั่ง GEE ทั้งหมด (filter → cloud mask → ดัชนี → median → กริด 250 ม.)
แล้วดาวน์โหลดเฉพาะตารางรายกริด (~MB) กับ metadata ของ scene ที่ใช้

ต้องทำครั้งแรกครั้งเดียว:  earthengine authenticate
และตั้ง Cloud project ที่ลงทะเบียน Earth Engine ไว้:  set EE_PROJECT=<project-id>
"""
import json
import os
import urllib.request
from datetime import datetime, timezone

import ee

from . import config as C

LANDSAT = ["LANDSAT/LC08/C02/T1_L2", "LANDSAT/LC09/C02/T1_L2"]
SCENE_PROPS = ["DATE_ACQUIRED", "SPACECRAFT_ID", "WRS_PATH", "WRS_ROW"]


def _aoi():
    """ขอบเขต กทม. จากไฟล์เขตชุดเดียวกับที่ใช้ join (ไม่พึ่ง boundary ของ GEE)"""
    gj = json.loads(C.DISTRICTS_GEOJSON.read_text(encoding="utf-8"))
    # ปัดพิกัด 4 ตำแหน่ง (~11 ม.) ให้ request เล็กลง; ละเอียดพอสำหรับกริด 250 ม.
    coords = [[[[round(x, 4), round(y, 4)] for x, y in ring] for ring in f["geometry"]["coordinates"]]
              for f in gj["features"]]
    return (ee.Geometry.MultiPolygon(coords, geodesic=False)
            .dissolve(maxError=30).buffer(C.AOI_BUFFER_M, maxError=30))


def _cloud_free(qa):
    return (qa.bitwiseAnd(1 << 1).eq(0)        # dilated cloud
            .And(qa.bitwiseAnd(1 << 3).eq(0))  # cloud
            .And(qa.bitwiseAnd(1 << 4).eq(0)))  # cloud shadow


def _prep(aoi):
    def f(img):
        clear = _cloud_free(img.select("QA_PIXEL"))
        frac = clear.reduceRegion(ee.Reducer.mean(), aoi, 100, maxPixels=1e9).get("QA_PIXEL")
        sr = img.select(["SR_B3", "SR_B4", "SR_B5", "SR_B6"]).multiply(0.0000275).add(-0.2)  # C2 L2 scale
        lst = img.select("ST_B10").multiply(0.00341802).add(149.0).subtract(273.15).rename("LST")
        bands = lst.addBands([
            sr.normalizedDifference(["SR_B5", "SR_B4"]).rename("NDVI"),
            sr.normalizedDifference(["SR_B6", "SR_B5"]).rename("NDBI"),
            sr.normalizedDifference(["SR_B3", "SR_B6"]).rename("MNDWI"),
        ]).updateMask(clear)
        return ee.Image(bands.copyProperties(img, SCENE_PROPS)).set("clear_frac", frac)
    return f


def run():
    project = os.environ.get("EE_PROJECT")
    if not project:
        raise SystemExit("ตั้ง EE_PROJECT=<Cloud project id ที่ลงทะเบียน Earth Engine> ก่อน")
    ee.Initialize(project=project)
    aoi = _aoi()

    col = (ee.ImageCollection(LANDSAT[0]).merge(ee.ImageCollection(LANDSAT[1]))
           .filterBounds(aoi).filterDate(C.START, C.END)
           .filter(ee.Filter.calendarRange(3, 5, "month"))
           .map(_prep(aoi))
           .filter(ee.Filter.gte("clear_frac", C.MIN_CLEAR)))

    # จำนวน observation ปลอดเมฆต่อพิกเซล (ใช้ตรวจคุณภาพ ไม่ใช่แค่นับ scene)
    n_obs = col.select("LST").count().rename("n_obs")
    comp = col.median().addBands(n_obs)
    comp = comp.updateMask(comp.select("MNDWI").lte(0)).select(["LST", "NDVI", "NDBI", "n_obs"])  # ตัดน้ำ

    proj = ee.Projection(C.CRS)
    grid = (comp.setDefaultProjection(proj.atScale(30))
            .reduceResolution(ee.Reducer.mean(), maxPixels=128)
            .reproject(proj.atScale(C.GRID_M))
            .clip(aoi))
    samples = grid.sample(region=aoi, scale=C.GRID_M, projection=proj, geometries=True, tileScale=4)

    scenes = col.toList(100).map(lambda i: ee.Image(i).toDictionary(SCENE_PROPS + ["clear_frac"])
                                 .set("id", ee.Image(i).get("system:index"))).getInfo()
    if not scenes:
        raise SystemExit("ไม่มี scene ที่ผ่านเกณฑ์ปลอดเมฆ — ลองลด MIN_CLEAR หรือเปลี่ยนช่วงเวลาใน config.py")
    print(f"scene ที่ผ่านเกณฑ์: {len(scenes)} | วันที่: {sorted({s['DATE_ACQUIRED'] for s in scenes})}")

    url = samples.getDownloadURL(filetype="csv", selectors=["LST", "NDVI", "NDBI", "n_obs", ".geo"],
                                 filename="cells")
    C.RAW.mkdir(parents=True, exist_ok=True)
    tmp = C.CELLS_CSV.with_suffix(".part")
    print("กำลังดาวน์โหลดตารางรายกริด…")
    with urllib.request.urlopen(url, timeout=900) as r, open(tmp, "wb") as f:
        f.write(r.read())
    tmp.replace(C.CELLS_CSV)  # เขียนไฟล์ใหม่ทับเมื่อดาวน์โหลดครบเท่านั้น

    meta = {
        "source": "USGS Landsat 8/9 Collection 2 Level-2 via Google Earth Engine",
        "collections": LANDSAT, "start": C.START, "end": C.END,
        "min_clear": C.MIN_CLEAR, "grid_m": C.GRID_M, "crs": C.CRS,
        "ingested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scenes": scenes,
    }
    C.META_JSON.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"บันทึก {C.CELLS_CSV} และ {C.META_JSON}")
