#!/usr/bin/env python3
"""Prueba de punta a punta de la capa de analisis sobre un panel SINTETICO
de 40 dias escrito en un directorio temporal con el esquema real (COLUMNS).

Es la garantia de que en noviembre, con 60+ dias, los scripts corren sin
tocar una linea: aqui se simula ese panel y se exige que los flags que hoy
salen PROVISIONAL pasen a fiables.

    python tests/test_analisis_e2e.py
"""

import csv
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from analisis import dispersion, oferta_real, panel as P, precios_faltantes, rigidez, run_all
from canasta.normalize import COLUMNS

N_DIAS, N_SKU = 40, 120
CADENAS = ["metro", "wong", "plaza_vea"]


def _escribir_panel(daily_dir):
    rng = np.random.default_rng(42)
    fechas = pd.date_range("2026-10-01", periods=N_DIAS, freq="D")
    base = np.exp(rng.normal(2.5, 0.7, N_SKU))
    for cad in CADENAS:
        efecto = {"metro": 1.0, "wong": 1.0, "plaza_vea": 1.08}[cad]
        for i, f in enumerate(fechas):
            filas = []
            for k in range(N_SKU):
                # SKU 0-9: oferta real de 5 dias a partir del dia 30
                en_oferta = k < 10 and 30 <= i < 35
                # SKU 10-19: tachado permanente
                tachado = 10 <= k < 20
                # SKU 20-24: hueco (desaparece) dias 20-22
                if 20 <= k < 25 and 20 <= i <= 22:
                    continue
                precio = round(base[k] * efecto * (0.8 if en_oferta else 1.0), 2)
                regular = round(precio / 0.8, 2) if (en_oferta or tachado) else precio
                filas.append({
                    "timestamp": f"{f.date()} 08:00:00-0500", "retailer": cad,
                    "product_id": f"p{k}", "item_id": f"{cad}-{k}",
                    "product_name": f"Producto {k} 500 g", "brand": "X",
                    "category": "Abarrotes", "category_path": "Abarrotes > Arroz",
                    "net_quantity": 0.5, "unit": "kg", "price": precio,
                    "regular_price": regular, "on_sale": regular > precio,
                    "available": True if k % 7 else False, "ean": f"77500000{k:05d}",
                    "url": f"https://x/{k}/p", "seller_name": cad, "scraper_version": "1.2",
                })
            with open(daily_dir / f"{f.date()}__{cad}.csv", "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
                w.writeheader()
                w.writerows(filas)


def _con_directorios_temporales(fn):
    tmp = pathlib.Path(tempfile.mkdtemp())
    daily, derived = tmp / "daily", tmp / "derived"
    daily.mkdir()
    orig = (P.DAILY_DIR, P.DERIVED_DIR, P.FIGURAS_DIR)
    P.DAILY_DIR, P.DERIVED_DIR, P.FIGURAS_DIR = daily, derived, derived / "figuras"
    try:
        _escribir_panel(daily)
        return fn(tmp)
    finally:
        P.DAILY_DIR, P.DERIVED_DIR, P.FIGURAS_DIR = orig


def test_descubre_los_dias_solo():
    def cuerpo(tmp):
        dias = P.dias_disponibles()
        assert len(dias) == N_DIAS and dias[-1] == "2026-11-09"
        assert P.ultima_fecha() == "2026-11-09"
        panel = P.cargar_panel()
        assert panel["fecha"].nunique() == N_DIAS
        assert set(panel["retailer"]) == set(CADENAS)
        assert panel["available"].eq(False).sum() > 0
    _con_directorios_temporales(cuerpo)


def test_oferta_real_se_vuelve_fiable_con_panel_largo():
    def cuerpo(tmp):
        flags, prod, cad = oferta_real.correr(silencioso=True)
        ult = prod[prod["retailer"] == "metro"].set_index("item_id")
        assert ult["fiable"].all()                                   # 40 dias > 21
        assert bool(ult.loc["metro-5", "oferta_real"]) is False       # la oferta ya termino
        assert bool(ult.loc["metro-15", "tachado_permanente"]) is True
        f = flags[(flags["retailer"] == "metro") & (flags["item_id"] == "metro-5")].sort_values("fecha")
        assert f.iloc[30:35]["oferta_real"].astype(bool).all()
        assert ult.loc["metro-22", "dias_con_hueco"] == 3
        c = cad.set_index("cadena")
        assert c.loc["metro", "no_evaluable"] == 0
        assert (tmp / "derived" / "oferta_real__2026-11-09.csv").exists()
    _con_directorios_temporales(cuerpo)


def test_rigidez_mide_habiles_y_horizontes():
    def cuerpo(tmp):
        agg, tr, hz, tam = rigidez.correr(silencioso=True)
        a = agg.set_index(["retailer", "tipo"])
        assert a.loc[("metro", "habil"), "n_transiciones"] > 15   # 40 dias con el feriado del 8-10
        assert a.loc[("metro", "cruza_finde"), "n_transiciones"] > 5
        # las 10 ofertas entran el dia 30 y salen el 35: 10 bajadas y 10 subidas
        assert a.loc[("metro", "total"), "sku_seguidos"] > 0
        assert tr["baja"].sum() == 10 * len(CADENAS) and tr["sube"].sum() == 10 * len(CADENAS)
        assert a.loc[("metro", "total"), "entra_oferta_real"] > 0
        h = hz.set_index(["retailer", "horizonte", "version"])
        assert h.loc[("metro", 7, "decl"), "origenes"] > 0
        assert h.loc[("metro", 30, "decl"), "origenes"] > 0
        assert h.loc[("metro", 7, "decl"), "positivos"] > 0
    _con_directorios_temporales(cuerpo)


def test_dispersion_y_faltantes_sobre_40_dias():
    def cuerpo(tmp):
        r = dispersion.correr(con_figuras=True, silencioso=True)
        assert len(r["evo"]) == N_DIAS
        pares = r["pares"].set_index("par")
        assert pares.loc["metro+wong", "identico"] > 0.99
        assert pares.loc["metro+plaza_vea", "identico"] < 0.01
        assert (tmp / "derived" / "figuras" / "evolucion.png").exists()
        assert (tmp / "derived" / "tablas_dispersion__2026-11-09.tex").exists()
        f = precios_faltantes.correr(silencioso=True)
        assert f["resumen"]["huecos"].sum() > 0
        assert not f["contraste"].empty and len(f["canasta"]) == 3 * len(CADENAS) * N_DIAS
        # correr dos veces no duplica derivados
        dispersion.correr(con_figuras=False, silencioso=True)
        assert len(list((tmp / "derived").glob("dispersion__*.csv"))) == 1
    _con_directorios_temporales(cuerpo)


def test_actualizar_informe_reemplaza_solo_el_bloque():
    tmp = pathlib.Path(tempfile.mkdtemp()) / "informe.txt"
    tmp.write_text("antes\n" + run_all.MARCA_INI + "\nviejo\n" + run_all.MARCA_FIN + "\ndespues\n",
                   encoding="utf-8")
    assert run_all.actualizar_informe(tmp, "\\begin{table}nuevo\\end{table}\n")
    t = tmp.read_text(encoding="utf-8")
    assert "viejo" not in t and "nuevo" in t and t.startswith("antes") and t.rstrip().endswith("despues")
    sin = tmp.parent / "sin_marcas.txt"
    sin.write_text("nada", encoding="utf-8")
    assert not run_all.actualizar_informe(sin, "x") and sin.read_text(encoding="utf-8") == "nada"


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
