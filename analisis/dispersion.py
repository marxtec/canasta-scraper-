"""Bloque 3 -- dispersion del precio entre cadenas y descomposicion de varianza.

    python -m analisis.dispersion
    python -m analisis.dispersion --incluir-internos   # replica el criterio de la bitacora
    python -m analisis.dispersion --sin-figuras

"Metro y Wong coinciden 67,7%" es descriptivo. Para que tenga valor de paper
hay que decir cuanto de la varianza del precio explica cada nivel. Sobre los
EAN presentes en 2+ cadenas el mismo dia (mediana cuando varios SKU
comparten EAN en la misma cadena), este modulo calcula:

1. Descomposicion de varianza de log(price) por efectos fijos anidados, con
   R2 incremental por nivel y en DOS ordenes, porque el reparto depende del
   orden:
     A (producto primero): dia -> producto (EAN x dia) -> grupo -> cadena
                           -> cadena x categoria
     B (sin producto):     dia -> categoria -> grupo -> cadena
                           -> cadena x categoria -> producto
   En A la categoria queda absorbida por el producto (es un atributo del
   EAN) y su incremento es 0 por construccion. El modelo completo es el
   mismo en ambos ordenes; solo cambia como se reparte.
   Los efectos fijos de producto se estiman por transformacion within
   (Frisch-Waugh-Lovell), acumulando las ecuaciones normales dia a dia, asi
   que la memoria no crece con el numero de dias.

2. Desviacion estandar del log-precio dentro de cada EAN-dia: la medida de
   Gorodnichenko y Talavera (2017), cuya referencia internacional para
   bienes identicos es 0,13-0,16.

3. Distribucion COMPLETA de la brecha max/min por EAN-dia: percentiles
   10/25/50/75/90 y la masa puntual en cero (proporcion de EAN-dias con
   precio identico en todas sus cadenas y proporcion de pares identicos),
   que es el hallazgo sobre backend de pricing compartido.

4. La tabla de coincidencia por pares de cadenas de la bitacora, sobre
   TODOS los dias.

5. Cada metrica dia a dia, para ver si el hallazgo es estable.

Criterio de EAN: `panel.ean_comparable` (12+ digitos, sin prefijo 2 de
codigos internos). `--incluir-internos` replica exactamente la tabla de la
bitacora del 20-09 (11.019 EAN en 2+ cadenas, 67,7% Metro-Wong).
"""

import argparse
import itertools

import numpy as np
import pandas as pd

from analisis import panel as P

TOL_IDENTICO = 0.005            # soles: misma cifra al centimo
PERCENTILES = (10, 25, 50, 75, 90)
MIN_DIAS_SOLIDO = 10            # dias para dar la descomposicion por estable
REFERENCIA_GT = (0.13, 0.16)    # SD log-precio, Gorodnichenko y Talavera (2017)
BINS_BRECHA = [0, 1e-9, 0.02, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50, 1.0, 100]

# Paleta categorica (misma para todas las figuras, un color por cadena, en
# orden fijo). Cencosud en dos azules, SPSA en dos naranjas, Falabella verde.
COLORES = {
    "metro": "#2a78d6", "wong": "#9085e9",
    "plaza_vea": "#eb6834", "vivanda": "#eda100",
    "tottus": "#1baf7a",
}
NOMBRES = {"metro": "Metro", "wong": "Wong", "plaza_vea": "Plaza Vea",
           "vivanda": "Vivanda", "tottus": "Tottus"}


# ---------------------------------------------------------------------------
# Observaciones EAN x cadena x dia
# ---------------------------------------------------------------------------

