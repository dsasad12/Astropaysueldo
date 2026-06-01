"""
AstroPay HTTP API client.
Reemplaza la automatización de Playwright con llamadas directas a la API móvil.

Flujo:
1. POST /v4/auth/refresh          → access_token
2. GET  /v1/payment-elements/DR   → payment_element_external_id de la tarjeta
3. POST /v1/purchases/preview/authorization  → validación (respuesta vacía)
4. POST /v2/purchases/preview/summary        → preview_external_id + fees
5. Obtener tokenizer JWT  (endpoint a confirmar en primera ejecución real)
6. POST tokenizer.cc.astropay.com/public/v1/token  → card_token
7. POST /v2/purchases/confirm                → depósito final

TODO (first real deposit): capture steps 5-7 in HTTP Catcher to verify endpoints.
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://mapi.astropaycard.com"
TOKENIZER_URL = "https://tokenizer.cc.astropay.com"
USER_AGENT = "AstroPay/816 CFNetwork/3860.600.12 Darwin/25.5.0"
DEVICE_ID = "B7C457FE-AAF1-4504-9E61-8928CF1762DC"


class AstroPayWebError(Exception):
    pass


def _headers(access_token: str | None = None) -> dict:
    h = {
        "AppName": "APC",
        "Accept": "*/*",
        "Country": "AR",
        "Platform": "iOS",
        "Device-Info": "iPhone14,5 | 26.5 | iPhone | Jailbreak false",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "es-AR",
        "User-Agent": USER_AGENT,
        "AppVersion": "5.71.0",
        "TimeZone": "America/Argentina/Buenos_Aires",
        "Device-ID": DEVICE_ID,
        "AMP-Device-ID": DEVICE_ID,
        "Content-Type": "application/json",
        "Connection": "keep-alive",
    }
    if access_token:
        h["Authorization"] = f"Bearer {access_token}"
    return h


def _refresh_access_token(refresh_token: str) -> tuple[str, str]:
    resp = requests.post(
        f"{BASE_URL}/v4/auth/refresh",
        json={"refresh_token": refresh_token},
        headers=_headers(),
        timeout=30,
    )
    if not resp.ok:
        raise AstroPayWebError(
            f"Error al refrescar token: {resp.status_code} — {resp.text[:200]}"
        )
    session = resp.json()["session"]
    logger.info(
        "Token refrescado. Expira en %ss.", session.get("access_expires_in", "?")
    )
    return session["access_token"], session.get("refresh_token", refresh_token)


def _get_card_external_id(access_token: str, card_last4: str) -> str:
    resp = requests.get(
        f"{BASE_URL}/v1/payment-elements/DR",
        headers=_headers(access_token),
        timeout=30,
    )
    resp.raise_for_status()
    for card in resp.json().get("payment_elements", []):
        if card.get("last_four_digits") == card_last4 and card.get("enable"):
            eid = card["payment_element_external_id"]
            logger.info(
                "Tarjeta encontrada: %s **** %s", card.get("brand", ""), card_last4
            )
            return eid
    raise AstroPayWebError(f"Tarjeta **** {card_last4} no encontrada o deshabilitada.")


def _purchase_preview(
    access_token: str, amount: float, card_external_id: str
) -> str:
    """Corre el flujo preview y devuelve preview_external_id."""
    body = {
        "amount": int(amount),
        "operation": "WALLET_BALANCE",
        "currency": "ARS",
        "payment_method": "DR",
        "payment_element_external_id": card_external_id,
    }
    r = requests.post(
        f"{BASE_URL}/v1/purchases/preview/authorization",
        json=body,
        headers=_headers(access_token),
        timeout=30,
    )
    if not r.ok:
        raise AstroPayWebError(
            f"Error en preview/authorization: {r.status_code} — {r.text[:200]}"
        )

    r = requests.post(
        f"{BASE_URL}/v2/purchases/preview/summary",
        json=body,
        headers=_headers(access_token),
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()

    for item in data.get("summary", []):
        logger.info("  %s: %s", item["label"], item["value"])

    return data["preview_external_id"]


def _get_tokenizer_jwt(access_token: str, preview_external_id: str) -> str:
    """
    Obtiene el JWT de corta duración para el tokenizador.

    TODO: el endpoint exacto debe confirmarse en la primera ejecución real.
    Capturá en HTTP Catcher la llamada que precede a tokenizer.cc.astropay.com
    y actualizá esta función con el URL correcto.
    """
    candidates = [
        ("GET",  f"{BASE_URL}/v1/purchases/{preview_external_id}/tokenizer-jwt"),
        ("GET",  f"{BASE_URL}/v2/purchases/{preview_external_id}/tokenizer-jwt"),
        ("POST", f"{BASE_URL}/v1/purchases/{preview_external_id}/tokenizer"),
        ("GET",  f"{BASE_URL}/v1/tokenizer/jwt"),
        ("POST", f"{TOKENIZER_URL}/public/v1/auth"),
    ]
    for method, url in candidates:
        try:
            if method == "GET":
                r = requests.get(url, headers=_headers(access_token), timeout=10)
            else:
                r = requests.post(
                    url,
                    json={"access_token": access_token},
                    headers=_headers(access_token),
                    timeout=10,
                )
            if r.ok and r.content:
                payload = r.json()
                jwt = (
                    payload.get("token")
                    or payload.get("jwt")
                    or payload.get("access_token")
                )
                if jwt and "." in str(jwt):
                    logger.info("Tokenizer JWT obtenido de: %s", url)
                    return str(jwt)
        except Exception as exc:
            logger.debug("Fallo %s %s: %s", method, url, exc)

    logger.warning(
        "No se obtuvo tokenizer JWT — usando access_token como fallback. "
        "Si el depósito falla aquí, capturá el endpoint correcto en HTTP Catcher."
    )
    return access_token


def _tokenize_cvv(tokenizer_jwt: str, cvv: str) -> str:
    resp = requests.post(
        f"{TOKENIZER_URL}/public/v1/token",
        json={"value": cvv, "volatile": True},
        headers={
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "es-419,es;q=0.9",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "Authorization": f"Bearer {tokenizer_jwt}",
        },
        timeout=30,
    )
    if not resp.ok:
        raise AstroPayWebError(
            f"Error en tokenizer: {resp.status_code} — {resp.text[:200]}"
        )
    token = resp.json()["token"]
    logger.info("CVV tokenizado. Token: %s…", token[:20])
    return token


def _confirm_purchase(
    access_token: str,
    preview_external_id: str,
    card_token: str,
    amount: float,
    card_external_id: str,
) -> bool:
    """
    Confirma la compra.

    TODO: verificar endpoint y cuerpo exactos capturando en HTTP Catcher
    durante el primer depósito real (slide-to-confirm en la app).
    """
    body = {
        "preview_external_id": preview_external_id,
        "payment_element_external_id": card_external_id,
        "payment_method": "DR",
        "operation": "WALLET_BALANCE",
        "currency": "ARS",
        "amount": int(amount),
        "card_token": card_token,
    }
    resp = requests.post(
        f"{BASE_URL}/v2/purchases/confirm",
        json=body,
        headers=_headers(access_token),
        timeout=60,
    )
    logger.info("Confirm HTTP %s", resp.status_code)

    if not resp.ok:
        logger.error("Error en confirm: %s — %s", resp.status_code, resp.text[:500])
        return False

    data = resp.json()
    logger.info("Confirm response: %s", data)

    status = str(data.get("status", "")).lower()
    return status in {"approved", "success", "completed", "ok", "aprobado", "exitoso"}


def deposit(amount: float, card_last4: str) -> bool:
    refresh_token = os.getenv("ASTROPAY_REFRESH_TOKEN", "")
    cvv = os.getenv("CARD_CVV", "")

    if not refresh_token:
        raise AstroPayWebError("Falta ASTROPAY_REFRESH_TOKEN en los secrets.")
    if not cvv:
        raise AstroPayWebError("Falta CARD_CVV en los secrets.")

    logger.info(
        "=== Iniciando depósito %s ARS → tarjeta **** %s ===", int(amount), card_last4
    )

    access_token, _ = _refresh_access_token(refresh_token)
    card_external_id = _get_card_external_id(access_token, card_last4)

    logger.info("Obteniendo preview...")
    preview_id = _purchase_preview(access_token, amount, card_external_id)
    logger.info("preview_external_id: %s", preview_id)

    logger.info("Tokenizando CVV...")
    tokenizer_jwt = _get_tokenizer_jwt(access_token, preview_id)
    card_token = _tokenize_cvv(tokenizer_jwt, cvv)

    logger.info("Confirmando depósito...")
    ok = _confirm_purchase(access_token, preview_id, card_token, amount, card_external_id)

    if ok:
        logger.info("Depósito de %s ARS completado.", int(amount))
    else:
        logger.warning("Depósito no confirmado — revisar logs para el próximo paso.")

    return ok
