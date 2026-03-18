#!/usr/bin/env python3
"""
Monitor de oportunidades de casting, audições e seleções de elenco.

Critérios de alerta:
  - Gênero: Homem
  - Idade: Acima de 40 anos OU aparência entre 35-50 anos OU não especificado

Fontes:
  - Guia do Ator (guiadoator.com.br)
  - Elenco Digital (elencdigital.com.br)
  - Oppah (oppah.com.br)
  - Nossa Senhora do Casting (nossasenhora.com.br)
  - Castapp (castapp.com.br)
  - Open Auditions (openauditions.com)
  - Rede Globo (globo.com)
  - Rede Record (record.com.br)
  - Páginas/perfis com "casting" ou "elenco" no nome

Cada oportunidade retornada contém:
  titulo, descricao, genero, idade_minima, idade_maxima, aparencia,
  data_inscricao_inicio, data_inscricao_fim, data_teste, data_gravacao,
  cache, o_que_levar, endereco, link_inscricao, link_formulario, email_contato,
  categoria (teatro, audiovisual, navios, resorts, etc), localizacao,
  link_detalhe, fonte
"""
from __future__ import annotations

import logging
import re
import time
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
TIMEOUT = 20
SLEEP = 0.4

# ─────────────────────────────────────────────────────────────────
# Critérios de filtro
# ─────────────────────────────────────────────────────────────────

GENEROS_ALVO = ["homem", "masculino", "m", "não especificado", "qualquer", "ambos"]
IDADE_MINIMA_ALVO = 40
APARENCIA_MINIMA = 35
APARENCIA_MAXIMA = 50

# ─────────────────────────────────────────────────────────────────
# Filtro étnico/racial — excluir seleções EXCLUSIVAS para grupos incompatíveis
# ─────────────────────────────────────────────────────────────────
# Termos que, quando associados a exclusividade, indicam que a seleção
# é restrita a um grupo étnico/racial incompatível com o perfil do usuário
# (homem branco, descendente de italiano, brasileiro).
ETNIAS_EXCLUSIVAS_EXCLUIR = [
    # Raça/cor explícita
    "negro", "negra", "negros", "negras",
    "preto", "preta", "pretos", "pretas",
    "pardo", "parda", "pardos", "pardas",
    "afrodescendente", "afro-descendente", "afrobrasileiro",
    "afro-brasileiro", "afro brasileiro",
    "oriental", "orientais", "asiático", "asiática", "asiáticos",
    "indígena", "indígenas", "indigena", "indigenas",
    "quilombola", "quilombolas",
    # Termos em inglês
    "black", "african american", "afro-american",
    "asian", "indigenous", "native",
    # Termos de diversidade racial exclusiva
    "pessoas negras", "artistas negros", "atores negros",
    "cantores negros", "performers negros",
]

# Modificadores de exclusividade — a etnia só exclui quando acompanhada
# de um desses termos, indicando restrição (não mera menção).
# IMPORTANTE: evitar termos genéricos como "para" isolado para não gerar falsos positivos.
MODIFICADORES_EXCLUSIVIDADE = [
    "exclusiv",          # exclusivo, exclusivamente, exclusiva
    "somente",
    "apenas",
    "só para",           # "só para negros" (não "só" isolado)
    "restrit",           # restrito, restrita, restritos
    "destinad",          # destinado, destinada
    "voltad",            # voltado, voltada
    "prioritari",        # prioritariamente
    "obrigatoriamente",
    "unicamente",
    "only",
    "exclusively",
    "restricted to",
    "vagas para",
    "oportunidade para",
    "seleção para",
    "casting para",
    "procuramos",        # "procuramos atores negros"
    "buscamos",          # "buscamos cantores negros"
    "procura-se",
    "busca-se",
    "vaga para",
    "aberto para",       # "casting aberto para negros" (exclusivo)
    "direcionad",        # direcionado, direcionada
]


# Termos que, quando presentes na janela, ANULAM a exclusividade
# (indicam que a seleção é inclusiva, não exclusiva).
# Nota: listas de etnias só são inclusivas quando incluem "brancos" ou "caucasianos".
CONTRA_EXCLUSIVIDADE = [
    "todos", "todas", "qualquer", "qualquer perfil", "todos os perfis",
    "aberto a todos", "aberto para todos", "sem restrição",
    "independente", "independentemente",
    "inclusive", "inclusão", "inclusivo", "inclusiva",
    "e brancos",        # lista que inclui brancos = inclusivo para o usuário
    "brancos e",        # idem
    "caucasianos",      # menciona caucasianos na lista
    "anyone", "everyone", "all backgrounds", "all ethnicities",
    "todas as etnias", "todas as raças", "qualquer etnia",
]


def _excluir_por_etnia(texto: str) -> bool:
    """
    Retorna True se a oportunidade deve ser EXCLUÍDA por ser
    exclusivamente destinada a uma etnia/raça incompatível com o perfil
    do usuário (homem branco, descendente de italiano, brasileiro).

    A exclusão só ocorre quando há combinação de:
    - Termo de etnia incompatível
    - Modificador de exclusividade (somente, apenas, exclusivo, etc.)
    - E NÃO há contra-indicadores de inclusividade (todos, diversidade, etc.)

    Seleções que mencionam diversidade sem exclusividade são mantidas.
    """
    if not texto:
        return False

    t = texto.lower()

    # Verificar cada etnia incompatível
    for etnia in ETNIAS_EXCLUSIVAS_EXCLUIR:
        if etnia not in t:
            continue
        # Verificar se há modificador de exclusividade próximo (janela de 150 chars)
        idx = t.find(etnia)
        janela_inicio = max(0, idx - 150)
        janela_fim = min(len(t), idx + len(etnia) + 150)
        janela = t[janela_inicio:janela_fim]

        # Verificar se há contra-indicador de inclusividade na janela
        if any(contra in janela for contra in CONTRA_EXCLUSIVIDADE):
            continue  # É inclusivo, não excluir

        if any(mod in janela for mod in MODIFICADORES_EXCLUSIVIDADE):
            return True  # Exclusão confirmada

    return False  # Manter a oportunidade