def observaciones(panel, incluir_internos=False, solo_disponibles=False):
    """Mediana de price por (fecha, ean, retailer); solo EAN en 2+ cadenas
    ese dia. Añade categoria modal del EAN y log_p.

    Por defecto entra todo precio publicado, este o no disponible el
    producto: lo que se compara es lo que la cadena PUBLICA. Con
    `solo_disponibles` se exigen filas con available != False (Plaza Vea y
    Vivanda publican precio en un 16% y 33% de sus filas comparables sin
    stock; Tottus no publica stock y entra entera)."""
    d = panel[panel["price"].notna() & P.ean_comparable(panel["ean"], incluir_internos)]
    if solo_disponibles and "available" in d:
        d = d[d["available"].ne(False)]
    if d.empty:
        return pd.DataFrame(columns=["fecha", "ean", "retailer", "grupo", "price",
                                     "log_p", "categoria", "n_cadenas"])
    obs = (d.groupby(["fecha", "ean", "retailer"], observed=True)["price"]
           .median().reset_index())
    # categoria del producto = la mas frecuente entre sus filas (todas las
    # cadenas, todos los dias); asi es un atributo del EAN y no de la cadena
    cat = (d.groupby(["ean", "categoria_comun"], observed=True).size()
           .reset_index(name="n").sort_values(["ean", "n"], ascending=[True, False])
           .drop_duplicates("ean").set_index("ean")["categoria_comun"])
    obs["categoria"] = obs["ean"].map(cat)
    obs["grupo"] = obs["retailer"].map(P.GRUPOS)
    obs["log_p"] = np.log(obs["price"])
    obs["n_cadenas"] = obs.groupby(["fecha", "ean"])["retailer"].transform("size")
    return obs[obs["n_cadenas"] >= 2].reset_index(drop=True)


def por_ean_dia(obs):
    """Una fila por (fecha, ean): n_cadenas, sd_log, brecha max/min, identico."""
    g = obs.groupby(["fecha", "ean"])
    out = g.agg(n_cadenas=("retailer", "size"), sd_log=("log_p", lambda s: s.std(ddof=1)),
                p_min=("price", "min"), p_max=("price", "max")).reset_index()
    out["brecha"] = out["p_max"] / out["p_min"] - 1
    out["identico_todas"] = (out["p_max"] - out["p_min"]).abs() < TOL_IDENTICO
    return out


def pares(obs):
    """Comparacion por pares de cadenas sobre todos los EAN-dias comunes.
    Devuelve (resumen_pares, detalle_pares_por_dia)."""
    w = obs.pivot_table(index=["fecha", "ean"], columns="retailer", values="price")
    filas, detalle = [], []
    for a, b in itertools.combinations(P.CADENAS, 2):
        if a not in w or b not in w:
            continue
        s = w[[a, b]].dropna()
        if s.empty:
            continue
        ident = (s[a] - s[b]).abs() < TOL_IDENTICO
        brecha = (s[[a, b]].max(axis=1) / s[[a, b]].min(axis=1) - 1)
        rel = "mismo grupo" if P.GRUPOS[a] == P.GRUPOS[b] else "competidores"
        filas.append({"par": f"{a}+{b}", "relacion": rel, "ean_dias": len(s),
                      "ean_unicos": s.index.get_level_values("ean").nunique(),
                      "identico": ident.mean(), "brecha_mediana": brecha.median(),
                      "a_mas_barato": (s[a] < s[b] - TOL_IDENTICO).mean(),
                      "b_mas_barato": (s[b] < s[a] - TOL_IDENTICO).mean()})
        por_dia = pd.DataFrame({"identico": ident, "brecha": brecha}).groupby(level="fecha")
        dd = por_dia.agg(ean=("identico", "size"), identico=("identico", "mean"),
                         brecha_mediana=("brecha", "median")).reset_index()
        dd.insert(1, "par", f"{a}+{b}")
        detalle.append(dd)
    res = pd.DataFrame(filas).sort_values("identico", ascending=False).reset_index(drop=True)
    det = pd.concat(detalle, ignore_index=True) if detalle else pd.DataFrame()
    return res, det


def distribucion_brecha(ed):
    """Percentiles y masa en cero por subconjunto (>=2, >=3, =5 cadenas)."""
    filas = []
    for etiqueta, sel in (("n_cadenas>=2", ed["n_cadenas"] >= 2),
                          ("n_cadenas>=3", ed["n_cadenas"] >= 3),
                          ("n_cadenas=5", ed["n_cadenas"] == 5)):
        s = ed[sel]
        fila = {"subconjunto": etiqueta, "ean_dias": len(s),
                "ean_unicos": s["ean"].nunique() if len(s) else 0,
                "masa_cero": s["identico_todas"].mean() if len(s) else np.nan,
                "sd_log_media": s["sd_log"].mean() if len(s) else np.nan,
                "sd_log_mediana": s["sd_log"].median() if len(s) else np.nan}
        for p in PERCENTILES:
            fila[f"p{p}"] = s["brecha"].quantile(p / 100) if len(s) else np.nan
        filas.append(fila)
    return pd.DataFrame(filas)


