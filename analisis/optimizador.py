"""Optimizador v0 -- la pieza prescriptiva: que comprar y donde, hoy.

    python -m analisis.optimizador                  # ultimo dia del panel
    python -m analisis.optimizador --fecha 2026-11-20
    python -m analisis.optimizador --costo-visita 5 --max-cadenas 2   # escenario opcional

NO es el Modelo A. A predice precios que no se ven; el optimizador decide
con lo que se ve. Elegir el menor precio observado es esto, no A.

Version principal
-----------------
Minimiza el costo de HOY de la canasta de trabajo: cada item en la cadena
mas barata entre las que lo tienen emparejado, con `price` observado y
`available` distinto de False.

- El precio que se paga es `price`. C (oferta_real.py) no cambia ese monto.
- Un agotado (available=False) o una celda sin precio es INFACTIBLE. No se
  arrastra ningun precio de precios_faltantes.py: no se puede comprar a un
  precio arrastrado.
- Tottus no publica stock: no se inventa ni se supone siempre disponible.
  El plan sale en dos escenarios, `con_tottus` y `sin_tottus`.
- No se penaliza comprar en varias cadenas. El escenario con costo de
  visita F_c y tope de cadenas es OPCIONAL y esta apagado por defecto; se
  resuelve exacto enumerando los subconjuntos de cadenas (2^5 = 32), sin
  solver.

Linea base del sistema (definida aqui, antes de calcular cualquier ahorro)
------------------------------------------------------------------------
Una sola cadena para toda la canasta, comprando hoy, y creyendo el cartel:
el consumidor elige la cadena donde el cartel le PROMETE mas ahorro
(sum q * (regular_price - price) en los items con on_sale), entre las que
cubren el maximo de items. Paga `price`. Lo que cree ahorrar se reporta
partido en dos con C: el ahorro anunciado en ofertas reales y el anunciado
en ofertas fantasma. El fantasma NO cuenta como ahorro de la linea base; si
C no es fiable para un item, ese ahorro se reporta como no verificable.

La comparacion sistema vs linea base se hace sobre los MISMOS items (los
que la cadena de la linea base puede vender ese dia).

Lo que este modulo NO afirma: una comparacion de un dia, dentro de muestra,
no es el ahorro del sistema. El ahorro se mide fuera de muestra cuando haya
panel (backtest); hoy se imprime con esa advertencia.

Enchufes para despues
---------------------
- `a_pred`: precios predichos por el Modelo A para celdas SIN precio
  observado (nunca para agotados). Apagado mientras A no este entrenado.
- `accion_temporal`: la decision comprar hoy / esperar con el Modelo B. Si
  el item ya esta en oferta real hoy se compra hoy por regla; un fantasma
  no es motivo ni para comprar ya ni para esperar. Sin B: comprar hoy.

Sin canasta de trabajo o sin C fiable el optimizador igual corre: como
comparador del precio observado (sin canasta, sobre el universo comun de
EAN de fabricante en 4+ cadenas, un envase de cada uno; eso NO es la
canasta y la salida lo dice).
"""

import argparse
import itertools

import numpy as np
import pandas as pd

from analisis import oferta_real as OR
from analisis import panel as P
from analisis import canasta_trabajo as CT

ORDEN_CADENAS = ["metro", "wong", "plaza_vea", "vivanda", "tottus"]
ESCENARIOS_TOTTUS = {"con_tottus": True, "sin_tottus": False}
UMBRAL_ESPERA = 0.0        # soles de ahorro esperado por esperar (enchufe de B)

COLS_PRECIO = ["retailer", "item_id", "price", "regular_price", "on_sale", "available"]


# ---------------------------------------------------------------------------
# Entradas
# ---------------------------------------------------------------------------

