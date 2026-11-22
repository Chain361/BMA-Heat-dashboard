// นับภาพ Landsat 8/9 ช่วงหน้าร้อน (มี.ค.–พ.ค.) ที่ปลอดเมฆเหนือพื้นที่ศึกษา แยกตามปี
// วางสคริปต์นี้ใน https://code.earthengine.google.com แล้วกด Run

// พื้นที่ศึกษา: กรุงเทพฯ (กรอบสี่เหลี่ยมโดยประมาณ) — เปลี่ยนเป็น boundary จริงได้
var aoi = ee.Geometry.Rectangle([100.33, 13.49, 100.94, 13.96]);
var YEARS = [2024, 2025, 2026];
var MIN_CLEAR = 0.8; // ต้องปลอดเมฆอย่างน้อย 80% ของพื้นที่ศึกษา

var l89 = ee.ImageCollection('LANDSAT/LC08/C02/T1_L2')
  .merge(ee.ImageCollection('LANDSAT/LC09/C02/T1_L2'))
  .filterBounds(aoi);

// คำนวณสัดส่วนพิกเซลปลอดเมฆ "ในพื้นที่ศึกษา" (ค่า CLOUD_COVER ของ metadata คิดทั้งภาพ ไม่ใช่เฉพาะ AOI)
function addClearFrac(img) {
  var qa = img.select('QA_PIXEL');
  // bit 1 dilated cloud, 3 cloud, 4 cloud shadow
  var clear = qa.bitwiseAnd(1 << 1).eq(0)
    .and(qa.bitwiseAnd(1 << 3).eq(0))
    .and(qa.bitwiseAnd(1 << 4).eq(0));
  var frac = clear.reduceRegion({
    reducer: ee.Reducer.mean(), geometry: aoi, scale: 100, maxPixels: 1e9
  }).get('QA_PIXEL');
  return img.set('clear_frac', frac);
}

YEARS.forEach(function (y) {
  var col = l89.filterDate(y + '-03-01', y + '-06-01').map(addClearFrac);
  var good = col.filter(ee.Filter.gte('clear_frac', MIN_CLEAR));
  print(y + ' | ภาพทั้งหมด', col.size(), 'ปลอดเมฆ ≥' + MIN_CLEAR * 100 + '%', good.size());
  print(y + ' | รายละเอียด', col.reduceColumns(
    ee.Reducer.toList(3), ['DATE_ACQUIRED', 'SPACECRAFT_ID', 'clear_frac']).get('list'));
});

Map.centerObject(aoi, 10);
Map.addLayer(aoi, {color: 'red'}, 'AOI');
