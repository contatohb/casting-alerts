#!/usr/bin/env python3
"""
Alerta diário de novas oportunidades de casting.

Executa o monitor_casting.py, filtra apenas oportunidades NOVAS
(não alertadas antes) e envia email detalhado via Gmail MCP.

Uso:
    python3 alerta_casting.py [--force-send] [--no-enrich]
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import date
from typing import Dict, List

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("alerta_casting")

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPTS_DIR)

if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_PROJECT_DIR, ".env"))
except Exception:
    pass

RECIPIENT = os.getenv("MONITOR_RECIPIENT", "huddsong@gmail.com")
SEEN_PATH = os.path.join(_PROJECT_DIR, "data", "casting_seen.json")


# ─────────────────────────────────────────────────────────────────
# Histórico de oportunidades já alertadas
# ─────────────────────────────────────────────────────────────────

def load_seen(path: str) -> dict:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_seen(seen: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────────────────────────
# Envio de email via SMTP (Gmail com App Password)
# ─────────────────────────────────────────────────────────────────

GMAIL_SENDER = os.getenv("GMAIL_SENDER", "huddsong@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")


def send_email(subject: str, body: str, recipient: str) -> bool:
    """
    Envia email via SMTP usando Gmail com App Password.
    Requer a variável de ambiente GMAIL_APP_PASSWORD configurada.
    """
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    if not GMAIL_APP_PASSWORD:
        logger.error("GMAIL_APP_PASSWORD não configurado. Defina o secret no GitHub Actions.")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = GMAIL_SENDER
        msg["To"] = recipient

        # Corpo em texto puro
        part_text = MIMEText(body, "plain", "utf-8")
        msg.attach(part_text)

        # Corpo em HTML (converte quebras de linha e preserva formatação)
        html_body = (
            "<html><body><pre style='font-family:monospace;font-size:13px;'>"
            + body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            + "</pre></body></html>"
        )
        part_html = MIMEText(html_body, "html", "utf-8")
        msg.attach(part_html)

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_SENDER, recipient, msg.as_string())

        logger.info(f"Email enviado para {recipient} via SMTP")
        return True

    except smtplib.SMTPAuthenticationError:
        logger.error("Falha de autenticação SMTP. Verifique GMAIL_APP_PASSWORD.")
        return False
    except Exception as exc:
        logger.error(f"Erro ao enviar email via SMTP: {exc}")
        return False


# ─────────────────────────────────────────────────────────────────
# Principal
# ─────────────────────────────────────────────────────────────────

def main():
    import warnings
    warnings.filterwarnings("ignore")

    force_send = "--force-send" in sys.argv
    no_enrich = "--no-enrich" in sys.argv

    today = date.today()
    logger.info(f"Alerta de casting — {today.isoformat()}")

    # Importar módulo de casting
    try:
        from monitor_casting import (
            buscar_casting,
            filtrar_novas_oportunidades,
            formatar_email_casting,
        )
    except ImportError as e:
        logger.error(f"Erro ao importar monitor_casting: {e}")
        return 1

    # Buscar oportunidades
    logger.info("Buscando oportunidades de casting...")
    oportunidades, erros = buscar_casting(
        enriquecer_detalhes=not no_enrich,
        max_enriquecimento=30,
    )
    logger.info(f"Oportunidades filtradas: {len(oportunidades)}")

    # Carregar histórico e filtrar novas
    seen = load_seen(SEEN_PATH)
    novas, seen_atualizado = filtrar_novas_oportunidades(oportunidades, seen)
    logger.info(f"Oportunidades novas (não alertadas antes): {len(novas)}")

    # Salvar histórico atualizado
    save_seen(seen_atualizado, SEEN_PATH)

    # Gerar relatório
    corpo = formatar_email_casting(novas, erros)
    print(corpo)

    # Definir assunto
    if novas:
        assunto = f"[Casting] {len(novas)} nova(s) oportunidade(s) — {today.strftime('%d/%m/%Y')}"
    else:
        assunto = f"[Casting] Nenhuma oportunidade nova — {today.strftime('%d/%m/%Y')}"

    # Enviar email se há novidades ou se forçado
    if novas or force_send:
        ok = send_email(assunto, corpo, RECIPIENT)
        return 0 if ok else 1
    else:
        logger.info("Sem novidades — email não enviado (use --force-send para forçar)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
