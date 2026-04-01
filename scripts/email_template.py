#!/usr/bin/env python3
"""
email_template.py — Template HTML premium para o alerta diário de casting.

Gera um email HTML responsivo, com design premium de newsletter,
organizado por categoria e sub-agrupado por cidade/estado.
"""
from __future__ import annotations

import html as html_lib
import re
from datetime import date
from typing import Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# PALETA DE CORES E ÍCONES POR CATEGORIA
# ─────────────────────────────────────────────────────────────────────────────

CATEGORIA_CONFIG = {
    "Teatro": {
        "cor": "#1a3a5c",
        "cor_clara": "#e8f0f8",
        "cor_borda": "#2563a8",
        "icone": "🎭",
        "emoji_local": "📍",
    },
    "Audiovisual": {
        "cor": "#1a3a2c",
        "cor_clara": "#e8f5ee",
        "cor_borda": "#16a34a",
        "icone": "🎬",
        "emoji_local": "📍",
    },
    "Navios/Cruzeiros": {
        "cor": "#1a2a4a",
        "cor_clara": "#e8eef8",
        "cor_borda": "#1d4ed8",
        "icone": "🚢",
        "emoji_local": "🌊",
    },
    "Resorts/Hotéis": {
        "cor": "#3a1a2c",
        "cor_clara": "#f8e8f0",
        "cor_borda": "#9333ea",
        "icone": "🏨",
        "emoji_local": "📍",
    },
    "Outros": {
        "cor": "#2a2a2a",
        "cor_clara": "#f0f0f0",
        "cor_borda": "#6b7280",
        "icone": "🎪",
        "emoji_local": "📍",
    },
}

ORDEM_CATEGORIAS = ["Teatro", "Audiovisual", "Navios/Cruzeiros", "Resorts/Hotéis", "Outros"]


# ─────────────────────────────────────────────────────────────────────────────
# UTILITÁRIOS
# ─────────────────────────────────────────────────────────────────────────────

def _esc(text: str) -> str:
    """Escapa caracteres HTML."""
    return html_lib.escape(str(text or ""), quote=True)


# Mapeamento de siglas de estado para nomes
_SIGLAS_UF = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas",
    "BA": "Bahia", "CE": "Ceará", "DF": "Distrito Federal", "ES": "Espírito Santo",
    "GO": "Goiás", "MA": "Maranhão", "MT": "Mato Grosso", "MS": "Mato Grosso do Sul",
    "MG": "Minas Gerais", "PA": "Pará", "PB": "Paraíba", "PR": "Paraná",
    "PE": "Pernambuco", "PI": "Piauí", "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte",
    "RS": "Rio Grande do Sul", "RO": "Rondônia", "RR": "Roraima", "SC": "Santa Catarina",
    "SP": "São Paulo", "SE": "Sergipe", "TO": "Tocantins",
}

# Mapeamento de cidade → UF para cidades que aparecem no título
_CIDADE_UF_TITULO = {
    "(SP)": "São Paulo, SP",
    "(RJ)": "Rio de Janeiro, RJ",
    "(MG)": "Belo Horizonte, MG",
    "(SC)": "Santa Catarina, SC",
    "(PR)": "Paraná, PR",
    "(RS)": "Rio Grande do Sul, RS",
    "(BA)": "Salvador, BA",
    "(PE)": "Recife, PE",
    "(CE)": "Fortaleza, CE",
    "(DF)": "Brasília, DF",
    "(AM)": "Manaus, AM",
    "(ES)": "Vitória, ES",
    "(GO)": "Goiânia, GO",
    "(PA)": "Belém, PA",
}


