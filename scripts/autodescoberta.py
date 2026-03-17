"""
Módulo de autodescoberta automática de novos perfis de casting, elenco e agências.

A cada execução, este módulo:
1. Busca no Google por novos perfis do Instagram com palavras-chave relevantes
2. Extrai os handles encontrados
3. Filtra os que ainda não estão no dicionário FONTES_INSTAGRAM
4. Visita cada perfil novo para extrair o link da bio
5. Avalia a relevância do perfil para casting de atores/cantores
6. Adiciona os relevantes ao arquivo fontes_descobertas.json (persistido no repositório)

O arquivo fontes_descobertas.json é carregado pelo monitor_casting.py como fontes adicionais.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}
TIMEOUT = 15
SLEEP = 1.0

# Arquivo onde as fontes descobertas são persistidas
DATA_DIR = Path(__file__).parent.parent / "data"
FONTES_DESCOBERTAS_PATH = DATA_DIR / "fontes_descobertas.json"

# Palavras-chave de busca para autodescoberta
QUERIES_AUTODESCOBERTA = [
    'site:instagram.com "casting" atores cantores Brasil',
    'site:instagram.com "elenco" casting atores Brasil',
    'site:instagram.com "agência" OR "agencia" talentos atores cantores Brasil',
    'site:instagram.com "agency" OR "talent" atores cantores Brazil',
    'site:instagram.com "casting" cruzeiros navios performers Brasil',
    'site:instagram.com "audição" OR "audicao" atores cantores Brasil',
    'site:instagram.com "open call" performers atores Brazil',
]

# Palavras que indicam relevância para casting de atores/cantores
PALAVRAS_RELEVANTES = [
    "casting", "elenco", "audição", "audicao", "ator", "atriz", "atores",
    "cantor", "cantora", "cantores", "performer", "artista", "teatro",
    "audiovisual", "novela", "série", "filme", "musical", "cruzeiro",
    "navio", "talent", "agency", "agência", "agencia", "representação",
    "representacao", "open call", "audition", "seleção", "selecao",
]

# Palavras que indicam NÃO relevância (falsos positivos comuns)
PALAVRAS_IRRELEVANTES = [
    "moda", "fashion", "modelo", "model", "influencer", "influenciador",
    "marketing", "publicidade", "propaganda", "digital", "social media",
    "fotografia", "photography", "beleza", "beauty", "makeup", "maquiagem",
    "fitness", "academia", "personal trainer", "nutrição", "nutricao",
]


def carregar_fontes_descobertas() -> Dict:
    """Carrega o arquivo de fontes descobertas automaticamente."""
    if FONTES_DESCOBERTAS_PATH.exists():
        try:
            with open(FONTES_DESCOBERTAS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Erro ao carregar fontes descobertas: {e}")
    return {}


def salvar_fontes_descobertas(fontes: Dict) -> None:
    """Salva o arquivo de fontes descobertas."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(FONTES_DESCOBERTAS_PATH, "w", encoding="utf-8") as f:
        json.dump(fontes, f, ensure_ascii=False, indent=2)
    logger.info(f"Fontes descobertas salvas: {len(fontes)} total")


def buscar_handles_google(query: str, session: requests.Session) -> List[str]:
    """
    Busca no Google por handles do Instagram usando uma query específica.
    Extrai handles do Instagram dos resultados.
    """
    handles = []
    try:
        url = f"https://www.google.com/search?q={requests.utils.quote(query)}&num=20"
        resp = session.get(url, headers=HEADERS, timeout=TIMEOUT)
        if resp.status_code != 200:
            return handles

        soup = BeautifulSoup(resp.text, "html.parser")
        texto = soup.get_text(" ", strip=True)

        # Extrair handles do Instagram dos resultados
        # Padrão 1: instagram.com/handle/
        handles_encontrados = re.findall(
            r'instagram\.com/([a-zA-Z0-9_.]{3,40})/?',
            texto
        )
        # Padrão 2: @handle em texto
        handles_arroba = re.findall(
            r'@([a-zA-Z0-9_.]{3,40})',
            texto
        )

        for h in handles_encontrados + handles_arroba:
            h_clean = h.lower().strip().rstrip('/')
            # Filtrar handles inválidos (páginas do Instagram, não perfis)
            if h_clean not in ('p', 'reel', 'stories', 'explore', 'accounts',
                               'about', 'help', 'legal', 'privacy', 'safety',
                               'press', 'api', 'blog', 'jobs', 'directory'):
                if len(h_clean) >= 3:
                    handles.append(h_clean)

        time.sleep(SLEEP)
    except Exception as e:
        logger.debug(f"Erro na busca Google '{query[:50]}': {e}")

    return list(dict.fromkeys(handles))  # deduplicar mantendo ordem


