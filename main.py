#!/usr/bin/env python3
"""
Retiro automático de sueldo docente → AstroPay
Auxiliares docentes del Ministerio de Educación de la Provincia de Buenos Aires.

Ejecutar manualmente:
    python main.py

Ejecutar sin verificar si es día de cobro (forzar depósito):
    python main.py --force

Mostrar próximos días de cobro:
    python main.py --preview
"""

import argparse
import json
import logging
import os
import sys
from datetime import date, timedelta

from dotenv import load_dotenv

from astropay_client import AstroPayClient, AstroPayError
from holidays_ar import get_nth_business_day, is_salary_day

load_dotenv()

# ─── Logging ──────────────────────────────────────────────────────────────────

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


# ─── Configuración ────────────────────────────────────────────────────────────

def load_config() -> dict:
    required = [
        "ASTROPAY_MERCHANT_ID",
        "ASTROPAY_API_KEY",
        "ASTROPAY_SECRET_KEY",
        "CARD_NUMBER",
        "CARD_EXPIRY_MONTH",
        "CARD_EXPIRY_YEAR",
        "CARD_CVV",
        "CARD_HOLDER_NAME",
    ]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        logger.error("Faltan variables de entorno obligatorias: %s", ", ".join(missing))
        logger.error("Copiá .env.example a .env y completá los valores.")
        sys.exit(1)

    amount_raw = os.getenv("DEPOSIT_AMOUNT", "0")
    try:
        amount = float(amount_raw)
    except ValueError:
        logger.error("DEPOSIT_AMOUNT debe ser un número. Valor recibido: %s", amount_raw)
        sys.exit(1)

    if amount <= 0 and os.getenv("DEPOSIT_FULL_BALANCE", "false").lower() != "true":
        logger.error(
            "Configurá DEPOSIT_AMOUNT con el monto a depositar, "
            "o activá DEPOSIT_FULL_BALANCE=true para depositar el saldo completo."
        )
        sys.exit(1)

    return {
        "merchant_id": os.environ["ASTROPAY_MERCHANT_ID"],
        "api_key": os.environ["ASTROPAY_API_KEY"],
        "secret_key": os.environ["ASTROPAY_SECRET_KEY"],
        "card_number": os.environ["CARD_NUMBER"],
        "card_expiry_month": os.environ["CARD_EXPIRY_MONTH"],
        "card_expiry_year": os.environ["CARD_EXPIRY_YEAR"],
        "card_cvv": os.environ["CARD_CVV"],
        "card_holder_name": os.environ["CARD_HOLDER_NAME"],
        "amount": amount,
        "currency": os.getenv("DEPOSIT_CURRENCY", "ARS"),
        "full_balance": os.getenv("DEPOSIT_FULL_BALANCE", "false").lower() == "true",
        "env": os.getenv("ASTROPAY_ENV", "sandbox"),
    }


# ─── Preview de días de cobro ─────────────────────────────────────────────────

def show_preview():
    """Muestra los próximos 6 días de cobro (5to día hábil de cada mes)."""
    today = date.today()
    print("\nPróximos días de cobro (5to día hábil):\n")
    months_shown = 0
    year, month = today.year, today.month

    while months_shown < 6:
        try:
            salary_day = get_nth_business_day(year, month, n=5)
            marker = " ← HOY" if salary_day == today else (" ← PRÓXIMO" if salary_day >= today and months_shown == 0 else "")
            print(f"  {salary_day.strftime('%d/%m/%Y')} ({salary_day.strftime('%A')}){marker}")
            months_shown += 1
        except ValueError as e:
            print(f"  {year}/{month:02d}: error calculando → {e}")
            months_shown += 1

        month += 1
        if month > 12:
            month = 1
            year += 1

    print()


# ─── Lógica principal ─────────────────────────────────────────────────────────

def run_deposit(cfg: dict, force: bool = False) -> int:
    today = date.today()

    if not force and not is_salary_day(today):
        salary_day = get_nth_business_day(today.year, today.month, n=5)
        days_left = (salary_day - today).days
        if days_left > 0:
            logger.info(
                "Hoy (%s) no es día de cobro. El próximo es %s (en %d días).",
                today,
                salary_day,
                days_left,
            )
        else:
            # Ya pasó el día de cobro este mes, calcular el del mes que viene
            next_month = (today.month % 12) + 1
            next_year = today.year + (1 if next_month == 1 else 0)
            next_salary_day = get_nth_business_day(next_year, next_month, n=5)
            logger.info(
                "Hoy (%s) no es día de cobro. El próximo es %s.",
                today,
                next_salary_day,
            )
        return 0

    client = AstroPayClient(
        merchant_id=cfg["merchant_id"],
        api_key=cfg["api_key"],
        secret_key=cfg["secret_key"],
        env=cfg["env"],
    )

    amount = cfg["amount"]
    if cfg["full_balance"]:
        logger.info("DEPOSIT_FULL_BALANCE=true — se usará el monto configurado como total del sueldo.")

    logger.info(
        "=== Iniciando depósito: %s %s → AstroPay [env=%s] ===",
        amount,
        cfg["currency"],
        cfg["env"],
    )

    try:
        result = client.create_deposit(
            amount=amount,
            currency=cfg["currency"],
            card_number=cfg["card_number"],
            card_expiry_month=cfg["card_expiry_month"],
            card_expiry_year=cfg["card_expiry_year"],
            card_cvv=cfg["card_cvv"],
            card_holder_name=cfg["card_holder_name"],
            description=f"Sueldo docente {today.strftime('%B %Y')}",
        )
    except AstroPayError as e:
        logger.error("Error al procesar el depósito: %s", e)
        if e.response:
            logger.error("Detalle de la API: %s", json.dumps(e.response, ensure_ascii=False))
        return 1

    status = result.get("status", "UNKNOWN")
    logger.info("Depósito enviado | status: %s | respuesta: %s", status, json.dumps(result, ensure_ascii=False))

    # Si la API requiere acción adicional (3DS / OTP)
    redirect_url = result.get("redirect_url") or result.get("action_url")
    if redirect_url:
        logger.warning(
            "La transacción requiere verificación adicional (3DS/OTP). "
            "Abrí esta URL en el navegador para completarla: %s",
            redirect_url,
        )
        print(f"\n⚠️  Acción requerida — abrí esta URL en el navegador:\n  {redirect_url}\n")

    if status in ("APPROVED", "SUCCESS", "COMPLETED"):
        logger.info("✓ Depósito completado exitosamente.")
        return 0
    elif status == "PENDING":
        logger.info("Depósito pendiente de confirmación. Revisá tu cuenta AstroPay.")
        return 0
    else:
        logger.warning("Estado inesperado: %s — revisá el log y tu cuenta AstroPay.", status)
        return 1


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Depósito automático de sueldo docente desde Visa Débito Banco Provincia a AstroPay"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ejecutar el depósito aunque hoy no sea el día de cobro.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Mostrar los próximos días de cobro y salir.",
    )
    args = parser.parse_args()

    if args.preview:
        show_preview()
        return

    cfg = load_config()
    sys.exit(run_deposit(cfg, force=args.force))


if __name__ == "__main__":
    main()
