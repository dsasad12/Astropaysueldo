"""
Automatización de AstroPay via Playwright con modo stealth.
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
    "--disable-blink-features=AutomationControlled",
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class AstroPayWebError(Exception):
    pass


def _save_session(context):
    SESSION_FILE.write_text(json.dumps(context.storage_state()))
    logger.debug("Sesión guardada.")


def _new_page(context):
    page = context.new_page()
    # Ocultar que es un browser automatizado
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3] });
        window.chrome = { runtime: {} };
    """)
    return page


def _do_login(page, email: str, pin: str):
    logger.info("Navegando al login...")
    page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded", timeout=30000)

    # Esperar que aparezca el campo de email
    page.wait_for_selector('input[type="email"], input[name="email"]', timeout=15000)
    page.screenshot(path="debug_login.png")

    page.locator('input[type="email"]').or_(
        page.locator('input[name="email"]')
    ).first.fill(email)

    page.locator('input[type="password"]').or_(
        page.locator('input[name="password"]')
    ).first.fill(pin)

    page.locator('button[type="submit"]').first.click()

    # Esperar que desaparezca el formulario de login
    try:
        page.wait_for_selector('input[type="password"]', state="detached", timeout=20000)
    except PlaywrightTimeout:
        pass

    page.wait_for_load_state("networkidle", timeout=20000)
    page.screenshot(path="debug_after_login.png")
    logger.info("Login enviado. URL actual: %s", page.url)


def deposit(amount: float, card_last4: str) -> bool:
    email = os.getenv("ASTROPAY_EMAIL", "")
    pin = os.getenv("ASTROPAY_PASSWORD", "")

    if not email or not pin:
        raise AstroPayWebError("Faltan ASTROPAY_EMAIL o ASTROPAY_PASSWORD en los secrets.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=CHROMIUM_ARGS)
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 390, "height": 844},
            locale="es-AR",
        )

        page = _new_page(context)

        try:
            # Siempre hacer login explícito (GitHub Actions no tiene sesión)
            _do_login(page, email, pin)

            # Navegar al home después del login
            if "/home" not in page.url:
                page.goto(f"{BASE_URL}/home", wait_until="domcontentloaded", timeout=30000)

            # Esperar que cargue el contenido React (hasta 20s)
            logger.info("Esperando contenido del home...")
            try:
                page.wait_for_selector("button, [role='button']", timeout=20000)
            except PlaywrightTimeout:
                logger.warning("No aparecieron botones en 20s.")

            page.screenshot(path="debug_home.png")

            # Loguear botones visibles
            try:
                visible = page.evaluate(
                    "() => Array.from(document.querySelectorAll("
                    "'button, a, [role=\"button\"], [role=\"link\"]'))"
                    ".map(el => el.innerText.trim()).filter(t => t).join(' | ')"
                )
                logger.info("Botones en home: %s", visible)
            except Exception:
                pass

            logger.info("Buscando botón de depósito...")
            deposit_btn = (
                page.locator('[data-testid*="deposit"]')
                .or_(page.locator('[data-testid*="add-funds"]'))
                .or_(page.locator('[data-testid*="recharge"]'))
                .or_(page.get_by_text("Agregar saldo", exact=False))
                .or_(page.get_by_text("Cargar saldo", exact=False))
                .or_(page.get_by_text("Depositar", exact=False))
                .or_(page.get_by_text("Recargar", exact=False))
                .or_(page.get_by_text("Add funds", exact=False))
                .first
            )
            deposit_btn.click(timeout=15000)
            page.wait_for_load_state("networkidle")
            page.screenshot(path="debug_deposit_page.png")

            # Loguear contenido de la página de depósito
            try:
                visible2 = page.evaluate(
                    "() => Array.from(document.querySelectorAll("
                    "'button, a, [role=\"button\"], input'))"
                    ".map(el => (el.innerText || el.placeholder || el.type || '').trim())"
                    ".filter(t => t).join(' | ')"
                )
                logger.info("Elementos en página depósito: %s", visible2)
            except Exception:
                pass

            # Seleccionar tarjeta
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
                logger.info("Depósito de %s ARS completado.", int(amount))
                _save_session(context)
                return True
            except PlaywrightTimeout:
                page.screenshot(path="deposit_result.png")
                logger.warning("Sin confirmación visible. Revisá deposit_result.png")
                return False

        except PlaywrightTimeout as e:
            page.screenshot(path="deposit_error.png")
            raise AstroPayWebError(f"Timeout: {e}") from e
        finally:
            browser.close()
