#!/usr/bin/env python3
"""Tests del bloque 5 (huecos y regla de precios faltantes). Sin red, sin data/.

    python tests/test_precios_faltantes.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from analisis import panel as P
from analisis import precios_faltantes as F

F5 = pd.date_range("2026-09-21", periods=5, freq="D")


def _panel(celdas, cadena="metro"):
    """celdas = [(item, dia_idx, price, available, ruta)]"""
    filas = []
    for sku, i, price, av, ruta in celdas:
        filas.append({"retailer": cadena, "item_id": sku, "fecha": F5[i],
                      "price": price, "available": av, "category_path": ruta,
                      "categoria_comun": "abarrotes", "ean": None, "category": "Abarrotes"})
    return pd.DataFrame(filas)


def test_tipos_de_hueco():
    df = _panel([
        ("A", 0, 10.0, True, "Abarrotes > Arroz"),
        ("A", 1, np.nan, False, "Abarrotes > Arroz"),     # agotado (precio 0 -> NaN)
        ("A", 2, 10.0, False, "Abarrotes > Arroz"),       # agotado con precio publicado
        ("A", 3, 10.0, True, "Abarrotes > Arroz"),
        ("A", 4, 10.0, True, "Abarrotes > Arroz"),
        ("B", 0, 5.0, True, "Abarrotes > Fideos"),        # desaparece el dia 1 (hoja recorrida)
        ("B", 2, 5.0, True, "Abarrotes > Fideos"),        # desaparece dias 3 y 4 (hoja NO recorrida)
        ("C", 3, 7.0, True, "Abarrotes > Sal"),           # aparece tarde: antes no cuenta
    ])
    hojas = {"metro": {
        F5[0]: {"Abarrotes > Arroz", "Abarrotes > Fideos"},
        F5[1]: {"Abarrotes > Arroz", "Abarrotes > Fideos"},
        F5[2]: {"Abarrotes > Arroz", "Abarrotes > Fideos"},
        F5[3]: {"Abarrotes > Arroz", "Abarrotes > Sal"},
        # F5[4]: sin arbol
    }}
    rachas, celdas = F.huecos(df, hojas)
    c = celdas.set_index(["item_id", "fecha"])["tipo"]
    assert c[("A", F5[1])] == "agotado_listado"
    assert c[("A", F5[2])] == "agotado_listado"
    assert c[("B", F5[1])] == "desaparecido_catalogo"
    assert c[("B", F5[3])] == "desaparecido_arbol"
    assert c[("B", F5[4])] == "desaparecido_sin_arbol"
    assert ("C", F5[0]) not in c.index and ("C", F5[4]) in c.index
    r = rachas.set_index("item_id")
    assert r.loc["A", "dias"] == 2 and not r.loc["A", "censurada"]
    rb = rachas[rachas["item_id"] == "B"].sort_values("inicio")
    assert list(rb["dias"]) == [1, 2] and list(rb["censurada"]) == [False, True]
    res = F.resumen_huecos(celdas, df).iloc[0]
    assert res["celdas_sku_dia"] == 5 + 5 + 2
    assert res["huecos"] == 2 + 3 + 1


def _matriz(vals):
    m = pd.DataFrame(vals, index=[f"e{i}" for i in range(len(vals))], columns=F5, dtype=float)
    return m


def test_arrastre_con_tope():
    m = _matriz([[10, np.nan, np.nan, np.nan, np.nan]])
    a = F.regla_arrastre(m, tope=2)
    assert list(a.iloc[0]) == [10, 10, 10] + [None, None] or (
        a.iloc[0, 1] == 10 and a.iloc[0, 2] == 10 and np.isnan(a.iloc[0, 3]) and np.isnan(a.iloc[0, 4]))
    a7 = F.regla_arrastre(m, tope=7)
    assert (a7.iloc[0] == 10).all()
    # un precio nuevo corta el arrastre
    m2 = _matriz([[10, np.nan, 12, np.nan, np.nan]])
    a2 = F.regla_arrastre(m2, tope=7)
    assert list(a2.iloc[0]) == [10, 10, 12, 12, 12]


def test_imputacion_por_categoria():
    # e0 falta el dia 2; e1 y e2 (misma categoria) suben 10% entre dia 1 y 2
    m = _matriz([[10, 10, np.nan, 10, 10],
                 [20, 20, 22, 22, 22],
                 [30, 30, 33, 33, 33],
                 [50, 50, 50, 50, 50]])          # otra categoria, plana
    cat = pd.Series({"e0": "a", "e1": "a", "e2": "a", "e3": "b"})
    imp = F.regla_imputacion(m, cat)
    assert abs(imp.loc["e0", F5[2]] - 11.0) < 1e-9
    assert imp.loc["e0", F5[3]] == 10.0            # vuelve a observarse: no se imputa
    m2 = _matriz([[10, np.nan, np.nan, 10, 10], [50, 50, 50, 50, 50]])
    cat2 = pd.Series({"e0": "a", "e1": "b"})
    imp2 = F.regla_imputacion(m2, cat2)
    # sin pares en su categoria, usa la variacion global (0%): arrastre de facto
    assert imp2.loc["e0", F5[1]] == 10.0 and imp2.loc["e0", F5[2]] == 10.0


def test_las_tres_reglas_coinciden_sin_huecos():
    m = _matriz([[10, 11, 12, 12, 12], [20, 20, 20, 21, 21]])
    cat = pd.Series({"e0": "a", "e1": "a"})
    can = F.canasta_por_regla({"metro": m}, cat)
    w = can.pivot_table(index="fecha", columns="regla", values="indice")
    assert np.allclose(w["arrastre"], w["exclusion"]) and np.allclose(w["arrastre"], w["imputacion"])
    assert abs(w["arrastre"].iloc[-1] - 100 * 33 / 30) < 1e-9
    con = F.contraste(can)
    assert con.iloc[0]["indiferente"] and con.iloc[0]["divergencia_max"] < 1e-9


def test_las_reglas_divergen_con_huecos():
    # e0 desaparece los dias 2-4 mientras e1 sube 10% el dia 2
    m = _matriz([[100, 100, np.nan, np.nan, np.nan], [100, 100, 110, 110, 110]])
    cat = pd.Series({"e0": "a", "e1": "a"})
    can = F.canasta_por_regla({"metro": m}, cat, tope=1)
    w = can.pivot_table(index="fecha", columns="regla", values="indice")
    # arrastre (tope 1): dia 2 = (100 + 110)/200 -> 105; dia 3 el tope vence y
    # se compara solo e1: 110/100 -> 110
    assert abs(w.loc[F5[2], "arrastre"] - 105) < 1e-9
    assert abs(w.loc[F5[3], "arrastre"] - 110) < 1e-9
    # imputacion: e0 sigue a su categoria -> 110 todos los dias desde el 2
    assert abs(w.loc[F5[2], "imputacion"] - 110) < 1e-9
    # exclusion encadenada: dia 2 solo e1 en ambos dias -> 110
    assert abs(w.loc[F5[2], "exclusion"] - 110) < 1e-9
    con = F.contraste(can).iloc[0]
    assert not con["indiferente"] and abs(con["divergencia_max"] - 5) < 1e-9


def test_matriz_canasta_exige_disponibilidad_y_cadenas():
    filas = []
    for cad in P.CADENAS:
        for i in range(2):
            filas.append({"retailer": cad, "item_id": f"{cad}1", "fecha": F5[i], "ean": "7750000000001",
                          "price": 10.0, "available": None if cad == "tottus" else True,
                          "categoria_comun": "abarrotes"})
            filas.append({"retailer": cad, "item_id": f"{cad}2", "fecha": F5[i], "ean": "7750000000002",
                          "price": 10.0, "available": False if cad == "metro" else True,
                          "categoria_comun": "abarrotes"})
    df = pd.DataFrame(filas)
    mats, cat, fechas = F.matriz_canasta(df, min_cadenas=5)
    assert set(mats) == set(P.CADENAS)
    assert list(mats["metro"].index) == ["7750000000001"]      # el 2 esta sin stock en metro
    mats4, _, _ = F.matriz_canasta(df, min_cadenas=4)
    assert len(mats4["wong"].index) == 2


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