def _extrair_cidade_estado(local: str, titulo: str = "") -> str:
    """Extrai cidade/estado do campo local (ou do título) para agrupamento."""
    if not local:
        # Tentar extrair do título: padrão "(SP)", "(RJ)", etc.
        if titulo:
            for sigla, cidade_label in _CIDADE_UF_TITULO.items():
                if sigla in titulo:
                    return cidade_label
            # Padrão "Região Norte", "Região Sul", etc.
            m_regiao = re.search(r"Regi[aã]o\s+(Norte|Sul|Leste|Oeste|Nordeste|Sudeste|Centro-Oeste)", titulo, re.IGNORECASE)
            if m_regiao:
                return f"Região {m_regiao.group(1).title()}"
        return "Nacional / Online"

    # Padrões comuns: "Cidade, UF", "Cidade - UF", "Cidade (UF)"
    # UF = 2 letras maiúsculas
    m = re.search(
        r"([A-ZÀ-Ú][a-zà-ú]+(?:\s+[A-ZÀ-Ú][a-zà-ú]+)*)"
        r"[\s,\-–]+([A-Z]{2})\b",
        local
    )
    if m:
        return f"{m.group(1)}, {m.group(2)}"

    # Apenas UF no final
    m = re.search(r"\b([A-Z]{2})\s*$", local.strip())
    if m:
        return m.group(1)

    # Cidade conhecida sem UF
    cidades = {
        "são paulo": "São Paulo, SP",
        "rio de janeiro": "Rio de Janeiro, RJ",
        "belo horizonte": "Belo Horizonte, MG",
        "florianópolis": "Florianópolis, SC",
        "curitiba": "Curitiba, PR",
        "porto alegre": "Porto Alegre, RS",
        "salvador": "Salvador, BA",
        "recife": "Recife, PE",
        "fortaleza": "Fortaleza, CE",
        "brasília": "Brasília, DF",
        "manaus": "Manaus, AM",
        "vitória": "Vitória, ES",
        "guarulhos": "Guarulhos, SP",
        "campinas": "Campinas, SP",
    }
    local_lower = local.lower()
    for cidade, label in cidades.items():
        if cidade in local_lower:
            return label

    # Retorna o local original truncado
    return local[:40] if len(local) > 40 else local


def _agrupar_por_cidade(oportunidades: List[Dict]) -> Dict[str, List[Dict]]:
    """Agrupa lista de oportunidades por cidade/estado."""
    grupos: Dict[str, List[Dict]] = {}
    for opp in oportunidades:
        local = opp.get("local", "")
        titulo = opp.get("titulo", "")
        cidade = _extrair_cidade_estado(local, titulo)
        grupos.setdefault(cidade, []).append(opp)

    # Ordenar: cidades nomeadas primeiro, depois Nacional/Online
    def _sort_key(k):
        if k == "Nacional / Online":
            return "zzz"
        return k.lower()

    return dict(sorted(grupos.items(), key=lambda x: _sort_key(x[0])))


def _badge(texto: str, cor_bg: str, cor_texto: str = "#ffffff") -> str:
    """Gera um badge/tag HTML inline."""
    return (
        f'<span style="display:inline-block;background:{cor_bg};color:{cor_texto};'
        f'font-size:11px;font-weight:600;padding:2px 8px;border-radius:12px;'
        f'margin:2px 2px 2px 0;letter-spacing:0.3px;white-space:nowrap;">'
        f'{_esc(texto)}</span>'
    )


def _campo_linha(icone: str, label: str, valor: str, cor_label: str = "#555") -> str:
    """Gera uma linha de campo com ícone, label e valor."""
    return (
        f'<tr>'
        f'<td style="padding:3px 8px 3px 0;vertical-align:top;white-space:nowrap;'
        f'font-size:12px;color:{cor_label};font-weight:600;width:1%;">'
        f'{icone} {_esc(label)}</td>'
        f'<td style="padding:3px 0;font-size:13px;color:#1a1a1a;vertical-align:top;">'
        f'{_esc(valor)}</td>'
        f'</tr>'
    )


