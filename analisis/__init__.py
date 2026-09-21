"""Capa de analisis sobre el panel acumulado en data/daily/.

Separada a proposito del paquete `canasta/` (el recolector): esto se puede
romper, reescribir o correr diez veces sin que la captura diaria de precios
--lo unico irrecuperable-- se entere.

Todo script del paquete descubre solo que dias hay en data/daily/ y trabaja
con todos. Nada de fechas ni recuentos de dias fijos: lo que corre hoy con
dos dias tiene que correr en noviembre con sesenta sin tocar una linea.

    python -m analisis.run_all              # regenera todos los derivados
    python -m analisis.oferta_real          # bloque 1
    python -m analisis.rigidez              # bloque 2
    python -m analisis.dispersion           # bloque 3
    python -m analisis.auditoria_validez    # bloque 4 (necesita navegador)
    python -m analisis.precios_faltantes    # bloque 5

Dependencias: requirements-analisis.txt (pandas, numpy, matplotlib). El
scraper sigue con requests + PyYAML y no las necesita.
"""
