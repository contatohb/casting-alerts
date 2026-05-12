#!/usr/bin/env python3
"""
audit_filters.py — Verificação periódica da eficácia dos filtros editoriais.

Funcionamento:
  1. Coleta TODOS os itens brutos de cada feed RSS (sem aplicar filtros)
  2. Aplica os filtros e registra o que foi aceito vs. rejeitado
  3. Usa heurísticas para detectar:
     - Falsos positivos: itens aceitos que parecem editoriais
     - Falsos negativos: itens rejeitados que parecem ser casting calls reais
  4. Persiste o histórico de auditoria em data/audit_history.json
  5. Envia email de relatório se houver problemas ou se for execução semanal

Uso:
  python audit_filters.py            # Auditoria completa + email se houver problemas
  python audit_filters.py --force    # Força envio do relatório mesmo sem problemas
  python audit_filters.py --dry-run  # Apenas imprime o relatório, não envia email
"""

import argparse
import html
import json
import logging
import os
import re
import smtplib
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

# Adicionar o diretório de scripts ao path
SCRIPTS_DIR = Path(__file__).parent
DATA_DIR = SCRIPTS_DIR.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(SCRIPTS_DIR))

from monitor_casting import (
    HEADERS, TIMEOUT, RSS_FEEDS,
    KW_OPORTUNIDADE, KW_EDITORIAL, KW_EDITORIAL_PT,
    FONTES_COM_EDITORIAL, CATS_EXCLUIR_GDA,
    _inferir_categoria,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

AUDIT_HISTORY_FILE = DATA_DIR / "audit_history.json"

# ─────────────────────────────────────────────────────────────────────────────
# HEURÍSTICAS PARA DETECÇÃO DE ANOMALIAS
# ─────────────────────────────────────────────────────────────────────────────

# Indicadores fortes de que um item aceito pode ser editorial (falso positivo)
KW_FALSO_POSITIVO = re.compile(
    r"\b(?:how\s+to|tips?\s+for|guide\s+to|best\s+\w+\s+for|top\s+\d+\s+\w+|"
    r"everything\s+you\s+need|what\s+you\s+need|"
    r"como\s+(?:se\s+)?(?:preparar|fazer|ser|tornar|melhorar|conseguir)\b|"
    r"dicas?\s+(?:para|de)\s+\w+|guia\s+(?:para|de|completo)\s+\w+|"
    r"\d+\s+(?:dicas?|maneiras?|formas?|passos?|erros?|segredos?)|"
    r"tudo\s+(?:sobre|que\s+você)|entrevista\s+(?:com|exclusiva)|"
    r"perfil\s+de\s+(?:ator|atriz|artista)|saiba\s+(?:mais|como|tudo)\s+sobre|"
    r"descubra\s+como|conheça\s+(?:o|a|os|as)\s+\w+)\b",
    re.IGNORECASE,
)

# Indicadores fortes de que um item rejeitado pode ser casting call real (falso negativo)
KW_FALSO_NEGATIVO = re.compile(
    r"\b(?:casting\s+call|open\s+(?:call|audition)|audição\s+aberta|"
    r"seleção\s+de\s+elenco|chamada\s+de\s+elenco|inscri[çc][õo]es?\s+abertas?|"
    r"now\s+casting|paid\s+(?:role|acting)|background\s+actors?|"
    r"procuramos?\s+(?:atores?|atrizes?|cantores?)|"
    r"buscamos?\s+(?:atores?|atrizes?|cantores?)|"
    r"vaga[s]?\s+(?:para|de)\s+(?:ator|atriz|cantor|dançarino))\b",
    re.IGNORECASE,
)


# ─────────────────────────────────────────────────────────────────────────────
# COLETA BRUTA DE ITENS RSS (sem filtros)
# ─────────────────────────────────────────────────────────────────────────────

def _coletar_itens_brutos(feed: Dict) -> List[Dict]:
    """Coleta todos os itens de um feed RSS sem aplicar nenhum filtro."""
    itens = []
    try:
        resp = requests.get(feed["url"], timeout=TIMEOUT, headers=HEADERS)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        channel = root.find("channel")
        if channel is None:
            return []
        for item in channel.findall("item"):
            title = html.unescape((item.findtext("title") or "").strip())
            link = (item.findtext("link") or "").strip()
            desc_raw = item.findtext("description") or ""
            desc = html.unescape(BeautifulSoup(desc_raw, "html.parser").get_text())
            cats = [c.text.lower() for c in item.findall("category") if c.text]
            pub_date = item.findtext("pubDate") or ""
            if title and link:
                itens.append({
                    "titulo": title,
                    "link": link,
                    "descricao": desc[:300],
                    "categorias_rss": cats,
                    "pub_date": pub_date,
                })
    except Exception as e:
        logger.warning(f"Erro ao coletar {feed['nome']}: {e}")
    return itens


# ─────────────────────────────────────────────────────────────────────────────
# APLICAÇÃO DOS FILTROS (simulação do _processar_item_rss)
# ─────────────────────────────────────────────────────────────────────────────

def _aplicar_filtros(item: Dict, fonte: str) -> Tuple[bool, str]:
    """
    Simula os filtros do _processar_item_rss.
    Retorna (aceito: bool, motivo_rejeicao: str).
    """
    titulo = item["titulo"]
    desc = item["descricao"]
    cats = item["categorias_rss"]
    texto = f"{titulo} {desc}"

    # Filtro 1: categorias do Guia do Ator
    if fonte == "Guia do Ator":
        if any(c in CATS_EXCLUIR_GDA for c in cats):
            if not KW_OPORTUNIDADE.search(titulo):
                return False, "categoria_gda_excluida"

    # Filtro 2: KW_EDITORIAL (EN) — todas as fontes
    if KW_EDITORIAL.search(titulo):
        return False, "kw_editorial_en"

    # Filtro 3: KW_EDITORIAL_PT — todas as fontes
    if KW_EDITORIAL_PT.search(titulo):
        return False, "kw_editorial_pt"

    # Filtro 4: Project Casting — exige indicadores de ação
    if fonte == "Project Casting":
        kw_acao = re.compile(
            r"\b(casting\s+call|open\s+call|open\s+audition|audition\s+notice|"
            r"now\s+casting|seeking\s+\w|looking\s+for\s+\w|"
            r"paid\s+(?:acting|casting|role)|background\s+(?:actors?|extras?)|"
            r"actors?\s+needed|talent\s+needed|submit\s+(?:now|today|here)|"
            r"apply\s+(?:now|today|here)|deadline|submissions?\s+(?:open|due)|"
            r"role[s]?\s+available|\bextras?\b|\bbackground\b)\b",
            re.IGNORECASE,
        )
        if not kw_acao.search(texto):
            return False, "project_casting_sem_acao"

    # Filtro 5: KW_OPORTUNIDADE — todas as fontes
    if not KW_OPORTUNIDADE.search(texto):
        return False, "sem_kw_oportunidade"

    return True, ""


# ─────────────────────────────────────────────────────────────────────────────
# DETECÇÃO DE ANOMALIAS
# ─────────────────────────────────────────────────────────────────────────────

def _detectar_anomalias(
    aceitos: List[Dict], rejeitados: List[Dict], fonte: str
) -> Dict:
    """Detecta falsos positivos e falsos negativos."""
    falsos_positivos = []
    falsos_negativos = []

    for item in aceitos:
        titulo = item["titulo"]
        if KW_FALSO_POSITIVO.search(titulo):
            falsos_positivos.append({
                "titulo": titulo,
                "link": item["link"],
                "suspeita": "título com padrão editorial não capturado pelo filtro",
            })

    for item in rejeitados:
        titulo = item["titulo"]
        motivo = item.get("motivo_rejeicao", "")
        # Só verificar itens rejeitados por filtro editorial (não por falta de KW_OPORTUNIDADE)
        if motivo in ("kw_editorial_en", "kw_editorial_pt", "project_casting_sem_acao"):
            if KW_FALSO_NEGATIVO.search(titulo):
                falsos_negativos.append({
                    "titulo": titulo,
                    "link": item["link"],
                    "motivo_rejeicao": motivo,
                    "suspeita": "título com indicadores de casting call rejeitado pelo filtro",
                })

    return {
        "falsos_positivos": falsos_positivos,
        "falsos_negativos": falsos_negativos,
    }


# ─────────────────────────────────────────────────────────────────────────────
# AUDITORIA PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def executar_auditoria() -> Dict:
    """Executa a auditoria completa de todos os feeds."""
    logger.info("Iniciando auditoria dos filtros editoriais...")
    resultado = {
        "data_auditoria": datetime.now(timezone.utc).isoformat(),
        "fontes": {},
        "resumo": {
            "total_itens": 0,
            "total_aceitos": 0,
            "total_rejeitados": 0,
            "total_falsos_positivos": 0,
            "total_falsos_negativos": 0,
            "fontes_com_anomalias": [],
        },
    }

    # Agrupar feeds por nome (evitar duplicatas do Guia do Ator)
    feeds_por_nome = {}
    for feed in RSS_FEEDS:
        nome = feed["nome"]
        if nome not in feeds_por_nome:
            feeds_por_nome[nome] = []
        feeds_por_nome[nome].append(feed)

    for nome_fonte, feeds in feeds_por_nome.items():
        logger.info(f"  Auditando: {nome_fonte}")
        todos_itens = []
        for feed in feeds:
            itens = _coletar_itens_brutos(feed)
            todos_itens.extend(itens)

        aceitos = []
        rejeitados = []
        motivos_rejeicao = {}

        for item in todos_itens:
            aceito, motivo = _aplicar_filtros(item, nome_fonte)
            if aceito:
                aceitos.append(item)
            else:
                item_rej = dict(item)
                item_rej["motivo_rejeicao"] = motivo
                rejeitados.append(item_rej)
                motivos_rejeicao[motivo] = motivos_rejeicao.get(motivo, 0) + 1

        anomalias = _detectar_anomalias(aceitos, rejeitados, nome_fonte)
        taxa_rejeicao = len(rejeitados) / len(todos_itens) * 100 if todos_itens else 0

        resultado["fontes"][nome_fonte] = {
            "total": len(todos_itens),
            "aceitos": len(aceitos),
            "rejeitados": len(rejeitados),
            "taxa_rejeicao_pct": round(taxa_rejeicao, 1),
            "motivos_rejeicao": motivos_rejeicao,
            "falsos_positivos": anomalias["falsos_positivos"],
            "falsos_negativos": anomalias["falsos_negativos"],
            "exemplos_aceitos": [i["titulo"] for i in aceitos[:3]],
            "exemplos_rejeitados": [
                {"titulo": i["titulo"], "motivo": i["motivo_rejeicao"]}
                for i in rejeitados[:3]
            ],
        }

        # Atualizar resumo
        resultado["resumo"]["total_itens"] += len(todos_itens)
        resultado["resumo"]["total_aceitos"] += len(aceitos)
        resultado["resumo"]["total_rejeitados"] += len(rejeitados)
        resultado["resumo"]["total_falsos_positivos"] += len(anomalias["falsos_positivos"])
        resultado["resumo"]["total_falsos_negativos"] += len(anomalias["falsos_negativos"])

        if anomalias["falsos_positivos"] or anomalias["falsos_negativos"]:
            resultado["resumo"]["fontes_com_anomalias"].append(nome_fonte)

    logger.info(
        f"Auditoria concluída: {resultado['resumo']['total_itens']} itens, "
        f"{resultado['resumo']['total_aceitos']} aceitos, "
        f"{resultado['resumo']['total_rejeitados']} rejeitados, "
        f"{resultado['resumo']['total_falsos_positivos']} falsos positivos, "
        f"{resultado['resumo']['total_falsos_negativos']} falsos negativos"
    )
    return resultado


# ─────────────────────────────────────────────────────────────────────────────
# PERSISTÊNCIA DO HISTÓRICO
# ─────────────────────────────────────────────────────────────────────────────

def carregar_historico() -> List[Dict]:
    if AUDIT_HISTORY_FILE.exists():
        try:
            with open(AUDIT_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def salvar_historico(historico: List[Dict], nova_auditoria: Dict) -> None:
    historico.append(nova_auditoria)
    # Manter apenas as últimas 12 auditorias (3 meses de histórico semanal)
    if len(historico) > 12:
        historico = historico[-12:]
    with open(AUDIT_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(historico, f, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# GERAÇÃO DO RELATÓRIO HTML
# ─────────────────────────────────────────────────────────────────────────────

def _gerar_html_relatorio(auditoria: Dict, historico: List[Dict]) -> str:
    """Gera o HTML do email de relatório de auditoria."""
    resumo = auditoria["resumo"]
    data_str = datetime.fromisoformat(auditoria["data_auditoria"]).strftime("%d/%m/%Y %H:%M")
    tem_anomalias = resumo["total_falsos_positivos"] > 0 or resumo["total_falsos_negativos"] > 0

    # Calcular tendência histórica
    historico_resumo = []
    for h in historico[-4:]:  # últimas 4 auditorias
        d = datetime.fromisoformat(h["data_auditoria"]).strftime("%d/%m")
        r = h["resumo"]
        historico_resumo.append({
            "data": d,
            "aceitos": r["total_aceitos"],
            "rejeitados": r["total_rejeitados"],
            "anomalias": r["total_falsos_positivos"] + r["total_falsos_negativos"],
        })

    # Cores por status
    cor_status = "#27ae60" if not tem_anomalias else "#e74c3c"
    texto_status = "Filtros funcionando corretamente" if not tem_anomalias else f"⚠ {len(resumo['fontes_com_anomalias'])} fonte(s) com anomalias detectadas"

    # Tabela de fontes
    linhas_fontes = ""
    for nome, dados in auditoria["fontes"].items():
        tem_prob = bool(dados["falsos_positivos"] or dados["falsos_negativos"])
        cor_linha = "#fef9e7" if tem_prob else "#f9f9f9"
        icone = "⚠" if tem_prob else "✓"
        cor_icone = "#e74c3c" if tem_prob else "#27ae60"
        linhas_fontes += f"""
        <tr style="background:{cor_linha}">
          <td style="padding:8px 12px;border-bottom:1px solid #eee;font-weight:500">
            <span style="color:{cor_icone};margin-right:6px">{icone}</span>{nome}
          </td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:center">{dados['total']}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:center;color:#27ae60;font-weight:600">{dados['aceitos']}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:center;color:#e67e22">{dados['rejeitados']}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:center">{dados['taxa_rejeicao_pct']}%</td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:center;color:#e74c3c;font-weight:600">{len(dados['falsos_positivos'])}</td>
          <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:center;color:#8e44ad;font-weight:600">{len(dados['falsos_negativos'])}</td>
        </tr>"""

    # Seção de anomalias detalhadas
    secao_anomalias = ""
    if tem_anomalias:
        for nome in resumo["fontes_com_anomalias"]:
            dados = auditoria["fontes"][nome]
            if dados["falsos_positivos"]:
                itens_fp = "".join(
                    f'<li style="margin:4px 0"><a href="{i["link"]}" style="color:#c0392b">{i["titulo"]}</a>'
                    f'<br><small style="color:#888">{i["suspeita"]}</small></li>'
                    for i in dados["falsos_positivos"]
                )
                secao_anomalias += f"""
                <div style="background:#fef5f5;border-left:4px solid #e74c3c;padding:12px 16px;margin:12px 0;border-radius:4px">
                  <strong style="color:#c0392b">Falsos positivos — {nome}</strong>
                  <p style="color:#666;font-size:13px;margin:4px 0">Itens aceitos que parecem editoriais:</p>
                  <ul style="margin:8px 0;padding-left:20px;font-size:13px">{itens_fp}</ul>
                </div>"""
            if dados["falsos_negativos"]:
                itens_fn = "".join(
                    f'<li style="margin:4px 0"><a href="{i["link"]}" style="color:#6c3483">{i["titulo"]}</a>'
                    f'<br><small style="color:#888">Rejeitado por: {i["motivo_rejeicao"]} | {i["suspeita"]}</small></li>'
                    for i in dados["falsos_negativos"]
                )
                secao_anomalias += f"""
                <div style="background:#f5eef8;border-left:4px solid #8e44ad;padding:12px 16px;margin:12px 0;border-radius:4px">
                  <strong style="color:#6c3483">Falsos negativos — {nome}</strong>
                  <p style="color:#666;font-size:13px;margin:4px 0">Casting calls reais que podem ter sido filtrados incorretamente:</p>
                  <ul style="margin:8px 0;padding-left:20px;font-size:13px">{itens_fn}</ul>
                </div>"""
    else:
        secao_anomalias = """
        <div style="background:#eafaf1;border-left:4px solid #27ae60;padding:12px 16px;margin:12px 0;border-radius:4px">
          <strong style="color:#1e8449">Nenhuma anomalia detectada</strong>
          <p style="color:#666;font-size:13px;margin:4px 0">Todos os filtros estão funcionando dentro dos parâmetros esperados.</p>
        </div>"""

    # Histórico de tendência
    secao_historico = ""
    if historico_resumo:
        linhas_hist = "".join(
            f'<tr><td style="padding:6px 12px;border-bottom:1px solid #eee">{h["data"]}</td>'
            f'<td style="padding:6px 12px;border-bottom:1px solid #eee;text-align:center;color:#27ae60">{h["aceitos"]}</td>'
            f'<td style="padding:6px 12px;border-bottom:1px solid #eee;text-align:center;color:#e67e22">{h["rejeitados"]}</td>'
            f'<td style="padding:6px 12px;border-bottom:1px solid #eee;text-align:center;color:{"#e74c3c" if h["anomalias"] > 0 else "#27ae60"}">{h["anomalias"]}</td></tr>'
            for h in historico_resumo
        )
        secao_historico = f"""
        <h3 style="color:#2c3e50;font-size:15px;margin:20px 0 10px">Histórico recente</h3>
        <table style="width:100%;border-collapse:collapse;font-size:13px">
          <thead>
            <tr style="background:#f0f0f0">
              <th style="padding:8px 12px;text-align:left">Data</th>
              <th style="padding:8px 12px;text-align:center">Aceitos</th>
              <th style="padding:8px 12px;text-align:center">Rejeitados</th>
              <th style="padding:8px 12px;text-align:center">Anomalias</th>
            </tr>
          </thead>
          <tbody>{linhas_hist}</tbody>
        </table>"""

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif">
<div style="max-width:680px;margin:24px auto;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.1)">

  <!-- Header -->
  <div style="background:linear-gradient(135deg,#1a1a2e 0%,#16213e 50%,#0f3460 100%);padding:28px 32px">
    <div style="color:#e2b96f;font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;margin-bottom:8px">
      Sistema de Casting — Auditoria de Filtros
    </div>
    <h1 style="color:#fff;font-size:22px;font-weight:700;margin:0 0 6px">
      Relatório Semanal de Eficácia
    </h1>
    <div style="color:#a0aec0;font-size:13px">{data_str} UTC</div>
  </div>

  <!-- Status geral -->
  <div style="padding:20px 32px;background:{cor_status}15;border-bottom:3px solid {cor_status}">
    <div style="font-size:16px;font-weight:700;color:{cor_status}">{texto_status}</div>
    <div style="display:flex;gap:32px;margin-top:12px;flex-wrap:wrap">
      <div><span style="font-size:28px;font-weight:800;color:#2c3e50">{resumo['total_itens']}</span>
           <div style="font-size:11px;color:#666;text-transform:uppercase;letter-spacing:1px">Total de itens</div></div>
      <div><span style="font-size:28px;font-weight:800;color:#27ae60">{resumo['total_aceitos']}</span>
           <div style="font-size:11px;color:#666;text-transform:uppercase;letter-spacing:1px">Aceitos</div></div>
      <div><span style="font-size:28px;font-weight:800;color:#e67e22">{resumo['total_rejeitados']}</span>
           <div style="font-size:11px;color:#666;text-transform:uppercase;letter-spacing:1px">Rejeitados</div></div>
      <div><span style="font-size:28px;font-weight:800;color:#e74c3c">{resumo['total_falsos_positivos']}</span>
           <div style="font-size:11px;color:#666;text-transform:uppercase;letter-spacing:1px">Falsos positivos</div></div>
      <div><span style="font-size:28px;font-weight:800;color:#8e44ad">{resumo['total_falsos_negativos']}</span>
           <div style="font-size:11px;color:#666;text-transform:uppercase;letter-spacing:1px">Falsos negativos</div></div>
    </div>
  </div>

  <!-- Corpo -->
  <div style="padding:24px 32px">

    <!-- Anomalias -->
    <h3 style="color:#2c3e50;font-size:15px;margin:0 0 12px">Anomalias detectadas</h3>
    {secao_anomalias}

    <!-- Tabela por fonte -->
    <h3 style="color:#2c3e50;font-size:15px;margin:20px 0 10px">Resultado por fonte</h3>
    <table style="width:100%;border-collapse:collapse;font-size:13px">
      <thead>
        <tr style="background:#f0f0f0">
          <th style="padding:8px 12px;text-align:left">Fonte</th>
          <th style="padding:8px 12px;text-align:center">Total</th>
          <th style="padding:8px 12px;text-align:center">Aceitos</th>
          <th style="padding:8px 12px;text-align:center">Rejeitados</th>
          <th style="padding:8px 12px;text-align:center">Taxa</th>
          <th style="padding:8px 12px;text-align:center">F. Pos.</th>
          <th style="padding:8px 12px;text-align:center">F. Neg.</th>
        </tr>
      </thead>
      <tbody>{linhas_fontes}</tbody>
    </table>

    {secao_historico}

  </div>

  <!-- Footer -->
  <div style="padding:16px 32px;background:#f8f8f8;border-top:1px solid #eee;font-size:11px;color:#999;text-align:center">
    Relatório gerado automaticamente pelo sistema de alertas de casting.
    Próxima verificação: em 7 dias.
  </div>

</div>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
# ENVIO DO EMAIL
# ─────────────────────────────────────────────────────────────────────────────

def enviar_relatorio(auditoria: Dict, historico: List[Dict]) -> bool:
    """Envia o relatório de auditoria por email."""
    gmail_user = os.environ.get("GMAIL_USER", "huddsonviana@gmail.com")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD", "")

    if not gmail_pass:
        logger.error("GMAIL_APP_PASSWORD não configurado — relatório não enviado.")
        return False

    resumo = auditoria["resumo"]
    tem_anomalias = resumo["total_falsos_positivos"] > 0 or resumo["total_falsos_negativos"] > 0
    data_str = datetime.fromisoformat(auditoria["data_auditoria"]).strftime("%d/%m/%Y")

    if tem_anomalias:
        assunto = f"[Casting] ⚠ Auditoria de Filtros — {resumo['total_falsos_positivos']} falsos positivos, {resumo['total_falsos_negativos']} falsos negativos — {data_str}"
    else:
        assunto = f"[Casting] ✓ Auditoria de Filtros — Tudo OK — {data_str}"

    html_body = _gerar_html_relatorio(auditoria, historico)

    # Versão texto simples
    texto_body = f"""AUDITORIA DE FILTROS EDITORIAIS — {data_str}

Status: {"ANOMALIAS DETECTADAS" if tem_anomalias else "Tudo OK"}

Resumo:
  Total de itens analisados: {resumo['total_itens']}
  Aceitos: {resumo['total_aceitos']}
  Rejeitados: {resumo['total_rejeitados']}
  Falsos positivos: {resumo['total_falsos_positivos']}
  Falsos negativos: {resumo['total_falsos_negativos']}

Fontes com anomalias: {', '.join(resumo['fontes_com_anomalias']) if resumo['fontes_com_anomalias'] else 'Nenhuma'}
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = assunto
    msg["From"] = gmail_user
    msg["To"] = gmail_user
    msg.attach(MIMEText(texto_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, gmail_user, msg.as_string())
        logger.info(f"Relatório enviado para {gmail_user}")
        return True
    except Exception as e:
        logger.error(f"Erro ao enviar email: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# PONTO DE ENTRADA
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Auditoria de filtros editoriais")
    parser.add_argument("--force", action="store_true", help="Forçar envio do relatório mesmo sem anomalias")
    parser.add_argument("--dry-run", action="store_true", help="Apenas imprimir o relatório, não enviar email")
    args = parser.parse_args()

    # Executar auditoria
    auditoria = executar_auditoria()
    historico = carregar_historico()

    # Salvar histórico
    if not args.dry_run:
        salvar_historico(historico, auditoria)

    # Imprimir resumo
    resumo = auditoria["resumo"]
    print(f"\n{'=' * 60}")
    print(f"AUDITORIA DE FILTROS — {datetime.fromisoformat(auditoria['data_auditoria']).strftime('%d/%m/%Y %H:%M')}")
    print(f"{'=' * 60}")
    print(f"Total de itens:     {resumo['total_itens']}")
    print(f"Aceitos:            {resumo['total_aceitos']}")
    print(f"Rejeitados:         {resumo['total_rejeitados']}")
    print(f"Falsos positivos:   {resumo['total_falsos_positivos']}")
    print(f"Falsos negativos:   {resumo['total_falsos_negativos']}")
    if resumo["fontes_com_anomalias"]:
        print(f"Fontes c/ anomalias: {', '.join(resumo['fontes_com_anomalias'])}")
    print(f"{'=' * 60}")

    # Detalhar por fonte
    for nome, dados in auditoria["fontes"].items():
        if dados["total"] > 0:
            print(f"\n{nome}: {dados['aceitos']}/{dados['total']} aceitos ({dados['taxa_rejeicao_pct']}% rejeitados)")
            if dados["falsos_positivos"]:
                print(f"  ⚠ Falsos positivos ({len(dados['falsos_positivos'])}):")
                for fp in dados["falsos_positivos"]:
                    print(f"    - {fp['titulo']}")
            if dados["falsos_negativos"]:
                print(f"  ⚠ Falsos negativos ({len(dados['falsos_negativos'])}):")
                for fn in dados["falsos_negativos"]:
                    print(f"    - {fn['titulo']} [{fn['motivo_rejeicao']}]")

    # Enviar email
    tem_anomalias = resumo["total_falsos_positivos"] > 0 or resumo["total_falsos_negativos"] > 0
    if not args.dry_run and (tem_anomalias or args.force):
        enviar_relatorio(auditoria, historico)
    elif args.dry_run:
        print("\n[dry-run] Email não enviado.")
    else:
        print("\nNenhuma anomalia detectada — email não enviado.")

    # Retornar código de saída não-zero se houver anomalias (para CI/CD)
    sys.exit(1 if tem_anomalias else 0)


if __name__ == "__main__":
    main()
