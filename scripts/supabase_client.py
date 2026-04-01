"""
supabase_client.py — Módulo de integração com o Supabase para o sistema de alertas de casting.

Responsabilidades:
- Verificar quais oportunidades já foram vistas (deduplicação)
- Persistir novas oportunidades no banco de dados
- Registrar log de execuções (sucesso, falha, sem novidades)
- Marcar oportunidades como enviadas por email

Tabelas utilizadas:
- casting_oportunidades: histórico completo de oportunidades
- casting_execucoes: log de cada execução do sistema
"""

import os
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── Configuração ──────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://wuadkgmggkmyglxpxeyh.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_ANON_KEY", "")  # Obrigatório via secret do GitHub

_TIMEOUT = 15  # segundos por requisição
_MAX_RETRIES = 3


def _headers(prefer: str = "return=minimal") -> dict:
    """Retorna headers atualizados com a chave atual (pode mudar via env)."""
    key = os.environ.get("SUPABASE_ANON_KEY", SUPABASE_KEY)
    return {
        "Content-Type": "application/json",
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Prefer": prefer,
    }


def _request(method: str, path: str, prefer: str = "return=minimal", **kwargs) -> Optional[requests.Response]:
    """
    Executa uma requisição HTTP ao Supabase com retry automático.
    Use o parâmetro `prefer` para customizar o header Prefer (ex: 'resolution=ignore-duplicates,return=minimal').
    Não passe `headers` diretamente — use `prefer` para customizar o header Prefer.
    """
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    # Remove 'headers' de kwargs se alguém passou por engano (evita conflito)
    kwargs.pop("headers", None)
    for tentativa in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.request(
                method, url, headers=_headers(prefer=prefer), timeout=_TIMEOUT, **kwargs
            )
            if resp.status_code < 500:
                return resp
            logger.warning(f"Supabase retornou {resp.status_code} (tentativa {tentativa}/{_MAX_RETRIES})")
        except requests.exceptions.RequestException as e:
            logger.warning(f"Erro de conexão com Supabase (tentativa {tentativa}/{_MAX_RETRIES}): {e}")
        if tentativa < _MAX_RETRIES:
            time.sleep(2 ** tentativa)  # backoff exponencial: 2s, 4s
    logger.error("Supabase indisponível após todas as tentativas.")
    return None


def disponivel() -> bool:
    """Verifica se o Supabase está acessível e configurado."""
    if not os.environ.get("SUPABASE_ANON_KEY"):
        logger.warning("SUPABASE_ANON_KEY não configurada — usando fallback local.")
        return False
    resp = _request("GET", "casting_oportunidades?select=id&limit=1")
    return resp is not None and resp.status_code == 200


def buscar_ids_vistos(dias: int = 30) -> set:
    """
    Retorna o conjunto de IDs de oportunidades JÁ ENVIADAS nos últimos N dias.
    Usado para deduplicação — substitui o casting_seen.json.
    CORREÇÃO (2026-03-26): Adicionar filtro enviado_email=eq.true para evitar reenvios.
    """
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
    resp = _request(
        "GET",
        f"casting_oportunidades?select=id&data_encontrada=gte.{cutoff}&enviado_email=eq.true"
    )
    if resp is None or resp.status_code != 200:
        logger.warning("Não foi possível buscar IDs vistos do Supabase.")
        return set()
    try:
        return {row["id"] for row in resp.json()}
    except Exception as e:
        logger.warning(f"Erro ao processar IDs vistos: {e}")
        return set()


