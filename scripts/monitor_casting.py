"""
monitor_casting.py — Coleta e filtragem de oportunidades de casting/audições.

Arquitetura:
  1. RSS Feeds (fontes primárias — confiáveis, sem bloqueio)
  2. Scraping estruturado de páginas que permitem acesso direto
  3. Filtros: gênero (homem), idade (40+ ou aparência 35-50 ou n/e), etnia

Perfil do usuário:
  - Homem, branco/caucasiano, descendente de italiano
  - Fala: português, inglês e espanhol
  - Faixa etária: acima de 40 anos (ou aparência 35-50)
"""

import html
import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}
TIMEOUT = 10

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTES DE FILTRAGEM
# ─────────────────────────────────────────────────────────────────────────────

# Palavras que indicam que o anúncio é uma oportunidade real de casting
KW_OPORTUNIDADE = re.compile(
    r"\b(sele[çc][aã]o|audi[çc][aã]o|casting|teste[s]?|vaga[s]?|"
    r"inscri[çc][õo]es?\s+abertas?|elenco\s+aberto|chamada\s+de\s+elenco|"
    r"open\s+call|open\s+audition|casting\s+call|"
    r"procuramos?|buscamos?|selecionamos?|contratamos?)\b",
    re.IGNORECASE,
)

# Termos étnicos incompatíveis com o perfil do usuário
ETNIAS_INCOMPATIVEIS = re.compile(
    r"\b(negr[oa]s?|pret[oa]s?|pard[oa]s?|afrodescendente[s]?|"
    r"afro-descendente[s]?|afrobrasileiro[s]?|afro-brasileiro[s]?|"
    r"oriental[is]?|asian[s]?|asiátic[oa]s?|japonês|japonesa|"
    r"chinês|chinesa|coreano[s]?|indígena[s]?|indio[s]?|índio[s]?|"
    r"quilombola[s]?|melanodérmico[s]?)\b",
    re.IGNORECASE,
)

# Modificadores que indicam exclusividade
MODIFICADORES_EXCLUSIVOS = re.compile(
    r"\b(somente|apenas|exclusivamente|exclusivo\s+para|"
    r"s[oó]\s+para|destinad[oa]\s+a[os]?|"
    r"vagas?\s+para|buscamos?\s+[a-z\s]{0,20}?(negr|pret|pard|oriental|indígena|quilombola)|"
    r"selecionamos?\s+[a-z\s]{0,20}?(negr|pret|pard|oriental|indígena|quilombola))\b",
    re.IGNORECASE,
)

# Contra-indicadores: quando a lista inclui brancos/caucasianos, não é exclusivo
CONTRA_INDICADORES = re.compile(
    r"\b(todos\s+os\s+perfis?|diversidade|inclus[aã]o|"
    r"independente\s+de\s+etnia|qualquer\s+etnia|"
    r"brancos?\s+e\s+negros?|negros?\s+e\s+brancos?|"
    r"caucasian[oa]s?\s+e|e\s+caucasian[oa]s?|"
    r"brancos?\s+e|e\s+brancos?)\b",
    re.IGNORECASE,
)

# Categorias por palavras-chave
CAT_KEYWORDS = {
    "Teatro": re.compile(
        r"\b(teatro|musical|peça|espetáculo|ópera|opereta|circo|palco|"
        r"broadway|off-broadway|temporada|dramaturgia|comédia\s+musical)\b",
        re.IGNORECASE,
    ),
    "Audiovisual": re.compile(
        r"\b(filme|cinema|série|novela|minissérie|comercial|publicidade|"
        r"propaganda|clipe|videoclipe|curta|longa|documentário|"
        r"streaming|netflix|amazon|globoplay|hbo|disney|paramount|"
        r"tv|televisão|emissora|gravação|audiovisual)\b",
        re.IGNORECASE,
    ),
    "Navios/Cruzeiros": re.compile(
        r"\b(navio|cruzeiro|cruise\s*ship|embarcação|bordo|"
        r"msc|royal\s*caribbean|carnival|norwegian|celebrity|"
        r"costa\s*cruises|princess|disney\s*cruise|holland\s*america|"
        r"cunard|viking|p&o|entertainment\s*at\s*sea)\b",
        re.IGNORECASE,
    ),
    "Resorts/Hotéis": re.compile(
        r"\b(resort|hotel|parque\s+temático|theme\s+park|spa|"
        r"entretenimento\s+hoteleiro|animação\s+cultural)\b",
        re.IGNORECASE,
    ),
}


def _inferir_categoria(titulo: str, conteudo: str) -> str:
    texto = f"{titulo} {conteudo}"
    for cat, pattern in CAT_KEYWORDS.items():
        if pattern.search(texto):
            return cat
    return "Outros"


def _atende_criterios_genero(genero: str, conteudo: str) -> bool:
    """Retorna True se a oportunidade é para homens ou não especifica gênero."""
    g = genero.lower()
    tc = conteudo.lower()

    # Padrões de exclusão no título/conteúdo
    # Feminino exclusivo no título ou conteúdo
    padroes_femininos_exclusivos = [
        r"\bfutebol\s+feminino\b",
        r"\bsele[cç][aã]o\s+de\s+atrizes?\b",
        r"\bcasting\s+feminino\b",
        r"\bapenas\s+mulheres?\b",
        r"\bsomente\s+mulheres?\b",
        r"\bexclusivo\s+para\s+mulheres?\b",
    ]
    for pat in padroes_femininos_exclusivos:
        if re.search(pat, tc):
            return False

    # Verificar "cantora(s)" sem "cantor" masculino
    if re.search(r"\bcantora[s]?\b", tc):
        if not re.search(r"\bcantores?\b", tc):  # Não há "cantor" ou "cantores" masculino
            return False

    # Padrões de exclusão por faixa etária infantil/juvenil no título
    padroes_infantis = [
        r"\bmenino[s]?\b",
        r"\bmenina[s]?\b",
        r"\bcrian[cç]a[s]?\b",
        r"\binfantil\b",
        r"\badolescente[s]?\b",
        r"\bjovem\s+perfil\s+adolescente\b",
    ]
    for pat in padroes_infantis:
        if re.search(pat, tc[:200]):  # Verificar apenas no início (título + começo do conteúdo)
            return False

    # Não especificado ou ambos: aceitar
    if g in ("não especificado", "homens e mulheres", "ambos", "todos", ""):
        return True
    # Explicitamente masculino: aceitar
    if any(x in g for x in ("homem", "masculino", "ator", "cantor")):
        return True
    # Explicitamente feminino: rejeitar
    if any(x in g for x in ("mulher", "feminino", "atriz", "cantora")):
        return False
    # Verificar no conteúdo: se menciona apenas mulheres sem homens
    tem_feminino = bool(re.search(r"\b(mulheres?|feminino|atriz|atrizes|cantora[s]?)\b", tc))
    tem_masculino = bool(re.search(r"\b(homens?|masculino|ator|atores|cantor[es]?)\b", tc))
    if tem_feminino and not tem_masculino:
        return False
    return True


