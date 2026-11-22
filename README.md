# Urban Heat Vulnerability — Bangkok

CS341 Big Data Engineering · Term Project (ภาคเรียนที่ 1/2569)

ระบบข้อมูลสำหรับตรวจสอบความสัมพันธ์จาก Causal Loop Diagram "Urban Heat Vulnerability" ในกรุงเทพฯ ด้วยข้อมูลดาวเทียม Landsat 8/9 ช่วงหน้าร้อนปี 2026 (มี.ค.–พ.ค.)

| สมมติฐาน | ความสัมพันธ์ใน CLD | ผล (25,124 กริด 250 ม., 50 เขต) |
|---|---|---|
| **H1:** สัดส่วนพื้นที่สีเขียว (NDVI) สัมพันธ์เชิงลบกับอุณหภูมิพื้นผิว (LST) | Green cover → LST (−) | ✅ สนับสนุน: r = −0.63, β_std = −0.63 [−0.67, −0.59], R² = 0.40 |
| **H2:** ความหนาแน่นสิ่งปลูกสร้าง (NDBI) สัมพันธ์เชิงบวกกับ LST | Built-up density → LST (+) | ✅ สนับสนุน: r = +0.70, β_std = +0.70 [+0.67, +0.74], R² = 0.49 |

p-value คำนวณจาก cluster-robust SE (บล็อก 2.5 กม.) ได้ < 1e-200 ทั้งคู่ ผลนี้เป็นความสัมพันธ์จากการวิเคราะห์ภาคตัดขวาง ไม่ได้พิสูจน์ความเป็นเหตุและผล

## Architecture

```
Landsat 8/9 ─► ① ingest (GEE) ─► data/raw/ ─► ② validate + integrate ─► data/warehouse.duckdb ─► ③ analyze ─► output/ ─► web/
Bangkok-districts.geojson ──────────► data/raw/ ──┘          │
                                                  data/quality_reports/<run_id>.json
```

ดู diagram ฉบับเต็ม, data model, quality checks, design decisions และ trade-offs ได้ที่ **[docs/architecture.md](docs/architecture.md)**

## วิธีรัน

**สิ่งที่ต้องมี:** Python 3.11 ขึ้นไป

```bash
pip install -r requirements.txt
```

### ทางเลือก A: รันทั้ง pipeline รวมดึงข้อมูลใหม่จาก Google Earth Engine
ต้องมีบัญชี Earth Engine และ Cloud project ที่ลงทะเบียนไว้แล้ว (ทำครั้งแรกครั้งเดียว)

```bash
earthengine authenticate
```

```bash
set EE_PROJECT=<your-cloud-project-id>
```

ใน bash ใช้ `export EE_PROJECT=...` แทน `set`

```bash
python run_pipeline.py
```

### ทางเลือก B: ไม่มีบัญชี GEE
ใช้ข้อมูลดิบที่ commit ไว้ใน `data/raw/`

```bash
python run_pipeline.py --skip-ingest
```

ทุก run จะได้ `run_id` และผ่าน 3 stage:
1. **Ingest:** GEE filter/mask/composite/รวมกริด แล้วดาวน์โหลดเข้า `data/raw/`
2. **Validate + Integrate + Store:** spatial join กริดเข้ากับเขต แล้วตรวจคุณภาพ 14 ข้อ ผลเขียนลง `data/quality_reports/<run_id>.json` ถ้ามี error จะหยุดทันที ถ้าผ่านจะโหลดเข้า DuckDB
3. **Analyze + Publish:** ทดสอบ H1/H2 แล้วเขียนผลลงตาราง `hypothesis_results` และไฟล์ใน `output/`

### ดูผลบนเว็บ
```bash
python -m http.server 8000
```
แล้วเปิด http://localhost:8000/web/

ต้องรันจาก root ของ repo เพราะเว็บอ่านไฟล์จาก `../output/` และเปิดไฟล์ HTML ตรงๆ แบบ `file://` ไม่ได้

