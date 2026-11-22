"""ค่าตั้งของ pipeline ทั้งหมดอยู่ที่นี่ที่เดียว (ingest, quality checks และ analysis ใช้ร่วมกัน)"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"                  # zone 1: ข้อมูลดิบ (ห้ามแก้หลัง ingest)
WAREHOUSE = ROOT / "data" / "warehouse.duckdb"  # zone 2: ข้อมูลที่ผ่านการตรวจและเชื่อมโยงแล้ว
SERVING = ROOT / "output"                    # zone 3: ไฟล์สำหรับเว็บ/รายงาน
REPORTS = ROOT / "data" / "quality_reports"  # quality report ของทุก run

CELLS_CSV = RAW / "landsat_cells_250m.csv"
META_JSON = RAW / "landsat_meta.json"
DISTRICTS_GEOJSON = RAW / "Bangkok-districts.geojson"

# ---- ingest (Google Earth Engine) ----
START, END = "2026-03-01", "2026-06-01"  # ช่วงหน้าร้อน มี.ค.–พ.ค.
MIN_CLEAR = 0.8          # สัดส่วนพิกเซลปลอดเมฆขั้นต่ำของแต่ละ scene ภายใน AOI
GRID_M = 250             # ขนาดกริด (เมตร)
CRS = "EPSG:32647"       # UTM 47N
AOI_BUFFER_M = 1000      # buffer รอบขอบเขต กทม. กันกริดริมขอบหาย

# ---- quality checks ----
LST_RANGE = (40, 60)     # °C
MIN_DATES = 3            # จำนวนวันที่ถ่ายภาพ (ไม่ซ้ำ) ขั้นต่ำ
MIN_OBS = 2              # จำนวน observation ปลอดเมฆต่อพิกเซลขั้นต่ำ
MIN_OBS_SHARE = 0.90     # สัดส่วนกริดที่ต้องมี n_obs ≥ MIN_OBS
MIN_DISTRICT_COVERAGE = 0.5  # กริดที่ได้ / กริดที่ควรได้ ต่อเขต (ต่ำกว่านี้ = เตือน)
N_DISTRICTS = 50
BBOX = (100.3, 13.45, 101.0, 14.0)  # lon_min, lat_min, lon_max, lat_max (เผื่อขอบ)

# ---- analysis ----
CLUSTER_BLOCK_M = 2500   # ขนาดบล็อกสำหรับ cluster-robust SE
MORAN_K = 8
