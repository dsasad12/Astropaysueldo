#!/usr/bin/env python3
"""
Configuración inicial — ejecutar UNA SOLA VEZ antes de usar main.py.
Abre el browser de AstroPay, iniciás sesión y guarda la sesión para
que el script corra automáticamente sin pedirte contraseña.

Uso:
    python setup.py
"""

import os
import sys
import logging

from dotenv import load_dotenv
from astropay_web import login

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)


def main():
    email = os.getenv("ASTROPAY_EMAIL")
    password = os.getenv("ASTROPAY_PASSWORD")

    if not email or not password:
        print("\nERROR: Completá ASTROPAY_EMAIL y ASTROPAY_PASSWORD en el archivo .env primero.\n")
        sys.exit(1)

    print("\nSe va a abrir un browser para iniciar sesión en AstroPay.")
    print("Completá el login si hace falta y esperá que se cierre solo.\n")

    login(email=email, password=password, headless=False)
    print("\n✓ Sesión guardada. Ya podés ejecutar main.py automáticamente.\n")


if __name__ == "__main__":
    main()