def histograma_brecha(ed):
    d = ed[ed["n_cadenas"] >= 2]
    b = pd.cut(d["brecha"], BINS_BRECHA, right=False, include_lowest=True)
    cnt = b.value_counts(sort=False)
    return pd.DataFrame({"bin_inf": [i.left for i in cnt.index],
                         "bin_sup": [i.right for i in cnt.index],
                         "n": cnt.values, "share": cnt.values / max(len(d), 1)})


# ---------------------------------------------------------------------------
# Descomposicion de varianza
# ---------------------------------------------------------------------------

def _dummies(df, niveles):
    """Matriz de dummies (float64) para las columnas indicadas, sin
    eliminar categorias: la colinealidad se resuelve con pinv."""
    partes = [np.ones((len(df), 1))]
    nombres = ["const"]
    for niv in niveles:
        d = pd.get_dummies(df[niv].astype(str), prefix=niv, dtype=float)
        partes.append(d.to_numpy())
        nombres += list(d.columns)
    return np.hstack(partes), nombres


def _ssr(XtX, Xty, yty, idx):
    """SSR del modelo con las columnas idx, desde las ecuaciones normales."""
    if not idx:
        return yty
    A = XtX[np.ix_(idx, idx)]
    b = np.linalg.pinv(A, rcond=1e-10) @ Xty[idx]
    return float(yty - b @ Xty[idx])