### Query ข้อมูลใน warehouse
```bash
python -c "import duckdb; print(duckdb.connect('data/warehouse.duckdb', read_only=True).sql(open('sql/examples.sql', encoding='utf-8').read()))"
```
ตัวอย่าง query อื่นอยู่ใน [`sql/examples.sql`](sql/examples.sql)

## โครงสร้าง repo

| path | หน้าที่ |
|---|---|
| `run_pipeline.py` | ตัวควบคุม pipeline: สร้าง run_id, บันทึก lineage (git commit, sha256 ของ input, config), รันทีละ stage และบันทึกสถานะ |
| `pipeline/config.py` | ค่าตั้งทั้งหมด (ช่วงเวลา, เกณฑ์ปลอดเมฆ, ขนาดกริด, เกณฑ์ quality) |
| `pipeline/ingest.py` | ① GEE Python API → `data/raw/` |
| `pipeline/process.py` | ② spatial join + quality checks + โหลดเข้า DuckDB |
| `pipeline/analyze.py` | ③ สถิติ H1/H2 + เขียน serving files |
| `data/raw/` | **Zone 1** ข้อมูลดิบ: `landsat_cells_250m.csv`, `landsat_meta.json`, `Bangkok-districts.geojson` |
| `data/warehouse.duckdb` | **Zone 2** warehouse (ไม่อยู่ใน git เพราะสร้างใหม่ได้ด้วย `--skip-ingest`) |
| `data/quality_reports/` | quality report ของแต่ละ run |
| `output/` | **Zone 3** serving: `results.json`, `cells.csv`, `districts.geojson`, `scatter.png` |
| `web/index.html` | เว็บแอปแบบ static (MapLibre) |
| `sql/examples.sql` | ตัวอย่าง query |
| `gee/` | สคริปต์ GEE Code Editor: สำรวจข้อมูล และ ingest แบบ manual สำรอง |
| `docs/architecture.md` | Architecture, data model, design decisions |

## แหล่งข้อมูล

| ข้อมูล | แหล่ง | ความละเอียด | ช่วงเวลา |
|---|---|---|---|
| LST (`ST_B10`), NDVI, NDBI, MNDWI (SR bands), `QA_PIXEL` | USGS Landsat 8/9 Collection 2 Level-2 (ผ่าน Google Earth Engine) | 30 ม. (thermal 100 ม.) → กริด 250 ม. | มี.ค.–พ.ค. 2026: 8 scenes จาก 4 วันที่ถ่าย |
| ขอบเขตเขต | `Bangkok-districts.geojson` (50 เขต) | polygon | คงที่ |

## ข้อจำกัด
- Landsat ถ่ายภาพราว 10:30 น. จึงไม่มีข้อมูลตอนบ่ายหรือกลางคืน
- LST คืออุณหภูมิพื้นผิว ไม่ใช่อุณหภูมิอากาศที่คนรู้สึก
- NDVI กับ NDBI มาจาก band ชุดเดียวกัน (r ≈ −0.84) จึงตอบ H1/H2 ด้วยโมเดลแยกตัวแปร
- Residual มี spatial autocorrelation (Moran's I ≈ 0.75) จึงใช้ cluster-robust SE แทน OLS ธรรมดา
- เกณฑ์ภาพปลอดเมฆ ≥ 80%, กริด 250 ม. และบล็อก 2.5 กม. เป็นค่าที่ตั้งขึ้นเอง
- ข้อมูลดิบชุดปัจจุบันมาจาก export แบบ manual จึงยังไม่มี `n_obs` และ path/row ของ scene ต้องรัน ingest ใหม่ถึงจะมี

## ผู้จัดทำ
โครงงานเดี่ยว: ผู้จัดทำคนเดียวรับผิดชอบทุกส่วน (problem framing, pipeline, analysis, web)

## การใช้ AI
_TODO: ระบุว่าใช้ AI (Claude) ช่วยในส่วนไหน และตรวจสอบผลลัพธ์อย่างไร เช่น รันซ้ำแล้วเทียบตัวเลข, ทดสอบให้ quality gate fail, ตรวจแผนที่ด้วยตา_