def _atende_criterios_idade(faixa_etaria: str, conteudo: str) -> bool:
    """
    Retorna True se:
    - Faixa não especificada
    - Faixa inclui 40+ anos
    - Faixa de aparência inclui 35-50 anos
    - Faixa é ampla o suficiente (ex: 18-60)
    """
    if not faixa_etaria:
        return True  # Não especificado: aceitar

    # Tentar extrair números da faixa
    nums = re.findall(r"\d+", faixa_etaria)
    if not nums:
        return True

    if len(nums) == 1:
        # "A partir de X" ou "acima de X"
        idade_min = int(nums[0])
        return idade_min <= 50  # Aceitar se min <= 50

    if len(nums) >= 2:
        idade_min = int(nums[0])
        idade_max = int(nums[1])
        # Aceitar se a faixa inclui 40 anos ou se max >= 35
        if idade_max >= 35 and idade_min <= 60:
            return True
        return False

    return True


def _excluir_por_etnia(conteudo: str) -> bool:
    """
    Retorna True (excluir) se o anúncio é exclusivamente para etnias
    incompatíveis com o perfil do usuário (branco/caucasiano).
    """
    if not ETNIAS_INCOMPATIVEIS.search(conteudo):
        return False  # Sem menção a etnias incompatíveis: manter

    # Verificar se há contra-indicadores (lista inclusiva com brancos)
    if CONTRA_INDICADORES.search(conteudo):
        return False  # Lista inclusiva: manter

    # Padrões de exclusividade étnica direta no título ou conteúdo
    padroes_exclusivos_diretos = [
        r"\bascend[eê]ncia\s+(coreana|japonesa|chinesa|oriental|indígena|africana)\b",
        r"\bde\s+origem\s+(coreana|japonesa|chinesa|oriental|indígena|africana|negra)\b",
        r"\bperfil\s+(negro|negra|oriental|indígena|asiático|asiática)\b",
        r"\bDreamgirls\b",  # Musical com elenco predominantemente negro
        r"\bHamilton\b",    # Musical com casting diverso mas histórico
    ]
    for pat in padroes_exclusivos_diretos:
        if re.search(pat, conteudo, re.IGNORECASE):
            return True

    # Verificar se há modificador de exclusividade próximo ao termo étnico
    if MODIFICADORES_EXCLUSIVOS.search(conteudo):
        return True  # Exclusivo para etnia incompatível: excluir

    # Se o único perfil mencionado é de etnia incompatível (sem mencionar brancos)
    # e o conteúdo é curto (provavelmente um card estruturado), excluir
    if len(conteudo) < 500:
        etnias_encontradas = ETNIAS_INCOMPATIVEIS.findall(conteudo)
        if etnias_encontradas and not re.search(r"\b(branco[s]?|caucasian[oa]s?|european[oa]s?)", conteudo, re.IGNORECASE):
            return True

    return False


def _extrair_perfil_completo(conteudo: str) -> str:
    """Extrai o detalhamento completo do perfil procurado no anúncio."""
    campos = []

    # Gênero
    if re.search(r"\b(homens?\s+e\s+mulheres?|ambos|todos\s+os\s+gêneros?)\b", conteudo, re.I):
        campos.append("Gênero: Homens e Mulheres")
    elif re.search(r"\b(somente\s+homens?|apenas\s+homens?|masculino)\b", conteudo, re.I):
        campos.append("Gênero: Masculino")
    elif re.search(r"\b(somente\s+mulheres?|apenas\s+mulheres?|feminino)\b", conteudo, re.I):
        campos.append("Gênero: Feminino")

    # Faixa etária
    fa = re.search(
        r"(\d{1,2})\s*(?:a|ao?|[-–])\s*(\d{1,2})\s*anos?", conteudo, re.I
    )
    if fa:
        campos.append(f"Idade: {fa.group(1)}-{fa.group(2)} anos")
    else:
        fa2 = re.search(
            r"(?:a partir|acima|mais)\s+de\s+(\d{1,2})\s*anos?", conteudo, re.I
        )
        if fa2:
            campos.append(f"Idade: A partir de {fa2.group(1)} anos")

    # Etnia/cor
    etnia_m = re.search(
        r"\b(branco[s]?|caucasian[oa]s?|negr[oa]s?|pard[oa]s?|"
        r"oriental[is]?|indígena[s]?|latin[oa]s?|mediterrâne[oa]s?|"
        r"european[oa]s?|italian[oa]s?|todos\s+os\s+tipos?\s+étnicos?)\b",
        conteudo, re.I
    )
    if etnia_m:
        campos.append(f"Etnia/cor: {etnia_m.group(0)}")

    # Idioma
    idioma_m = re.findall(
        r"\b(inglês|espanhol|português|italiano|francês|alemão|"
        r"bilíngue|trilíngue|fluente\s+em\s+\w+|fluência\s+em\s+\w+)\b",
        conteudo, re.I
    )
    if idioma_m:
        campos.append(f"Idioma: {', '.join(dict.fromkeys(idioma_m))}")

    # Tipo físico
    fisico_m = re.findall(
        r"\b(alt[oa]s?|baix[oa]s?|magr[oa]s?|atletic[oa]s?|"
        r"barba|carec[oa]|loir[oa]s?|moren[oa]s?|ruiv[oa]s?|"
        r"olhos?\s+claros?|olhos?\s+escuros?|tipo\s+europe[uo]|"
        r"tipo\s+mediterrâne[uo]|tipo\s+latin[oa])\b",
        conteudo, re.I
    )
    if fisico_m:
        campos.append(f"Tipo físico: {', '.join(dict.fromkeys(fisico_m))}")

    # Habilidades
    hab_m = re.findall(
        r"\b(cant[oa]r?|canto|dança[r]?|danç[ao]|atuação|"
        r"instrumento\s+musical|\w+ista|acrobacia|circo|"
        r"dublagem|locução|comédia|improvisação|stand.?up|"
        r"natação|equitação|artes\s+marciais)\b",
        conteudo, re.I
    )
    if hab_m:
        campos.append(f"Habilidades: {', '.join(dict.fromkeys(hab_m[:5]))}")

    # Requisitos
    req_m = re.findall(
        r"\b(experiência\s+comprovada|sem\s+experiência|"
        r"currículo|portfólio|foto\s+recente|vídeo\s+de\s+apresentação|"
        r"book\s+fotográfico|fotos?\s+3x4|fotos?\s+recentes?)\b",
        conteudo, re.I
    )
    if req_m:
        campos.append(f"Requisitos: {', '.join(dict.fromkeys(req_m[:3]))}")

    return " | ".join(campos) if campos else ""


# ─────────────────────────────────────────────────────────────────────────────
# SCRAPER 1: RSS FEEDS (Guia do Ator + A Broadway é Aqui)
# ─────────────────────────────────────────────────────────────────────────────

