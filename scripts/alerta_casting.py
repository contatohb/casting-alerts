#!/usr/bin/env python3
"""
Alerta diário de novas oportunidades de casting.

Executa o monitor_casting.py, filtra apenas oportunidades NOVAS
(não alertadas antes) e envia email HTML premium via SMTP.

Backend de persistência: Supabase (primário) com fallback para JSON local.

Uso:
    python3 alerta_casting.py [--force-send] [--no-enrich]
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import date, datetime, timezone
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

# Limiar de falhas consecutivas para enviar alerta de sistema
ALERTA_FALHAS_CONSECUTIVAS = 2


# ─────────────────────────────────────────────────────────────────
# Histórico local (fallback quando Supabase indisponível)
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


def send_email(subject: str, body_html: str, body_text: str, recipient: str) -> bool:
    """
    Envia email multipart (HTML + texto puro) via SMTP usando Gmail com App Password.
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

        # Parte texto puro (fallback)
        part_text = MIMEText(body_text, "plain", "utf-8")
        msg.attach(part_text)

        # Parte HTML (preferencial — clientes modernos usam esta)
        part_html = MIMEText(body_html, "html", "utf-8")
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


def send_alerta_falha(falhas: int, ultimo_sucesso: datetime = None) -> None:
    """Envia email de alerta quando o sistema falha consecutivamente."""
    if not GMAIL_APP_PASSWORD:
        return

    ultimo_str = (
        ultimo_sucesso.strftime("%d/%m/%Y %H:%M") if ultimo_sucesso else "desconhecido"
    )
    assunto = f"[Casting] ⚠️ ALERTA: Sistema com falhas consecutivas ({falhas}x)"
    corpo_html = f"""
    <html><body style="font-family: Arial, sans-serif; padding: 20px;">
    <h2 style="color: #c0392b;">⚠️ Alerta do Sistema de Casting</h2>
    <p>O sistema de alertas de casting falhou <strong>{falhas} vezes consecutivas</strong>.</p>
    <p><strong>Último envio bem-sucedido:</strong> {ultimo_str}</p>
    <p>Verifique os logs no <a href="https://github.com/contatohb/casting-alerts/actions">GitHub Actions</a>.</p>
    </body></html>
    """
    corpo_texto = f"Sistema de casting com {falhas} falhas consecutivas. Último sucesso: {ultimo_str}."
    send_email(assunto, corpo_html, corpo_texto, RECIPIENT)


# ─────────────────────────────────────────────────────────────────
# Principal
# ─────────────────────────────────────────────────────────────────

def main():
    import warnings
    warnings.filterwarnings("ignore")

    inicio = time.time()
    force_send = "--force-send" in sys.argv

    today = date.today()
    logger.info(f"Alerta de casting — {today.isoformat()}")

    # Importar módulos
    try:
        from monitor_casting import (
            buscar_casting,
            filtrar_novas_oportunidades,
        )
        from email_template import gerar_email_html, gerar_email_texto
        import supabase_client as sb
    except ImportError as e:
        logger.error(f"Erro ao importar módulos: {e}")
        return 1

    # Verificar disponibilidade do Supabase
    usar_supabase = sb.disponivel()
    if usar_supabase:
        logger.info("Supabase disponível — usando como backend de persistência.")
    else:
        logger.warning("Supabase indisponível — usando fallback JSON local.")

    # Buscar oportunidades
    logger.info("Buscando oportunidades de casting...")
    try:
        oportunidades, erros = buscar_casting()
        logger.info(f"Oportunidades filtradas: {len(oportunidades)}")
    except Exception as e:
        duracao = time.time() - inicio
        logger.error(f"Erro crítico ao buscar oportunidades: {e}")
        if usar_supabase:
            sb.registrar_execucao(
                status="falha",
                duracao_segundos=duracao,
                erro_mensagem=str(e),
            )
            falhas = sb.contar_falhas_consecutivas()
            if falhas >= ALERTA_FALHAS_CONSECUTIVAS:
                ultimo = sb.ultima_execucao_com_sucesso()
                send_alerta_falha(falhas, ultimo)
        return 1

    # Determinar oportunidades novas (deduplicação)
    if usar_supabase:
        ids_vistos = sb.buscar_ids_vistos(dias=30)
        novas = [op for op in oportunidades if op.get("id") not in ids_vistos]
        logger.info(f"Oportunidades novas (Supabase): {len(novas)}")
        # Salvar novas no Supabase
        if novas:
            inseridos = sb.salvar_oportunidades(novas)
            logger.info(f"Registros salvos no Supabase: {inseridos}")
        # Manter fallback JSON sincronizado
        seen = load_seen(SEEN_PATH)
        seen_atualizado = {**seen, **{op["id"]: True for op in novas}}
        save_seen(seen_atualizado, SEEN_PATH)
    else:
        # Fallback: usar JSON local
        seen = load_seen(SEEN_PATH)
        novas, seen_atualizado = filtrar_novas_oportunidades(oportunidades, seen)
        logger.info(f"Oportunidades novas (JSON local): {len(novas)}")
        save_seen(seen_atualizado, SEEN_PATH)

    # Gerar corpo do email (HTML + texto puro)
    corpo_html = gerar_email_html(novas, erros)
    corpo_texto = gerar_email_texto(novas, erros)

    # Imprimir versão texto no log para debug
    print(corpo_texto)

    # Definir assunto
    if novas:
        assunto = f"[Audições e Jobs] {len(novas)} nova(s) oportunidade(s) — {today.strftime('%d/%m/%Y')}"
    else:
        assunto = f"[Audições e Jobs] Nenhuma oportunidade nova — {today.strftime('%d/%m/%Y')}"

    # Enviar email se há novidades ou se forçado
    duracao = time.time() - inicio
    enviado = False
    if novas or force_send:
        ok = send_email(assunto, corpo_html, corpo_texto, RECIPIENT)
        enviado = ok
        
        # CORREÇÃO (2026-03-26): Marcar como enviadas ANTES de registrar execução
        if ok and usar_supabase and novas:
            ids_enviados = [op["id"] for op in novas if op.get("id")]
            if sb.marcar_como_enviadas(ids_enviados):
                logger.info(f"Marcadas {len(ids_enviados)} oportunidades como enviadas no Supabase")
            else:
                logger.error(f"Falha ao marcar {len(ids_enviados)} oportunidades como enviadas!")
        
        # Registrar execução no Supabase
        if usar_supabase:
            sb.registrar_execucao(
                status="sucesso" if ok else "falha",
                total_encontradas=len(oportunidades),
                total_novas=len(novas),
                total_enviadas=len(novas) if ok else 0,
                duracao_segundos=duracao,
                erro_mensagem=None if ok else "Falha no envio SMTP",
            )
            if not ok:
                falhas = sb.contar_falhas_consecutivas()
                if falhas >= ALERTA_FALHAS_CONSECUTIVAS:
                    ultimo = sb.ultima_execucao_com_sucesso()
                    send_alerta_falha(falhas, ultimo)
        return 0 if ok else 1
    else:
        logger.info("Sem novidades — email não enviado (use --force-send para forçar)")
        if usar_supabase:
            sb.registrar_execucao(
                status="sem_novidades",
                total_encontradas=len(oportunidades),
                total_novas=0,
                total_enviadas=0,
                duracao_segundos=duracao,
            )
        return 0


if __name__ == "__main__":
    sys.exit(main())