def canasta_desde_universo(dia, min_cadenas=CT.MIN_CADENAS):
    """Modo comparador: EAN de fabricante en >= min_cadenas cadenas ese dia,
    un envase de cada uno. NO es la canasta de trabajo."""
    d = dia[P.ean_comparable(dia["ean"])]
    n = d.groupby("ean")["retailer"].nunique()
    eans = n[n >= min_cadenas].index
    d = d[d["ean"].isin(eans)].sort_values(["ean", "retailer", "item_id"])
    d = d.drop_duplicates(["ean", "retailer"])
    return pd.DataFrame({
        "id": d["ean"], "descripcion": d["product_name"], "grupo": "universo",
        "cadena": d["retailer"], "item_id": d["item_id"], "ean": d["ean"],
        "cantidad": 1.0, "paquetes": 1.0, "unidad": "envase",
    })


def celdas(canasta, precios_dia, c_dia=None, a_pred=None):
    """Una fila por (item, cadena emparejada) con lo que se paga y si se puede.

    `precios_dia`: filas del panel del dia (COLS_PRECIO). `c_dia`: tabla de
    C del dia (oferta_real.tabla_c filtrada). `a_pred`: DataFrame (cadena,
    item_id, precio_predicho) del Modelo A, solo para celdas sin precio
    observado."""
    p = precios_dia[COLS_PRECIO].drop_duplicates(["retailer", "item_id"], keep="last")
    p = p.rename(columns={"retailer": "cadena"})
    c = canasta[["id", "descripcion", "grupo", "cadena", "item_id", "ean", "cantidad",
                 "paquetes", "unidad"]].copy()
    c["item_id"] = c["item_id"].astype(str)
    p["item_id"] = p["item_id"].astype(str)
    m = c.merge(p, on=["cadena", "item_id"], how="left")
    m["observado"] = m["price"].notna() & (m["price"] > 0)
    m["agotado"] = m["available"].eq(False)
    m["factible"] = m["observado"] & ~m["agotado"]
    m["fuente_precio"] = np.where(m["factible"], "observado", "")
    m["precio_pagado"] = m["price"].where(m["factible"])

    if a_pred is not None and len(a_pred):
        a = a_pred.rename(columns={"retailer": "cadena"})[["cadena", "item_id", "precio_predicho"]]
        a = a.assign(item_id=a["item_id"].astype(str))
        m = m.merge(a, on=["cadena", "item_id"], how="left")
        rellenar = ~m["observado"] & m["precio_predicho"].notna()     # nunca un agotado
        m.loc[rellenar, "precio_pagado"] = m.loc[rellenar, "precio_predicho"]
        m.loc[rellenar, "factible"] = True
        m.loc[rellenar, "fuente_precio"] = "modelo_a"
        m = m.drop(columns="precio_predicho")

    m["costo"] = m["paquetes"] * m["precio_pagado"]
    m["on_sale"] = m["on_sale"].astype("boolean").fillna(False).astype(bool)
    reg = m["regular_price"].where(m["regular_price"] > m["price"])
    m["ahorro_anunciado"] = (m["paquetes"] * (reg - m["price"])).where(m["on_sale"], 0.0).fillna(0.0)

    if c_dia is not None and len(c_dia):
        cc = c_dia.rename(columns={"retailer": "cadena"})[
            ["cadena", "item_id", "fiable", "oferta_real", "fantasma", "rebaja_silenciosa",
             "precio_habitual", "descuento_real"]]
        cc = cc.assign(item_id=cc["item_id"].astype(str))
        m = m.merge(cc, on=["cadena", "item_id"], how="left")
    else:
        for col in ("oferta_real", "fantasma", "rebaja_silenciosa"):
            m[col] = pd.NA
        m["fiable"] = False
        m["precio_habitual"] = np.nan
        m["descuento_real"] = np.nan
    m["fiable"] = m["fiable"].astype("boolean").fillna(False).astype(bool)
    return m


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

def _rango(cadenas):
    return {c: i for i, c in enumerate(ORDEN_CADENAS + sorted(set(cadenas) - set(ORDEN_CADENAS)))}


