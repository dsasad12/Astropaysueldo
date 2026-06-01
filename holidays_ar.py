"""
Cálculo de feriados nacionales argentinos y días hábiles.
Incluye los feriados fijos, puentes y días no laborables habituales.
"""

import holidays
from datetime import date, timedelta


def get_ar_holidays(year: int) -> set[date]:
    """Retorna el conjunto de feriados nacionales argentinos para el año dado."""
    ar = holidays.Argentina(years=year)
    # Si el año abarca dos años (para calcular días hábiles cerca de fin de año)
    ar_next = holidays.Argentina(years=year + 1)
    return set(ar.keys()) | set(ar_next.keys())


def is_business_day(d: date, holiday_set: set[date]) -> bool:
    """Retorna True si el día es hábil (lunes a viernes, no feriado)."""
    return d.weekday() < 5 and d not in holiday_set


def get_nth_business_day(year: int, month: int, n: int = 5) -> date:
    """
    Retorna el N-ésimo día hábil del mes para Argentina.
    El día 1 del mes se considera el primer día hábil si aplica.
    """
    holiday_set = get_ar_holidays(year)
    current = date(year, month, 1)
    count = 0

    while True:
        if is_business_day(current, holiday_set):
            count += 1
            if count == n:
                return current
        current += timedelta(days=1)
        # Salvaguarda: no pasar al mes siguiente
        if current.month != month:
            raise ValueError(
                f"No se encontró el día hábil {n} en {year}/{month:02d}"
            )


def is_salary_day(target_date: date | None = None) -> bool:
    """
    Retorna True si hoy (o target_date) es el 5to día hábil del mes,
    que es cuando cobran los auxiliares docentes del Ministerio de Educación
    de la Provincia de Buenos Aires.
    """
    d = target_date or date.today()
    try:
        salary_day = get_nth_business_day(d.year, d.month, n=5)
        return d == salary_day
    except ValueError:
        return False
