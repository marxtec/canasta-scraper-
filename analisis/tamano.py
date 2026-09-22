"""Efecto del tamano del envase en el precio por kg, y la tolerancia de tamano.

    python -m analisis.tamano          # imprime beta por item con todo el panel

Mismo producto, misma cadena, distinto tamano: el paquete grande sale mas
barato por kilo. Se mide con efectos fijos por linea (cadena x nombre sin
cantidad ni envase):

    ln p_s = alfa_linea + beta * ln w_s + e_s

con p el precio por kg o litro (mediana del SKU en la ventana) y w el
tamano. Con beta se despeja la relacion de tamanos maxima que mantiene el
sesgo por tamano bajo DELTA:

    r_max = exp(|ln(1 - DELTA) / beta|)

Es la unica tolerancia numerica de la canasta (docs: Metodologia de
estandarizacion, secciones 3 y 7). La estima canasta_trabajo.correr() con la
misma ventana que decide la inclusion y la escribe al lado de la canasta: el
cruce la recibe hecha y no mira precios.
"""

import argparse

import numpy as np
import pandas as pd

from analisis import panel as P
from analisis import presentacion as PR

DELTA = 0.05                # sesgo maximo aceptado por diferencia de tamano
MIN_LINEAS = 20             # menos lineas: se usa el beta del panel entero
BETA_DEFECTO = -0.316       # panel del 22-09-2026, 2.621 lineas (docs, seccion 3)

COLUMNAS = ["id", "beta", "lineas", "fuente", "r_max"]


def r_max(beta, delta=DELTA):
    """Relacion de tamanos maxima con sesgo <= delta. beta >= 0: sin limite util."""
    if beta is None or not np.isfinite(beta) or beta >= 0:
        return float("inf")
    return float(np.exp(abs(np.log(1 - delta) / beta)))


def _lineas(d):
    """SKU con precio por unidad base y su linea, sin packs de otra cosa ni granel."""
    d = d[d["price"].notna() & d["unit"].isin(["kg", "l"]) & (d["net_quantity"] > 0)].copy()
    d["linea"] = d["product_name"].map(PR.linea)
    sku = (d.groupby(["retailer", "item_id", "linea", "unit"])
           .agg(price=("price", "median"), w=("net_quantity", "median")).reset_index())
    sku = sku[sku["linea"] != ""]
    sku["lp"] = np.log(sku["price"] / sku["w"])
    sku["lw"] = np.log(sku["w"])
    clave = ["retailer", "linea", "unit"]
    sku = sku[sku.groupby(clave)["w"].transform("nunique") >= 2]
    return sku, clave


def beta_efectos_fijos(sku, clave):
    """(beta, n_lineas) de la regresion con efectos fijos por linea."""
    if sku.empty:
        return np.nan, 0
    lw = sku["lw"] - sku.groupby(clave)["lw"].transform("mean")
    lp = sku["lp"] - sku.groupby(clave)["lp"].transform("mean")
    den = float((lw ** 2).sum())
    if den == 0:
        return np.nan, 0
    return float((lw * lp).sum() / den), int(sku.groupby(clave).ngroups)


def estimar(panel, reglas, cumple):
    """Tabla (id, beta, lineas, fuente, r_max) para cada regla envasada.

    `cumple(d, regla)` es la mascara de la regla (canasta_trabajo._cumple);
    se recibe para no duplicar la logica de emparejamiento."""
    sku, clave = _lineas(panel)
    b_panel, n_panel = beta_efectos_fijos(sku, clave)
    if not np.isfinite(b_panel) or n_panel < MIN_LINEAS:
        b_panel = BETA_DEFECTO
    filas = []
    base = sku.merge(panel.drop_duplicates(["retailer", "item_id"])[
        ["retailer", "item_id", "plano", "marca_plana"]], on=["retailer", "item_id"], how="left")
    for rid, regla in reglas.iterrows():
        if regla["tipo"] not in ("envasado", "por_unidad"):
            continue
        s = base[base["unit"].eq(regla["unidad"]) & cumple(base, regla)]
        b, n = beta_efectos_fijos(s, clave)
        if np.isfinite(b) and n >= MIN_LINEAS:
            filas.append({"id": rid, "beta": b, "lineas": n, "fuente": "item"})
        else:
            filas.append({"id": rid, "beta": b_panel, "lineas": n, "fuente": "panel"})
    t = pd.DataFrame(filas, columns=["id", "beta", "lineas", "fuente"])
    t["r_max"] = t["beta"].map(r_max)
    return t, b_panel


def main(argv=None):
    from analisis import canasta_trabajo as CT
    P.configurar_stdout()
    argparse.ArgumentParser(description=__doc__.split("\n")[0]).parse_args(argv)
    panel = P.cargar_panel(columnas=CT.COLUMNAS_PANEL)
    d = CT.preparar_panel(panel, conservar_precio=True)
    t, b = estimar(d, CT.cargar_reglas().set_index("id"), CT._cumple)
    print(f"beta del panel: {b:.3f} -> r_max {r_max(b):.2f} (DELTA {DELTA:.0%})")
    print(t.round(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