def plan_principal(cel):
    """Cada item en su cadena factible mas barata. Empate: orden fijo de
    cadenas. Devuelve (plan, sin_cadena)."""
    f = cel[cel["factible"]].copy()
    rk = _rango(f["cadena"])
    f["_rk"] = f["cadena"].map(rk)
    f = f.sort_values(["id", "costo", "_rk"])
    plan = f.drop_duplicates("id", keep="first").drop(columns="_rk")
    sin = sorted(set(cel["id"]) - set(plan["id"]))
    return plan, sin


def plan_con_visita(cel, costo_visita, max_cadenas=None):
    """Escenario opcional: min sum costo + sum F_c por cadena usada, con a lo
    sumo `max_cadenas` cadenas, cubriendo todo item que el escenario puede
    cubrir. Exacto por enumeracion de subconjuntos."""
    f = cel[cel["factible"]]
    cadenas = sorted(f["cadena"].unique(), key=_rango(f["cadena"]).get)
    cubribles = set(f["id"])
    F = costo_visita if isinstance(costo_visita, dict) else {c: float(costo_visita) for c in cadenas}
    k_max = max_cadenas or len(cadenas)
    mejor = None
    for k in range(1, k_max + 1):
        for sub in itertools.combinations(cadenas, k):
            g = f[f["cadena"].isin(sub)]
            if set(g["id"]) != cubribles:
                continue
            plan, _ = plan_principal(g)
            usadas = set(plan["cadena"])
            total = plan["costo"].sum() + sum(F.get(c, 0.0) for c in usadas)
            if mejor is None or total < mejor[0] - 1e-9:
                mejor = (total, plan, sorted(usadas))
    if mejor is None:
        return None, None, None
    return mejor[1], mejor[0], mejor[2]


def linea_base(cel):
    """La regla de la linea base, fija: una cadena, hoy, creyendo el cartel.

    Devuelve (cadena_elegida, tabla_por_cadena). La tabla trae, para cada
    cadena, lo que costaria la canasta ahi (los items que puede vender) y
    lo que el cartel promete, partido por C."""
    f = cel[cel["factible"] & cel["fuente_precio"].eq("observado")]
    filas = []
    for cad, g in f.groupby("cadena"):
        real = g["oferta_real"].astype("boolean").fillna(False).astype(bool)
        fant = g["fantasma"].astype("boolean").fillna(False).astype(bool)
        fiable = g["fiable"]
        filas.append({
            "cadena": cad,
            "items": g["id"].nunique(),
            "costo": g["costo"].sum(),
            "ahorro_anunciado": g["ahorro_anunciado"].sum(),
            "ahorro_anunciado_en_oferta_real": g.loc[fiable & real, "ahorro_anunciado"].sum(),
            "ahorro_anunciado_fantasma": g.loc[fiable & fant, "ahorro_anunciado"].sum(),
            "ahorro_anunciado_no_verificable": g.loc[~fiable, "ahorro_anunciado"].sum(),
        })
    t = pd.DataFrame(filas)
    if t.empty:
        return None, t
    rk = _rango(t["cadena"])
    t["_rk"] = t["cadena"].map(rk)
    t = t.sort_values(["items", "ahorro_anunciado", "_rk"], ascending=[False, False, True])
    elegida = t.iloc[0]["cadena"]
    t["elegida"] = t["cadena"] == elegida
    return elegida, t.drop(columns="_rk").sort_values("cadena").reset_index(drop=True)


