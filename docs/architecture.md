# Architecture (ฉบับ Checkpoint)

## Pipeline

รันทั้งหมดด้วยคำสั่งเดียว: `python run_pipeline.py` (หรือ `--skip-ingest` เพื่อใช้ข้อมูลดิบที่มีอยู่แล้ว)

```mermaid
flowchart LR
  subgraph SRC["Data Sources"]
    L["Landsat 8/9 C2 L2<br/>USGS · ทั้งคลังเป็นระดับ TB"]
    D["Bangkok-districts.geojson<br/>50 เขต"]
  end

  subgraph S1["① Ingest — pipeline/ingest.py<br/>(ประมวลผลบน Google Earth Engine)"]
    G1["filter: AOI กทม., มี.ค.–พ.ค. 2026,<br/>scene ปลอดเมฆ ≥ 80%"]
    G2["QA cloud/shadow mask<br/>LST · NDVI · NDBI · MNDWI<br/>+ n_obs ต่อพิกเซล"]
    G3["median composite<br/>ตัดน้ำที่ MNDWI > 0"]
    G4["รวมเป็นกริด 250 ม.<br/>(EPSG:32647)"]
    G1 --> G2 --> G3 --> G4
  end

  subgraph RAW["Zone 1: data/raw/"]
    R1[("landsat_cells_250m.csv")]
    R2[("landsat_meta.json<br/>scene, วันที่, path/row")]
    R3[("Bangkok-districts.geojson")]
  end

  subgraph S2["② Validate + Integrate + Store — pipeline/process.py"]
    J["spatial join<br/>กริด → เขต"]
    V{"Quality checks<br/>schema · range · integration<br/>completeness · temporal"}
    QR[("data/quality_reports/<br/>&lt;run_id&gt;.json")]
    J --> V
    V --> QR
    V -- "มี error" --> X["หยุด<br/>run = failed_quality"]
  end

  subgraph WH["Zone 2: data/warehouse.duckdb"]
    T[("cells · districts · scenes<br/>runs · quality_checks<br/>hypothesis_results")]
  end

  subgraph S3["③ Analyze + Publish — pipeline/analyze.py"]
    A["H1/H2: Pearson/Spearman, OLS (std)<br/>cluster-robust SE · Moran's I"]
  end

  subgraph SRV["Zone 3: output/ (serving)"]
    O[("results.json · cells.csv<br/>districts.geojson · scatter.png")]
  end

  W["web/index.html<br/>MapLibre แบบ static"]
  Q["SQL query<br/>(DuckDB)"]

  L --> G1
  D -.ใช้เป็น AOI.-> G1
  D --> R3
  G4 -- "getDownloadURL (CSV)" --> R1
  G4 --> R2
  R1 & R2 & R3 --> J
  V -- ผ่าน --> T
  T --> A --> O
  A --> T
  O --> W
  T --> Q
```

## Data Model (DuckDB)

```mermaid
erDiagram
  runs ||--o{ cells : "run_id"
  runs ||--o{ scenes : "run_id"
  runs ||--o{ quality_checks : "run_id"
  runs ||--o{ hypothesis_results : "run_id"
  districts ||--o{ cells : "dcode (FK)"

  runs {
    varchar run_id PK
    timestamp started_at
    timestamp finished_at
    varchar status "running / success / failed_quality / failed"
    varchar git_commit
    json inputs "sha256 ของไฟล์ raw"
    json config
    int n_cells
  }
  districts {
    varchar dcode PK
    varchar dname
    varchar dname_e
    double area_km2
  }
  cells {
    varchar run_id
    double lon
    double lat
    varchar dcode FK
    double lst
    double ndvi
    double ndbi
    double n_obs
  }
  scenes {
    varchar run_id
    varchar scene_id
    date date
    varchar spacecraft
    int wrs_path
    int wrs_row
    double clear_frac
  }
  quality_checks {
    varchar run_id
    varchar check_name
    varchar category
    varchar severity "error / warn / info"
    double value
    varchar threshold
    boolean passed
  }
  hypothesis_results {
    varchar run_id
    varchar hypothesis
    double pearson_r
    double beta_std
    double r2
    double p_cluster
    double morans_i
    boolean supported
  }
```

- `cells` เป็น fact table (1 แถวต่อกริดต่อ run) ส่วน `districts` เป็น dimension
- ทุกตารางมี `run_id` จึงเก็บผลของทุก run ไว้ได้ เทียบข้าม run ได้ และย้อนไปดูได้ว่าผลแต่ละชุดมาจาก input/config/commit ไหน
- view `latest_cells` ให้ข้อมูลของ run ล่าสุดที่สำเร็จ

## Quality Checks

| หมวด | check | ระดับ | เกณฑ์ |
|---|---|---|---|
| schema | มีคอลัมน์ครบ, เป็นตัวเลข, ไม่มีค่าว่าง, พิกัดอยู่ในกรอบ, ไม่มีกริดซ้ำ | error | 0 ปัญหา |
| range | LST | error | 20–60°C |
| range | NDVI, NDBI | error | [−1, 1] |
| integration | ครบทุกเขตหลัง spatial join | error | 50 เขต |
| integration | สัดส่วนกริดนอกเขตที่ถูกตัดทิ้ง | info | — |
| completeness | coverage ต่อเขต = กริดที่ได้ ÷ (พื้นที่เขต ÷ 0.0625 ตร.กม.) | warn | ≥ 50% |
| temporal | จำนวนวันที่ถ่ายภาพ (ไม่ซ้ำ) | error | ≥ 3 |
| temporal | สัดส่วนกริดที่มี observation ปลอดเมฆ ≥ 2 | warn | ≥ 90% |