RSS_FEEDS = [
    # ── Guia do Ator ──────────────────────────────────────────────────────────
    {
        "nome": "Guia do Ator",
        "url": "https://guiadoator.com.br/category/testes/feed/",
        "categoria_default": "Outros",
    },
    {
        "nome": "Guia do Ator",
        "url": "https://guiadoator.com.br/category/remunerados/feed/",
        "categoria_default": "Outros",
    },
    # ── A Broadway é Aqui ─────────────────────────────────────────────────────
    {
        "nome": "A Broadway é Aqui",
        "url": "https://abroadwayeaqui.com.br/category/audicoes/feed/",
        "categoria_default": "Teatro",
    },
    # ── Navio Cabaré ──────────────────────────────────────────────────────────
    {
        "nome": "Navio Cabaré",
        "url": "https://naviocabare.com.br/feed/",
        "categoria_default": "Navios/Cruzeiros",
    },
    # ── Project Casting (EUA/Internacional) ───────────────────────────────────
    {
        "nome": "Project Casting",
        "url": "https://projectcasting.com/feed/",
        "categoria_default": "Audiovisual",
    },
    # ── Pesquisa de Elenco ────────────────────────────────────────────────────
    {
        "nome": "Pesquisa de Elenco",
        "url": "https://pesquisadeelenco.com/feed/",
        "categoria_default": "Outros",
    },
    # ── Marini Casting ────────────────────────────────────────────────────────
    {
        "nome": "Marini Casting",
        "url": "https://marinicasting.com.br/feed/",
        "categoria_default": "Audiovisual",
    },
    # ── Mesa de Booker ────────────────────────────────────────────────────────
    {
        "nome": "Mesa de Booker",
        "url": "https://mesadebooker.com.br/feed/",
        "categoria_default": "Outros",
    },
    # ── Orenda Casting ────────────────────────────────────────────────────────
    {
        "nome": "Orenda Casting",
        "url": "https://orendacasting.com.br/feed/",
        "categoria_default": "Audiovisual",
    },
    # ── Victoria Casting ──────────────────────────────────────────────────────
    {
        "nome": "Victoria Casting",
        "url": "https://victoriacasting.com.br/feed/",
        "categoria_default": "Audiovisual",
    },
    # ── Carla Lima Casting ────────────────────────────────────────────────────
    {
        "nome": "Carla Lima Casting",
        "url": "https://carlalima.com.br/feed/",
        "categoria_default": "Audiovisual",
    },
    # ── Personajes Brasil ─────────────────────────────────────────────────────
    {
        "nome": "Personajes Brasil",
        "url": "https://personajesbr.com/feed/",
        "categoria_default": "Outros",
    },
    # ── Erika Slama Casting ───────────────────────────────────────────────────
    {
        "nome": "Erika Slama Casting",
        "url": "https://erikaslama.com.br/feed/",
        "categoria_default": "Audiovisual",
    },
    # ── Ella Casting (Suécia/Internacional) ───────────────────────────────────
    {
        "nome": "Ella Casting",
        "url": "https://ellacasting.se/feed/",
        "categoria_default": "Outros",
    },
    # ── Nord Casting (Suécia/Internacional) ───────────────────────────────────
    {
        "nome": "Nord Casting",
        "url": "https://www.nordcasting.se/feed/",
        "categoria_default": "Outros",
    },
    # ── TLA Produções Artísticas ──────────────────────────────────────────────
    {
        "nome": "TLA Produções Artísticas",
        "url": "https://tlaproducoesartisticas.com.br/feed/",
        "categoria_default": "Teatro",
    },
    # ── YR Agenciamento ───────────────────────────────────────────────────────
    {
        "nome": "YR Agenciamento",
        "url": "https://yolandarodriguesproducoes.com.br/feed/",
        "categoria_default": "Audiovisual",
    },
    # ── Viktoria Talent (Internacional) ───────────────────────────────────────
    {
        "nome": "Viktoria Talent",
        "url": "https://viktoriia.management/feed/",
        "categoria_default": "Navios/Cruzeiros",
    },
]

# Palavras-chave que identificam artigos editoriais/notícias (não são chamadas de elenco)
# Aplicado a TODAS as fontes para filtrar conteúdo não-oportunidade
KW_EDITORIAL = re.compile(
    r"^(?:how\s+to|tips?\s+for|guide\s+to|best\s+\w+\s+for|top\s+\d+|"  
    r"what\s+is|why\s+you|when\s+to|where\s+to|the\s+best|"  
    r"\d+\s+(?:tips?|ways?|reasons?|things?|steps?|secrets?|mistakes?)|"  
    r"industry\s+news|actor\s+news|casting\s+news|entertainment\s+news|"  
    r"how\s+actors?|acting\s+tips?|acting\s+advice|career\s+advice|"  
    r"resume\s+tips?|headshot\s+tips?|audition\s+tips?|"  
    r"everything\s+you\s+need|what\s+you\s+need|all\s+you\s+need)",
    re.IGNORECASE,
)

# Palavras-chave editoriais em português (para fontes brasileiras)
KW_EDITORIAL_PT = re.compile(
    r"^(?:como\s+(?:se\s+)?(?:preparar|fazer|ser|tornar|melhorar|conseguir)|"  
    r"dicas?\s+(?:para|de)|guia\s+(?:para|de|completo)|"  
    r"\d+\s+(?:dicas?|maneiras?|formas?|passos?|erros?|segredos?|motivos?)|"  
    r"tudo\s+(?:sobre|que\s+você)|o\s+que\s+(?:é|são|fazer)|"  
    r"por\s+que\s+(?:você|todo)|quando\s+(?:você|é\s+hora)|"  
    r"notícias?\s+do\s+(?:teatro|cinema|mercado)|"  
    r"entrevista\s+(?:com|exclusiva)|perfil\s+de\s+(?:ator|atriz|artista)|"  
    r"conheça\s+|descubra\s+|saiba\s+(?:mais|como|tudo))",
    re.IGNORECASE,
)

# Fontes que exigem filtro mais rigoroso (publicam artigos misturados com casting calls)
FONTES_COM_EDITORIAL = {"Project Casting", "Backstage"}

# Categorias a excluir do Guia do Ator (não são oportunidades de casting)
CATS_EXCLUIR_GDA = {
    "cursos", "curso", "workshop", "notícias", "noticias",
    "dica cultural", "internet", "oscar", "cinema", "música",
    "séries", "series", "geral", "novidades",
}


