#!/usr/bin/env python3
"""Tests del bloque 3 (dispersion) con paneles sinteticos. Sin red, sin data/.

    python tests/test_dispersion.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from analisis import dispersion as D
from analisis import panel as P


def _panel(rng, n_ean=300, dias=2, efecto_cadena=None, ruido=0.0, identicos=0):
    """log p = efecto producto + efecto cadena + ruido. Los primeros
    `identicos` EAN tienen precio identico en todas las cadenas."""
    efecto_cadena = efecto_cadena or {c: 0.0 for c in P.CADENAS}
    filas = []
    base = rng.normal(2.5, 0.8, n_ean)
    cats = rng.choice(["Abarrotes", "Lácteos", "Bebidas"], n_ean)
    for d in range(dias):
        fecha = pd.Timestamp("2026-09-19") + pd.Timedelta(days=d)
        for i in range(n_ean):
            ean = f"77500{i:08d}"
            for c in P.CADENAS:
                e = 0.0 if i < identicos else efecto_cadena[c] + rng.normal(0, ruido)
                filas.append({"fecha": fecha, "retailer": c, "item_id": f"{c}{i}",
                              "ean": ean, "price": round(float(np.exp(base[i] + e)), 2),
                              "category": cats[i]})
    df = pd.DataFrame(filas)
    df["categoria_comun"] = df["category"].map(P.categoria_comun)
    df["grupo"] = df["retailer"].map(P.GRUPOS)
    return df


def test_ean_comparable():
    s = pd.Series(["7750001234567", "2549990000006", "123456", None, "775000123456",
                   "abc", "20000000000001"])
    m = P.ean_comparable(s)
    assert list(m) == [True, False, False, False, True, False, False]
    m2 = P.ean_comparable(s, incluir_internos=True)
    assert list(m2) == [True, True, False, False, True, False, True]


def test_masa_cero_y_pares():
    rng = np.random.default_rng(1)
    df = _panel(rng, n_ean=100, dias=1, efecto_cadena={"metro": 0, "wong": 0,
                "plaza_vea": 0.1, "vivanda": 0.2, "tottus": 0.3}, identicos=25)
    obs = D.observaciones(df)
    ed = D.por_ean_dia(obs)
    assert len(ed) == 100 and (ed["n_cadenas"] == 5).all()
    assert abs(ed["identico_todas"].mean() - 0.25) < 1e-9
    res, det = D.pares(obs)
    r = res.set_index("par")
    assert r.loc["metro+wong", "identico"] == 1.0            # mismo efecto: siempre identico
    assert abs(r.loc["plaza_vea+vivanda", "identico"] - 0.25) < 1e-9
    assert r.loc["plaza_vea+vivanda", "relacion"] == "mismo grupo"
    assert r.loc["metro+tottus", "relacion"] == "competidores"
    assert len(det) == 10


def test_descomposicion_recupera_el_efecto_cadena():
    rng = np.random.default_rng(2)
    ef = {"metro": 0.0, "wong": 0.0, "plaza_vea": 0.15, "vivanda": 0.15, "tottus": -0.15}
    df = _panel(rng, n_ean=400, dias=2, efecto_cadena=ef, ruido=0.05)
    obs = D.observaciones(df)
    dec = D.descomposicion(obs).set_index(["orden", "nivel"])
    # el producto domina, pero la cadena explica casi todo lo que queda
    assert dec.loc[("A", "producto"), "r2_acumulado"] > 0.9
    assert dec.loc[("A", "cadena"), "share_within"] + dec.loc[("A", "grupo"), "share_within"] > 0.8
    assert dec.loc[("A", "cadena x categoria"), "share_within"] < 0.1
    # el modelo completo tiene el mismo R2 en ambos ordenes
    full_a = dec.loc[("A", "cadena x categoria"), "r2_acumulado"]
    full_b = dec.loc[("B", "producto"), "r2_acumulado"]
    assert abs(full_a - full_b) < 1e-6
    # el grupo captura parte del efecto (SPSA sube, Falabella baja)
    assert dec.loc[("A", "grupo"), "r2_incremental"] > 0
    # la categoria queda absorbida en A
    assert dec.loc[("A", "categoria"), "r2_incremental"] == 0.0


def test_sin_efecto_cadena_share_within_es_casi_cero():
    rng = np.random.default_rng(3)
    df = _panel(rng, n_ean=400, dias=1, ruido=0.1)
    obs = D.observaciones(df)
    dec = D.descomposicion(obs).set_index(["orden", "nivel"])
    assert dec.loc[("A", "cadena"), "share_within"] < 0.05


def test_sd_log_es_la_de_gorodnichenko():
    df = pd.DataFrame({
        "fecha": pd.Timestamp("2026-09-19"), "retailer": ["metro", "wong", "tottus"],
        "item_id": ["1", "2", "3"], "ean": "7750001234567",
        "price": [10.0, 12.0, 15.0], "category": "Abarrotes"})
    df["categoria_comun"] = "abarrotes"
    df["grupo"] = df["retailer"].map(P.GRUPOS)
    ed = D.por_ean_dia(D.observaciones(df))
    assert abs(ed.iloc[0]["sd_log"] - np.std(np.log([10, 12, 15]), ddof=1)) < 1e-12
    assert abs(ed.iloc[0]["brecha"] - 0.5) < 1e-12


def test_mediana_cuando_varios_sku_comparten_ean():
    df = pd.DataFrame({
        "fecha": pd.Timestamp("2026-09-19"),
        "retailer": ["metro", "metro", "metro", "wong"],
        "item_id": ["1", "2", "3", "4"], "ean": "7750001234567",
        "price": [10.0, 11.0, 30.0, 11.0], "category": "Abarrotes"})
    df["categoria_comun"] = "abarrotes"
    df["grupo"] = df["retailer"].map(P.GRUPOS)
    obs = D.observaciones(df).set_index("retailer")
    assert obs.loc["metro", "price"] == 11.0
    res, _ = D.pares(D.observaciones(df))
    assert res.iloc[0]["identico"] == 1.0


def test_evolucion_una_fila_por_dia():
    rng = np.random.default_rng(4)
    df = _panel(rng, n_ean=50, dias=3, ruido=0.05)
    obs = D.observaciones(df)
    ed = D.por_ean_dia(obs)
    _, det = D.pares(obs)
    evo = D.evolucion(obs, ed, det)
    assert len(evo) == 3
    assert "identico_metro+wong" in evo and "r2_producto" in evo


def test_tex_es_balanceado():
    rng = np.random.default_rng(5)
    df = _panel(rng, n_ean=50, dias=1, ruido=0.05)
    obs = D.observaciones(df)
    ed = D.por_ean_dia(obs)
    res, _ = D.pares(obs)
    tex = D.tablas_tex(res, D.distribucion_brecha(ed), D.descomposicion(obs), 1,
                       "2026-09-19", False)
    assert tex.count("\\begin{table}") == 3 == tex.count("\\end{table}")
    assert tex.count("\\begin{tabular}") == tex.count("\\end{tabular}")
    assert "tab:identidad" in tex and "tab:varianza" in tex


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    fallos = 0
    for nombre, fn in fns:
        try:
            fn()
            print(f"  ok    {nombre}")
        except AssertionError as e:
            fallos += 1
            print(f"  FALLA {nombre}  {e}")
        except Exception as e:
            fallos += 1
            print(f"  ERROR {nombre}  {type(e).__name__}: {e}")
    print(f"\n{len(fns) - fallos}/{len(fns)} tests pasan")
    sys.exit(1 if fallos else 0)