def _extrair_perfil_completo(texto: str) -> str:
    """
    Extrai e consolida o detalhamento completo do perfil procurado:
    etnia, idioma, tipo físico, habilidades, experiência, características
    especiais e quaisquer outros requisitos mencionados na oportunidade.

    Retorna uma string formatada com os itens encontrados,
    ou string vazia se nenhum detalhe for identificado.
    """
    if not texto:
        return ""

    t = texto.lower()
    itens = []

    # ── Etnia / Cor ──
    etnias_mencoes = [
        ("branco", "Branco/Caucasiano"),
        ("caucasiano", "Caucasiano"),
        ("europeu", "Europeu"),
        ("italiano", "Italiano/Descendente"),
        ("latino", "Latino"),
        ("hispânico", "Hispânico"),
        ("negro", "Negro"),
        ("preto", "Preto"),
        ("pardo", "Pardo"),
        ("oriental", "Oriental/Asiático"),
        ("asiático", "Asiático"),
        ("indígena", "Indígena"),
        ("indigena", "Indígena"),
        ("afrodescendente", "Afrodescendente"),
        ("mestiço", "Mestiço"),
    ]
    for termo, label in etnias_mencoes:
        if termo in t:
            itens.append(f"Etnia/Cor: {label}")
            break  # Apenas o primeiro match por categoria

    # ── Idioma ──
    idiomas = [
        (r'ingl[eê]s', "Inglês"),
        (r'espanhol', "Espanhol"),
        (r'portugu[eê]s', "Português"),
        (r'italiano', "Italiano"),
        (r'franc[eê]s', "Francês"),
        (r'alem[ãa]o', "Alemão"),
        (r'mandar[ií]m|chin[eê]s', "Mandarim/Chinês"),
        (r'japon[eê]s', "Japonês"),
        (r'bilíngue|bilingue', "Bilíngue"),
        (r'fluente', "Fluência exigida"),
    ]
    idiomas_encontrados = []
    for padrao, label in idiomas:
        if re.search(padrao, t):
            idiomas_encontrados.append(label)
    if idiomas_encontrados:
        itens.append(f"Idioma: {', '.join(idiomas_encontrados)}")

    # ── Tipo físico / aparência ──
    tipos_fisicos = [
        (r'alto|alta|altura', "Alto(a)"),
        (r'baixo|baixa', "Baixo(a)"),
        (r'magro|magra|esbelto', "Magro(a)/Esbelto"),
        (r'gordo|gorda|plus size|sobrepeso', "Plus size/Sobrepeso"),
        (r'musculoso|atlético|atleti', "Atlético/Musculoso"),
        (r'barba', "Com barba"),
        (r'sem barba|barbeado', "Sem barba"),
        (r'cabelo longo', "Cabelo longo"),
        (r'cabelo curto', "Cabelo curto"),
        (r'careca|sem cabelo', "Careca"),
        (r'loiro|loira|louro', "Loiro(a)"),
        (r'moreno|morena', "Moreno(a)"),
        (r'ruivo|ruiva', "Ruivo(a)"),
        (r'olhos claros|olhos azuis|olhos verdes', "Olhos claros"),
        (r'olhos escuros|olhos castanhos', "Olhos escuros"),
        (r'tipo europeu', "Tipo europeu"),
        (r'tipo mediterrâneo|mediterraneo', "Tipo mediterrâneo"),
    ]
    for padrao, label in tipos_fisicos:
        if re.search(padrao, t):
            itens.append(f"Tipo físico: {label}")

    # ── Habilidades ──
    habilidades = [
        (r'cant[ao]r|canto|voz', "Canto/Voz"),
        (r'dan[cç][ao]r|dan[cç]a', "Dança"),
        (r'ator|atuar|atua[cç][ãa]o', "Atuação"),
        (r'musiqu[ao]|instrumento|tocar', "Música/Instrumento"),
        (r'acrobaci[ao]|acrobata', "Acrobacia"),
        (r'circo|circense', "Circo/Cirquense"),
        (r'malabar[ie]', "Malabarismo"),
        (r'teatro musical|musical', "Teatro Musical"),
        (r'dublagem|dublar', "Dublagem"),
        (r'locução|locutor', "Locução"),
        (r'comédia|humor', "Comédia/Humor"),
        (r'improvis[ao]', "Improvisação"),
        (r'stand.?up', "Stand-up"),
        (r'natação|nadar', "Natação"),
        (r'equitação|cavalgar|cavalo', "Equitação"),
        (r'lut[ao]|artes marciais|capoeira', "Artes marciais/Luta"),
    ]
    habilidades_encontradas = []
    for padrao, label in habilidades:
        if re.search(padrao, t):
            habilidades_encontradas.append(label)
    if habilidades_encontradas:
        itens.append(f"Habilidades: {', '.join(habilidades_encontradas)}")

    # ── Experiência ──
    experiencias = [
        (r'experi[eê]ncia comprov', "Experiência comprovada exigida"),
        (r'sem experi[eê]ncia|iniciante|estreante', "Sem experiência necessária"),
        (r'profissional', "Perfil profissional"),
        (r'amador', "Amador aceito"),
        (r'curriculum|currículo|portfólio|portfolio', "Currículo/Portfólio exigido"),
        (r'foto[s]?\s+(?:recente|atual|3x4)', "Foto recente exigida"),
        (r'vídeo|video\s+(?:de\s+)?apresenta[cç][ãa]o', "Vídeo de apresentação exigido"),
        (r'book\s+fotográfico|book\s+fotografico|book\s+de\s+fotos', "Book fotográfico exigido"),
    ]
    for padrao, label in experiencias:
        if re.search(padrao, t):
            itens.append(f"Requisito: {label}")

    # ── Características especiais ──
    especiais = [
        (r'pessoa[s]?\s+com\s+defici[eê]ncia|pcd', "PCD (Pessoa com Deficiência)"),
        (r'lgbtq|lgbt|trans|transgênero|não.?binário', "LGBTQIA+"),
        (r'criança|infantil', "Criança/Infantil"),
        (r'idoso|terceira\s+idade|sênior', "Idoso/Sênior"),
        (r'gestante|grávida', "Gestante"),
        (r'gêmeos|gêmeas', "Gêmeos"),
        (r'tatuagem|tatuado', "Com tatuagem"),
        (r'sem\s+tatuagem', "Sem tatuagem"),
        (r'piercing', "Com piercing"),
    ]
    for padrao, label in especiais:
        if re.search(padrao, t):
            itens.append(f"Característica: {label}")

    return " | ".join(itens) if itens else ""


# Palavras-chave para categorizar oportunidades
CATEGORIAS_TEATRO = ["teatro", "peça", "dramaturgia", "palco", "cena"]
CATEGORIAS_AUDIOVISUAL = ["filme", "série", "novela", "comercial", "videoclipe", "web", "youtube", "tiktok"]
CATEGORIAS_NAVIOS = ["navio", "cruzeiro", "marítimo", "embarcação"]
CATEGORIAS_RESORTS = ["resort", "hotel", "hospedagem", "turismo", "animador"]
CATEGORIAS_OUTRAS = ["evento", "propaganda", "publicidade", "show", "musical", "dança"]