def descomposicion(obs):
    """R2 acumulado e incremental por nivel, ordenes A y B (ver docstring).

    Se acumulan X'X, X'y, y'y dia a dia: la transformacion within por
    producto (EAN x dia) es exacta hecha por dias porque el producto-dia
    esta anidado en el dia."""
    if obs.empty:
        return pd.DataFrame()
    obs = obs.copy()
    obs["cad_cat"] = obs["retailer"].astype(str) + "|" + obs["categoria"].astype(str)
    obs["dia"] = obs["fecha"].dt.strftime("%Y-%m-%d")
    niveles_B = ["dia", "categoria", "grupo", "retailer", "cad_cat"]
    niveles_A = ["grupo", "retailer", "cad_cat"]

    # columnas globales (mismo diseño en todos los dias)
    cats = {niv: sorted(obs[niv].astype(str).unique()) for niv in niveles_B}

    def diseño(df, niveles):
        partes, nombres = [np.ones((len(df), 1))], ["const"]
        for niv in niveles:
            vals = df[niv].astype(str).to_numpy()
            M = (vals[:, None] == np.array(cats[niv])[None, :]).astype(float)
            partes.append(M)
            nombres += [f"{niv}={v}" for v in cats[niv]]
        return np.hstack(partes), nombres

    y_all = obs["log_p"].to_numpy()
    n = len(y_all)
    ybar = y_all.mean()
    sst = float(((y_all - ybar) ** 2).sum())

    # --- orden B: crudo ---
    XB, nomB = diseño(obs.iloc[:0], niveles_B)
    kB = XB.shape[1]
    XtX_B, Xty_B, yty_B = np.zeros((kB, kB)), np.zeros(kB), 0.0
    # --- orden A: within producto-dia ---
    XA, nomA = diseño(obs.iloc[:0], niveles_A)
    kA = XA.shape[1]
    XtX_A, Xty_A, yty_A = np.zeros((kA, kA)), np.zeros(kA), 0.0

    for _, g in obs.groupby("dia"):
        y = g["log_p"].to_numpy()
        X, _ = diseño(g, niveles_B)
        XtX_B += X.T @ X
        Xty_B += X.T @ y
        yty_B += float(y @ y)
        # within EAN (dentro del dia)
        med = g.groupby("ean")["log_p"].transform("mean").to_numpy()
        yw = y - med
        Xa, _ = diseño(g, niveles_A)
        Xa_w = Xa - pd.DataFrame(Xa).groupby(g["ean"].to_numpy()).transform("mean").to_numpy()
        XtX_A += Xa_w.T @ Xa_w
        Xty_A += Xa_w.T @ yw
        yty_A += float(yw @ yw)

    def cols(nombres, prefijos):
        return [i for i, nm in enumerate(nombres)
                if nm == "const" or any(nm.startswith(p + "=") for p in prefijos)]

    filas = []
    # orden B
    prev = sst
    acumulados = []
    for i, niv in enumerate(niveles_B):
        ssr = _ssr(XtX_B, Xty_B, yty_B, cols(nomB, niveles_B[:i + 1]))
        acumulados.append(niv)
        r2 = 1 - ssr / sst
        filas.append({"orden": "B", "nivel": niv, "r2_acumulado": r2,
                      "r2_incremental": (prev - ssr) / sst})
        prev = ssr
    # producto al final de B = modelo completo (within + cad_cat)
    ssr_full = _ssr(XtX_A, Xty_A, yty_A, cols(nomA, niveles_A))
    filas.append({"orden": "B", "nivel": "producto", "r2_acumulado": 1 - ssr_full / sst,
                  "r2_incremental": (prev - ssr_full) / sst})
    # orden A
    ssr_dia = _ssr(XtX_B, Xty_B, yty_B, cols(nomB, ["dia"]))
    filas.append({"orden": "A", "nivel": "dia", "r2_acumulado": 1 - ssr_dia / sst,
                  "r2_incremental": 1 - ssr_dia / sst})
    prev = yty_A                    # SSR del modelo solo con producto-dia
    filas.append({"orden": "A", "nivel": "producto", "r2_acumulado": 1 - prev / sst,
                  "r2_incremental": (ssr_dia - prev) / sst})
    filas.append({"orden": "A", "nivel": "categoria", "r2_acumulado": 1 - prev / sst,
                  "r2_incremental": 0.0})
    for i, niv in enumerate(niveles_A):
        ssr = _ssr(XtX_A, Xty_A, yty_A, cols(nomA, niveles_A[:i + 1]))
        filas.append({"orden": "A", "nivel": niv, "r2_acumulado": 1 - ssr / sst,
                      "r2_incremental": (prev - ssr) / sst})
        prev = ssr
    out = pd.DataFrame(filas)
    out["nivel"] = out["nivel"].replace({"retailer": "cadena", "cad_cat": "cadena x categoria"})
    # Lo que importa para la pregunta: de la varianza que queda DENTRO del
    # producto (la dispersion entre cadenas), que fraccion es sistematica por
    # grupo, por cadena o por cadena x categoria. El resto es idiosincratico.
    within = 1 - (1 - (yty_A / sst))          # = varianza within / total
    out["share_within"] = np.nan
    es_a = (out["orden"] == "A") & out["nivel"].isin(["grupo", "cadena", "cadena x categoria"])
    out.loc[es_a, "share_within"] = out.loc[es_a, "r2_incremental"] / within if within > 0 else np.nan
    out["n_obs"] = n
    out["var_total_log_p"] = sst / (n - 1) if n > 1 else np.nan
    return out


# ---------------------------------------------------------------------------
# Evolucion diaria
# ---------------------------------------------------------------------------

def evolucion(obs, ed, det_pares):
    filas = []
    for fecha, g in ed.groupby("fecha"):
        o = obs[obs["fecha"] == fecha]
        dec = descomposicion(o).set_index(["orden", "nivel"]) if len(o) else None
        fila = {"fecha": fecha,
                "ean_2mas": len(g), "ean_5": int((g["n_cadenas"] == 5).sum()),
                "sd_log_media": g["sd_log"].mean(),
                "sd_log_media_5": g.loc[g["n_cadenas"] == 5, "sd_log"].mean(),
                "brecha_mediana": g["brecha"].median(),
                "brecha_mediana_5": g.loc[g["n_cadenas"] == 5, "brecha"].median(),
                "brecha_p90": g["brecha"].quantile(0.9),
                "masa_cero": g["identico_todas"].mean()}
        if dec is not None:
            fila["r2_producto"] = dec.loc[("A", "producto"), "r2_acumulado"]
            fila["r2_inc_grupo"] = dec.loc[("A", "grupo"), "r2_incremental"]
            fila["r2_inc_cadena"] = dec.loc[("A", "cadena"), "r2_incremental"]
            fila["r2_inc_cadena_cat"] = dec.loc[("A", "cadena x categoria"), "r2_incremental"]
            fila["within_grupo"] = dec.loc[("A", "grupo"), "share_within"]
            fila["within_cadena"] = dec.loc[("A", "cadena"), "share_within"]
            fila["within_cadena_cat"] = dec.loc[("A", "cadena x categoria"), "share_within"]
        if not det_pares.empty:
            dp = det_pares[det_pares["fecha"] == fecha].set_index("par")
            for par in ("metro+wong", "plaza_vea+vivanda", "plaza_vea+tottus"):
                if par in dp.index:
                    fila[f"identico_{par}"] = dp.loc[par, "identico"]
        filas.append(fila)
    return pd.DataFrame(filas)