def extrair_info_perfil_instagram(handle: str, session: requests.Session) -> Optional[Dict]:
    """
    Visita o perfil do Instagram e extrai nome, bio e links externos.
    Retorna None se o perfil não existir ou não for acessível.
    """
    url = f"https://www.instagram.com/{handle}/"
    try:
        resp = session.get(url, headers=HEADERS, timeout=TIMEOUT)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        texto = soup.get_text(" ", strip=True)

        # Verificar se o perfil existe
        if "Sorry, this page isn't available" in texto:
            return None
        if "No posts yet" in texto and "0 posts" in texto and "0 followers" in texto:
            return None  # Conta vazia/inativa

        # Extrair links externos da bio (aparecem como meta tags ou links)
        links_externos = []

        # Meta tags com URL
        for meta in soup.find_all("meta", property="og:description"):
            content = meta.get("content", "")
            urls = re.findall(r'https?://[^\s"<>]+', content)
            links_externos.extend(urls)

        # Links diretos na página
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("http") and "instagram.com" not in href:
                links_externos.append(href)

        # Extrair nome do perfil
        nome = ""
        title_tag = soup.find("title")
        if title_tag:
            # Formato: "Nome (@handle) • Instagram photos and videos"
            match = re.match(r'^(.+?)\s*\(@', title_tag.get_text())
            if match:
                nome = match.group(1).strip()

        # Extrair bio do texto
        bio = ""
        og_desc = soup.find("meta", property="og:description")
        if og_desc:
            bio = og_desc.get("content", "")

        # URL principal (primeira URL externa encontrada)
        url_site = links_externos[0] if links_externos else ""

        # Filtrar URLs do Instagram e linktrees que redirecionam
        urls_validas = [
            u for u in links_externos
            if not any(x in u for x in ["instagram.com", "facebook.com/sharer",
                                          "twitter.com/intent", "api.whatsapp"])
        ]
        url_site = urls_validas[0] if urls_validas else ""

        return {
            "handle": handle,
            "nome": nome or handle,
            "bio": bio[:300],
            "url_site": url_site,
            "texto_pagina": texto[:500],
        }

    except Exception as e:
        logger.debug(f"Erro ao acessar perfil @{handle}: {e}")
        return None


def avaliar_relevancia(info: Dict) -> tuple[bool, str]:
    """
    Avalia se um perfil é relevante para casting de atores/cantores.
    Retorna (é_relevante, categoria).
    """
    texto = (
        info.get("bio", "") + " " +
        info.get("nome", "") + " " +
        info.get("handle", "") + " " +
        info.get("texto_pagina", "")
    ).lower()

    # Contar palavras relevantes e irrelevantes
    score_relevante = sum(1 for p in PALAVRAS_RELEVANTES if p in texto)
    score_irrelevante = sum(1 for p in PALAVRAS_IRRELEVANTES if p in texto)

    # Relevante se tem pelo menos 2 palavras relevantes e não é dominado por irrelevantes
    if score_relevante < 2:
        return False, ""
    if score_irrelevante > score_relevante * 1.5:
        return False, ""

    # Determinar categoria
    if any(p in texto for p in ["cruzeiro", "navio", "cruise", "ship"]):
        categoria = "Navios/Cruzeiros"
    elif any(p in texto for p in ["teatro", "musical", "palco", "peça", "theater"]):
        categoria = "Teatro"
    elif any(p in texto for p in ["filme", "série", "novela", "audiovisual", "tv", "cinema"]):
        categoria = "Audiovisual"
    else:
        categoria = "Geral"

    return True, categoria


def descobrir_novas_fontes(fontes_existentes: Dict) -> Dict:
    """
    Executa o processo completo de autodescoberta.
    Retorna dicionário com as novas fontes descobertas (apenas as novas, não as existentes).
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    handles_candidatos = set()

    logger.info("Iniciando autodescoberta de novos perfis de casting/elenco/agências...")

    # Buscar handles via Google
    for query in QUERIES_AUTODESCOBERTA:
        novos = buscar_handles_google(query, session)
        handles_candidatos.update(novos)
        logger.debug(f"Query '{query[:50]}': {len(novos)} handles encontrados")
        time.sleep(SLEEP)

    logger.info(f"Total de handles candidatos: {len(handles_candidatos)}")

    # Filtrar os que já existem
    handles_novos = [
        h for h in handles_candidatos
        if h not in fontes_existentes
    ]
    logger.info(f"Handles novos (não catalogados): {len(handles_novos)}")

    # Visitar cada perfil novo e avaliar relevância
    novas_fontes = {}
    for handle in handles_novos:
        try:
            info = extrair_info_perfil_instagram(handle, session)
            if not info:
                continue

            relevante, categoria = avaliar_relevancia(info)
            if not relevante:
                continue

            url = info.get("url_site", "") or f"https://www.instagram.com/{handle}/"
            nome = info.get("nome", handle)

            novas_fontes[handle] = {
                "nome": nome,
                "url": url,
                "categoria": categoria,
                "descoberto_em": str(__import__("datetime").date.today()),
                "via": "autodescoberta",
            }
            logger.info(f"Nova fonte descoberta: @{handle} ({nome}) — {categoria}")
            time.sleep(SLEEP)

        except Exception as e:
            logger.debug(f"Erro ao processar @{handle}: {e}")

    logger.info(f"Novas fontes relevantes descobertas: {len(novas_fontes)}")
    return novas_fontes


def executar_autodescoberta(fontes_instagram_existentes: Dict) -> Dict:
    """
    Ponto de entrada principal do módulo.
    Recebe o dicionário FONTES_INSTAGRAM atual e retorna o dicionário
    de fontes descobertas atualizado (existentes + novas).

    Uso em monitor_casting.py:
        from autodescoberta import executar_autodescoberta
        fontes_extras = executar_autodescoberta(FONTES_INSTAGRAM)
    """
    # Carregar fontes já descobertas anteriormente
    fontes_descobertas = carregar_fontes_descobertas()

    # Combinar fontes existentes (dicionário fixo + descobertas anteriores)
    todas_existentes = {**fontes_instagram_existentes, **fontes_descobertas}

    # Descobrir novas
    novas = descobrir_novas_fontes(todas_existentes)

    if novas:
        # Adicionar às descobertas e salvar
        fontes_descobertas.update(novas)
        salvar_fontes_descobertas(fontes_descobertas)
        logger.info(f"Autodescoberta concluída: {len(novas)} nova(s) fonte(s) adicionada(s)")
    else:
        logger.info("Autodescoberta concluída: nenhuma nova fonte encontrada")

    return fontes_descobertas