def accion_temporal(cel_plan, b_pred=None, umbral=UMBRAL_ESPERA):
    """Enchufe del Modelo B. Sin B: comprar hoy.

    Con B (cadena, item_id, p_oferta, profundidad): si el item ya esta en
    oferta real hoy -> comprar hoy (regla). Si no, esperar cuando
    p_oferta * profundidad * precio_habitual * paquetes > umbral. El
    fantasma no entra en la decision: ni empuja a comprar ni a esperar."""
    out = cel_plan.copy()
    out["accion"] = "comprar_hoy"
    out["motivo_accion"] = "sin_modelo_b"
    real_hoy = out["oferta_real"].astype("boolean").fillna(False).astype(bool)
    out.loc[real_hoy, "motivo_accion"] = "oferta_real_hoy"
    if b_pred is None or not len(b_pred):
        return out
    b = b_pred.rename(columns={"retailer": "cadena"})[["cadena", "item_id", "p_oferta", "profundidad"]]
    b = b.assign(item_id=b["item_id"].astype(str))
    out = out.merge(b, on=["cadena", "item_id"], how="left")
    ref = out["precio_habitual"].fillna(out["precio_pagado"])
    ahorro_esp = out["p_oferta"] * out["profundidad"] * ref * out["paquetes"]
    esperar = ~real_hoy & (ahorro_esp > umbral)
    out.loc[~real_hoy, "motivo_accion"] = "modelo_b"
    out.loc[esperar, "accion"] = "esperar"
    return out


def resolver(canasta, precios_dia, c_dia=None, costo_visita=None, max_cadenas=None,
             a_pred=None, b_pred=None):
    """Corre los dos escenarios de Tottus. Devuelve (plan, resumen, base)."""
    planes, resumenes, bases = [], [], []
    todas = celdas(canasta, precios_dia, c_dia, a_pred)
    for esc, con_tottus in ESCENARIOS_TOTTUS.items():
        cel = todas if con_tottus else todas[todas["cadena"] != "tottus"]
        plan, sin = plan_principal(cel)
        plan = accion_temporal(plan, b_pred)
        elegida, tabla = linea_base(cel)
        tabla = tabla.assign(escenario=esc)
        items_base = set(cel[(cel["cadena"] == elegida) & cel["factible"]
                             & cel["fuente_precio"].eq("observado")]["id"]) if elegida else set()
        fila_base = tabla[tabla["elegida"]].iloc[0] if elegida else None
        costo_sis_mismos = plan[plan["id"].isin(items_base)]["costo"].sum()
        r = {
            "escenario": esc,
            "items_canasta": cel["id"].nunique(),
            "items_con_plan": plan["id"].nunique(),
            "items_sin_cadena_factible": len(sin),
            "costo_sistema": plan["costo"].sum(),
            "cadenas_usadas": ",".join(sorted(plan["cadena"].unique(), key=_rango(plan["cadena"]).get)),
            "items_de_precio_predicho": int((plan["fuente_precio"] == "modelo_a").sum()),
            "linea_base_cadena": elegida,
            "linea_base_items": len(items_base),
            "linea_base_costo": fila_base["costo"] if fila_base is not None else np.nan,
            "sistema_costo_mismos_items": costo_sis_mismos,
            "linea_base_ahorro_anunciado": fila_base["ahorro_anunciado"] if fila_base is not None else np.nan,
            "linea_base_ahorro_en_oferta_real": fila_base["ahorro_anunciado_en_oferta_real"] if fila_base is not None else np.nan,
            "linea_base_ahorro_fantasma": fila_base["ahorro_anunciado_fantasma"] if fila_base is not None else np.nan,
            "linea_base_ahorro_no_verificable": fila_base["ahorro_anunciado_no_verificable"] if fila_base is not None else np.nan,
            "visita_activa": costo_visita is not None,
        }
        if costo_visita is not None:
            pv, total, usadas = plan_con_visita(cel, costo_visita, max_cadenas)
            r["visita_costo_total"] = total
            r["visita_cadenas"] = ",".join(usadas) if usadas else ""
            if pv is not None:
                planes.append(pv.assign(escenario=f"{esc}+visita"))
        planes.append(plan.assign(escenario=esc))
        resumenes.append(r)
        bases.append(tabla)
    return (pd.concat(planes, ignore_index=True), pd.DataFrame(resumenes),
            pd.concat(bases, ignore_index=True))