# ---------------------------------------------------------------------------
# Salidas
# ---------------------------------------------------------------------------

def _fmt_pct(x, d=1):
    return "--" if pd.isna(x) else f"{100 * x:.{d}f}\\,\\%"


def _fmt_n(x):
    return f"{int(x):,}".replace(",", "\\,")


def tablas_tex(res_pares, dist, dec, n_dias, fecha, incluir_internos):
    """Tablas booktabs listas para pegar en informe_avance.txt."""
    nota = ("todos los d\\'ias del panel" if n_dias > 1 else "un solo d\\'ia")
    crit = ("EAN de 12+ d\\'igitos, incluidos c\\'odigos internos" if incluir_internos
            else "EAN de fabricante (12+ d\\'igitos, sin prefijo 2)")
    L = []
    L.append("% Generado por analisis/dispersion.py -- NO EDITAR A MANO.")
    L.append(f"% Panel: {n_dias} dia(s), ultimo {fecha}. Criterio: {crit}.")
    L.append("")
    # pares
    L.append("\\begin{table}[htbp]")
    L.append(f"\\caption{{Precio id\\'entico entre pares de cadenas para el mismo EAN, "
             f"{nota} ({n_dias} d\\'ias, \\'ultimo {fecha})}}")
    L.append("\\label{tab:identidad}")
    L.append("\\centering\\footnotesize")
    L.append("\\begin{tabular}{@{}llrrr@{}}")
    L.append("\\toprule")
    L.append("Par & Relaci\\'on & EAN-d\\'ias & Id\\'entico & Brecha med. \\\\")
    L.append("\\midrule")
    for _, r in res_pares.iterrows():
        a, b = r["par"].split("+")
        L.append(f"{NOMBRES[a]} + {NOMBRES[b]} & {r['relacion']} & {_fmt_n(r['ean_dias'])} & "
                 f"{_fmt_pct(r['identico'])} & {_fmt_pct(r['brecha_mediana'])} \\\\")
    L.append("\\bottomrule\\end{tabular}\\end{table}")
    L.append("")
    # brecha
    L.append("\\begin{table}[htbp]")
    L.append(f"\\caption{{Distribuci\\'on de la brecha m\\'ax/m\\'in del precio por EAN-d\\'ia "
             f"y masa en cero, {nota}}}")
    L.append("\\label{tab:brecha}")
    L.append("\\centering\\footnotesize")
    L.append("\\begin{tabular}{@{}lrrrrrrrrr@{}}")
    L.append("\\toprule")
    L.append("Subconjunto & EAN-d\\'ias & Masa en 0 & p10 & p25 & p50 & p75 & p90 & SD log \\\\")
    L.append("\\midrule")
    etiquetas = {"n_cadenas>=2": "$\\geq 2$ cadenas", "n_cadenas>=3": "$\\geq 3$ cadenas",
                 "n_cadenas=5": "las 5 cadenas"}
    for _, r in dist.iterrows():
        sd = "--" if pd.isna(r["sd_log_media"]) else f"{r['sd_log_media']:.3f}"
        L.append(f"{etiquetas.get(r['subconjunto'], r['subconjunto'])} & "
                 f"{_fmt_n(r['ean_dias'])} & {_fmt_pct(r['masa_cero'])} & "
                 + " & ".join(_fmt_pct(r[f"p{p}"]) for p in PERCENTILES)
                 + f" & {sd} \\\\")
    L.append("\\bottomrule\\end{tabular}")
    L.append("\\par\\smallskip\\raggedright\\scriptsize SD log: desviaci\\'on est\\'andar media del "
             "log-precio dentro del EAN-d\\'ia; referencia de Gorodnichenko y Talavera (2017): "
             f"{REFERENCIA_GT[0]:.2f}--{REFERENCIA_GT[1]:.2f}.")
    L.append("\\end{table}")
    L.append("")
    # varianza
    L.append("\\begin{table}[htbp]")
    L.append(f"\\caption{{Descomposici\\'on de la varianza de $\\log$(precio) por efectos fijos "
             f"anidados, {nota}}}")
    L.append("\\label{tab:varianza}")
    L.append("\\centering\\footnotesize")
    L.append("\\begin{tabular}{@{}llrrr@{}}")
    L.append("\\toprule")
    L.append("Orden & Nivel & $R^2$ acum. & $R^2$ incr. & \\% var. intra-prod. \\\\")
    L.append("\\midrule")
    for orden in ("A", "B"):
        sub = dec[dec["orden"] == orden]
        for i, (_, r) in enumerate(sub.iterrows()):
            nivel = r["nivel"].replace(" x ", " $\\times$ ")
            L.append(f"{orden if i == 0 else ''} & {nivel} & {_fmt_pct(r['r2_acumulado'], 2)} & "
                     f"{_fmt_pct(r['r2_incremental'], 2)} & {_fmt_pct(r['share_within'])} \\\\")
        if orden == "A":
            L.append("\\midrule")
    L.append("\\bottomrule\\end{tabular}")
    L.append("\\par\\smallskip\\raggedright\\scriptsize Orden A: producto (EAN$\\times$d\\'ia) "
             "primero; la categor\\'ia queda absorbida. Orden B: sin producto hasta el final. "
             f"$n$ = {_fmt_n(dec['n_obs'].iloc[0])} observaciones EAN$\\times$cadena$\\times$d\\'ia.")
    L.append("\\end{table}")
    return "\n".join(L) + "\n"


