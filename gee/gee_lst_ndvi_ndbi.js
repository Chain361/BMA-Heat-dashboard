// [สคริปต์สำรองแบบ manual] pipeline หลักใช้ pipeline/ingest.py ซึ่งทำงานเดียวกันผ่าน Python API
// ใช้ไฟล์นี้เมื่อดาวน์โหลดผ่าน API ไม่ได้ (เช่น timeout) แล้ว export ผ่าน Drive แทน
// คำนวณ LST, NDVI, NDBI จาก Landsat 8/9 ภาพชุดเดียวกัน → รวมเป็นกริด 250 ม. → export CSV
// วางใน https://code.earthengine.google.com แล้วกด Run จากนั้นไปกด RUN ที่แท็บ Tasks

// AOI = ขอบเขต กทม. จาก FAO GAUL (+ buffer 1 กม. กันขอบหาย) ใช้คัดภาพ/clip ใน GEE
// ส่วนขอบเขตจริงตัดอีกรอบใน analysis.py ด้วย data/Bangkok-districts.geojson
var aoi = ee.FeatureCollection('FAO/GAUL/2015/level1')
  .filterBounds(ee.Geometry.Point(100.50, 13.75)).first().geometry().buffer(1000);
var START = '2026-03-01', END = '2026-06-01'; // ถ้าภาพไม่พอ เปลี่ยนเป็น '2024-03-01' และใช้ filter เดือนด้านล่าง
var MIN_CLEAR = 0.8;
var GRID = 250; // เมตร

function cloudMask(qa) {
  return qa.bitwiseAnd(1 << 1).eq(0)       // dilated cloud
    .and(qa.bitwiseAnd(1 << 3).eq(0))      // cloud
    .and(qa.bitwiseAnd(1 << 4).eq(0));     // cloud shadow
}

function prep(img) {
  var clear = cloudMask(img.select('QA_PIXEL'));
  var frac = clear.reduceRegion({reducer: ee.Reducer.mean(), geometry: aoi, scale: 100, maxPixels: 1e9})
    .get('QA_PIXEL');
  // scale factors ของ Collection 2 Level-2
  var sr = img.select(['SR_B3', 'SR_B4', 'SR_B5', 'SR_B6']).multiply(0.0000275).add(-0.2);
  var lst = img.select('ST_B10').multiply(0.00341802).add(149.0).subtract(273.15).rename('LST');
  var ndvi = sr.normalizedDifference(['SR_B5', 'SR_B4']).rename('NDVI');
  var ndbi = sr.normalizedDifference(['SR_B6', 'SR_B5']).rename('NDBI');
  var mndwi = sr.normalizedDifference(['SR_B3', 'SR_B6']).rename('MNDWI');
  return ee.Image(lst.addBands([ndvi, ndbi, mndwi]).updateMask(clear)
    .copyProperties(img, ['DATE_ACQUIRED'])).set('clear_frac', frac);
}

var col = ee.ImageCollection('LANDSAT/LC08/C02/T1_L2')
  .merge(ee.ImageCollection('LANDSAT/LC09/C02/T1_L2'))
  .filterBounds(aoi)
  .filterDate(START, END)
  .filter(ee.Filter.calendarRange(3, 5, 'month'))
  .map(prep)
  .filter(ee.Filter.gte('clear_frac', MIN_CLEAR));
print('จำนวนภาพที่ใช้', col.size());

// median composite แล้วตัดแหล่งน้ำออก (MNDWI > 0 = น้ำ)
var comp = col.median();
comp = comp.updateMask(comp.select('MNDWI').lte(0)).select(['LST', 'NDVI', 'NDBI']);

// รวมเป็นกริด 250 ม. (ค่าเฉลี่ยของพิกเซล 30 ม. ข้างใน)
var proj = ee.Projection('EPSG:32647'); // UTM 47N
var grid = comp.setDefaultProjection(proj.atScale(30))
  .reduceResolution({reducer: ee.Reducer.mean(), maxPixels: 128})
  .reproject(proj.atScale(GRID))
  .clip(aoi);

var samples = grid.sample({region: aoi, scale: GRID, projection: proj, geometries: true});
print('จำนวน cell', samples.size());
print('ตัวอย่าง', samples.limit(5));

Export.table.toDrive({
  collection: samples,
  description: 'bkk_lst_ndvi_ndbi_250m',
  fileFormat: 'CSV',
  selectors: ['LST', 'NDVI', 'NDBI', '.geo']
});

// metadata ของภาพที่ใช้ (1 แถว) → เว็บใช้แสดงที่มาของข้อมูล
var meta = ee.Feature(null, {
  n_images: col.size(),
  dates: col.aggregate_array('DATE_ACQUIRED').sort().join(','),
  start: START, end: END, min_clear: MIN_CLEAR, grid_m: GRID
});
Export.table.toDrive({
  collection: ee.FeatureCollection([meta]),
  description: 'bkk_meta',
  fileFormat: 'CSV',
  selectors: ['n_images', 'dates', 'start', 'end', 'min_clear', 'grid_m']
});

Map.centerObject(aoi, 10);
Map.addLayer(grid.select('LST'), {min: 30, max: 50, palette: ['blue', 'yellow', 'red']}, 'LST (°C)');
Map.addLayer(grid.select('NDVI'), {min: 0, max: 0.6, palette: ['white', 'green']}, 'NDVI', false);
Map.addLayer(grid.select('NDBI'), {min: -0.3, max: 0.2, palette: ['white', 'purple']}, 'NDBI', false);
