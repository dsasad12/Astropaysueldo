#!/usr/bin/env python3
"""
Retiro automático de sueldo docente → AstroPay
Auxiliares docentes del Ministerio de Educación de la Provincia de Buenos Aires.

Ejecutar manualmente:
    python main.py

Forzar depósito aunque no sea el día de cobro (para probar):
    python main.py --force

Ver próximos días de cobro:
    python main.py --preview
"""

import argparse
import logging
import os
import sys
from datetime import date

from dotenv import load_dotenv

from astropay_web import deposit, AstroPayWebError
from holidays_ar import get_nth_business_day, is_salary_day

load_dotenv()

LOG_FILE = os.getenv("LOG_FILE", "astropay_salary.log")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def load_config() -> dict:
    required = ["ASTROPAY_EMAIL", "ASTROPAY_PASSWORD", "CARD_LAST4", "DEPOSIT_AMOUNT"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        logger.error("Faltan variables en .env: %s", ", ".join(missing))
        sys.exit(1)

    try:
        amount = float(os.environ["DEPOSIT_AMOUNT"])
    except ValueError:
        logger.error("DEPOSIT_AMOUNT debe ser un número. Ej: 150000")
        sys.exit(1)

    if amount <= 0:
        logger.error("DEPOSIT_AMOUNT debe ser mayor a 0.")
        sys.exit(1)

    return {
        "amount": amount,
        "card_last4": os.environ["CARD_LAST4"],
    }


def show_preview():
    today = date.today()
    print("\nPróximos días de cobro (5to día hábil):\n")
    year, month = today.year, today.month
    shown = 0
    while shown < 6:
        try:
            d = get_nth_business_day(year, month, n=5)
            tag = " ← HOY" if d == today else ""
            print(f"  {d.strftime('%d/%m/%Y')} ({d.strftime('%A')}){tag}")
            shown += 1
        except ValueError:
            pass
        month += 1
        if month > 12:
            month, year = 1, year + 1
    print()


def run(cfg: dict, force: bool = False) -> int:
    today = date.today()

    if not force and not is_salary_day(today):
        salary_day = get_nth_business_day(today.year, today.month, n=5)
        if salary_day > today:
            logger.info("Hoy no es día de cobro. Próximo: %s (%d días).",
                        salary_day, (salary_day - today).days)
        else:
            nm = (today.month % 12) + 1
            ny = today.year + (1 if nm == 1 else 0)
            next_day = get_nth_business_day(ny, nm, n=5)
            logger.info("Hoy no es día de cobro. Próximo: %s.", next_day)
        return 0

    logger.info("=== Día de cobro detectado. Iniciando depósito de %s ARS ===", cfg["amount"])

    try:
        ok = deposit(amount=cfg["amount"], card_last4=cfg["card_last4"])
    except AstroPayWebError as e:
        logger.error("Error en la automatización: %s", e)
        return 1

    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                        help="Ejecutar depósito aunque no sea día de cobro.")
    parser.add_argument("--preview", action="store_true",
                        help="Mostrar próximos días de cobro.")
    args = parser.parse_args()

    if args.preview:
        show_preview()
        return

    cfg = load_config()
    sys.exit(run(cfg, force=args.force))


if __name__ == "__main__":
    main()