def _processar_item_rss(item: ET.Element, fonte: str, categoria_default: str) -> Optional[Dict]:
    """Processa um item de feed RSS e retorna um dicionário de oportunidade ou None."""
    link = (item.findtext("link") or "").strip()
    title = html.unescape((item.findtext("title") or "").strip())
    desc_raw = item.findtext("description") or ""
    desc = html.unescape(BeautifulSoup(desc_raw, "html.parser").get_text())
    cats = [c.text.lower() for c in item.findall("category") if c.text]
    pub_date = _normalizar_data(item.findtext("pubDate") or "")

    if not title or not link:
        return None

    # Filtrar categorias não relevantes (apenas para Guia do Ator)
    if fonte == "Guia do Ator":
        if any(c in CATS_EXCLUIR_GDA for c in cats):
            if not KW_OPORTUNIDADE.search(title):
                return None

    # Filtrar artigos editoriais — KW_EDITORIAL (EN) e KW_EDITORIAL_PT (PT) aplicados a TODAS as fontes
    if KW_EDITORIAL.search(title) or KW_EDITORIAL_PT.search(title):
        return None

    # Para fontes com alto volume editorial: exigir indicadores mais fortes de casting call
    if fonte in FONTES_COM_EDITORIAL:
        # Para Project Casting: exigir indicadores mais fortes de casting call
        if fonte == "Project Casting":
            # Deve ter palavras de ação direta no título ou na descrição
            kw_acao = re.compile(
                r"\b(casting\s+call|open\s+call|open\s+audition|audition\s+notice|"  
                r"now\s+casting|seeking\s+\w|looking\s+for\s+\w|"  
                r"paid\s+(?:acting|casting|role)|background\s+(?:actors?|extras?)|"  
                r"actors?\s+needed|talent\s+needed|submit\s+(?:now|today|here)|"  
                r"apply\s+(?:now|today|here)|deadline|submissions?\s+(?:open|due)|"  
                r"role[s]?\s+available|\bextras?\b|\bbackground\b)\b",
                re.IGNORECASE,
            )
            if not kw_acao.search(f"{title} {desc}"):
                return None

    # Verificar se é uma oportunidade real (KW_OPORTUNIDADE aplicado a todas as fontes)
    texto_completo = f"{title} {desc}"
    if not KW_OPORTUNIDADE.search(texto_completo):
        return None

    # Rejeitar pelo título se for exclusivamente feminino
    if re.search(r"\b(sele[çc][aã]o\s+de\s+atrizes?|casting\s+feminino|apenas\s+mulheres?)\b", title, re.IGNORECASE):
        return None

    # Buscar conteúdo completo do post (com timeout curto)
    conteudo = desc
    try:
        pr = requests.get(link, timeout=6, headers=HEADERS)
        soup_post = BeautifulSoup(pr.content, "html.parser")
        # Tentar diferentes seletores para o conteúdo principal
        for sel in ["div.td-post-content", "div.entry-content", "div.post-content", "div.content"]:
            entry = soup_post.select_one(sel)
            if entry:
                conteudo = entry.get_text(separator="\n", strip=True)
                break
    except Exception:
        pass  # Usa o conteúdo do RSS se o post não carregar

    # Aplicar filtros
    genero = _detectar_genero(f"{title} {conteudo}")
    faixa_etaria = _detectar_faixa_etaria(conteudo)

    if not _atende_criterios_genero(genero, f"{title} {conteudo}"):
        return None
    if not _atende_criterios_idade(faixa_etaria, conteudo):
        return None
    if _excluir_por_etnia(f"{title} {conteudo}"):
        return None

    # Extrair campos
    data_inscricao = _extrair_data_inscricao(conteudo)
    data_teste = _extrair_data_teste(conteudo)
    cache = _extrair_cache(conteudo)
    o_que_levar = _extrair_o_que_levar(conteudo)
    local = _extrair_local(conteudo)
    email_contato = _extrair_email(conteudo)
    link_inscricao = _extrair_link_inscricao(conteudo, link)
    categoria = _inferir_categoria(title, conteudo) if categoria_default == "Outros" else categoria_default
    perfil = _extrair_perfil_completo(conteudo)

    return {
        "id": link,
        "titulo": title,
        "descricao": desc[:500],
        "fonte": fonte,
        "categoria": categoria,
        "link": link,
        "data_publicacao": pub_date,
        "genero": genero,
        "faixa_etaria": faixa_etaria,
        "data_inscricao": data_inscricao,
        "data_teste": data_teste,
        "cache": cache,
        "o_que_levar": o_que_levar,
        "local": local,
        "email_contato": email_contato,
        "link_inscricao": link_inscricao,
        "perfil_procurado": perfil,
    }


def _buscar_rss_feeds() -> List[Dict]:
    """Busca oportunidades via feeds RSS de todas as fontes configuradas."""
    resultados: List[Dict] = []
    vistos: set = set()

    for feed_config in RSS_FEEDS:
        fonte = feed_config["nome"]
        url = feed_config["url"]
        cat_default = feed_config["categoria_default"]

        try:
            r = requests.get(url, timeout=TIMEOUT, headers=HEADERS)
            if r.status_code != 200:
                logger.warning(f"Feed {fonte} retornou {r.status_code}")
                continue

            root = ET.fromstring(r.content)
            items = root.findall(".//item")

            for item in items:
                link = (item.findtext("link") or "").strip()
                if link in vistos:
                    continue
                vistos.add(link)

                opp = _processar_item_rss(item, fonte, cat_default)
                if opp:
                    resultados.append(opp)

            logger.info(f"  {fonte} ({url.split('/')[-2]}): {len([o for o in resultados if o['fonte'] == fonte])} oportunidades")

        except Exception as e:
            logger.warning(f"Erro no feed {fonte} ({url}): {e}")

    return resultados


# ─────────────────────────────────────────────────────────────────────────────
# SCRAPER 2: ELENCO DIGITAL (cards estruturados)
# ─────────────────────────────────────────────────────────────────────────────

def _buscar_elenco_digital() -> List[Dict]:
    """Busca casting calls estruturados do Elenco Digital."""
    resultados: List[Dict] = []
    try:
        r = requests.get(
            "https://elencodigital.com.br/casting-calls",
            timeout=TIMEOUT, headers=HEADERS
        )
        if r.status_code != 200:
            return resultados

        soup = BeautifulSoup(r.content, "html.parser")
        cards = soup.find_all("div", class_="casting-call")

        for card in cards:
            # Hashtag / ID
            hashtag_el = card.find("h2", class_="casting-call__hashtag")
            hashtag = hashtag_el.get_text(strip=True) if hashtag_el else ""

            # Título
            title_el = card.find("h3", class_="casting-call__title")
            titulo = title_el.get_text(strip=True) if title_el else hashtag

            if not titulo:
                continue

            # Diretor/Produtora
            company_el = card.find("small", class_="casting-call__company")
            diretor = company_el.get_text(strip=True).replace("Por:", "").strip() if company_el else ""

            # Datas
            created_el = card.find("small", class_="casting-call_created_at")
            data_listagem = created_el.get_text(strip=True).replace("Listado em:", "").strip() if created_el else ""
            expires_el = card.find("small", class_="casting-call_expires_at")
            data_expira = expires_el.get_text(strip=True).replace("Expira em:", "").strip() if expires_el else ""

            # Tags estruturadas
            tags = card.find_all("span", class_="casting-call-tag")
            campos_tag: Dict[str, str] = {}
            for tag in tags:
                label_el = tag.find("span", class_="casting-call-tag__label")
                value_el = tag.find("span", class_="casting-call-tag__value")
                if label_el and value_el:
                    campos_tag[label_el.get_text(strip=True).lower()] = value_el.get_text(strip=True)

            genero = campos_tag.get("gênero", campos_tag.get("genero", "Não especificado"))
            faixa_etaria = campos_tag.get("faixa etária", campos_tag.get("faixa etaria", ""))
            etnia = campos_tag.get("etnia", "")
            idioma = campos_tag.get("idioma", "")
            local = campos_tag.get("localização", campos_tag.get("localizacao", ""))

            # Descrição
            desc_el = card.find("div", class_="casting-call__description")
            descricao = desc_el.get_text(strip=True) if desc_el else ""

            # Link — tenta classe específica primeiro, depois qualquer <a> no card
            link_el = card.find("a", class_="casting-call__link")
            if not link_el:
                link_el = card.find("a", href=True)   # fallback: primeiro link do card
            link = link_el.get("href", "") if link_el else ""
            if link and not link.startswith("http"):
                link = f"https://elencodigital.com.br{link}"
            # Se ainda sem link, usar URL base com âncora no hashtag
            if not link and hashtag:
                slug = hashtag.lstrip("#").strip()
                link = f"https://elencodigital.com.br/casting-calls/{slug}" if slug else ""
            # Item sem nenhum link não tem como o usuário se inscrever — pular
            if not link:
                logger.debug(f"Elenco Digital: item '{titulo}' sem link, pulando.")
                continue

            conteudo = f"{titulo} {descricao} {etnia} {idioma} {local}"

            # Filtrar etnia incompatível diretamente do campo estruturado
            if etnia and ETNIAS_INCOMPATIVEIS.search(etnia):
                if not CONTRA_INDICADORES.search(etnia):
                    continue

            # Aplicar filtros
            if not _atende_criterios_genero(genero, conteudo):
                continue
            if not _atende_criterios_idade(faixa_etaria, conteudo):
                continue
            if _excluir_por_etnia(conteudo):
                continue

            # Construir perfil
            perfil_parts = []
            if genero and genero != "Não especificado":
                perfil_parts.append(f"Gênero: {genero}")
            if faixa_etaria:
                perfil_parts.append(f"Idade: {faixa_etaria}")
            if etnia:
                perfil_parts.append(f"Etnia: {etnia}")
            if idioma:
                perfil_parts.append(f"Idioma: {idioma}")
            perfil = " | ".join(perfil_parts)

            categoria = _inferir_categoria(titulo, descricao)

            resultados.append({
                "id": link or f"ed_{hashtag}",
                "titulo": titulo,
                "descricao": descricao[:500],
                "fonte": "Elenco Digital",
                "categoria": categoria,
                "link": link,
                "data_publicacao": data_listagem,
                "genero": genero,
                "faixa_etaria": faixa_etaria,
                "data_inscricao": data_expira,
                "data_teste": "",
                "cache": campos_tag.get("cachê", campos_tag.get("cache", "")),
                "o_que_levar": "",
                "local": local,
                "email_contato": "",
                "link_inscricao": link,
                "perfil_procurado": perfil,
            })

    except Exception as e:
        logger.warning(f"Erro no Elenco Digital: {e}")

    return resultados


