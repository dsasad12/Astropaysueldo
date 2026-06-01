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


def _do_login(page, email: str, pin: str):
    """Completa el formulario de login en la página actual."""
    logger.info("Iniciando sesión...")
    page.goto(f"{BASE_URL}/login", wait_until="networkidle", timeout=30000)
    page.screenshot(path="debug_login.png")

    # Rellenar email
    page.locator('input[type="email"]').or_(
        page.locator('input[name="email"]')
    ).first.fill(email)

    # Rellenar contraseña/PIN
    page.locator('input[type="password"]').or_(
        page.locator('input[name="password"]')
    ).first.fill(pin)

    page.locator('button[type="submit"]').first.click()

    try:
        page.wait_for_url("**/home**", timeout=20000)
    except PlaywrightTimeout:
        page.wait_for_selector('input[type="password"]', state="hidden", timeout=15000)

    page.screenshot(path="debug_after_login.png")
    logger.info("Login exitoso.")


def login(email: str, pin: str, headless: bool = False):
    """Login inicial — ejecutar una sola vez localmente."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=CHROMIUM_ARGS)
        context = browser.new_context()
        page = context.new_page()
        _do_login(page, email, pin)
        _save_session(context)
        browser.close()


def deposit(amount: float, card_last4: str) -> bool:
    """
    Realiza un depósito en AstroPay usando la tarjeta guardada.
    Retorna True si fue exitoso.
    """
    email = os.getenv("ASTROPAY_EMAIL", "")
    pin = os.getenv("ASTROPAY_PASSWORD", "")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=CHROMIUM_ARGS)

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

            if "/login" in page.url:
                if not email or not pin:
                    raise AstroPayWebError(
                        "Sesión expirada y no hay credenciales configuradas."
                    )
                _do_login(page, email, pin)
                _save_session(context)

            page.screenshot(path="debug_home.png")
            logger.info("Home cargado. URL: %s", page.url)

            # Loguear todo el texto visible para diagnóstico
            try:
                visible_text = page.evaluate("""
                    () => Array.from(document.querySelectorAll('button, a, [role="button"]'))
                         .map(el => el.innerText.trim())
                         .filter(t => t.length > 0)
                         .join(' | ')
                """)
                logger.info("Botones/links visibles en home: %s", visible_text)
            except Exception:
                pass

            # Buscar botón de agregar saldo — selectores separados para evitar error de sintaxis
            logger.info("Buscando botón de depósito...")
            deposit_btn = (
                page.locator('[data-testid*="deposit"]')
                .or_(page.locator('[data-testid*="add-funds"]'))
                .or_(page.locator('[aria-label*="saldo"]'))
                .or_(page.locator('[aria-label*="deposit"]'))
                .or_(page.get_by_text("Agregar saldo", exact=False))
                .or_(page.get_by_text("Depositar", exact=False))
                .or_(page.get_by_text("Add funds", exact=False))
                .first
            )
            deposit_btn.click(timeout=10000)
            page.wait_for_load_state("networkidle")
            page.screenshot(path="debug_deposit_page.png")

            # Seleccionar tarjeta por últimos 4 dígitos
            logger.info("Seleccionando tarjeta **** %s...", card_last4)
            page.get_by_text(card_last4, exact=False).first.click(timeout=10000)
            page.wait_for_load_state("networkidle")

            # Ingresar monto
            logger.info("Ingresando monto: %s ARS...", int(amount))
            amount_input = (
                page.locator('input[type="number"]')
                .or_(page.locator('input[inputmode="numeric"]'))
                .or_(page.locator('input[inputmode="decimal"]'))
                .or_(page.locator('input[placeholder*="monto"]'))
                .or_(page.locator('input[placeholder*="amount"]'))
                .first
            )
            amount_input.fill(str(int(amount)))

            # Confirmar
            confirm_btn = (
                page.locator('button[type="submit"]')
                .or_(page.get_by_text("Confirmar", exact=False))
                .or_(page.get_by_text("Continuar", exact=False))
                .first
            )
            confirm_btn.click(timeout=10000)

            # Esperar confirmación
            try:
                page.wait_for_selector(
                    ':text("exitoso"), :text("aprobado"), :text("success"), :text("approved")',
                    timeout=20000,
                )
                logger.info("Depósito de %s ARS completado exitosamente.", int(amount))
                _save_session(context)
                return True
            except PlaywrightTimeout:
                page.screenshot(path="deposit_result.png")
                logger.warning("No se detectó confirmación. Revisá deposit_result.png")
                return False

        except PlaywrightTimeout as e:
            page.screenshot(path="deposit_error.png")
            raise AstroPayWebError(
                f"Timeout en AstroPay: {e}. Revisá deposit_error.png"
            ) from e
        finally:
            browser.close()