# ---------------------------------------------------------------------------
# Corrida
# ---------------------------------------------------------------------------

COLS_PLAN = ["escenario", "id", "descripcion", "grupo", "cadena", "item_id", "ean",
             "cantidad", "unidad", "paquetes", "precio_pagado", "fuente_precio", "costo",
             "on_sale", "fiable", "oferta_real", "fantasma", "rebaja_silenciosa",
             "accion", "motivo_accion"]


def correr(fecha=None, costo_visita=None, max_cadenas=None, panel_df=None,
           canasta_path=CT.SALIDA, silencioso=False):
    panel = panel_df if panel_df is not None else P.cargar_panel(
        columnas=["retailer", "item_id", "product_name", "ean", "price", "regular_price",
                  "on_sale", "available"])
    if panel.empty:
        raise SystemExit("Panel vacio.")
    fecha = pd.Timestamp(fecha) if fecha else panel["fecha"].max()
    hist = panel[panel["fecha"] <= fecha]
    dia = hist[hist["fecha"] == fecha]
    if dia.empty:
        raise SystemExit(f"No hay datos del {fecha.date()}.")

    flags = OR.calcular(hist[["retailer", "item_id", "fecha", "price", "on_sale", "regular_price"]])
    c_dia = OR.tabla_c(flags[flags["fecha"] == fecha])
    c_fiable = bool(c_dia["fiable"].any())

    if canasta_path is not None and canasta_path.exists():
        canasta = pd.read_csv(canasta_path, dtype={"item_id": str, "ean": str})
        modo = f"canasta_trabajo ({canasta['estado'].iloc[0]})"
    else:
        canasta = canasta_desde_universo(dia)
        modo = "comparador_universo (NO es la canasta)"

    plan, resumen, base = resolver(canasta, dia, c_dia, costo_visita, max_cadenas)
    resumen.insert(0, "modo", modo)
    resumen.insert(1, "fecha", fecha.date())
    resumen.insert(2, "c_fiable", c_fiable)
    tag = str(fecha.date())
    P.escribir_csv(plan.reindex(columns=COLS_PLAN), "optimizador_plan", tag)
    P.escribir_csv(resumen, "optimizador_resumen", tag)
    P.escribir_csv(base, "optimizador_linea_base", tag)

    if not silencioso:
        print(f"=== optimizador v0 | {tag} | modo {modo} | C fiable: {c_fiable} ===\n")
        v = resumen.drop(columns=["modo", "fecha"]).T
        print(v.to_string(header=False))
        print("\nNO ES UN HALLAZGO: comparacion de un dia, dentro de muestra. El ahorro del\n"
              "sistema se mide fuera de muestra (backtest) cuando haya panel.")
        if not c_fiable:
            print("C no es fiable todavia: el optimizador no usa el cartel y el ahorro\n"
                  "anunciado de la linea base sale entero como no verificable.")
        print(f"-> optimizador_plan__{tag}.csv, optimizador_resumen__{tag}.csv, "
              f"optimizador_linea_base__{tag}.csv")
    return plan, resumen, base


def main(argv=None):
    P.configurar_stdout()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fecha", help="dia a optimizar (def. el ultimo del panel)")
    ap.add_argument("--costo-visita", type=float, default=None,
                    help="escenario opcional: soles por cadena visitada (apagado por defecto)")
    ap.add_argument("--max-cadenas", type=int, default=None,
                    help="escenario opcional: tope de cadenas (solo con --costo-visita)")
    args = ap.parse_args(argv)
    if args.max_cadenas and args.costo_visita is None:
        ap.error("--max-cadenas va con --costo-visita (use 0 para solo el tope)")
    correr(args.fecha, args.costo_visita, args.max_cadenas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