# ─────────────────────────────────────────────────────────────────────────────
# FUNÇÕES AUXILIARES DE EXTRAÇÃO
# ─────────────────────────────────────────────────────────────────────────────

def _detectar_genero(conteudo: str) -> str:
    tc = conteudo.lower()
    if re.search(r"\bhomens?\s+e\s+mulheres?\b|\bambos\b|\btodos\s+os\s+gêneros?\b", tc):
        return "Homens e Mulheres"
    if re.search(r"\b(somente|apenas)\s+(mulheres?|feminino)\b", tc):
        return "Mulher"
    if re.search(r"\b(somente|apenas)\s+(homens?|masculino)\b", tc):
        return "Homem"
    if re.search(r"\bmulheres?\b", tc) and not re.search(r"\bhomens?\b", tc):
        if re.search(r"\batriz|atrizes\b", tc) and not re.search(r"\bator|atores\b", tc):
            return "Mulher"
    if re.search(r"\bhomens?\b", tc) and not re.search(r"\bmulheres?\b", tc):
        return "Homem"
    return "Não especificado"


def _detectar_faixa_etaria(conteudo: str) -> str:
    # Padrão "Entre X e Y anos" (muito comum em português)
    fa_m0 = re.search(r"[Ee]ntre\s+(\d{1,2})\s+e\s+(\d{1,2})\s+anos?", conteudo, re.IGNORECASE)
    if fa_m0:
        return f"{fa_m0.group(1)} - {fa_m0.group(2)}"
    # Padrão "X a Y anos" ou "X-Y anos"
    fa_m = re.search(r"(\d{1,2})\s*(?:a|ao?|[-–])\s*(\d{1,2})\s*anos?", conteudo, re.IGNORECASE)
    if fa_m:
        return f"{fa_m.group(1)} - {fa_m.group(2)}"
    # Padrão "a partir de X anos" ou "acima de X anos"
    fa_m2 = re.search(r"(?:a partir|acima|mais)\s+de\s+(\d{1,2})\s*anos?", conteudo, re.IGNORECASE)
    if fa_m2:
        return f"A partir de {fa_m2.group(1)}"
    return ""


# Mapeamento de meses para normalização de datas
_MESES_PT = {
    "janeiro": "01", "fevereiro": "02", "março": "03", "marco": "03",
    "abril": "04", "maio": "05", "junho": "06",
    "julho": "07", "agosto": "08", "setembro": "09",
    "outubro": "10", "novembro": "11", "dezembro": "12",
    "jan": "01", "fev": "02", "mar": "03", "abr": "04",
    "mai": "05", "jun": "06", "jul": "07", "ago": "08",
    "set": "09", "out": "10", "nov": "11", "dez": "12",
}
_MESES_EN = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}


def _normalizar_data(texto: str) -> str:
    """
    Converte qualquer representação de data para dd/mm/aaaa.
    Retorna o texto original se não conseguir normalizar.
    """
    if not texto:
        return ""
    texto = texto.strip()
    import datetime
    ano_atual = datetime.date.today().year

    # Já está no formato dd/mm/aaaa
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", texto)
    if m:
        return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{m.group(3)}"

    # Formato dd/mm/aa (ano com 2 dígitos)
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2})$", texto)
    if m:
        ano = int(m.group(3))
        ano_full = 2000 + ano if ano < 50 else 1900 + ano
        return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{ano_full}"

    # Formato dd/mm (sem ano)
    m = re.match(r"^(\d{1,2})/(\d{1,2})$", texto)
    if m:
        return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{ano_atual}"

    # Formato dd-mm-aaaa ou dd-mm-aa
    m = re.match(r"^(\d{1,2})-(\d{1,2})-(\d{2,4})$", texto)
    if m:
        ano = int(m.group(3))
        if ano < 100:
            ano = 2000 + ano if ano < 50 else 1900 + ano
        return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{ano}"

    # Formato americano mm/dd/aaaa (detectado quando mês > 12 seria inválido)
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", texto)
    if m:
        mes, dia, ano = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if mes > 12 and dia <= 12:  # Provavelmente mm/dd
            return f"{dia:02d}/{mes:02d}/{ano}"

    # Formato "DD de Mês" ou "DD de Mês de AAAA" (português)
    m = re.match(
        r"^(\d{1,2})\s+de\s+([a-záàâãéêíóôõúç]+)(?:\s+de\s+(\d{4}))?$",
        texto, re.IGNORECASE
    )
    if m:
        dia = int(m.group(1))
        mes_nome = m.group(2).lower()
        ano = int(m.group(3)) if m.group(3) else ano_atual
        mes_num = _MESES_PT.get(mes_nome)
        if mes_num:
            return f"{dia:02d}/{mes_num}/{ano}"

    # Formato "Mês DD, AAAA" ou "Mês DD AAAA" (inglês)
    m = re.match(
        r"^([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?[,\s]+(\d{4})$",
        texto, re.IGNORECASE
    )
    if m:
        mes_nome = m.group(1).lower()
        dia = int(m.group(2))
        ano = int(m.group(3))
        mes_num = _MESES_EN.get(mes_nome)
        if mes_num:
            return f"{dia:02d}/{mes_num}/{ano}"

    # Formato "DD Mês AAAA" (inglês sem vírgula)
    m = re.match(
        r"^(\d{1,2})\s+([a-z]+)\s+(\d{4})$",
        texto, re.IGNORECASE
    )
    if m:
        dia = int(m.group(1))
        mes_nome = m.group(2).lower()
        ano = int(m.group(3))
        mes_num = _MESES_EN.get(mes_nome) or _MESES_PT.get(mes_nome)
        if mes_num:
            return f"{dia:02d}/{mes_num}/{ano}"

    # Formato ISO aaaa-mm-dd
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", texto)
    if m:
        return f"{int(m.group(3)):02d}/{int(m.group(2)):02d}/{m.group(1)}"

    # Formato RFC 2822 (pubDate de feeds RSS): "Thu, 02 May 2026 13:00:00 +0000"
    try:
        from email.utils import parsedate
        t = parsedate(texto)
        if t and t[0] and t[1] and t[2]:
            return f"{t[2]:02d}/{t[1]:02d}/{t[0]}"
    except Exception:
        pass

    # Não foi possível normalizar: retornar original
    return texto