- `error` ไม่ผ่าน → pipeline หยุด, run ถูกบันทึกเป็น `failed_quality` และไม่มีข้อมูลเข้า `cells` หรือ serving
- `warn` → ทำงานต่อได้ แต่จะแสดงผลทั้งในเว็บและใน report
- report ของทุก run ถูกเขียน**ก่อน**ตัดสินว่าจะไปต่อหรือไม่ แม้ run ที่ fail ก็ตรวจย้อนได้
- เกณฑ์ทุกข้ออยู่ใน `pipeline/config.py` ที่เดียว

## Design Decisions

| # | การตัดสินใจ | เหตุผล | Trade-off |
|---|---|---|---|
| 1 | **ประมวลผลภาพบน GEE แล้วส่งกลับมาเฉพาะผลรวมรายกริด** | ภาพ Landsat ทั้งคลังมีขนาดระดับ TB ถ้าดาวน์โหลดมาประมวลผลเองต้องใช้ storage และ compute มาก แต่ GEE ทำ filter/mask/composite ฝั่ง server แล้วส่งกลับมาแค่ประมาณ 8 MB | ต้องพึ่ง GEE (บัญชี, Cloud project, quota) จึงแก้โดย commit ข้อมูลดิบไว้ใน `data/raw/` และมี `--skip-ingest` ให้คนที่ไม่มีบัญชีรันขั้นที่เหลือได้ |
| 2 | **ใช้ไฟล์ขอบเขตเขตไฟล์เดียวกันทั้งเป็น AOI ใน GEE และใช้ join** | ขอบเขตตรงกันทั้ง pipeline ไม่ต้องพึ่ง boundary ของ GEE (FAO GAUL) ที่อาจไม่ตรงกับเขตปกครองจริง | ต้องส่ง polygon ไปกับ request จึงปัดพิกัดเหลือ 4 ตำแหน่ง (~11 ม.) เพื่อลดขนาด |
| 3 | **คำนวณ LST, NDVI, NDBI จากภาพชุดเดียวกัน** แล้วทำ median composite | ตัวแปรทั้งสามตรงกันทั้งเวลาและพื้นที่ ส่วน median ช่วยลดผลจากเมฆที่ mask ไม่หมด | เกณฑ์ปลอดเมฆ ≥ 80% ทำให้เหลือแค่ 4 วันที่ถ่าย และทุกภาพถ่ายช่วง 10:30 น. เท่านั้น |
| 4 | **ใช้กริด 250 ม. เป็นหน่วยวิเคราะห์** และเสนอผลรายเขตด้วย | ลด noise ของพิกเซล 30 ม. ใกล้เคียงความละเอียดจริงของ thermal band (100 ม.) ส่วนผลรายเขตใช้ตอบคำถามเชิงนโยบาย | ผลอาจเปลี่ยนตามขนาดกริดที่เลือก (MAUP) ยังไม่ได้ทำ sensitivity test |
| 5 | **ใช้ DuckDB (ไฟล์เดียว) เป็น warehouse** | รองรับ SQL, join, window function, ไม่ต้องมี server และกรรมการ clone ไปรันได้ทันที ข้อมูลระดับหมื่นแถวต่อ run จึงไม่ต้องใช้ระบบกระจาย | เขียนได้ทีละ process และไม่รองรับผู้ใช้หลายคนพร้อมกัน ซึ่งไม่ใช่ requirement ของงานนี้ |
| 6 | **แยก serving zone (ไฟล์ static) ออกจาก warehouse** | เว็บไม่ต้องมี backend จึง host บน GitHub Pages ได้ และ serving มีเฉพาะผลของ run ที่ผ่าน quality gate | ต้อง publish ใหม่ทุกครั้งที่รัน pipeline ซึ่ง `run_pipeline.py` ทำให้อัตโนมัติ |
| 7 | **Quality gate แบบ fail-fast + บันทึกทุก run** | ข้อมูลผิดปกติหลุดขึ้นเว็บไม่ได้ และมีหลักฐานตรวจย้อนได้ (report + ตาราง runs ที่เก็บ sha256 ของ input และ git commit) | เกณฑ์บางข้อทีมตั้งขึ้นเอง (ดู `config.py`) |
| 8 | **ตอบ H1/H2 ด้วยโมเดลแยกตัวแปร** และใช้ cluster-robust SE | NDVI กับ NDBI มี r ≈ −0.84 ถ้าใส่รวมกันในโมเดลเดียวจะเกิด suppression effect ส่วน residual มี Moran's I ≈ 0.75 ทำให้ p-value จาก OLS ต่ำเกินจริง | cluster-robust SE ง่ายกว่า spatial lag/error model แต่ไม่ได้จำลองโครงสร้างเชิงพื้นที่โดยตรง |

## เทคโนโลยีที่ไม่ได้เลือก
- **Spark / Hadoop / cloud data warehouse / AWS pipeline:** ข้อมูลหลังผ่าน GEE มีแค่ระดับ MB และรันปีละไม่กี่ครั้ง ถ้าใช้เครื่องมือพวกนี้จะเพิ่มความซับซ้อนและต้นทุนโดยไม่มี requirement รองรับ (spec ข้อ 5)
- **PostgreSQL/PostGIS:** ต้องดูแล server เพิ่ม แต่ข้อมูลมีขนาดเล็กและไม่มีการเขียนพร้อมกันหลายคน
- **Scheduler (cron/Airflow):** ข้อมูลเป็นภาพหน้าร้อนปีละครั้ง จึง trigger แบบ manual ก็พอ

