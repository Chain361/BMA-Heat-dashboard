"""Stage 3 — Analyze + Publish: DuckDB (run ล่าสุดที่ผ่าน) → ทดสอบ H1/H2 → serving files

เอาต์พุต (output/ = serving zone ที่เว็บอ่าน):
  results.json, cells.csv, districts.geojson, scatter.png
และเก็บผลสรุปลงตาราง hypothesis_results ใน DuckDB เพื่อ query ย้อนหลังข้าม run ได้
"""
import json

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import statsmodels.formula.api as smf  # noqa: E402
from scipy import sparse, stats  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402
from statsmodels.stats.outliers_influence import variance_inflation_factor  # noqa: E402

from . import config as C  # noqa: E402

RESULTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS hypothesis_results (
  run_id VARCHAR, hypothesis VARCHAR, x VARCHAR, n INTEGER, pearson_r DOUBLE, beta_std DOUBLE,
  ci_low DOUBLE, ci_high DOUBLE, r2 DOUBLE, p_cluster DOUBLE, morans_i DOUBLE, supported BOOLEAN);
"""


def _weights(m):
    _, idx = cKDTree(m).query(m, C.MORAN_K + 1)
    n, k = len(m), C.MORAN_K
    return sparse.csr_matrix((np.full(n * k, 1 / k), (np.repeat(np.arange(n), k), idx[:, 1:].ravel())), shape=(n, n))


def _morans_i(y, W, perms=199, seed=0):
    def I(s):
        return len(s) / W.sum() * (s @ (W @ s)) / (s @ s)
    y = y - y.mean()
    rng = np.random.default_rng(seed)
    obs, sims = I(y), np.array([I(rng.permutation(y)) for _ in range(perms)])
    return float(obs), float((np.sum(sims >= obs) + 1) / (perms + 1))


def run(run_id):
    con = duckdb.connect(str(C.WAREHOUSE))
    df = con.execute("SELECT lon, lat, dcode, lst AS LST, ndvi AS NDVI, ndbi AS NDBI, n_obs FROM cells "
                     "WHERE run_id = ?", [run_id]).df()
    checks = con.execute("SELECT check_name, category, severity, value, threshold, passed, detail "
                         "FROM quality_checks WHERE run_id = ?", [run_id]).df()
    scenes = con.execute("SELECT * FROM scenes WHERE run_id = ? ORDER BY date", [run_id]).df()
    dist = con.execute("SELECT * FROM districts").df().set_index("dcode")

    # พิกัดเป็นเมตร (ประมาณจาก lon/lat; พอสำหรับเมืองขนาดกรุงเทพฯ)
    m = np.c_[(df.lon - df.lon.mean()) * 111320 * np.cos(np.radians(df.lat.mean())),
              (df.lat - df.lat.mean()) * 110540]
    # ponytail: cluster-robust SE ตามบล็อก 2.5 กม. แทน spatial lag/error model; เปลี่ยนเป็น pysal spreg ถ้าต้องการ
    b = C.CLUSTER_BLOCK_M
    blocks = np.floor(m[:, 0] / b).astype(int) * 10000 + np.floor(m[:, 1] / b).astype(int)
    W = _weights(m)
    z = df[["LST", "NDVI", "NDBI"]].apply(stats.zscore)

    def fit(formula):
        ols = smf.ols(formula, data=z).fit()
        rob = smf.ols(formula, data=z).fit(cov_type="cluster", cov_kwds={"groups": blocks})
        I, pI = _morans_i(ols.resid.values, W)
        coefs = {v: {"beta_std": float(rob.params[v]), "ci95": [float(c) for c in rob.conf_int().loc[v]],
                     "p_naive": float(ols.pvalues[v]), "p_cluster": float(rob.pvalues[v])}
                 for v in ols.params.index.drop("Intercept")}
        return {"formula": formula, "r2": float(ols.rsquared), "coefs": coefs, "morans_i_resid": I, "morans_p": pI}

    def hypothesis(var, sign):
        r, p = stats.pearsonr(df[var], df.LST)
        rho, prho = stats.spearmanr(df[var], df.LST)
        lr = stats.linregress(df[var], df.LST)
        H, xe, ye = np.histogram2d(df[var], df.LST, bins=40)
        res = fit(f"LST ~ {var}")
        bb = res["coefs"][var]
        return {"x": var, "y": "LST", "expected_sign": sign,
                "pearson_r": float(r), "pearson_p": float(p), "spearman_rho": float(rho), "spearman_p": float(prho),
                **res,
                "supported": bool(np.sign(bb["beta_std"]) == sign and bb["p_cluster"] < 0.05),
                "slope_raw": float(lr.slope), "intercept_raw": float(lr.intercept),  # °C ต่อ 1 หน่วยดัชนี
                "hist2d": {"x_edges": xe.round(4).tolist(), "y_edges": ye.round(3).tolist(),
                           "counts": H.astype(int).tolist()}}

    X = z[["NDVI", "NDBI"]].assign(const=1.0)
    run_row = con.execute("SELECT git_commit, inputs FROM runs WHERE run_id = ?", [run_id]).fetchone()
    results = {
        "meta": {"run_id": run_id, "git_commit": run_row[0] if run_row else None,
                 "inputs": json.loads(run_row[1]) if run_row and run_row[1] else None,
                 "start": C.START, "end": C.END, "min_clear": C.MIN_CLEAR, "grid_m": C.GRID_M,
                 "dates": sorted(scenes.date.astype(str).unique().tolist()), "n_scenes": len(scenes),
                 "n_cells": len(df), "n_districts": int(df.dcode.nunique())},
        "quality_checks": json.loads(checks.to_json(orient="records", force_ascii=False)),
        "H1": hypothesis("NDVI", -1),
        "H2": hypothesis("NDBI", +1),
        # ภาคผนวก: ไม่ใช้ตอบ H1/H2 เพราะ NDVI–NDBI สัมพันธ์กันสูง (suppression effect)
        "appendix_combined": {**fit("LST ~ NDVI + NDBI"), "corr_ndvi_ndbi": float(df.NDVI.corr(df.NDBI)),
                              "vif": {v: float(variance_inflation_factor(X.values, i))
                                      for i, v in enumerate(["NDVI", "NDBI"])}},
    }

    con.execute(RESULTS_SCHEMA)
    con.execute("DELETE FROM hypothesis_results WHERE run_id = ?", [run_id])
    for h in ["H1", "H2"]:
        r = results[h]
        bb = r["coefs"][r["x"]]
        con.execute("INSERT INTO hypothesis_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    [run_id, h, r["x"], len(df), r["pearson_r"], bb["beta_std"], *bb["ci95"], r["r2"],
                     bb["p_cluster"], r["morans_i_resid"], r["supported"]])
        print(f"{h}: r = {r['pearson_r']:+.3f} | β_std = {bb['beta_std']:+.3f} {np.round(bb['ci95'], 3)} "
              f"| R² = {r['r2']:.3f} | p(cluster) = {bb['p_cluster']:.1e} | Moran's I = {r['morans_i_resid']:.2f} "
              f"→ {'สนับสนุน' if r['supported'] else 'ไม่สนับสนุน'}")

    # ---- serving files ----
    out = C.SERVING
    out.mkdir(exist_ok=True)
    (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    df.round({"lon": 5, "lat": 5, "LST": 2, "NDVI": 3, "NDBI": 3})[
        ["lon", "lat", "LST", "NDVI", "NDBI", "dcode"]].to_csv(out / "cells.csv", index=False)

    summ = con.execute("""
        SELECT dcode, avg(lst) AS LST, avg(ndvi) AS NDVI, avg(ndbi) AS NDBI, count(*) AS n_cells,
               rank() OVER (ORDER BY avg(lst) DESC) AS rank_LST
        FROM cells WHERE run_id = ? GROUP BY dcode""", [run_id]).df().set_index("dcode")
    con.close()
    gj = json.loads(C.DISTRICTS_GEOJSON.read_text(encoding="utf-8"))
    feats = []
    for f in gj["features"]:
        d = f["properties"]["dcode"]
        s = summ.loc[d]
        feats.append({"type": "Feature",
                      "properties": {"dcode": d, "dname": dist.loc[d, "dname"], "dname_e": dist.loc[d, "dname_e"],
                                     "LST": round(float(s.LST), 2), "NDVI": round(float(s.NDVI), 3),
                                     "NDBI": round(float(s.NDBI), 3), "n_cells": int(s.n_cells),
                                     "rank_LST": int(s.rank_LST)},
                      "geometry": {"type": "Polygon", "coordinates": [np.round(ring, 5).tolist()
                                                                      for ring in f["geometry"]["coordinates"]]}})
    (out / "districts.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats},
                                                      ensure_ascii=False), encoding="utf-8")

    fig, ax = plt.subplots(1, 2, figsize=(11, 4.5), sharey=True)
    for a, h in zip(ax, ["H1", "H2"]):
        r, v = results[h], results[h]["x"]
        a.hexbin(df[v], df.LST, gridsize=60, bins="log", cmap="viridis")
        xs = np.linspace(df[v].min(), df[v].max(), 2)
        a.plot(xs, r["intercept_raw"] + r["slope_raw"] * xs, "r-", lw=2, label=f"r = {r['pearson_r']:.2f}")
        a.set(xlabel=v, title=f"{h}: LST vs {v}")
        a.legend()
    ax[0].set_ylabel("LST (°C)")
    fig.tight_layout()
    fig.savefig(out / "scatter.png", dpi=150)
    plt.close(fig)
    print(f"เขียน serving files → {out}")