def _extrair_data_inscricao(conteudo: str) -> str:
    for pat in [
        r"inscri[çc][õo]es?\s+at[eé]\s+([\d/]+(?:\s+de\s+\w+(?:\s+de\s+\d{4})?)?)",
        r"at[eé]\s+([\d]{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?)",
        r"at[eé]\s+(\d{1,2}\s+de\s+\w+(?:\s+de\s+\d{4})?)",
        r"prazo[:\s]+([^\n.]{5,40})",
        r"deadline[:\s]+([^\n.]{5,40})",
    ]:
        m = re.search(pat, conteudo, re.IGNORECASE)
        if m:
            return _normalizar_data(m.group(1).strip())
    return ""


def _extrair_data_teste(conteudo: str) -> str:
    for pat in [
        r"(?:data\s+do\s+teste|data\s+da\s+audi[çc][aã]o|data\s+da\s+sele[çc][aã]o)[:\s]+([^\n.]{5,50})",
        r"(?:teste|audi[çc][aã]o|sele[çc][aã]o)\s+(?:ser[aá]\s+)?(?:realizada?|ocorrer[aá])\s+(?:em\s+|no\s+dia\s+)?([^\n.]{5,40})",
        r"(?:dia|data)[:\s]+(\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?)",
    ]:
        m = re.search(pat, conteudo, re.IGNORECASE)
        if m:
            return _normalizar_data(m.group(1).strip())
    return ""


def _extrair_cache(conteudo: str) -> str:
    m = re.search(
        r"R\$\s*[\d.,]+(?:\s*(?:brutos?|líquidos?|por\s+dia|diária|por\s+hora|mensais?|por\s+espetáculo))?",
        conteudo, re.IGNORECASE
    )
    if m:
        return m.group(0).strip()
    # Cachê em outras moedas (para trabalhos no exterior)
    m2 = re.search(
        r"(?:USD|EUR|GBP|\$|€|£)\s*[\d.,]+(?:\s*(?:per\s+day|per\s+show|per\s+week))?",
        conteudo, re.IGNORECASE
    )
    if m2:
        return m2.group(0).strip()
    return ""


def _extrair_o_que_levar(conteudo: str) -> str:
    m = re.search(
        r"(?:levar|trazer|apresentar|enviar|trazer|preparar)[:\s]+([^\n.]{10,300})",
        conteudo, re.IGNORECASE
    )
    if m:
        return m.group(1).strip()
    # Alternativa: buscar lista de requisitos
    m2 = re.search(
        r"(?:material\s+necessário|documentos?\s+necessários?|o\s+que\s+trazer)[:\s]+([^\n.]{10,300})",
        conteudo, re.IGNORECASE
    )
    if m2:
        return m2.group(1).strip()
    return ""


def _extrair_local(conteudo: str) -> str:
    # Buscar "Local: ..." ou "Endereço: ..." mas excluir "endereço de e-mail"
    m = re.search(
        r"(?:local|endere[çc]o)\s+(?:do\s+teste|da\s+audi[çc][aã]o|da\s+sele[çc][aã]o)[:\s]+([^\n.]{10,150})",
        conteudo, re.IGNORECASE
    )
    if m:
        return m.group(1).strip()
    # Buscar "Local:" ou "Endereço:" seguido de endereço físico (não de e-mail)
    m2 = re.search(
        r"(?:^|\n)(?:local|endere[çc]o)[:\s]+([^\n.]{10,150})",
        conteudo, re.IGNORECASE | re.MULTILINE
    )
    if m2:
        val = m2.group(1).strip()
        # Excluir se capturou texto de formulário de comentários
        if any(x in val.lower() for x in ["e-mail", "email", "incorreto", "digite seu", "por favor"]):
            pass  # Ignorar
        else:
            return val
    # Buscar endereço físico direto
    m3 = re.search(
        r"(?:Rua|Avenida|Av\.|Praça|Alameda|Travessa)\s+[^,\n]+(?:,\s*n[º°]?\s*\d+)?(?:,\s*[^,\n]+)?",
        conteudo, re.IGNORECASE
    )
    if m3:
        return m3.group(0).strip()
    return ""


def _extrair_email(conteudo: str) -> str:
    m = re.search(r"[\w.+-]+@[\w.-]+\.\w{2,}", conteudo)
    return m.group(0) if m else ""


def _extrair_link_inscricao(conteudo: str, link_original: str) -> str:
    # Priorizar links de formulários conhecidos
    m = re.search(
        r"(https?://(?:docs\.google\.com/forms?|forms\.gle|bit\.ly|"
        r"typeform\.com|tally\.so|jotform\.com|surveymonkey\.com)\S+)",
        conteudo, re.IGNORECASE
    )
    if m:
        return m.group(1).strip().rstrip(".,)")
    return link_original


# ─────────────────────────────────────────────────────────────────────────────
# AUTODESCOBERTA DE NOVOS FEEDS RSS
# ─────────────────────────────────────────────────────────────────────────────

import json
from pathlib import Path
from urllib.parse import urlparse

DATA_DIR = Path(__file__).parent.parent / "data"
FEEDS_DESCOBERTOS_PATH = DATA_DIR / "feeds_descobertos.json"

# Sites candidatos a monitorar (sem RSS confirmado ainda)
# A função _verificar_rss_sites() testa estes periodicamente
SITES_CANDIDATOS = [
    {"nome": "Elenco da Raquel", "url": "https://www.elencodaraquel.com", "categoria_default": "Audiovisual"},
    {"nome": "Ranieri Full Casting", "url": "https://ranierifullcasting.com.br", "categoria_default": "Audiovisual"},
    {"nome": "No Ar Casting", "url": "http://noarcasting.com.br", "categoria_default": "Audiovisual"},
    {"nome": "Avante Casting", "url": "https://www.avantecasting.com.br", "categoria_default": "Audiovisual"},
    {"nome": "Army Casting", "url": "https://www.armycasting.com.br", "categoria_default": "Audiovisual"},
    {"nome": "Attos Casting", "url": "https://attoscasting.com", "categoria_default": "Audiovisual"},
    {"nome": "Agência Império", "url": "https://agenciaimperio.com.br", "categoria_default": "Audiovisual"},
    {"nome": "Pearson Casting", "url": "https://www.pearsoncasting.com", "categoria_default": "Audiovisual"},
    {"nome": "ME Casting", "url": "https://www.mecastingteam.me", "categoria_default": "Audiovisual"},
    {"nome": "Anne Trevisan", "url": "https://www.annetrevisan.com", "categoria_default": "Audiovisual"},
    {"nome": "Autêntica Prod", "url": "https://www.autenticaprod.com", "categoria_default": "Audiovisual"},
    {"nome": "Mondiale Casting", "url": "http://agenciamondiale.com.br", "categoria_default": "Audiovisual"},
    {"nome": "DEA Diretores", "url": "http://diretoresdeelenco.com.br", "categoria_default": "Audiovisual"},
    {"nome": "SBT Elenco", "url": "https://elenco.tvsbt.com.br", "categoria_default": "Audiovisual"},
    {"nome": "Royal Caribbean Entertainment", "url": "https://royalcaribbeanentertainment.com", "categoria_default": "Navios/Cruzeiros"},
    {"nome": "NCLH Creative Studios", "url": "https://nclhcreativestudios.com", "categoria_default": "Navios/Cruzeiros"},
    {"nome": "Celebrity Cruises Entertainment", "url": "https://www.celebritycruisesentertainment.com", "categoria_default": "Navios/Cruzeiros"},
    {"nome": "Carnival Entertainment", "url": "https://www.carnivalentertainment.com", "categoria_default": "Navios/Cruzeiros"},
    {"nome": "Backstage", "url": "https://www.backstage.com", "categoria_default": "Outros"},
    {"nome": "Casting Brasil", "url": "https://castingbrasil.com.br", "categoria_default": "Outros"},
    {"nome": "Open Auditions UK", "url": "https://www.openauditions.uk", "categoria_default": "Outros"},
    {"nome": "Luz Casting", "url": "https://luzcasting.live", "categoria_default": "Audiovisual"},
    {"nome": "SetVerso", "url": "https://setverso.com", "categoria_default": "Audiovisual"},
    # Classificados Artísticos: apenas Instagram (@classificadosartisticos), sem site externo ou RSS
    # Monitorado via autodescoberta quando/se lançar site ou RSS
]