def figuras(ed, dec, evo, fecha):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates
    import matplotlib.pyplot as plt

    P.FIGURAS_DIR.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.size": 9, "axes.spines.top": False,
                         "axes.spines.right": False, "axes.grid": True,
                         "grid.color": "#e5e5e3", "grid.linewidth": 0.6})

    # 1. histograma de la brecha con la masa en cero visible
    d = ed[ed["n_cadenas"] >= 2]
    fig, ax = plt.subplots(figsize=(6.5, 3.4))
    cero = d["identico_todas"].mean()
    resto = d.loc[~d["identico_todas"], "brecha"].clip(upper=1.0)
    bins = np.linspace(0, 1.0, 41)
    pesos = np.full(len(resto), 1 / max(len(d), 1))
    ax.hist(resto, bins=bins, weights=pesos, color="#2a78d6", edgecolor="white", linewidth=0.5,
            label="brecha > 0")
    ax.bar([0], [cero], width=bins[1] - bins[0], color="#eb6834", align="edge",
           label=f"idéntico en todas las cadenas ({100 * cero:.1f}%)")
    ax.set_xlabel("Brecha (precio máximo / mínimo − 1) por EAN y día; truncada en 100%")
    ax.set_ylabel("Proporción de EAN-días")
    ax.set_title("Dispersión del precio entre cadenas para el mismo EAN\n"
                 + f"{len(d):,} EAN-días, panel hasta {fecha}".replace(",", "\u202f"),
                 fontsize=10, loc="left")
    ax.legend(frameon=False)
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    fig.tight_layout()
    fig.savefig(P.FIGURAS_DIR / "brecha_hist.png", dpi=160)
    plt.close(fig)

    # 2. descomposicion de varianza
    fig, axes = plt.subplots(1, 2, figsize=(8, 3.3))
    for ax, orden in zip(axes, ("B", "A")):
        sub = dec[dec["orden"] == orden].reset_index(drop=True)
        ys = np.arange(len(sub))
        ax.barh(ys, sub["r2_incremental"], color="#2a78d6")
        for y, v in zip(ys, sub["r2_incremental"]):
            ax.text(min(v, 0.85) + 0.01, y, f"{100 * v:.2f}%", va="center", fontsize=8)
        ax.set_yticks(ys)
        ax.set_yticklabels(sub["nivel"])
        ax.invert_yaxis()
        ax.set_title("Orden B: sin producto hasta el final" if orden == "B"
                     else "Orden A: producto primero", fontsize=9)
        ax.set_xlabel("R² incremental de log(precio)")
        ax.set_xlim(0, 1.0)
        ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    a = dec[(dec["orden"] == "A") & dec["share_within"].notna()]
    resto = 1 - a["share_within"].sum()
    txt = "De la varianza intra-producto: " + ", ".join(
        f"{n} {100 * s:.1f}%" for n, s in zip(a["nivel"], a["share_within"])
    ) + f", idiosincrático {100 * resto:.1f}%"
    fig.suptitle("¿Qué nivel explica la varianza del precio? (efectos fijos anidados)\n" + txt,
                 fontsize=9)
    fig.tight_layout()
    fig.savefig(P.FIGURAS_DIR / "descomposicion_varianza.png", dpi=160)
    plt.close(fig)

    # 3. evolucion temporal
    if not evo.empty:
        fig, axes = plt.subplots(1, 3, figsize=(10.5, 3.4))
        x = pd.to_datetime(evo["fecha"])
        series = [
            (axes[0], [("masa_cero", "idéntico en todas sus cadenas", "#52514e"),
                       ("identico_metro+wong", "Metro = Wong", COLORES["metro"]),
                       ("identico_plaza_vea+vivanda", "Plaza Vea = Vivanda", COLORES["plaza_vea"]),
                       ("identico_plaza_vea+tottus", "Plaza Vea = Tottus", COLORES["tottus"])],
             "Proporción con precio idéntico"),
            (axes[1], [("sd_log_media", "todos (≥2 cadenas)", "#2a78d6"),
                       ("sd_log_media_5", "en las 5 cadenas", "#9085e9")],
             "SD del log-precio dentro del EAN-día"),
            (axes[2], [("within_grupo", "grupo", "#9085e9"),
                       ("within_cadena", "cadena", "#eb6834"),
                       ("within_cadena_cat", "cadena × categoría", "#1baf7a")],
             "% de var. intra-producto explicada"),
        ]
        for ax, lineas, titulo in series:
            for col, etiqueta, color in lineas:
                if col in evo:
                    ax.plot(x, evo[col], marker="o", ms=4, lw=1.6, color=color, label=etiqueta)
            ax.set_title(titulo)
            ax.legend(frameon=False, fontsize=7)
            ax.xaxis.set_major_locator(matplotlib.dates.DayLocator(interval=max(1, len(evo) // 8)))
            ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%d-%m"))
            ax.tick_params(axis="x", rotation=45)
        axes[1].axhspan(*REFERENCIA_GT, color="#cccccc", alpha=0.4, lw=0)
        axes[1].text(x.iloc[0], REFERENCIA_GT[1], " ref. G&T 2017", fontsize=7, va="bottom")
        axes[0].yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
        axes[2].yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
        fig.suptitle(f"Evolución diaria de las métricas de dispersión ({len(evo)} días)")
        fig.tight_layout()
        fig.savefig(P.FIGURAS_DIR / "evolucion.png", dpi=160)
        plt.close(fig)


def correr(incluir_internos=False, con_figuras=True, panel_df=None, silencioso=False,
           solo_disponibles=False):
    df = panel_df if panel_df is not None else P.cargar_panel(
        columnas=["retailer", "item_id", "ean", "price", "category", "available"])
    if df.empty:
        raise SystemExit("Panel vacio.")
    fecha = str(df["fecha"].max().date())
    n_dias = df["fecha"].nunique()

    obs = observaciones(df, incluir_internos, solo_disponibles)
    if obs.empty:
        raise SystemExit("Ningun EAN en 2+ cadenas: no hay nada que comparar.")
    ed = por_ean_dia(obs)
    res_pares, det_pares = pares(obs)
    dist = distribucion_brecha(ed)
    hist = histograma_brecha(ed)
    dec = descomposicion(obs)
    evo = evolucion(obs, ed, det_pares)

    # dispersion__<fecha>.csv: metricas agregadas en formato largo
    largo = []
    for _, r in dist.iterrows():
        for k, v in r.items():
            if k != "subconjunto":
                largo.append({"bloque": "brecha", "subconjunto": r["subconjunto"],
                              "metrica": k, "valor": v})
    for _, r in dec.iterrows():
        largo.append({"bloque": "varianza", "subconjunto": f"orden {r['orden']}",
                      "metrica": f"r2_incremental[{r['nivel']}]", "valor": r["r2_incremental"]})
        largo.append({"bloque": "varianza", "subconjunto": f"orden {r['orden']}",
                      "metrica": f"r2_acumulado[{r['nivel']}]", "valor": r["r2_acumulado"]})
    largo.append({"bloque": "panel", "subconjunto": "", "metrica": "n_dias", "valor": n_dias})
    largo.append({"bloque": "panel", "subconjunto": "", "metrica": "incluir_internos",
                  "valor": int(incluir_internos)})
    largo.append({"bloque": "panel", "subconjunto": "", "metrica": "solo_disponibles",
                  "valor": int(solo_disponibles)})
    P.escribir_csv(pd.DataFrame(largo), "dispersion", fecha)
    P.escribir_csv(res_pares, "dispersion_pares", fecha)
    P.escribir_csv(det_pares, "dispersion_pares_diario", fecha)
    P.escribir_csv(dec, "dispersion_varianza", fecha)
    P.escribir_csv(evo, "dispersion_diaria", fecha)
    P.escribir_csv(hist, "dispersion_brecha_hist", fecha)
    tex = tablas_tex(res_pares, dist, dec, n_dias, fecha, incluir_internos)
    P.escribir_texto(tex, "tablas_dispersion", fecha)
    if con_figuras:
        figuras(ed, dec, evo, fecha)

    if not silencioso:
        print(f"=== dispersion | ultimo dia {fecha} | {n_dias} dias | "
              f"{len(obs):,} obs EAN x cadena x dia | criterio: "
              f"{'12+ digitos con internos' if incluir_internos else 'EAN de fabricante'} ===\n")
        v = res_pares.copy()
        for c in ("identico", "brecha_mediana", "a_mas_barato", "b_mas_barato"):
            v[c] = v[c].map(P.pct)
        print("--- coincidencia por pares (todos los dias) ---")
        print(P.tabla(v))
        print("\n--- brecha max/min por EAN-dia ---")
        v = dist.copy()
        for c in ["masa_cero"] + [f"p{p}" for p in PERCENTILES]:
            v[c] = v[c].map(P.pct)
        print(P.tabla(v))
        print(f"\nSD log-precio media (>=2 cadenas): {dist.iloc[0]['sd_log_media']:.3f}  |  "
              f"referencia G&T 2017: {REFERENCIA_GT[0]}-{REFERENCIA_GT[1]}")
        print("\n--- descomposicion de varianza de log(precio) ---")
        v = dec[["orden", "nivel", "r2_acumulado", "r2_incremental", "share_within"]].copy()
        for c in ("r2_acumulado", "r2_incremental", "share_within"):
            v[c] = v[c].map(lambda x: P.pct(x, 2))
        print(P.tabla(v))
        print("\n--- evolucion diaria ---")
        print(P.tabla(evo, "{:.3f}"))
        print()
        print(P.aviso_fiabilidad(n_dias, MIN_DIAS_SOLIDO, "estabilidad de la dispersion en el tiempo"))
        print("Los niveles (masa en cero, coincidencia por pares, R2) ya son medidas del\n"
              "panel entero; lo que falta por ver es si se mueven con las semanas.")
        print(f"-> data/derived/tablas_dispersion__{fecha}.tex y figuras en data/derived/figuras/")
    return {"obs": obs, "ean_dia": ed, "pares": res_pares, "dist": dist, "dec": dec, "evo": evo}


def main(argv=None):
    P.configurar_stdout()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--incluir-internos", action="store_true",
                    help="incluye los EAN con prefijo 2 (criterio de la bitacora 20-09)")
    ap.add_argument("--sin-figuras", action="store_true")
    ap.add_argument("--solo-disponibles", action="store_true",
                    help="excluye filas con available=False (precio publicado sin stock)")
    args = ap.parse_args(argv)
    correr(args.incluir_internos, not args.sin_figuras, solo_disponibles=args.solo_disponibles)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
