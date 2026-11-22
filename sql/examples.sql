-- ตัวอย่าง query บน data/warehouse.duckdb
-- (คำสั่งใน README จะแสดงผลของ query สุดท้ายในไฟล์นี้; ลองเปลี่ยนลำดับ หรือรันทีละ query ใน DuckDB CLI)

-- 1) ประวัติการรัน pipeline (lineage)
-- SELECT run_id, status, git_commit, n_cells, finished_at FROM runs ORDER BY started_at DESC;

-- 2) quality check ที่ไม่ผ่านหรือเป็น warning ใน run ล่าสุด
-- SELECT check_name, severity, value, threshold, detail
-- FROM quality_checks WHERE run_id = (SELECT max(run_id) FROM runs) AND passed IS NOT TRUE;

-- 3) scene ที่ใช้ (ตรวจว่า 1 วันมีหลาย path/row หรือไม่)
-- SELECT date, count(*) AS n_scenes, list(DISTINCT wrs_row) AS rows FROM scenes
-- WHERE run_id = (SELECT max(run_id) FROM runs) GROUP BY date ORDER BY date;

-- 4) ผลทดสอบสมมติฐานข้าม run
-- SELECT run_id, hypothesis, round(pearson_r, 3) AS r, round(r2, 3) AS r2, supported FROM hypothesis_results;

-- 5) 10 เขตที่ร้อนที่สุด พร้อม NDVI/NDBI เฉลี่ย (run ล่าสุดที่สำเร็จ)
SELECT d.dname_e AS district,
       round(avg(c.lst), 1)  AS lst_c,
       round(avg(c.ndvi), 3) AS ndvi,
       round(avg(c.ndbi), 3) AS ndbi,
       count(*)              AS n_cells
FROM latest_cells c JOIN districts d USING (dcode)
GROUP BY d.dname_e
ORDER BY lst_c DESC
LIMIT 10;