RSS_SUFFIXES = ["/feed/", "/feed", "/rss/", "/rss", "/rss.xml", "/feed.xml", "/?feed=rss2"]


def _carregar_feeds_descobertos() -> List[Dict]:
    """Carrega feeds RSS descobertos automaticamente."""
    if FEEDS_DESCOBERTOS_PATH.exists():
        try:
            with open(FEEDS_DESCOBERTOS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _salvar_feeds_descobertos(feeds: List[Dict]) -> None:
    """Salva feeds RSS descobertos automaticamente."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(FEEDS_DESCOBERTOS_PATH, "w", encoding="utf-8") as f:
        json.dump(feeds, f, ensure_ascii=False, indent=2)


def _verificar_rss_sites() -> List[Dict]:
    """
    Verifica se algum dos SITES_CANDIDATOS passou a ter RSS.
    Retorna lista de novos feeds encontrados.
    Executa apenas uma vez por semana (controlado por timestamp no arquivo).
    """
    import datetime
    import concurrent.futures

    # Verificar se já rodou esta semana
    timestamp_path = DATA_DIR / "rss_check_timestamp.txt"
    if timestamp_path.exists():
        try:
            ts = datetime.date.fromisoformat(timestamp_path.read_text().strip())
            if (datetime.date.today() - ts).days < 7:
                logger.debug("Verificação de RSS candidatos: já executada esta semana, pulando.")
                return []
        except Exception:
            pass

    logger.info("Verificando novos RSS feeds em sites candidatos...")
    feeds_existentes_urls = {f["url"] for f in RSS_FEEDS}
    feeds_descobertos = _carregar_feeds_descobertos()
    feeds_descobertos_urls = {f["url"] for f in feeds_descobertos}
    todos_conhecidos = feeds_existentes_urls | feeds_descobertos_urls

    novos_feeds = []

    def checar_site(site):
        parsed = urlparse(site["url"])
        base = f"{parsed.scheme}://{parsed.netloc}"
        for suffix in RSS_SUFFIXES:
            url_rss = base + suffix
            if url_rss in todos_conhecidos:
                continue
            try:
                r = requests.get(url_rss, timeout=5, headers=HEADERS, allow_redirects=True)
                if r.status_code == 200:
                    ct = r.headers.get("content-type", "").lower()
                    content = r.text[:300].lower()
                    if any(x in ct for x in ["xml", "rss", "atom"]) or \
                       any(x in content for x in ["<rss", "<feed", "<channel>", "<?xml"]):
                        return {"nome": site["nome"], "url": url_rss, "categoria_default": site["categoria_default"]}
            except Exception:
                pass
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        resultados = list(ex.map(checar_site, SITES_CANDIDATOS))

    for r in resultados:
        if r and r["url"] not in todos_conhecidos:
            novos_feeds.append(r)
            logger.info(f"  Novo RSS descoberto: {r['nome']} → {r['url']}")

    if novos_feeds:
        feeds_descobertos.extend(novos_feeds)
        _salvar_feeds_descobertos(feeds_descobertos)
        logger.info(f"  {len(novos_feeds)} novo(s) feed(s) RSS descoberto(s) e salvo(s)")
    else:
        logger.info("  Nenhum novo RSS encontrado nos candidatos")

    # Atualizar timestamp
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    timestamp_path.write_text(str(datetime.date.today()))

    return novos_feeds


# ─────────────────────────────────────────────────────────────────────────────
# FUNÇÃO PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def buscar_casting() -> Tuple[List[Dict], List[str]]:
    """
    Executa todos os scrapers e retorna lista consolidada de oportunidades filtradas.
    Retorna: (lista_de_oportunidades, lista_de_erros)
    """
    logger.info("Iniciando busca de oportunidades de casting...")
    todas: List[Dict] = []
    ids_vistos: set = set()
    erros: List[str] = []

    # 0. Autodescoberta semanal de novos RSS feeds em sites candidatos
    try:
        novos_rss = _verificar_rss_sites()
        if novos_rss:
            RSS_FEEDS.extend(novos_rss)
            logger.info(f"  → {len(novos_rss)} novo(s) feed(s) RSS adicionado(s) dinamicamente")
    except Exception as e:
        logger.warning(f"Autodescoberta RSS: {str(e)[:100]}")

    # 0b. Carregar feeds RSS descobertos anteriormente
    try:
        feeds_descobertos = _carregar_feeds_descobertos()
        feeds_existentes_urls = {f["url"] for f in RSS_FEEDS}
        for fd in feeds_descobertos:
            if fd["url"] not in feeds_existentes_urls:
                RSS_FEEDS.append(fd)
                logger.debug(f"  Feed descoberto carregado: {fd['nome']}")
    except Exception as e:
        logger.warning(f"Carregamento de feeds descobertos: {str(e)[:100]}")

    # 1. RSS Feeds (todas as fontes configuradas + descobertas)
    logger.info(f"Buscando via RSS feeds ({len(RSS_FEEDS)} feeds)...")
    try:
        rss = _buscar_rss_feeds()
        logger.info(f"  → {len(rss)} oportunidades via RSS")
        for op in rss:
            if op["id"] not in ids_vistos:
                ids_vistos.add(op["id"])
                todas.append(op)
    except Exception as e:
        erros.append(f"RSS Feeds: {str(e)[:100]}")

    # 2. Elenco Digital (cards estruturados)
    logger.info("Buscando Elenco Digital...")
    try:
        ed = _buscar_elenco_digital()
        logger.info(f"  → {len(ed)} oportunidades")
        for op in ed:
            if op["id"] not in ids_vistos:
                ids_vistos.add(op["id"])
                todas.append(op)
    except Exception as e:
        erros.append(f"Elenco Digital: {str(e)[:100]}")

    # 3. Autodescoberta de novos perfis de casting via Google (semanal)
    try:
        from autodescoberta import executar_autodescoberta
        # Passa dicionário vazio pois os handles são gerenciados internamente
        fontes_extras = executar_autodescoberta({})
        if fontes_extras:
            logger.info(f"  Autodescoberta: {len(fontes_extras)} perfis catalogados")
    except ImportError:
        logger.debug("Módulo autodescoberta não disponível")
    except Exception as e:
        logger.warning(f"Autodescoberta: {str(e)[:100]}")

    logger.info(f"Total de oportunidades após filtros: {len(todas)}")
    return todas, erros


def filtrar_novas_oportunidades(
    oportunidades: List[Dict],
    historico: Dict,
) -> Tuple[List[Dict], Dict]:
    """Retorna apenas oportunidades novas (não alertadas antes) e atualiza o histórico."""
    from datetime import date, timedelta
    novas: List[Dict] = []
    hoje = str(date.today())

    # Normalizar histórico: suporte ao formato legado {id: True} (anterior ao dict)
    historico_normalizado: Dict = {}
    for k, v in historico.items():
        if isinstance(v, dict):
            historico_normalizado[k] = v
        else:
            # Formato legado: marcar como alerta antigo para não bloquear limpeza
            historico_normalizado[k] = {"titulo": k, "fonte": "legado", "data_alerta": "2000-01-01"}

    historico_atualizado = historico_normalizado.copy()

    def _parse_data(texto: str):
        """Tenta converter dd/mm/aaaa → date. Retorna None se inválido."""
        if not texto:
            return None
        import re as _re
        m = _re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", texto.strip())
        if m:
            try:
                from datetime import date as _date
                return _date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                return None
        return None

    hoje_date = date.today()
    LIMITE_PUBLICACAO = hoje_date - timedelta(days=45)   # descarta publicados há >45 dias
    descartadas_antigas = 0
    descartadas_expiradas = 0

    for opp in oportunidades:
        opp_id = opp.get("id", "")
        if not opp_id:
            continue

        # ── Filtro 1: data de inscrição já passou ────────────────────────────
        data_inscricao = _parse_data(opp.get("data_inscricao", ""))
        if data_inscricao and data_inscricao < hoje_date:
            descartadas_expiradas += 1
            logger.debug(f"Expirada (inscrições encerraram {data_inscricao}): {opp.get('titulo','')[:60]}")
            continue

        # ── Filtro 2: publicada há mais de 45 dias ───────────────────────────
        data_pub = _parse_data(opp.get("data_publicacao", ""))
        if data_pub and data_pub < LIMITE_PUBLICACAO:
            descartadas_antigas += 1
            logger.debug(f"Antiga (publicada {data_pub}): {opp.get('titulo','')[:60]}")
            continue

        if opp_id not in historico_atualizado:
            novas.append(opp)
            historico_atualizado[opp_id] = {
                "titulo": opp.get("titulo", ""),
                "fonte": opp.get("fonte", ""),
                "data_alerta": hoje,
            }

    if descartadas_expiradas or descartadas_antigas:
        logger.info(f"  Filtro de datas: {descartadas_expiradas} expiradas, {descartadas_antigas} antigas (>45d) descartadas")

    # Limpar histórico com mais de 90 dias (inclui entradas legadas com data 2000-01-01)
    limite = date.today() - timedelta(days=90)
    historico_atualizado = {
        k: v for k, v in historico_atualizado.items()
        if v.get("data_alerta", hoje) >= str(limite)
    }
    return novas, historico_atualizado


def formatar_email_casting(oportunidades: List[Dict], erros: List[str]) -> str:
    """Formata o email HTML de alerta de novas oportunidades de casting."""
    from datetime import date
    hoje = date.today().strftime("%d/%m/%Y")

    # Agrupar por categoria
    por_categoria: Dict[str, List[Dict]] = {}
    for opp in oportunidades:
        cat = opp.get("categoria", "Outros")
        por_categoria.setdefault(cat, []).append(opp)

    ordem_cats = ["Teatro", "Audiovisual", "Navios/Cruzeiros", "Resorts/Hotéis", "Outros"]

    linhas = [
        f"ALERTA DE OPORTUNIDADES DE CASTING/AUDIÇÕES — {hoje}",
        f"Perfil: Homem | Branco/Caucasiano | Descendente de italiano | Português, inglês e espanhol",
        f"Critérios: Acima de 40 anos OU aparência 35-50 anos OU não especificado",
        f"Excluídas: seleções exclusivas para etnias incompatíveis com o perfil",
        f"Total de oportunidades novas: {len(oportunidades)}",
        "",
    ]

    for cat in ordem_cats:
        if cat not in por_categoria:
            continue
        opps_cat = por_categoria[cat]
        linhas.append(f"{'='*60}")
        linhas.append(f"  {cat.upper()} ({len(opps_cat)} oportunidade(s))")
        linhas.append(f"{'='*60}")
        linhas.append("")

        for opp in opps_cat:
            linhas.append(f"▶ {opp['titulo']}")
            linhas.append(f"  Fonte: {opp['fonte']}")

            if opp.get("perfil_procurado"):
                linhas.append(f"  Perfil procurado: {opp['perfil_procurado']}")
            if opp.get("data_inscricao"):
                linhas.append(f"  Inscrições até: {opp['data_inscricao']}")
            if opp.get("data_teste"):
                linhas.append(f"  Data do teste/audição: {opp['data_teste']}")
            if opp.get("cache"):
                linhas.append(f"  Cachê: {opp['cache']}")
            if opp.get("o_que_levar"):
                linhas.append(f"  O que levar/apresentar: {opp['o_que_levar']}")
            if opp.get("local"):
                linhas.append(f"  Local/Endereço: {opp['local']}")
            if opp.get("email_contato"):
                linhas.append(f"  Email de contato: {opp['email_contato']}")
            if opp.get("link_inscricao") and opp["link_inscricao"] != opp.get("link"):
                linhas.append(f"  Link de inscrição: {opp['link_inscricao']}")
            linhas.append(f"  Link completo: {opp['link']}")
            if opp.get("descricao"):
                linhas.append(f"  Resumo: {opp['descricao'][:300]}")
            linhas.append("")

    # Categorias não previstas
    for cat, opps_cat in por_categoria.items():
        if cat not in ordem_cats:
            linhas.append(f"{'='*60}")
            linhas.append(f"  {cat.upper()} ({len(opps_cat)} oportunidade(s))")
            linhas.append(f"{'='*60}")
            linhas.append("")
            for opp in opps_cat:
                linhas.append(f"▶ {opp['titulo']}")
                linhas.append(f"  Fonte: {opp['fonte']}")
                if opp.get("perfil_procurado"):
                    linhas.append(f"  Perfil procurado: {opp['perfil_procurado']}")
                if opp.get("data_inscricao"):
                    linhas.append(f"  Inscrições até: {opp['data_inscricao']}")
                if opp.get("data_teste"):
                    linhas.append(f"  Data do teste/audição: {opp['data_teste']}")
                if opp.get("cache"):
                    linhas.append(f"  Cachê: {opp['cache']}")
                if opp.get("o_que_levar"):
                    linhas.append(f"  O que levar/apresentar: {opp['o_que_levar']}")
                if opp.get("local"):
                    linhas.append(f"  Local/Endereço: {opp['local']}")
                if opp.get("email_contato"):
                    linhas.append(f"  Email de contato: {opp['email_contato']}")
                if opp.get("link_inscricao") and opp["link_inscricao"] != opp.get("link"):
                    linhas.append(f"  Link de inscrição: {opp['link_inscricao']}")
                linhas.append(f"  Link completo: {opp['link']}")
                if opp.get("descricao"):
                    linhas.append(f"  Resumo: {opp['descricao'][:300]}")
                linhas.append("")

    if erros:
        linhas.append(f"{'─'*60}")
        linhas.append(f"Avisos técnicos ({len(erros)} fonte(s) com erro):")
        for e in erros:
            linhas.append(f"  • {e}")

    return "\n".join(linhas)
