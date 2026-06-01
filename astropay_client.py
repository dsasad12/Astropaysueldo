"""
Cliente de la API Merchant de AstroPay.
Autenticación mediante HMAC-SHA256 según la especificación de AstroPay Business.
"""

import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

ENDPOINTS = {
    "sandbox": "https://sandbox-api.astropay.com",
    "production": "https://api.astropay.com",
}


class AstroPayError(Exception):
    def __init__(self, message: str, status_code: int | None = None, response: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class AstroPayClient:
    def __init__(
        self,
        merchant_id: str,
        api_key: str,
        secret_key: str,
        env: str = "sandbox",
        timeout: int = 30,
    ):
        if env not in ENDPOINTS:
            raise ValueError(f"env debe ser 'sandbox' o 'production', recibido: {env}")
        self.merchant_id = merchant_id
        self.api_key = api_key
        self.secret_key = secret_key
        self.base_url = ENDPOINTS[env]
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def _sign(self, payload: str) -> str:
        """Genera la firma HMAC-SHA256 del cuerpo del request."""
        return hmac.new(
            self.secret_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _build_headers(self, payload: str) -> dict[str, str]:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        signature = self._sign(payload)
        return {
            "Authorization": f"Bearer {self.api_key}",
            "X-Merchant-Id": self.merchant_id,
            "X-Timestamp": timestamp,
            "X-Signature": signature,
        }

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        payload = json.dumps(body, separators=(",", ":"))
        headers = self._build_headers(payload)

        logger.debug("POST %s | body: %s", url, payload)

        try:
            resp = self.session.post(url, data=payload, headers=headers, timeout=self.timeout)
        except requests.exceptions.RequestException as exc:
            raise AstroPayError(f"Error de red al conectar con AstroPay: {exc}") from exc

        try:
            data = resp.json()
        except ValueError:
            data = {"raw": resp.text}

        if not resp.ok:
            raise AstroPayError(
                f"AstroPay respondió con código {resp.status_code}: {data}",
                status_code=resp.status_code,
                response=data,
            )

        logger.debug("Respuesta %s: %s", resp.status_code, data)
        return data

    def create_deposit(
        self,
        amount: float,
        currency: str,
        card_number: str,
        card_expiry_month: str,
        card_expiry_year: str,
        card_cvv: str,
        card_holder_name: str,
        description: str = "Depósito sueldo docente",
    ) -> dict[str, Any]:
        """
        Inicia un depósito (cash-in) cargando la tarjeta Visa débito indicada.
        Retorna la respuesta completa de la API de AstroPay.

        NOTA: Si la API requiere 3DS/OTP, la respuesta incluirá una URL de
        redirect_url o action_url que deberás completar manualmente.
        """
        transaction_id = str(uuid.uuid4())
        body = {
            "merchant_deposit_id": transaction_id,
            "amount": round(amount, 2),
            "currency": currency,
            "description": description,
            "card": {
                "number": card_number,
                "expiry_month": str(card_expiry_month).zfill(2),
                "expiry_year": str(card_expiry_year).zfill(2),
                "cvv": card_cvv,
                "holder_name": card_holder_name,
            },
        }

        logger.info(
            "Iniciando depósito | monto: %s %s | transaction_id: %s",
            amount,
            currency,
            transaction_id,
        )
        return self._post("/v1/deposit", body)

    def get_deposit_status(self, merchant_deposit_id: str) -> dict[str, Any]:
        """Consulta el estado de un depósito por su ID de transacción."""
        return self._post("/v1/deposit/status", {"merchant_deposit_id": merchant_deposit_id})
