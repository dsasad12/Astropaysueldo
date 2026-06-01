"""
Automatización de AstroPay via Playwright.
Compatible con ejecución local y en contenedores (GitHub Actions, Docker).
"""

import json
import logging
import os
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)

SESSION_FILE = Path("astropay_session.json")
BASE_URL = "https://app.astropay.com"

# Flags necesarios para correr Chromium en contenedores (GitHub Actions, Docker)
CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
]


class AstroPayWebError(Exception):
    pass


def _save_session(context):
    SESSION_FILE.write_text(json.dumps(context.storage_state()))
    logger.debug("Sesión guardada en %s", SESSION_FILE)


def _do_login(page, email: str, password: str):
    """Completa el formulario de login en la página actual."""
    logger.info("Iniciando sesión como %s...", email)
    page.goto(f"{BASE_URL}/login", wait_until="networkidle", timeout=30000)
    page.fill('input[type="email"], input[name="email"]', email)
    page.fill('input[type="password"], input[name="password"]', password)
    page.click('button[type="submit"]')

    try:
        page.wait_for_url("**/home**", timeout=20000)
    except PlaywrightTimeout:
        # Algunas versiones del sitio no cambian la URL pero sí el contenido
        page.wait_for_selector(
            'input[type="password"]', state="hidden", timeout=15000
        )
    logger.info("Login exitoso.")


def login(email: str, password: str, headless: bool = False):
    """
    Login inicial — ejecutar una sola vez localmente con headless=False
    para ver el browser. Guarda la sesión para usos posteriores.
    En GitHub Actions no hace falta correr esto; el depósito hace login solo.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=CHROMIUM_ARGS)
        context = browser.new_context()
        page = context.new_page()
        _do_login(page, email, password)
        _save_session(context)
        browser.close()


def deposit(amount: float, card_last4: str) -> bool:
    """
    Realiza un depósito en AstroPay usando la tarjeta guardada identificada
    por los últimos 4 dígitos. Retorna True si fue exitoso.
    """
    email = os.getenv("ASTROPAY_EMAIL", "")
    password = os.getenv("ASTROPAY_PASSWORD", "")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=CHROMIUM_ARGS)

        # Usar sesión guardada si existe (ejecución local), si no login fresco
        if SESSION_FILE.exists():
            context = browser.new_context(
                storage_state=json.loads(SESSION_FILE.read_text())
            )
            logger.info("Sesión local cargada.")
        else:
            context = browser.new_context()
            logger.info("Sin sesión previa — se hará login automático.")

        page = context.new_page()

        try:
            page.goto(f"{BASE_URL}/home", wait_until="networkidle", timeout=30000)

            # Si redirigió al login, iniciar sesión automáticamente
            if "/login" in page.url:
                if not email or not password:
                    raise AstroPayWebError(
                        "Sesión expirada y no hay credenciales en ASTROPAY_EMAIL/ASTROPAY_PASSWORD."
                    )
                _do_login(page, email, password)
                _save_session(context)

            # Buscar el botón de agregar saldo
            logger.info("Buscando opción de depósito...")
            deposit_btn = page.locator(
                '[data-testid*="deposit"], [data-testid*="add-funds"], '
                '[aria-label*="saldo"], [aria-label*="deposit"], '
                'text=/agregar saldo/i, text=/depositar/i, text=/add funds/i'
            ).first
            deposit_btn.click(timeout=10000)
            page.wait_for_load_state("networkidle")

            # Seleccionar la tarjeta guardada por últimos 4 dígitos
            logger.info("Seleccionando tarjeta **** %s...", card_last4)
            card_option = page.locator(f'text="{card_last4}"').first
            card_option.click(timeout=10000)

            # Ingresar el monto
            logger.info("Ingresando monto: %s ARS...", int(amount))
            amount_input = page.locator(
                'input[type="number"], input[inputmode="numeric"], '
                'input[placeholder*="monto"], input[placeholder*="amount"], '
                'input[name*="amount"]'
            ).first
            amount_input.fill(str(int(amount)))

            # Confirmar
            confirm_btn = page.locator(
                'button[type="submit"], '
                'text=/confirmar/i, text=/continuar/i, text=/confirm/i'
            ).first
            confirm_btn.click(timeout=10000)

            # Esperar confirmación de éxito
            try:
                page.wait_for_selector(
                    'text=/exitoso/i, text=/aprobado/i, '
                    'text=/success/i, text=/approved/i',
                    timeout=20000,
                )
                logger.info("Depósito de %s ARS completado exitosamente.", int(amount))
                _save_session(context)
                return True
            except PlaywrightTimeout:
                page.screenshot(path="deposit_result.png")
                logger.warning(
                    "No se detectó confirmación. Revisá deposit_result.png"
                )
                return False

        except PlaywrightTimeout as e:
            page.screenshot(path="deposit_error.png")
            raise AstroPayWebError(
                f"Timeout en AstroPay: {e}. Revisá deposit_error.png"
            ) from e
        finally:
            browser.close()