def _extrair_idade(texto: str) -> Optional[Tuple[Optional[int], Optional[int]]]:
    """
    Extrai faixa etária do texto.
    Retorna (idade_minima, idade_maxima) ou (None, None) se não encontrar.
    """
    texto_lower = texto.lower()
    
    # Padrão: "18 a 30 anos" ou "18-30"
    m = re.search(r'(\d{1,2})\s*(?:a|até|-)\s*(\d{1,2})\s*anos', texto_lower)
    if m:
        try:
            return (int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    
    # Padrão: "acima de 40 anos" ou "maiores de 40"
    m = re.search(r'(?:acima de|maiores de|a partir de)\s*(\d{1,2})\s*anos', texto_lower)
    if m:
        try:
            idade = int(m.group(1))
            return (idade, None)
        except ValueError:
            pass
    
    # Padrão: "até 50 anos"
    m = re.search(r'até\s*(\d{1,2})\s*anos', texto_lower)
    if m:
        try:
            idade = int(m.group(1))
            return (None, idade)
        except ValueError:
            pass
    
    return (None, None)


def _extrair_aparencia(texto: str) -> Optional[Tuple[Optional[int], Optional[int]]]:
    """
    Extrai faixa de aparência do texto.
    Retorna (aparencia_minima, aparencia_maxima) ou (None, None) se não encontrar.
    """
    texto_lower = texto.lower()
    
    # Padrão: "aparentar entre 35 e 50 anos" ou "aparência 35-50"
    m = re.search(r'aparentar?\s*(?:entre)?\s*(\d{1,2})\s*(?:e|-)\s*(\d{1,2})\s*anos', texto_lower)
    if m:
        try:
            return (int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    
    # Padrão: "aparentar acima de 40"
    m = re.search(r'aparentar?\s*(?:acima de|maiores de)\s*(\d{1,2})\s*anos', texto_lower)
    if m:
        try:
            idade = int(m.group(1))
            return (idade, None)
        except ValueError:
            pass
    
    return (None, None)


def _atende_criterios_genero(texto_genero: str) -> bool:
    """
    Verifica se o gênero atende aos critérios (homem ou não especificado).

    Regras:
    - Campo vazio ou ausente: incluir (não especificado)
    - "Não especificado", "qualquer", "ambos", "todos": incluir
    - "Homem", "masculino": incluir
    - "Mulher", "feminino", "atriz" (sem mencionar homem): excluir
    - "Homens e mulheres" / "ambos os sexos": incluir (aberto para homens também)
    """
    if not texto_genero:
        return True  # Não especificado = incluir

    texto_lower = texto_genero.lower().strip()

    # Aceitar imediatamente se for explicitamente não especificado / aberto
    GENEROS_INCLUSIVOS = [
        "não especificado", "nao especificado", "qualquer", "ambos",
        "todos", "aberto", "qualquer perfil", "qualquer gênero",
        "homens e mulheres", "mulheres e homens",
        "ambos os sexos", "todos os gêneros",
    ]
    if any(x in texto_lower for x in GENEROS_INCLUSIVOS):
        return True

    # Aceitar se mencionar homem/masculino
    if any(x in texto_lower for x in ["homem", "masculino", "ator", "m ", "m,"]):
        return True

    # Excluir apenas se for EXCLUSIVAMENTE feminino (sem menção a homem)
    GENEROS_FEMININOS_EXCLUSIVOS = ["mulher", "feminino", "atriz", "atrice"]
    if any(x in texto_lower for x in GENEROS_FEMININOS_EXCLUSIVOS):
        return False

    # Qualquer outro valor não reconhecido: incluir por precaução
    return True


def _atende_criterios_idade_aparencia(oportunidade: Dict) -> bool:
    """
    Verifica se a oportunidade atende aos critérios de idade/aparência:
    - Acima de 40 anos OU
    - Aparência entre 35-50 anos OU
    - Não especificado
    """
    idade_min, idade_max = _extrair_idade(
        f"{oportunidade.get('idade_minima', '')} {oportunidade.get('idade_maxima', '')}"
    )
    aparencia_min, aparencia_max = _extrair_aparencia(oportunidade.get('aparencia', ''))
    
    # Se não há especificação de idade/aparência, incluir
    if idade_min is None and idade_max is None and aparencia_min is None and aparencia_max is None:
        return True
    
    # Critério 1: Acima de 40 anos (idade_minima >= 40 ou sem limite superior)
    if idade_min is not None and idade_min >= IDADE_MINIMA_ALVO:
        return True
    
    # Critério 2: Aparência entre 35-50 anos
    if aparencia_min is not None and aparencia_max is not None:
        # Verifica se a faixa se sobrepõe com 35-50
        if aparencia_min <= APARENCIA_MAXIMA and aparencia_max >= APARENCIA_MINIMA:
            return True
    elif aparencia_min is not None and aparencia_min >= APARENCIA_MINIMA:
        return True
    elif aparencia_max is not None and aparencia_max >= APARENCIA_MINIMA:
        return True
    
    # Critério 3: Sem limite máximo de idade (pode ser 40+)
    if idade_max is None and idade_min is not None and idade_min <= IDADE_MINIMA_ALVO:
        return True
    
    return False


def _categorizar_oportunidade(titulo: str, descricao: str) -> str:
    """Categoriza a oportunidade (teatro, audiovisual, navios, resorts, etc)."""
    texto = f"{titulo} {descricao}".lower()
    
    if any(x in texto for x in CATEGORIAS_TEATRO):
        return "Teatro"
    elif any(x in texto for x in CATEGORIAS_AUDIOVISUAL):
        return "Audiovisual"
    elif any(x in texto for x in CATEGORIAS_NAVIOS):
        return "Navios/Cruzeiros"
    elif any(x in texto for x in CATEGORIAS_RESORTS):
        return "Resorts/Hotéis"
    elif any(x in texto for x in CATEGORIAS_OUTRAS):
        return "Eventos/Outros"
    else:
        return "Geral"


def _extrair_data(texto: str, padrao: str = None) -> Optional[str]:
    """
    Extrai data do texto em formato DD/MM/YYYY.
    Se padrao for fornecido, procura especificamente por esse padrão.
    """
    if not texto:
        return None
    
    # Padrão genérico: DD/MM/YYYY ou DD/MM/YY
    m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{2,4})', texto)
    if m:
        return f"{m.group(1)}/{m.group(2)}/{m.group(3)}"
    
    # Padrão: "DD de mês de YYYY"
    meses = {
        'janeiro': '01', 'fevereiro': '02', 'março': '03', 'abril': '04',
        'maio': '05', 'junho': '06', 'julho': '07', 'agosto': '08',
        'setembro': '09', 'outubro': '10', 'novembro': '11', 'dezembro': '12'
    }
    for mes_nome, mes_num in meses.items():
        m = re.search(rf'(\d{{1,2}})\s+de\s+{mes_nome}\s+de\s+(\d{{4}})', texto, re.IGNORECASE)
        if m:
            return f"{m.group(1)}/{mes_num}/{m.group(2)}"
    
    return None


def _extrair_cache(texto: str) -> Optional[str]:
    """Extrai informação de cachê do texto."""
    if not texto:
        return None
    
    # Padrão: "R$ 500" ou "R$ 500,00" ou "500 reais"
    m = re.search(r'R\$\s*([\d.,]+)', texto, re.IGNORECASE)
    if m:
        return f"R$ {m.group(1)}"
    
    m = re.search(r'([\d.,]+)\s*reais', texto, re.IGNORECASE)
    if m:
        return f"R$ {m.group(1)}"
    
    return None


def _extrair_endereco(texto: str) -> Optional[str]:
    """Extrai endereço do texto."""
    if not texto:
        return None
    
    # Procura por padrões comuns de endereço
    # Rua/Avenida/Praça + número + complemento
    m = re.search(
        r'(?:Rua|Avenida|Av\.|Praça|Pça|Travessa|Trav\.|Alameda|Estrada|Rodovia)\s+'
        r'([^,\n]+(?:,\s*n[º°]?\s*\d+)?[^,\n]*)',
        texto,
        re.IGNORECASE
    )
    if m:
        return m.group(1).strip()
    
    return None


# ─────────────────────────────────────────────────────────────────
# Utilitários HTTP
# ─────────────────────────────────────────────────────────────────

def _get(url: str, session: requests.Session) -> Optional[str]:
    try:
        r = session.get(url, timeout=TIMEOUT, headers=HEADERS)
        r.raise_for_status()
        return r.text
    except Exception as exc:
        logger.debug(f"GET {url}: {exc}")
        return None


def _get_json(url: str, session: requests.Session) -> Optional[dict]:
    try:
        r = session.get(url, timeout=TIMEOUT, headers=HEADERS)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        logger.debug(f"GET JSON {url}: {exc}")
        return None


# ─────────────────────────────────────────────────────────────────
# Fontes externas identificadas (sites oficiais dos perfis seguidos)
# ─────────────────────────────────────────────────────────────────

FONTES_INSTAGRAM = {
    # ── CRUZEIROS / NAVIOS ─────────────────────────────────────────
    "carnivalentertainment": {"nome": "Carnival Entertainment", "url": "https://www.carnivalentertainment.com/", "categoria": "Navios/Cruzeiros"},
    "celebritycruisescasting": {"nome": "Celebrity Cruises Entertainment", "url": "https://www.celebritycruisesentertainment.com/", "categoria": "Navios/Cruzeiros"},
    "costacruisescasting": {"nome": "Costa Cruises Auditions", "url": "https://career.costacrociere.it/", "categoria": "Navios/Cruzeiros"},
    "disneyauditions": {"nome": "Disney Auditions", "url": "https://jobs.disneycareers.com/auditions", "categoria": "Navios/Cruzeiros"},
    "hollandamericacasting": {"nome": "Holland America Line", "url": "https://www.hollandamerica.com/", "categoria": "Navios/Cruzeiros"},
    "msccruisescasting": {"nome": "MSC Cruises Careers", "url": "https://careers.msccruises.com/", "categoria": "Navios/Cruzeiros"},
    "msccastingbrazil": {"nome": "MSC Cruises Casting Brazil", "url": "https://careers.msccruises.com/", "categoria": "Navios/Cruzeiros"},
    "norwegiancruisecasting": {"nome": "NCLH Shows & Experiences", "url": "https://nclhcreativestudios.com/", "categoria": "Navios/Cruzeiros"},
    "nclcasting": {"nome": "Norwegian Cruise Line Creative Studios", "url": "https://nclhcreativestudios.com/", "categoria": "Navios/Cruzeiros"},
    "royalcaribbeancareers": {"nome": "Royal Caribbean Group Careers", "url": "https://careers.royalcaribbeangroup.com/", "categoria": "Navios/Cruzeiros"},
    "royalcaribbeanentertainment": {"nome": "Royal Caribbean Productions", "url": "https://royalcaribbeanentertainment.com/", "categoria": "Navios/Cruzeiros"},
    "aidacasting": {"nome": "AIDA Casting", "url": "https://aida.de/careers/en/casting", "categoria": "Navios/Cruzeiros"},
    # ── TEATRO ─────────────────────────────────────────────────────
    "cirquedusoleil": {"nome": "Cirque du Soleil", "url": "https://www.cirquedusoleil.com/", "categoria": "Teatro"},
    "cirquedusoleilcasting": {"nome": "Cirque du Soleil Casting", "url": "https://casting.cirquedusoleil.com/", "categoria": "Teatro"},
    "disneytheatricalcasting": {"nome": "Disney Theatrical Productions", "url": "https://disneyonbroadway.com/casting/", "categoria": "Teatro"},
    "broadwaycasting": {"nome": "Broadway Casting", "url": "https://www.playbill.com/casting", "categoria": "Teatro"},
    # ── AUDIOVISUAL BRASILEIRO ──────────────────────────────────────
    "elencodigital": {"nome": "Elenco Digital", "url": "https://elencodigital.com.br/", "categoria": "Audiovisual"},
    "guiadoator": {"nome": "Guia do Ator", "url": "https://guiadoator.com.br/", "categoria": "Audiovisual"},
    "globocasting": {"nome": "Globo Captação de Talentos", "url": "https://captacao.talentosartisticos.g.globo/", "categoria": "Audiovisual"},
    "globofilmes": {"nome": "Globo Filmes", "url": "https://gshow.globo.com/cultura-pop/filmes/globo-filmes/", "categoria": "Audiovisual"},
    "a2filmesoficial": {"nome": "A2 Filmes", "url": "https://a2filmes.com.br/", "categoria": "Audiovisual"},
    "sradocasting": {"nome": "Sra. do Casting", "url": "https://www.facebook.com/sra.docasting/", "categoria": "Audiovisual"},
    "marcelaaltberg": {"nome": "Marcela Altberg", "url": "https://www.facebook.com/elenco.marcelaaltberg/", "categoria": "Audiovisual"},
    "annetrevisan_elenco": {"nome": "Anne Trevisan", "url": "https://www.annetrevisan.com/", "categoria": "Audiovisual"},
    "melina_anthis_elenco": {"nome": "Melina Anthís Youkali Elenco", "url": "https://melinaanthis.elencodigital.com.br/", "categoria": "Audiovisual"},
    "renatamedeiros_el": {"nome": "Renata Medeiros", "url": "https://renatamedeiros.elencodigital.com.br/", "categoria": "Audiovisual"},
    "betaborges_elenco": {"nome": "Beta Borges", "url": "https://betaborges.elencodigital.com.br/", "categoria": "Audiovisual"},
    "we_elenco": {"nome": "We Elenco Produções Artísticas", "url": "https://www.facebook.com/we.elenco/", "categoria": "Audiovisual"},
    "yr.elenco": {"nome": "YR Agenciamento", "url": "https://yolandarodriguesproducoes.com.br/", "categoria": "Audiovisual"},
    "dea_diretores_de_elenco": {"nome": "DEA - Diretores de Elenco Associados", "url": "http://diretoresdeelenco.com.br/", "categoria": "Audiovisual"},
    "luz.elenco": {"nome": "Felipe Luz (Luz Elenco)", "url": "https://www.facebook.com/elencoluz/?locale=pt_BR", "categoria": "Audiovisual"},
    "testedeelencobrasil": {"nome": "Teste de Elenco Brasil", "url": "https://www.facebook.com/testedeelencobrasil/", "categoria": "Audiovisual"},
    "selecaodeelenco": {"nome": "Seleção de Elenco", "url": "https://www.facebook.com/selecaodeelenco/", "categoria": "Audiovisual"},
    "elencotla": {"nome": "T.L.A. Produções Artísticas", "url": "https://tlaproducoesartisticas.com.br/", "categoria": "Audiovisual"},
    "autentica.elenco": {"nome": "Talentos Autêntica", "url": "https://www.autenticaprod.com/", "categoria": "Audiovisual"},
    "vcelenco": {"nome": "VC Elenco", "url": "https://www.facebook.com/vcelenco/", "categoria": "Audiovisual"},
    "mondicasting": {"nome": "Mondiale Agência de Elenco", "url": "http://agenciamondiale.com.br/", "categoria": "Audiovisual"},
    "ritchellyelencoefig": {"nome": "Ritchelly Elenco e Figuração", "url": "https://www.facebook.com/RITCHELLYprodutor2/", "categoria": "Audiovisual"},
    "redetvcasting": {"nome": "RedeTV! Casting", "url": "https://www.redetv.uol.com.br/", "categoria": "Audiovisual"},
    # ── CASTING GERAL ──────────────────────────────────────────────
    "projectcasting": {"nome": "Project Casting", "url": "https://projectcasting.com/", "categoria": "Geral"},
    "openauditions": {"nome": "Open Auditions", "url": "https://www.openauditions.uk/", "categoria": "Geral"},
    "ooppahoficial": {"nome": "Ooppah Soluções Artísticas", "url": "https://ooppah.com/", "categoria": "Geral"},
    "nscasting": {"nome": "Nossa Senhora do Casting", "url": "https://nscast.me/", "categoria": "Geral"},
    "castapp.oficial": {"nome": "Castapp", "url": "https://castapp.pro/", "categoria": "Geral"},
    "pesquisadeelenco": {"nome": "Pesquisa de Elenco", "url": "https://pesquisadeelenco.com/", "categoria": "Geral"},
    "elencodaraquel": {"nome": "Raquel Neves Casting", "url": "https://www.elencodaraquel.com/", "categoria": "Geral"},
    "ranierifullcasting": {"nome": "Ranieri Full Casting", "url": "https://ranierifullcasting.com.br/", "categoria": "Geral"},
    "marinicasting": {"nome": "Marini Casting", "url": "https://marinicasting.com.br/", "categoria": "Geral"},
    "mesadebooker": {"nome": "Mesa de Booker", "url": "https://mesadebooker.com.br/", "categoria": "Geral"},
    "orenda.casting": {"nome": "Orenda Casting", "url": "https://orendacasting.com.br/", "categoria": "Geral"},
    "noarcasting": {"nome": "No Ar Casting", "url": "http://noarcasting.com.br/", "categoria": "Geral"},
    "luzcastingg": {"nome": "Luz Casting", "url": "https://luzcasting.live/", "categoria": "Geral"},
    "avantecasting": {"nome": "Avante Casting", "url": "https://www.avantecasting.com.br/", "categoria": "Geral"},
    "armyagency_casting": {"nome": "Army Agency Casting", "url": "https://www.armycasting.com.br/", "categoria": "Geral"},
    "attos.casting": {"nome": "Attos Casting", "url": "https://attoscasting.com/", "categoria": "Geral"},
    "agenciavictoriacasting": {"nome": "Agência Victoria Casting", "url": "https://victoriacasting.com.br/", "categoria": "Geral"},
    "castingimperio": {"nome": "Agência Império", "url": "https://agenciaimperio.com.br", "categoria": "Geral"},
    "castinghouse_br": {"nome": "Casting House", "url": "https://castinghouse.com.br/", "categoria": "Geral"},
    "armandocasting": {"nome": "Armando Casting", "url": "https://www.facebook.com/armandocasting/", "categoria": "Geral"},
    "carlalima.casting": {"nome": "Carla Lima Produções Artísticas", "url": "https://carlalima.com.br/", "categoria": "Geral"},
    "personajes.br": {"nome": "Personajes Brasil", "url": "https://personajesbr.com/", "categoria": "Geral"},
    "pearsoncasting": {"nome": "Pearson Casting", "url": "https://www.pearsoncasting.com/", "categoria": "Geral"},
    "dobcasting": {"nome": "Debbie O'Brien Casting", "url": "https://www.debbieobrien.net/", "categoria": "Geral"},
    "erikaslama": {"nome": "Erika Slama", "url": "https://erikaslama.com.br/", "categoria": "Geral"},
    "flowcasting": {"nome": "Flow Casting", "url": "https://flow.page/flowcastingjobs", "categoria": "Geral"},
    "emmecasting": {"nome": "EMME Casting", "url": "https://www.facebook.com/emmecasting/", "categoria": "Geral"},
    "merlincastings": {"nome": "Merlin Castings", "url": "https://www.facebook.com/MerlinCastings/", "categoria": "Geral"},
    "ummacasting": {"nome": "Umma Casting", "url": "https://bio.site/ummacasting", "categoria": "Geral"},
    "nossocasting": {"nome": "Nosso Casting", "url": "https://www.facebook.com/nossocasting/", "categoria": "Geral"},
    "clubedocasting": {"nome": "Clube do Casting", "url": "https://www.facebook.com/clubedocasting/", "categoria": "Geral"},
    "santocasting": {"nome": "Santo Casting", "url": "https://www.facebook.com/santocasting/", "categoria": "Geral"},
    "leecastingelenco": {"nome": "Lee Casting", "url": "https://www.facebook.com/lee.casting/", "categoria": "Geral"},
    "etcelenco": {"nome": "Etc Elenco", "url": "https://www.facebook.com/EtcElenco/", "categoria": "Geral"},
    "me_casting": {"nome": "ME Casting Team", "url": "https://www.mecastingteam.me/", "categoria": "Geral"},
    "casting.eastwest": {"nome": "East West Entertainment Group", "url": "https://www.eastwestevents.ae/", "categoria": "Geral"},
    "ellacastingstockholm": {"nome": "Ella Casting", "url": "https://ellacasting.se/", "categoria": "Geral"},
    "nordcasting": {"nome": "Nord Casting", "url": "https://www.nordcasting.se/", "categoria": "Geral"},
    "synkcasting": {"nome": "Synk Casting", "url": "https://www.synkcasting.se/", "categoria": "Geral"},
    "lmcasting": {"nome": "LM Casting", "url": "https://www.facebook.com/lottamalmcasting/", "categoria": "Geral"},
    "universalcastingmiami": {"nome": "Universal Casting", "url": "https://www.universalcast.com/", "categoria": "Geral"},
    "yulicasting": {"nome": "Yuli Casting", "url": "https://br.linkedin.com/in/yuli-mota", "categoria": "Geral"},
    "viktoriia_talentguideagency": {"nome": "Viktoria Talent Management", "url": "https://viktoriia.management/", "categoria": "Geral"},
    "dc.casting": {"nome": "DC Casting & Produções", "url": "https://www.facebook.com/dccastiing/", "categoria": "Geral"},
    "vazprod": {"nome": "VAZ Produção e Casting", "url": "https://www.facebook.com/vazproducaoecasting/", "categoria": "Geral"},
    "agenciarenatareis": {"nome": "Agência de Casting Renata Reis", "url": "https://www.facebook.com/renata.reis.562/", "categoria": "Geral"},
    "mairareiscasting": {"nome": "Máira Reis Casting", "url": "http://www.instagram.com/mairareiscasting/", "categoria": "Geral"},
    "dii.casting": {"nome": "Dii Casting", "url": "https://www.instagram.com/dii.casting/", "categoria": "Geral"},
    "a2castinghouse": {"nome": "A2 Casting House", "url": "https://www.a2castinghouse.com/", "categoria": "Geral"},
    # ── ALIASES E HANDLES TRUNCADOS (mapeamento de handles da lista original) ──
    "cirquedusoleilca": {"nome": "Cirque du Soleil Casting", "url": "https://casting.cirquedusoleil.com/", "categoria": "Teatro"},
    "agenciavictoriacast": {"nome": "Agência Victoria Casting", "url": "https://victoriacasting.com.br/", "categoria": "Geral"},
    "miss.casting_isabe": {"nome": "Isabel Lobo Casting", "url": "https://isabellobo.elencodigital.com.br/", "categoria": "Audiovisual"},
    "miss.casting_isabellobo": {"nome": "Isabel Lobo Casting", "url": "https://isabellobo.elencodigital.com.br/", "categoria": "Audiovisual"},
    "pro_casting_and_c": {"nome": "Pro Casting and Coaching", "url": "https://www.procasting.se/", "categoria": "Geral"},
    "studiiocasting": {"nome": "Studiio Casting", "url": "https://www.instagram.com/studiiocasting/", "categoria": "Audiovisual"},
    "viktoriia_talentguid": {"nome": "Viktoria Talent Management", "url": "https://viktoriia.management/", "categoria": "Geral"},
    "melina_anthis_elen": {"nome": "Melina Anthís Youkali Elenco", "url": "https://melinaanthis.elencodigital.com.br/", "categoria": "Audiovisual"},
    "dea_diretores_de_": {"nome": "DEA - Diretores de Elenco Associados", "url": "http://diretoresdeelenco.com.br/", "categoria": "Audiovisual"},
    "ag_oknelle_elenco": {"nome": "Ag. Oknelle Elenco", "url": "https://www.instagram.com/ag_oknelle_elenco/", "categoria": "Audiovisual"},
    "pauloparedes.elenco": {"nome": "Paulo Paredes Elenco", "url": "https://www.instagram.com/pauloparedes.elenco/", "categoria": "Audiovisual"},
    # ── PERFIS REVISITADOS – links extraídos da bio ──
    # @patriciaprodutorad: perfil removido/inexistente no Instagram
    # @a2.casting: agência de marketing de influência (Be Around), não relevante para casting de atores
    # @castingbrasil, @telenovelascasting, @recordcasting, @sbttv_casting, @bandcasting: perfis inexistentes
    # @netflixbrasil_casting, @amazonprimecasting, @warnercasting, @universalpicturescasting: perfis inexistentes
    # @zarpellon_casting, @casadacasting, @elencobrasil, @dreamworksonboard: perfis inexistentes
    "castingrm": {"nome": "Casting RM (Rodrigo Morais)", "url": "https://www.instagram.com/castingrm/", "categoria": "Teatro"},
    "prooo_dutora": {"nome": "Prooo - Diretora de Elenco", "url": "https://elenconarede.com.br/", "categoria": "Audiovisual"},
    "elenconarede": {"nome": "Elenco na Rede", "url": "https://elenconarede.com.br/", "categoria": "Audiovisual"},
    "elenconegro": {"nome": "Elenco Negro", "url": "https://www.elenconegro.com.br/", "categoria": "Audiovisual"},
    "casting.brasil": {"nome": "Casting Brasil", "url": "https://www.instagram.com/casting.brasil/", "categoria": "Audiovisual"},
    "sixflags_casting": {"nome": "Six Flags Live Entertainment", "url": "https://jobs.sixflags.com/live-entertainment", "categoria": "Teatro"},
    "officialdisneyauditions": {"nome": "Disney Auditions (oficial)", "url": "https://jobs.disneycareers.com/auditions", "categoria": "Teatro"},
    "hbocasting": {"nome": "HBO Casting", "url": "https://www.backstage.com/casting/open-casting-calls/hbo/", "categoria": "Audiovisual"},
    "lm.casting": {"nome": "LM Casting", "url": "https://www.facebook.com/lottamalmcasting/", "categoria": "Geral"},
    # ── TV ABERTA BRASIL ──
    "sbt_casting": {"nome": "SBT Elenco", "url": "https://elenco.tvsbt.com.br/", "categoria": "Audiovisual"},
    "band_casting": {"nome": "Band Casting", "url": "https://band.jobs.recrut.ai/apply/UNA04Q", "categoria": "Audiovisual"},
    "redetv_casting": {"nome": "RedeTV! Talentos Brilhantes", "url": "https://www.talentosbrilhantes.com.br/cadastro/", "categoria": "Audiovisual"},
    "tvcultura_talentos": {"nome": "TV Cultura Banco de Talentos", "url": "https://tvcultura.com.br/bancodetalentos/", "categoria": "Audiovisual"},
    "tvbrasil_selecao": {"nome": "TV Brasil / EBC Seleção", "url": "https://selecao.tvbrasil.ebc.com.br/", "categoria": "Audiovisual"},
    # ── STREAMING / GLOBOPLAY ──
    "globoplay_talentos": {"nome": "Globoplay / Talentos Artísticos Globo", "url": "https://captacao.talentosartisticos.g.globo/home", "categoria": "Audiovisual"},
    # ── AGÊNCIAS DE TALENTOS ──
    "agenciafivecasting": {"nome": "Five Casting", "url": "https://fivecasting.com.br/", "categoria": "Audiovisual"},
    "agrain": {"nome": "Aline Grain Agência", "url": "https://www.alinegrain.com.br/", "categoria": "Audiovisual"},
    "fernandaribasmanagement": {"nome": "Fernanda Ribas Management", "url": "https://fernandaribas.com/", "categoria": "Audiovisual"},
    "glossmodel": {"nome": "Agência Gloss", "url": "https://glossmodel.com.br/", "categoria": "Geral"},
    "infinity.brazil": {"nome": "Infinity Brazil (Cruzeiros)", "url": "https://infinity-brazil.com.br/pt/", "categoria": "Navios/Cruzeiros"},
    "latinwe": {"nome": "Latin World Entertainment", "url": "https://www.latinwe.com/", "categoria": "Audiovisual"},
    "lmaproducoes": {"nome": "Grupo LMA Produções", "url": "https://www.lmaproducoes.com.br/", "categoria": "Audiovisual"},
    "voxtalents": {"nome": "Vox Talents", "url": "https://www.voxtalents.com/", "categoria": "Audiovisual"},
}


def scrape_fonte_generica(url: str, nome_fonte: str, categoria: str,
                          session: requests.Session) -> List[Dict]:
    """
    Scraper genérico para sites de casting.
    Extrai oportunidades de qualquer URL com estrutura HTML padrão.
    """
    oportunidades = []
    try:
        html = _get(url, session)
        if not html:
            return oportunidades

        soup = BeautifulSoup(html, "html.parser")

        # Remover scripts, estilos e navegação
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        # Estratégia 1: blocos com palavras-chave de casting
        candidatos = soup.find_all(
            lambda t: t.name in ["article", "div", "li", "section"]
            and any(kw in (t.get_text(" ", strip=True).lower())
                    for kw in ["casting", "audição", "seleção", "elenco",
                               "ator", "atriz", "cantor", "performer",
                               "audition", "open call", "teste"])
            and len(t.get_text(strip=True)) > 50
        )

        vistos = set()
        for item in candidatos[:30]:  # limitar para não sobrecarregar
            titulo_tag = item.find(["h1", "h2", "h3", "h4", "a"])
            if not titulo_tag:
                continue
            titulo = titulo_tag.get_text(strip=True)[:200]
            if not titulo or titulo in vistos:
                continue
            vistos.add(titulo)

            texto_completo = item.get_text(" ", strip=True)
            link_tag = item.find("a", href=True)
            link = ""
            if link_tag:
                href = link_tag["href"]
                if href.startswith("http"):
                    link = href
                elif href.startswith("/"):
                    from urllib.parse import urlparse
                    base = urlparse(url)
                    link = f"{base.scheme}://{base.netloc}{href}"

            oportunidade = {
                "titulo": titulo,
                "descricao": texto_completo[:500],
                "genero": "",
                "idade_minima": "",
                "idade_maxima": "",
                "aparencia": "",
                "data_inscricao_inicio": "",
                "data_inscricao_fim": _extrair_data(texto_completo) or "",
                "data_teste": "",
                "data_gravacao": "",
                "cache": _extrair_cache(texto_completo) or "",
                "o_que_levar": "",
                "endereco": _extrair_endereco(texto_completo) or "",
                "link_inscricao": link,
                "link_formulario": "",
                "email_contato": "",
                "categoria": categoria,
                "localizacao": "",
                "link_detalhe": link,
                "fonte": nome_fonte,
            }

            t = texto_completo.lower()
            emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', texto_completo)
            if emails:
                oportunidade["email_contato"] = emails[0]

            if any(x in t for x in ["homem", "masculino", "male", "man", "men"]):
                oportunidade["genero"] = "Homem"
            elif any(x in t for x in ["mulher", "feminino", "female", "woman", "women"]):
                oportunidade["genero"] = "Mulher"
            else:
                oportunidade["genero"] = "Não especificado"

            oportunidades.append(oportunidade)

        time.sleep(SLEEP)
    except Exception as e:
        logger.debug(f"Erro ao scrape {nome_fonte}: {e}")

    return oportunidades


# ─────────────────────────────────────────────────────────────────
# Scrapers por fonte
# ─────────────────────────────────────────────────────────────────

def scrape_guia_do_ator(session: requests.Session) -> List[Dict]:
    """Scrape de oportunidades do Guia do Ator."""
    oportunidades = []
    try:
        url = "https://www.guiadoator.com.br/casting"
        html = _get(url, session)
        if not html:
            return oportunidades
        
        soup = BeautifulSoup(html, "html.parser")
        
        # Procurar por elementos de casting (estrutura pode variar)
        for item in soup.find_all(class_=re.compile(r"casting|opportunity|oportunidade", re.IGNORECASE)):
            titulo = item.find(class_=re.compile(r"title|titulo|name", re.IGNORECASE))
            descricao = item.find(class_=re.compile(r"description|descricao|details", re.IGNORECASE))
            
            if titulo:
                oportunidade = {
                    "titulo": titulo.get_text(strip=True),
                    "descricao": descricao.get_text(strip=True) if descricao else "",
                    "genero": "",
                    "idade_minima": "",
                    "idade_maxima": "",
                    "aparencia": "",
                    "data_inscricao_inicio": "",
                    "data_inscricao_fim": "",
                    "data_teste": "",
                    "data_gravacao": "",
                    "cache": "",
                    "o_que_levar": "",
                    "endereco": "",
                    "link_inscricao": "",
                    "link_formulario": "",
                    "email_contato": "",
                    "categoria": _categorizar_oportunidade(titulo.get_text(strip=True), descricao.get_text(strip=True) if descricao else ""),
                    "localizacao": "",
                    "link_detalhe": item.find("a")["href"] if item.find("a") else "",
                    "fonte": "Guia do Ator"
                }
                
                # Extrair informações adicionais
                texto_completo = f"{oportunidade['titulo']} {oportunidade['descricao']}"
                oportunidade["cache"] = _extrair_cache(texto_completo) or ""
                oportunidade["endereco"] = _extrair_endereco(texto_completo) or ""
                
                # Extrair datas
                oportunidade["data_inscricao_fim"] = _extrair_data(texto_completo) or ""
                
                # Extrair emails
                emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', texto_completo)
                if emails:
                    oportunidade["email_contato"] = emails[0]
                
                # Extrair gênero
                if "homem" in texto_completo.lower() or "masculino" in texto_completo.lower():
                    oportunidade["genero"] = "Homem"
                elif "mulher" in texto_completo.lower() or "feminino" in texto_completo.lower():
                    oportunidade["genero"] = "Mulher"
                else:
                    oportunidade["genero"] = "Não especificado"
                
                oportunidades.append(oportunidade)
        
        time.sleep(SLEEP)
    except Exception as e:
        logger.debug(f"Erro ao scrape Guia do Ator: {e}")
    
    return oportunidades


def scrape_elenco_digital(session: requests.Session) -> List[Dict]:
    """Scrape de oportunidades do Elenco Digital."""
    oportunidades = []
    try:
        url = "https://www.elencdigital.com.br"
        html = _get(url, session)
        if not html:
            return oportunidades
        
        soup = BeautifulSoup(html, "html.parser")
        
        # Procurar por elementos de casting
        for item in soup.find_all(class_=re.compile(r"casting|job|opportunity", re.IGNORECASE)):
            titulo = item.find(["h2", "h3", "h4"])
            
            if titulo:
                oportunidade = {
                    "titulo": titulo.get_text(strip=True),
                    "descricao": item.get_text(strip=True),
                    "genero": "",
                    "idade_minima": "",
                    "idade_maxima": "",
                    "aparencia": "",
                    "data_inscricao_inicio": "",
                    "data_inscricao_fim": "",
                    "data_teste": "",
                    "data_gravacao": "",
                    "cache": "",
                    "o_que_levar": "",
                    "endereco": "",
                    "link_inscricao": "",
                    "link_formulario": "",
                    "email_contato": "",
                    "categoria": _categorizar_oportunidade(titulo.get_text(strip=True), item.get_text(strip=True)),
                    "localizacao": "",
                    "link_detalhe": item.find("a")["href"] if item.find("a") else "",
                    "fonte": "Elenco Digital"
                }
                
                texto_completo = f"{oportunidade['titulo']} {oportunidade['descricao']}"
                oportunidade["cache"] = _extrair_cache(texto_completo) or ""
                oportunidade["endereco"] = _extrair_endereco(texto_completo) or ""
                oportunidade["data_inscricao_fim"] = _extrair_data(texto_completo) or ""
                
                emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', texto_completo)
                if emails:
                    oportunidade["email_contato"] = emails[0]
                
                if "homem" in texto_completo.lower() or "masculino" in texto_completo.lower():
                    oportunidade["genero"] = "Homem"
                elif "mulher" in texto_completo.lower() or "feminino" in texto_completo.lower():
                    oportunidade["genero"] = "Mulher"
                else:
                    oportunidade["genero"] = "Não especificado"
                
                oportunidades.append(oportunidade)
        
        time.sleep(SLEEP)
    except Exception as e:
        logger.debug(f"Erro ao scrape Elenco Digital: {e}")
    
    return oportunidades


def scrape_oppah(session: requests.Session) -> List[Dict]:
    """Scrape de oportunidades do Oppah."""
    oportunidades = []
    try:
        url = "https://www.oppah.com.br"
        html = _get(url, session)
        if not html:
            return oportunidades
        
        soup = BeautifulSoup(html, "html.parser")
        
        for item in soup.find_all(class_=re.compile(r"casting|opportunity|job", re.IGNORECASE)):
            titulo = item.find(["h2", "h3", "a"])
            
            if titulo:
                oportunidade = {
                    "titulo": titulo.get_text(strip=True),
                    "descricao": item.get_text(strip=True),
                    "genero": "",
                    "idade_minima": "",
                    "idade_maxima": "",
                    "aparencia": "",
                    "data_inscricao_inicio": "",
                    "data_inscricao_fim": "",
                    "data_teste": "",
                    "data_gravacao": "",
                    "cache": "",
                    "o_que_levar": "",
                    "endereco": "",
                    "link_inscricao": "",
                    "link_formulario": "",
                    "email_contato": "",
                    "categoria": _categorizar_oportunidade(titulo.get_text(strip=True), item.get_text(strip=True)),
                    "localizacao": "",
                    "link_detalhe": item.find("a")["href"] if item.find("a") else "",
                    "fonte": "Oppah"
                }
                
                texto_completo = f"{oportunidade['titulo']} {oportunidade['descricao']}"
                oportunidade["cache"] = _extrair_cache(texto_completo) or ""
                oportunidade["endereco"] = _extrair_endereco(texto_completo) or ""
                oportunidade["data_inscricao_fim"] = _extrair_data(texto_completo) or ""
                
                emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', texto_completo)
                if emails:
                    oportunidade["email_contato"] = emails[0]
                
                if "homem" in texto_completo.lower() or "masculino" in texto_completo.lower():
                    oportunidade["genero"] = "Homem"
                elif "mulher" in texto_completo.lower() or "feminino" in texto_completo.lower():
                    oportunidade["genero"] = "Mulher"
                else:
                    oportunidade["genero"] = "Não especificado"
                
                oportunidades.append(oportunidade)
        
        time.sleep(SLEEP)
    except Exception as e:
        logger.debug(f"Erro ao scrape Oppah: {e}")
    
    return oportunidades


def buscar_casting(enriquecer_detalhes: bool = False, max_enriquecimento: int = 30) -> Tuple[List[Dict], List[str]]:
    """
    Busca oportunidades de casting de múltiplas fontes e filtra por critérios.

    Retorna:
        (lista_de_oportunidades, lista_de_erros)
    """
    oportunidades = []
    erros = []
    
    session = requests.Session()
    
    logger.info("Buscando casting no Guia do Ator...")
    try:
        oportunidades.extend(scrape_guia_do_ator(session))
    except Exception as e:
        erros.append(f"Guia do Ator: {str(e)[:100]}")
    
    logger.info("Buscando casting no Elenco Digital...")
    try:
        oportunidades.extend(scrape_elenco_digital(session))
    except Exception as e:
        erros.append(f"Elenco Digital: {str(e)[:100]}")
    
    logger.info("Buscando casting no Oppah...")
    try:
        oportunidades.extend(scrape_oppah(session))
    except Exception as e:
        erros.append(f"Oppah: {str(e)[:100]}")

    # Executar autodescoberta de novos perfis
    try:
        from scripts.autodescoberta import executar_autodescoberta
        fontes_descobertas = executar_autodescoberta(FONTES_INSTAGRAM)
        logger.info(f"Autodescoberta: {len(fontes_descobertas)} fonte(s) no banco de descobertas")
    except Exception as e:
        fontes_descobertas = {}
        logger.warning(f"Autodescoberta falhou (não crítico): {e}")

    # Combinar fontes fixas com fontes descobertas automaticamente
    todas_fontes = {**FONTES_INSTAGRAM, **fontes_descobertas}

    # Buscar em todas as fontes externas identificadas (sites dos perfis seguidos + descobertas)
    logger.info(f"Buscando em {len(todas_fontes)} fontes externas identificadas...")
    for handle, info in todas_fontes.items():
        nome = info["nome"]
        url  = info["url"]
        cat  = info["categoria"]
        try:
            resultados = scrape_fonte_generica(url, nome, cat, session)
            if resultados:
                logger.info(f"  {nome}: {len(resultados)} oportunidade(s)")
            oportunidades.extend(resultados)
        except Exception as e:
            erros.append(f"{nome}: {str(e)[:80]}")

    # Filtrar por critérios
    oportunidades_filtradas = []
    for opp in oportunidades:
        # Verificar gênero
        if not _atende_criterios_genero(opp.get("genero", "")):
            continue

        # Verificar idade/aparência
        if not _atende_criterios_idade_aparencia(opp):
            continue

        # Verificar exclusão étnica (excluir seleções exclusivas para etnias incompatíveis)
        texto_completo = f"{opp.get('titulo', '')} {opp.get('descricao', '')}"
        if _excluir_por_etnia(texto_completo):
            logger.debug(f"Excluído por etnia exclusiva: {opp.get('titulo', '')[:60]}")
            continue

        # Enriquecer com detalhamento completo do perfil procurado
        perfil = _extrair_perfil_completo(texto_completo)
        if perfil:
            opp["perfil_procurado"] = perfil

        oportunidades_filtradas.append(opp)
    
    logger.info(f"Total de oportunidades encontradas: {len(oportunidades)}")
    logger.info(f"Total após filtro: {len(oportunidades_filtradas)}")
    
    return oportunidades_filtradas, erros


def filtrar_novas_oportunidades(
    oportunidades: List[Dict],
    historico: Dict
) -> Tuple[List[Dict], Dict]:
    """
    Filtra apenas oportunidades novas (não alertadas antes).
    
    Retorna:
        (lista_de_novas_oportunidades, historico_atualizado)
    """
    novas = []
    historico_atualizado = historico.copy()
    
    for opp in oportunidades:
        # Gerar ID único para a oportunidade
        opp_id = f"{opp.get('titulo', '')}|{opp.get('fonte', '')}|{opp.get('link_detalhe', '')}"
        opp_id_hash = str(hash(opp_id))
        
        if opp_id_hash not in historico_atualizado:
            novas.append(opp)
            historico_atualizado[opp_id_hash] = {
                "titulo": opp.get("titulo", ""),
                "fonte": opp.get("fonte", ""),
                "data_alerta": str(__import__('datetime').date.today())
            }
    
    return novas, historico_atualizado


def formatar_email_casting(oportunidades: List[Dict], erros: List[str]) -> str:
    """Formata o email de alerta de novas oportunidades de casting."""
    from datetime import date
    hoje = date.today().strftime("%d/%m/%Y")
    
    linhas = [
        f"ALERTA DE OPORTUNIDADES DE CASTING/AUDIÇÕES — {hoje}",
        f"Perfil: Homem | Branco/Caucasiano | Descendente de italiano | Fala português, inglês e espanhol",
        f"Critérios de idade: Acima de 40 anos OU aparência 35-50 anos OU não especificado",
        f"Excluídas: seleções exclusivas para negros, pardos, orientais, indígenas ou outras etnias incompatíveis",
        f"Total de oportunidades novas encontradas: {len(oportunidades)}",
        "=" * 80,
        "",
    ]
    
    if not oportunidades:
        linhas.append("Nenhuma oportunidade nova encontrada hoje que atenda aos critérios.")
        linhas.append("")
    else:
        # Agrupar por categoria
        por_categoria = {}
        for opp in oportunidades:
            cat = opp.get("categoria", "Geral")
            if cat not in por_categoria:
                por_categoria[cat] = []
            por_categoria[cat].append(opp)
        
        for categoria in sorted(por_categoria.keys()):
            linhas.append(f"\n{'=' * 80}")
            linhas.append(f"CATEGORIA: {categoria}")
            linhas.append(f"{'=' * 80}\n")
            
            for i, opp in enumerate(por_categoria[categoria], 1):
                linhas.append(f"[{categoria[0]}{i}] {opp.get('titulo', 'Sem título')}")
                linhas.append("-" * 75)
                
                descricao = opp.get("descricao", "")
                if descricao:
                    linhas.append(f"  Descrição      : {descricao[:200]}")
                
                genero = opp.get("genero", "")
                if genero:
                    linhas.append(f"  Gênero         : {genero}")
                
                idade_min = opp.get("idade_minima", "")
                idade_max = opp.get("idade_maxima", "")
                if idade_min or idade_max:
                    idade_str = f"{idade_min} a {idade_max}" if idade_min and idade_max else (idade_min or idade_max)
                    linhas.append(f"  Idade          : {idade_str}")
                
                aparencia = opp.get("aparencia", "")
                if aparencia:
                    linhas.append(f"  Aparência      : {aparencia}")

                perfil_procurado = opp.get("perfil_procurado", "")
                if perfil_procurado:
                    # Exibir cada item do perfil em linha separada para facilitar leitura
                    itens_perfil = perfil_procurado.split(" | ")
                    linhas.append(f"  Perfil procurado:")
                    for item in itens_perfil:
                        linhas.append(f"    • {item}")
                
                cache = opp.get("cache", "")
                if cache:
                    linhas.append(f"  Cachê          : {cache}")
                
                o_que_levar = opp.get("o_que_levar", "")
                if o_que_levar:
                    linhas.append(f"  O que levar    : {o_que_levar}")
                
                endereco = opp.get("endereco", "")
                if endereco:
                    linhas.append(f"  Endereço       : {endereco}")
                
                di_ini = opp.get("data_inscricao_inicio", "")
                di_fim = opp.get("data_inscricao_fim", "")
                if di_ini and di_fim:
                    linhas.append(f"  Inscrições     : {di_ini} a {di_fim}")
                elif di_fim:
                    linhas.append(f"  Inscrições até : {di_fim}")
                
                data_teste = opp.get("data_teste", "")
                if data_teste:
                    linhas.append(f"  Data do teste  : {data_teste}")
                
                data_gravacao = opp.get("data_gravacao", "")
                if data_gravacao:
                    linhas.append(f"  Data gravação  : {data_gravacao}")
                
                link_inscricao = opp.get("link_inscricao", "")
                if link_inscricao:
                    linhas.append(f"  Link inscrição : {link_inscricao}")
                
                link_formulario = opp.get("link_formulario", "")
                if link_formulario:
                    linhas.append(f"  Link formulário: {link_formulario}")
                
                email_contato = opp.get("email_contato", "")
                if email_contato:
                    linhas.append(f"  Email contato  : {email_contato}")
                
                localizacao = opp.get("localizacao", "")
                if localizacao:
                    linhas.append(f"  Localização    : {localizacao}")
                
                link_detalhe = opp.get("link_detalhe", "")
                if link_detalhe:
                    linhas.append(f"  Mais detalhes  : {link_detalhe}")
                
                fonte = opp.get("fonte", "")
                if fonte:
                    linhas.append(f"  Fonte          : {fonte}")
                
                linhas.append("")
    
    linhas.append("=" * 80)
    
    if erros:
        linhas.append("\nAvisos técnicos:")
        for e in erros[:10]:
            linhas.append(f"  - {e}")
        linhas.append("")
    
    linhas.append("\nEste email foi gerado automaticamente pelo sistema de alertas de casting.")
    linhas.append("Apenas oportunidades NOVAS (não alertadas anteriormente) são incluídas.")
    linhas.append("Repositório: https://github.com/contatohb/casting-alerts")
    
    return "\n".join(linhas)


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    
    oportunidades, erros = buscar_casting(enriquecer_detalhes=False)
    print(f"\nTotal filtrado: {len(oportunidades)}")
    for opp in oportunidades[:5]:
        print(f"  - {opp['titulo'][:50]} | {opp['genero']} | {opp['fonte']}")