def _campo_link(icone: str, label: str, url: str, texto_link: str = None) -> str:
    """Gera uma linha de campo com link clicável."""
    texto = texto_link or url
    # Truncar URL longa para exibição
    if len(texto) > 60 and not texto_link:
        texto = texto[:57] + "..."
    return (
        f'<tr>'
        f'<td style="padding:3px 8px 3px 0;vertical-align:top;white-space:nowrap;'
        f'font-size:12px;color:#555;font-weight:600;width:1%;">'
        f'{icone} {_esc(label)}</td>'
        f'<td style="padding:3px 0;font-size:13px;vertical-align:top;">'
        f'<a href="{_esc(url)}" style="color:#1a6bbf;text-decoration:none;word-break:break-all;">'
        f'{_esc(texto)}</a></td>'
        f'</tr>'
    )


# ─────────────────────────────────────────────────────────────────────────────
# GERAÇÃO DO CARD DE OPORTUNIDADE
# ─────────────────────────────────────────────────────────────────────────────

def _gerar_card_oportunidade(opp: Dict, cor_borda: str) -> str:
    """Gera o card HTML de uma oportunidade individual."""
    titulo = opp.get("titulo", "Sem título")
    fonte = opp.get("fonte", "")
    perfil = opp.get("perfil_procurado", "")
    faixa_etaria = opp.get("faixa_etaria", "")
    data_inscricao = opp.get("data_inscricao", "")
    data_teste = opp.get("data_teste", "")
    cache = opp.get("cache", "")
    o_que_levar = opp.get("o_que_levar", "")
    local = opp.get("local", "")
    email_contato = opp.get("email_contato", "")
    link_inscricao = opp.get("link_inscricao", "")
    link = opp.get("link", "")
    descricao = opp.get("descricao", "")

    # Truncar descrição com elegância
    if descricao and len(descricao) > 280:
        # Cortar na última frase completa antes de 280 chars
        trunc = descricao[:280]
        ultimo_ponto = max(trunc.rfind(". "), trunc.rfind("! "), trunc.rfind("? "))
        if ultimo_ponto > 150:
            descricao_exib = descricao[:ultimo_ponto + 1]
        else:
            descricao_exib = trunc.rstrip() + "…"
    else:
        descricao_exib = descricao

    # Badges de perfil
    badges_html = ""
    if fonte:
        badges_html += _badge(fonte, "#334155", "#e2e8f0")
    if faixa_etaria and faixa_etaria not in ("não especificado", ""):
        badges_html += _badge(f"Idade: {faixa_etaria}", "#1e40af", "#dbeafe")
    if cache:
        badges_html += _badge(f"Cachê: {cache}", "#166534", "#dcfce7")

    # Campos estruturados
    campos_html = '<table style="border-collapse:collapse;width:100%;margin-top:8px;">'

    if perfil:
        campos_html += _campo_linha("👤", "Perfil:", perfil)
    if data_inscricao:
        campos_html += _campo_linha("📅", "Inscrições até:", data_inscricao, "#b45309")
    if data_teste:
        campos_html += _campo_linha("🎯", "Data do teste/gravação:", data_teste, "#7c3aed")
    if local:
        campos_html += _campo_linha("📍", "Local:", local)
    if o_que_levar:
        campos_html += _campo_linha("🎒", "O que levar/apresentar:", o_que_levar)
    if email_contato:
        campos_html += _campo_link("✉️", "Email de contato:", f"mailto:{email_contato}", email_contato)
    if link_inscricao and link_inscricao != link:
        campos_html += _campo_link("📝", "Inscrição:", link_inscricao, "Formulário de inscrição →")
    if link:
        campos_html += _campo_link("🔗", "Link completo:", link, "Ver oportunidade completa →")

    campos_html += "</table>"

    # Botão CTA clicável
    cta_html = ""
    cta_link = link_inscricao or link
    if cta_link:
        cta_html = (
            f'<div style="margin-top:14px;text-align:center;">'
            f'<a href="{_esc(cta_link)}" style="display:inline-block;padding:12px 28px;'
            f'background:#1a6bbf;color:#ffffff;font-size:14px;font-weight:700;'
            f'text-decoration:none;border-radius:6px;letter-spacing:0.3px;">'
            f'{"📝 Inscrever-se" if link_inscricao else "🔗 Ver oportunidade"} →</a>'
            f'</div>'
        )

    # Resumo (se houver)
    resumo_html = ""
    if descricao_exib:
        resumo_html = (
            f'<div style="margin-top:10px;padding:10px 12px;background:#f8fafc;'
            f'border-left:3px solid {cor_borda};border-radius:0 4px 4px 0;">'
            f'<p style="margin:0;font-size:12.5px;color:#475569;line-height:1.6;">'
            f'{_esc(descricao_exib)}</p>'
            f'</div>'
        )

    return f"""
    <div style="background:#ffffff;border:1px solid #e2e8f0;border-left:4px solid {cor_borda};
                border-radius:6px;padding:16px 18px;margin-bottom:12px;
                box-shadow:0 1px 3px rgba(0,0,0,0.06);">
      <h3 style="margin:0 0 8px 0;font-size:15px;font-weight:700;color:#0f172a;
                 line-height:1.3;">{_esc(titulo)}</h3>
      <div style="margin-bottom:10px;">{badges_html}</div>
      {campos_html}
      {resumo_html}
      {cta_html}
    </div>"""


