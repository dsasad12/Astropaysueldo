"""
Automatización de AstroPay via Playwright.
Inicia sesión, selecciona la tarjeta guardada y realiza el depósito.
"""

import json
import logging
import os
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)

SESSION_FILE = Path("astropay_session.json")
BASE_URL = "https://app.astropay.com"


class AstroPayWebError(Exception):
    pass


def _save_session(context):
    SESSION_FILE.write_text(json.dumps(context.storage_state()))
    logger.info("Sesión guardada en %s", SESSION_FILE)


def _load_session(playwright):
    browser = playwright.chromium.launch(headless=True)
    if SESSION_FILE.exists():
        context = browser.new_context(storage_state=json.loads(SESSION_FILE.read_text()))
        logger.info("Sesión existente cargada.")
    else:
        context = browser.new_context()
        logger.info("Sin sesión previa, se iniciará login.")
    return browser, context


def login(email: str, password: str, headless: bool = False):
    """
    Inicia sesión en AstroPay y guarda la sesión para usos futuros.
    Llamar una sola vez (setup). Después el script usa la sesión guardada.
    headless=False muestra el browser para que puedas ver lo que pasa.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()

        logger.info("Abriendo AstroPay...")
        page.goto(f"{BASE_URL}/login", wait_until="networkidle")

        page.fill('input[type="email"], input[name="email"]', email)
        page.fill('input[type="password"], input[name="password"]', password)
        page.click('button[type="submit"]')

        # Esperar a que cargue el dashboard
        try:
            page.wait_for_url("**/home**", timeout=15000)
        except PlaywrightTimeout:
            # Puede ser que la URL sea diferente, esperamos que desaparezca el login
            page.wait_for_selector('input[type="password"]', state="hidden", timeout=15000)

        _save_session(context)
        logger.info("Login exitoso.")
        browser.close()


def deposit(amount: float, card_last4: str) -> bool:
    """
    Realiza un depósito en AstroPay usando la tarjeta guardada identificada
    por los últimos 4 dígitos. Retorna True si fue exitoso.
    """
    with sync_playwright() as p:
        browser, context = _load_session(p)
        page = context.new_page()

        try:
            logger.info("Navegando a AstroPay...")
            page.goto(f"{BASE_URL}/home", wait_until="networkidle", timeout=30000)

            # Si redirige al login, la sesión expiró
            if "/login" in page.url:
                logger.warning("Sesión expirada. Re-autenticando...")
                email = os.environ["ASTROPAY_EMAIL"]
                password = os.environ["ASTROPAY_PASSWORD"]
                page.fill('input[type="email"], input[name="email"]', email)
                page.fill('input[type="password"], input[name="password"]', password)
                page.click('button[type="submit"]')
                page.wait_for_url("**/home**", timeout=15000)
                _save_session(context)

            # Buscar botón de agregar saldo / depositar
            logger.info("Buscando opción de depósito...")
            deposit_btn = page.locator(
                'text=/agregar saldo/i, text=/depositar/i, text=/add funds/i, '
                '[data-testid*="deposit"], [aria-label*="deposit"], [aria-label*="saldo"]'
            ).first
            deposit_btn.click(timeout=10000)
            page.wait_for_load_state("networkidle")

            # Seleccionar método de pago: tarjeta guardada
            logger.info("Seleccionando tarjeta **** %s...", card_last4)
            card_option = page.locator(f'text="{card_last4}", text="{card_last4}"').first
            card_option.click(timeout=10000)

            # Ingresar el monto
            logger.info("Ingresando monto: %s ARS...", amount)
            amount_input = page.locator(
                'input[type="number"], input[placeholder*="monto"], '
                'input[placeholder*="amount"], input[name*="amount"]'
            ).first
            amount_input.fill(str(int(amount)))

            # Confirmar
            confirm_btn = page.locator(
                'button[type="submit"], text=/confirmar/i, text=/continuar/i, text=/confirm/i'
            ).first
            confirm_btn.click(timeout=10000)

            # Esperar resultado
            try:
                page.wait_for_selector(
                    'text=/exitoso/i, text=/aprobado/i, text=/success/i, text=/approved/i',
                    timeout=20000,
                )
                logger.info("Depósito de %s ARS completado exitosamente.", amount)
                _save_session(context)
                return True
            except PlaywrightTimeout:
                # Capturar screenshot para diagnóstico
                page.screenshot(path="deposit_result.png")
                logger.warning(
                    "No se detectó confirmación de éxito. "
                    "Revisá deposit_result.png para ver el estado final."
                )
                return False

        except PlaywrightTimeout as e:
            page.screenshot(path="deposit_error.png")
            raise AstroPayWebError(
                f"Timeout esperando elemento en AstroPay: {e}. "
                "Revisá deposit_error.png"
            ) from e
        finally:
            browser.close()
