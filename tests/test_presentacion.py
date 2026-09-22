#!/usr/bin/env python3
"""Tests de la lectura de presentaciones y de la tolerancia de tamano.

    pytest tests/test_presentacion.py

Sin red y sin leer data/daily/.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from analisis import presentacion as PR
from analisis import tamano as TAM


def test_unidad_suelta_y_atado():
    assert PR.unidad_suelta("Choclo Especial x unid") == "unidad"
    assert PR.unidad_suelta("Pan Frances x und.") == "unidad"
    assert PR.unidad_suelta("Culantro x atado") == "atado"
    assert PR.unidad_suelta("Arroz Extra Paisana 5 kg") is None


def test_peso_aproximado_y_gramaje_ambiguo():
    assert PR.peso_aprox("Olluco Pack x 250g aprox.")
    assert not PR.peso_aprox("Olluco Entero x kg")
    assert PR.gramaje_ambiguo("Sazonador Ajinomoto 90g Sazonador Ajinomoto Sobre 100 g")
    assert not PR.gramaje_ambiguo("Aceite Primor 900ml + 100ml")          # regalo, no ambiguo
    assert not PR.gramaje_ambiguo("Galleta Soda Field Paquete 6un 34g")


def test_contenido_declarado():
    assert PR.contenido_declarado("280g") == (0.28, "kg")
    assert PR.contenido_declarado("5Kg") == (5.0, "kg")
    assert PR.contenido_declarado("500 ML") == (0.5, "l")
    assert PR.contenido_declarado("300") == (None, None)                 # sin unidad: no
    assert PR.contenido_declarado(None) == (None, None)


def test_linea_y_parecido_de_nombres():
    assert PR.linea("Arroz Extra Paisana Bolsa 5 kg") == PR.linea("Arroz Extra Paisana Bolsa 1 kg")
    a = PR.palabras("Semola MOLITALIA Bolsa 200g", "MOLITALIA")
    b = PR.palabras("Sémola Molitalia 200 g", "Molitalia")
    assert PR.jaccard(a, b) == 1.0


def test_r_max_desde_beta():
    """Documento, seccion 7: beta -0,10 (arroz) -> 1,67x; -0,316 -> 1,18x."""
    assert abs(TAM.r_max(-0.10) - 1.67) < 0.01
    assert abs(TAM.r_max(-0.316) - 1.18) < 0.01
    assert TAM.r_max(0.05) == float("inf")


def test_beta_con_efectos_fijos_recupera_el_parametro():
    rng = np.random.default_rng(0)
    filas = []
    for k in range(40):
        alfa = rng.normal(1.5, 0.5)
        for w in (0.25, 0.5, 1.0, 2.0):
            filas.append({"retailer": "metro", "linea": f"l{k}", "unit": "kg",
                          "lw": np.log(w), "lp": alfa - 0.2 * np.log(w) + rng.normal(0, 0.01)})
    b, n = TAM.beta_efectos_fijos(pd.DataFrame(filas), ["retailer", "linea", "unit"])
    assert n == 40 and abs(b + 0.2) < 0.01


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
