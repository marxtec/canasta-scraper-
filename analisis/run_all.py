"""Bloque 7 -- comando unico de regeneracion.

    python -m analisis.run_all                  # bloques 1, 2, 3 y 5 sobre todos los dias
    python -m analisis.run_all --con-auditoria  # ademas el bloque 4 (necesita navegador)
    python -m analisis.run_all --sin-informe    # no toca informe_avance.txt

Es el comando que se corre en noviembre con el panel completo: sin
argumentos, sin editar nada. Regenera TODOS los derivados de data/derived/
(CSV, tablas .tex, figuras), pega las tablas generadas dentro de
informe_avance.txt entre los marcadores %>>> / %<<< y termina con un
veredicto por resultado: SOLIDO o PROVISIONAL, y cuantos dias faltan.

Los umbrales de "solido" viven en cada modulo (MIN_*); aqui solo se leen.
"""

import argparse
import re
import time
import traceback

from analisis import dispersion, oferta_real, panel as P, precios_faltantes, rigidez

INFORME = P.ROOT / "docs" / "informe" / "informe_avance.txt"
MARCA_INI = "% >>> TABLAS GENERADAS por analisis/run_all.py -- NO EDITAR A MANO (se regeneran)"
MARCA_FIN = "% <<< TABLAS GENERADAS"


def actualizar_informe(path, tex):
    """Sustituye el bloque entre marcadores; si no hay marcadores no toca nada."""
    if not path.exists():
        return False
    crudo = path.read_bytes().decode("utf-8")
    fin_linea = "\r\n" if "\r\n" in crudo else "\n"       # se respeta el del archivo
    texto = crudo.replace("\r\n", "\n")
    if MARCA_INI not in texto or MARCA_FIN not in texto:
        return False
    patron = re.compile(re.escape(MARCA_INI) + r".*?" + re.escape(MARCA_FIN), re.DOTALL)
    nuevo = patron.sub(lambda _: f"{MARCA_INI}\n{tex.strip()}\n{MARCA_FIN}", texto)
    if nuevo != texto:
        path.write_bytes(nuevo.replace("\n", fin_linea).encode("utf-8"))
    return True


def main(argv=None):
    P.configurar_stdout()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--con-auditoria", action="store_true",
                    help="corre tambien el bloque 4 (abre navegador; ~35 min con N=50)")
    ap.add_argument("--n-auditoria", type=int, default=50)
    ap.add_argument("--sin-informe", action="store_true",
                    help="no pega las tablas en informe_avance.txt")
    ap.add_argument("--sin-figuras", action="store_true")
    args = ap.parse_args(argv)

    dias = P.dias_disponibles()
    fecha = dias[-1]
    n_dias = len(dias)
    print("=" * 78)
    print(f"REGENERACION COMPLETA | {n_dias} dias en data/daily/ ({dias[0]} .. {fecha})")
    print("=" * 78)

    veredictos, fallos = [], []
    t0 = time.time()

    def bloque(nombre, fn):
        print(f"\n{'#' * 78}\n# {nombre}\n{'#' * 78}")
        try:
            return fn()
        except SystemExit as exc:
            print(f"!! {nombre}: {exc}")
            fallos.append(nombre)
        except Exception:
            traceback.print_exc()
            fallos.append(nombre)
        return None

    r1 = bloque("Bloque 1: oferta_real", lambda: oferta_real.correr())
    veredictos.append(P.aviso_fiabilidad(n_dias, oferta_real.MIN_OBS_VENTANA + 1,
                                         "oferta_real / fantasma (C)"))

    r2 = bloque("Bloque 2: rigidez", lambda: rigidez.correr())
    if r2 is not None:
        agg, tr, hz, _ = r2
        n_hab = rigidez.transiciones_habiles(tr)
        if n_hab == 0:
            veredictos.append("PROVISIONAL: rigidez en dias habiles: 0 transiciones habiles "
                              "todavia; NO PUBLICAR la tasa diaria.")
        else:
            veredictos.append(P.aviso_fiabilidad(n_hab, rigidez.MIN_TRANSICIONES_HABILES,
                                                 "tasa de cambio en dias habiles"))
        medibles = hz[(hz["version"] == "decl") & (hz["origenes"] > 0)]["horizonte"].tolist()
        no = sorted(set(hz["horizonte"]) - set(medibles))
        veredictos.append(f"Modelo B: horizontes medidos {sorted(set(medibles)) or 'ninguno'}; "
                          f"sin origenes completos {no or 'ninguno'}"
                          + (f" (faltan {int(hz[hz['horizonte'] == max(no)]['dias_faltan_para_medir'].max())} "
                             f"dias para el mayor)" if no else "."))

    r3 = bloque("Bloque 3: dispersion", lambda: dispersion.correr(con_figuras=not args.sin_figuras))
    veredictos.append("SOLIDO: niveles de dispersion (masa en cero, coincidencia por pares, "
                      f"descomposicion) medidos sobre {n_dias} dias del panel entero.")
    veredictos.append(P.aviso_fiabilidad(n_dias, dispersion.MIN_DIAS_SOLIDO,
                                         "estabilidad temporal de la dispersion"))

    r5 = bloque("Bloque 5: precios faltantes", lambda: precios_faltantes.correr())
    veredictos.append(P.aviso_fiabilidad(n_dias, precios_faltantes.MIN_DIAS_SOLIDO,
                                         "contraste entre reglas de faltantes"))

    if args.con_auditoria:
        from analisis import auditoria_validez
        bloque("Bloque 4: auditoria de validez",
               lambda: auditoria_validez.correr(n=args.n_auditoria))
    else:
        veredictos.append("Bloque 4 (auditoria API vs pantalla) no corrido: usar --con-auditoria "
                          "o el workflow semanal; el acumulado esta en data/derived/auditoria/.")

    if not args.sin_informe:
        tex_path = next(P.DERIVED_DIR.glob("tablas_dispersion__*.tex"), None)
        if tex_path and actualizar_informe(INFORME, tex_path.read_text(encoding="utf-8")):
            print(f"\ninforme_avance.txt: bloque de tablas actualizado desde {tex_path.name}")
        else:
            print("\ninforme_avance.txt: sin marcadores o sin .tex; no se toco.")

    print("\n" + "=" * 78)
    print(f"VEREDICTO ({n_dias} dias, {time.time() - t0:.0f} s)")
    print("=" * 78)
    for v in veredictos:
        print(" -", v)
    if fallos:
        print("\nBLOQUES CON ERROR:", ", ".join(fallos))
        return 1
    print("\nDerivados en data/derived/ con sufijo __" + fecha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