# ─────────────────────────────────────────────────────────────────────────────
# GERAÇÃO DO BLOCO DE CATEGORIA
# ─────────────────────────────────────────────────────────────────────────────

def _gerar_bloco_categoria(cat: str, oportunidades: List[Dict]) -> str:
    """Gera o bloco HTML completo de uma categoria, com sub-agrupamento por cidade."""
    cfg = CATEGORIA_CONFIG.get(cat, CATEGORIA_CONFIG["Outros"])
    cor = cfg["cor"]
    cor_clara = cfg["cor_clara"]
    cor_borda = cfg["cor_borda"]
    icone = cfg["icone"]

    # Agrupar por cidade
    por_cidade = _agrupar_por_cidade(oportunidades)

    # Conteúdo das cidades
    cidades_html = ""
    for cidade, opps_cidade in por_cidade.items():
        cards_html = "".join(_gerar_card_oportunidade(opp, cor_borda) for opp in opps_cidade)
        cidades_html += f"""
        <div style="margin-bottom:20px;">
          <div style="display:flex;align-items:center;margin-bottom:10px;">
            <span style="font-size:13px;font-weight:700;color:{cor_borda};
                         text-transform:uppercase;letter-spacing:0.8px;
                         padding:4px 10px;background:{cor_clara};
                         border-radius:4px;border:1px solid {cor_borda}20;">
              📍 {_esc(cidade)} &nbsp;·&nbsp; {len(opps_cidade)} oportunidade{'s' if len(opps_cidade) > 1 else ''}
            </span>
          </div>
          {cards_html}
        </div>"""

    return f"""
    <div style="margin-bottom:28px;">
      <!-- Cabeçalho da categoria -->
      <div style="background:{cor};color:#ffffff;padding:14px 20px;border-radius:8px 8px 0 0;
                  display:flex;align-items:center;">
        <span style="font-size:22px;margin-right:10px;">{icone}</span>
        <div>
          <div style="font-size:17px;font-weight:800;letter-spacing:0.5px;
                      text-transform:uppercase;">{_esc(cat)}</div>
          <div style="font-size:12px;opacity:0.8;margin-top:2px;">
            {len(oportunidades)} oportunidade{'s' if len(oportunidades) > 1 else ''} encontrada{'s' if len(oportunidades) > 1 else ''}
          </div>
        </div>
      </div>
      <!-- Conteúdo da categoria -->
      <div style="background:{cor_clara};padding:16px;border:1px solid {cor_borda}30;
                  border-top:none;border-radius:0 0 8px 8px;">
        {cidades_html}
      </div>
    </div>"""