def salvar_oportunidades(oportunidades: list) -> int:
    """
    Insere novas oportunidades no banco de dados.
    Usa upsert com ON CONFLICT DO NOTHING para evitar duplicatas.
    Retorna o número de registros inseridos com sucesso.
    """
    if not oportunidades:
        return 0

    registros = []
    for op in oportunidades:
        registros.append({
            "id": op.get("id", ""),
            "titulo": op.get("titulo", "")[:500],
            "fonte": op.get("fonte", ""),
            "categoria": op.get("categoria", "Outros"),
            "local_vaga": (op.get("local") or "")[:200],
            "cache": (op.get("cache") or "")[:100],
            "faixa_etaria": (op.get("faixa_etaria") or "")[:100],
            "genero": (op.get("genero") or "")[:50],
            "data_inscricao": (op.get("data_inscricao") or "")[:50],
            "data_teste": (op.get("data_teste") or "")[:50],
            "link": (op.get("link") or "")[:1000],
            "resumo": (op.get("resumo") or "")[:2000],
            "data_publicacao": op.get("data_publicacao"),
            "enviado_email": False,
            "ativo": True,
        })

    # Upsert em lotes de 50
    inseridos = 0
    for i in range(0, len(registros), 50):
        lote = registros[i:i + 50]
        resp = _request(
            "POST",
            "casting_oportunidades",
            prefer="resolution=ignore-duplicates,return=minimal",
            json=lote,
        )
        if resp is not None and resp.status_code in (200, 201):
            inseridos += len(lote)
        else:
            status = resp.status_code if resp else "sem resposta"
            logger.warning(f"Erro ao salvar lote {i//50 + 1} no Supabase: {status}")

    return inseridos


def marcar_como_enviadas(ids: list) -> bool:
    """Marca as oportunidades como enviadas por email."""
    if not ids:
        return True
    agora = datetime.now(timezone.utc).isoformat()
    # Atualiza em lotes de 50 usando filtro IN
    for i in range(0, len(ids), 50):
        lote = ids[i:i + 50]
        ids_param = "(" + ",".join(f'"{id_}"' for id_ in lote) + ")"
        resp = _request(
            "PATCH",
            f"casting_oportunidades?id=in.{ids_param}",
            prefer="return=minimal",
            json={"enviado_email": True, "data_envio_email": agora},
        )
        if resp is None or resp.status_code not in (200, 204):
            status = resp.status_code if resp else "sem resposta"
            logger.warning(f"Erro ao marcar lote como enviado: {status}")
            return False
    return True


def registrar_execucao(
    status: str,
    total_encontradas: int = 0,
    total_novas: int = 0,
    total_enviadas: int = 0,
    duracao_segundos: float = 0.0,
    erro_mensagem: str = None,
    fontes_consultadas: dict = None,
    workflow_run_id: str = None,
) -> bool:
    """
    Registra o resultado de uma execução do sistema na tabela casting_execucoes.
    status: 'sucesso' | 'falha' | 'sem_novidades'
    """
    registro = {
        "status": status,
        "total_encontradas": total_encontradas,
        "total_novas": total_novas,
        "total_enviadas": total_enviadas,
        "duracao_segundos": round(duracao_segundos, 2),
        "erro_mensagem": erro_mensagem,
        "fontes_consultadas": fontes_consultadas,
        "workflow_run_id": workflow_run_id or os.environ.get("GITHUB_RUN_ID"),
    }
    resp = _request(
        "POST",
        "casting_execucoes",
        prefer="return=minimal",
        json=registro,
    )
    if resp is None or resp.status_code not in (200, 201):
        logger.warning(f"Não foi possível registrar execução no Supabase.")
        return False
    return True


def ultima_execucao_com_sucesso() -> Optional[datetime]:
    """
    Retorna a data/hora da última execução bem-sucedida.
    Usado para detectar falhas consecutivas e enviar alerta.
    """
    resp = _request(
        "GET",
        "casting_execucoes?status=eq.sucesso&order=data_execucao.desc&limit=1&select=data_execucao"
    )
    if resp is None or resp.status_code != 200:
        return None
    try:
        rows = resp.json()
        if rows:
            return datetime.fromisoformat(rows[0]["data_execucao"].replace("Z", "+00:00"))
    except Exception:
        pass
    return None


def contar_falhas_consecutivas() -> int:
    """
    Conta quantas execuções consecutivas falharam (da mais recente para trás).
    Usado para decidir se deve enviar alerta de falha.
    """
    resp = _request(
        "GET",
        "casting_execucoes?order=data_execucao.desc&limit=10&select=status"
    )
    if resp is None or resp.status_code != 200:
        return 0
    try:
        rows = resp.json()
        falhas = 0
        for row in rows:
            if row["status"] == "falha":
                falhas += 1
            else:
                break
        return falhas
    except Exception:
        return 0
