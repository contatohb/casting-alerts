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
import subprocess
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
# Envio de email via Gmail MCP
# ─────────────────────────────────────────────────────────────────

def send_email(subject: str, body: str, recipient: str) -> bool:
    import tempfile
    payload = {
        "messages": [{
            "subject": subject,
            "to": [recipient],
            "content": body,
        }]
    }
    # Salvar payload em arquivo temporário para evitar problemas de escaping no shell
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8')
    json.dump(payload, tmp, ensure_ascii=False)
    tmp.flush()
    tmp.close()
    try:
        with open(tmp.name, 'r', encoding='utf-8') as f:
            input_str = f.read()
        result = subprocess.run(
            ["manus-mcp-cli", "tool", "call", "gmail_send_messages",
             "--server", "gmail", "--input", input_str],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            logger.info(f"Email enviado para {recipient}")
            return True
        else:
            logger.error(f"Erro ao enviar email: {result.stderr[:300]}")
            return False
    except Exception as exc:
        logger.error(f"Gmail MCP: {exc}")
        return False
    finally:
        if os.path.exists(tmp.name):
            os.remove(tmp.name)


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