# ─────────────────────────────────────────────────────────────────────────────
# GERAÇÃO DO EMAIL COMPLETO
# ─────────────────────────────────────────────────────────────────────────────

def gerar_email_html(oportunidades: List[Dict], erros: List[str]) -> str:
    """
    Gera o email HTML premium completo com todas as oportunidades.

    Args:
        oportunidades: Lista de dicionários de oportunidades filtradas.
        erros: Lista de mensagens de erro técnico (fontes com falha).

    Returns:
        String HTML completa do email.
    """
    hoje = date.today()
    hoje_str = hoje.strftime("%d/%m/%Y")
    hoje_extenso = hoje.strftime("%-d de %B de %Y").replace(
        "January", "janeiro").replace("February", "fevereiro").replace(
        "March", "março").replace("April", "abril").replace(
        "May", "maio").replace("June", "junho").replace(
        "July", "julho").replace("August", "agosto").replace(
        "September", "setembro").replace("October", "outubro").replace(
        "November", "novembro").replace("December", "dezembro")

    total = len(oportunidades)

    # Agrupar por categoria
    por_categoria: Dict[str, List[Dict]] = {}
    for opp in oportunidades:
        cat = opp.get("categoria", "Outros")
        por_categoria.setdefault(cat, []).append(opp)

    # Gerar blocos de categorias
    blocos_html = ""
    for cat in ORDEM_CATEGORIAS:
        if cat in por_categoria:
            blocos_html += _gerar_bloco_categoria(cat, por_categoria[cat])
    # Categorias não previstas
    for cat, opps in por_categoria.items():
        if cat not in ORDEM_CATEGORIAS:
            blocos_html += _gerar_bloco_categoria(cat, opps)

    # Resumo por categoria para o header
    resumo_cats = ""
    for cat in ORDEM_CATEGORIAS:
        if cat in por_categoria:
            cfg = CATEGORIA_CONFIG.get(cat, CATEGORIA_CONFIG["Outros"])
            n = len(por_categoria[cat])
            resumo_cats += (
                f'<span style="display:inline-block;margin:3px 4px;padding:4px 10px;'
                f'background:{cfg["cor_clara"]};color:{cfg["cor"]};'
                f'border:1px solid {cfg["cor_borda"]}40;border-radius:20px;'
                f'font-size:12px;font-weight:600;">'
                f'{cfg["icone"]} {_esc(cat)}: {n}</span>'
            )

    # Bloco de erros técnicos
    erros_html = ""
    if erros:
        erros_lista = "".join(
            f'<li style="margin:4px 0;font-size:12px;color:#92400e;">{_esc(e)}</li>'
            for e in erros
        )
        erros_html = f"""
        <div style="margin-top:20px;padding:12px 16px;background:#fffbeb;
                    border:1px solid #f59e0b;border-radius:6px;">
          <p style="margin:0 0 6px 0;font-size:13px;font-weight:700;color:#92400e;">
            ⚠️ Avisos técnicos ({len(erros)} fonte(s) com erro):
          </p>
          <ul style="margin:0;padding-left:18px;">{erros_lista}</ul>
        </div>"""

    # Mensagem de nenhuma oportunidade
    if total == 0:
        blocos_html = """
        <div style="text-align:center;padding:40px 20px;color:#64748b;">
          <div style="font-size:48px;margin-bottom:12px;">🎭</div>
          <p style="font-size:16px;font-weight:600;margin:0 0 8px 0;">
            Nenhuma oportunidade nova hoje
          </p>
          <p style="font-size:14px;margin:0;">
            O sistema continua monitorando todas as fontes. Você receberá um novo alerta
            assim que surgirem oportunidades compatíveis com o seu perfil.
          </p>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="color-scheme" content="light">
  <title>Casting Alert — {hoje_str}</title>
  <!--[if mso]>
  <noscript><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch>
  </o:OfficeDocumentSettings></xml></noscript>
  <![endif]-->
  <style>
    @media print {{
      body {{ background:#ffffff !important; font-size:11pt; }}
      table {{ max-width:100% !important; }}
      a[href]:after {{ content:" (" attr(href) ")"; font-size:9pt; color:#475569; }}
      td, th {{ -webkit-print-color-adjust:exact; print-color-adjust:exact; }}
    }}
  </style>
</head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,
             'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;-webkit-font-smoothing:antialiased;">

  <!-- Wrapper -->
  <table role="presentation" cellpadding="0" cellspacing="0" border="0"
         style="width:100%;background:#f1f5f9;">
    <tr><td align="center" style="padding:24px 16px;">

      <!-- Container principal -->
      <table role="presentation" cellpadding="0" cellspacing="0" border="0"
             style="max-width:680px;width:100%;">

        <!-- ══════════════════════════════════════════════════ -->
        <!-- HEADER                                             -->
        <!-- ══════════════════════════════════════════════════ -->
        <tr><td>
          <div style="background:linear-gradient(135deg, #0f172a 0%, #1e3a5f 60%, #1a4a8a 100%);
                      border-radius:12px 12px 0 0;padding:28px 30px 24px;
                      text-align:center;">
            <!-- Título -->
            <div style="font-size:11px;font-weight:700;letter-spacing:3px;
                        color:#94a3b8;text-transform:uppercase;margin-bottom:6px;">
              ALERTA DIÁRIO
            </div>
            <h1 style="margin:0 0 4px 0;font-size:28px;font-weight:900;color:#ffffff;
                       letter-spacing:-0.5px;">
              🎭 CASTING ALERT
            </h1>
            <div style="font-size:14px;color:#93c5fd;margin-top:6px;font-weight:500;">
              {hoje_extenso}
            </div>

            <!-- Contador total -->
            <div style="margin-top:18px;display:inline-block;background:rgba(255,255,255,0.12);
                        border:1px solid rgba(255,255,255,0.2);border-radius:50px;
                        padding:8px 24px;">
              <span style="font-size:26px;font-weight:900;color:#ffffff;">{total}</span>
              <span style="font-size:13px;color:#bfdbfe;margin-left:6px;">
                nova{'s' if total != 1 else ''} oportunidade{'s' if total != 1 else ''}
              </span>
            </div>

            <!-- Resumo por categoria -->
            <div style="margin-top:14px;">{resumo_cats}</div>
          </div>
        </td></tr>

        <!-- ══════════════════════════════════════════════════ -->
        <!-- PERFIL DO USUÁRIO                                  -->
        <!-- ══════════════════════════════════════════════════ -->
        <tr><td>
          <div style="background:#1e293b;padding:12px 30px;border-bottom:1px solid #334155;">
            <table role="presentation" cellpadding="0" cellspacing="0" border="0"
                   style="width:100%;">
              <tr>
                <td style="font-size:11px;color:#94a3b8;font-weight:600;
                           letter-spacing:0.5px;text-transform:uppercase;
                           padding-right:12px;white-space:nowrap;vertical-align:middle;">
                  Perfil monitorado:
                </td>
                <td style="font-size:12px;color:#cbd5e1;vertical-align:middle;">
                  Homem · Branco/Caucasiano · Descendente de italiano ·
                  Português, inglês e espanhol ·
                  <strong style="color:#93c5fd;">40+ anos ou aparência 35–50 anos</strong>
                </td>
              </tr>
            </table>
          </div>
        </td></tr>

        <!-- ══════════════════════════════════════════════════ -->
        <!-- CONTEÚDO PRINCIPAL                                 -->
        <!-- ══════════════════════════════════════════════════ -->
        <tr><td>
          <div style="background:#f8fafc;padding:24px 20px;
                      border:1px solid #e2e8f0;border-top:none;">
            {blocos_html}
            {erros_html}
          </div>
        </td></tr>

        <!-- ══════════════════════════════════════════════════ -->
        <!-- FOOTER                                             -->
        <!-- ══════════════════════════════════════════════════ -->
        <tr><td>
          <div style="background:#0f172a;border-radius:0 0 12px 12px;
                      padding:18px 24px;text-align:center;">
            <p style="margin:0 0 6px 0;font-size:12px;color:#64748b;">
              Gerado automaticamente por
              <strong style="color:#94a3b8;">Casting Alert System</strong>
              via GitHub Actions
            </p>
            <p style="margin:0;font-size:11px;color:#475569;">
              Fontes monitoradas: Guia do Ator · Elenco Digital · A Broadway é Aqui ·
              Navio Cabaré · Castapp · e outras
            </p>
            <div style="margin-top:10px;padding-top:10px;border-top:1px solid #1e293b;">
              <span style="font-size:11px;color:#334155;">
                {hoje_str} · huddsong@gmail.com
              </span>
            </div>
          </div>
        </td></tr>

      </table>
      <!-- /Container principal -->

    </td></tr>
  </table>
  <!-- /Wrapper -->

</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
# GERAÇÃO DO TEXTO PLANO (fallback)
# ─────────────────────────────────────────────────────────────────────────────

def gerar_email_texto(oportunidades: List[Dict], erros: List[str]) -> str:
    """
    Gera versão em texto puro do email (fallback para clientes sem HTML).
    """
    hoje = date.today().strftime("%d/%m/%Y")
    total = len(oportunidades)

    linhas = [
        f"CASTING ALERT — {hoje}",
        f"Perfil: Homem | Branco/Caucasiano | Descendente de italiano | Português, inglês e espanhol",
        f"Critérios: Acima de 40 anos OU aparência 35-50 anos OU não especificado",
        f"Total de oportunidades novas: {total}",
        "",
    ]

    por_categoria: Dict[str, List[Dict]] = {}
    for opp in oportunidades:
        cat = opp.get("categoria", "Outros")
        por_categoria.setdefault(cat, []).append(opp)

    for cat in ORDEM_CATEGORIAS:
        if cat not in por_categoria:
            continue
        opps_cat = por_categoria[cat]
        linhas += [f"{'='*60}", f"  {cat.upper()} ({len(opps_cat)} oportunidade(s))", f"{'='*60}", ""]

        por_cidade = _agrupar_por_cidade(opps_cat)
        for cidade, opps_cidade in por_cidade.items():
            linhas += [f"  ── {cidade} ──", ""]
            for opp in opps_cidade:
                linhas.append(f"  ▶ {opp['titulo']}")
                linhas.append(f"    Fonte: {opp.get('fonte', '')}")
                if opp.get("perfil_procurado"):
                    linhas.append(f"    Perfil: {opp['perfil_procurado']}")
                if opp.get("data_inscricao"):
                    linhas.append(f"    Inscrições até: {opp['data_inscricao']}")
                if opp.get("data_teste"):
                    linhas.append(f"    Data do teste: {opp['data_teste']}")
                if opp.get("cache"):
                    linhas.append(f"    Cachê: {opp['cache']}")
                if opp.get("o_que_levar"):
                    linhas.append(f"    O que levar: {opp['o_que_levar']}")
                if opp.get("local"):
                    linhas.append(f"    Local: {opp['local']}")
                if opp.get("email_contato"):
                    linhas.append(f"    Email: {opp['email_contato']}")
                if opp.get("link_inscricao") and opp["link_inscricao"] != opp.get("link"):
                    linhas.append(f"    Inscrição: {opp['link_inscricao']}")
                linhas.append(f"    Link: {opp['link']}")
                if opp.get("descricao"):
                    desc = opp["descricao"][:280].rstrip()
                    linhas.append(f"    Resumo: {desc}{'…' if len(opp['descricao']) > 280 else ''}")
                linhas.append("")

    if erros:
        linhas += [f"{'─'*60}", f"Avisos técnicos ({len(erros)} fonte(s) com erro):"]
        for e in erros:
            linhas.append(f"  • {e}")

    return "\n".join(linhas)
