# Sistema de Alertas de Casting

Sistema automatizado de alertas diários para oportunidades de casting, audições e seleções de elenco para atores e cantores.

## Características

- Monitoramento diário de múltiplas fontes de casting
- Filtros personalizados por gênero, idade e aparência
- Organização de oportunidades por categoria (teatro, audiovisual, navios, resorts, etc)
- Envio automático de emails com informações completas
- Histórico de oportunidades já alertadas para evitar duplicatas

## Critérios de Filtro

O sistema filtra oportunidades de casting que atendem aos seguintes critérios:

- **Gênero**: Homem
- **Idade**: Acima de 40 anos OU aparência entre 35-50 anos OU não especificado

## Fontes Monitoradas

- Guia do Ator (guiadoator.com.br)
- Elenco Digital (elencdigital.com.br)
- Oppah (oppah.com.br)
- Nossa Senhora do Casting (nossasenhora.com.br)
- Castapp (castapp.com.br)
- Open Auditions (openauditions.com)
- Rede Globo (globo.com)
- Rede Record (record.com.br)
- Páginas/perfis com "casting" ou "elenco" no nome

## Estrutura do Projeto

```
casting-alerts/
├── scripts/
│   ├── monitor_casting.py       # Script principal de coleta e filtragem
│   └── alerta_casting.py        # Script de envio de email
├── .github/
│   └── workflows/
│       └── daily-casting-alert.yml  # Workflow do GitHub Actions
├── data/
│   └── casting_seen.json        # Histórico de oportunidades alertadas
├── .env.example                 # Variáveis de ambiente
└── README.md                    # Este arquivo
```

## Instalação

1. Clone o repositório:
```bash
git clone https://github.com/contatohb/casting-alerts.git
cd casting-alerts
```

2. Crie um arquivo `.env` baseado em `.env.example`:
```bash
cp .env.example .env
```

3. Instale as dependências:
```bash
pip install requests beautifulsoup4 python-dotenv
```

## Uso Local

Para executar o alerta manualmente:

```bash
python scripts/alerta_casting.py
```

Opções:
- `--force-send`: Força o envio do email mesmo sem novidades
- `--no-enrich`: Desativa enriquecimento de detalhes

## Automação com GitHub Actions

O sistema está configurado para executar automaticamente todos os dias às 10h (horário de Brasília).

Para executar manualmente via GitHub Actions:
1. Acesse a aba "Actions" do repositório
2. Selecione "Daily Casting Alert"
3. Clique em "Run workflow"

## Informações Incluídas nos Emails

Para cada oportunidade encontrada, o email inclui:

- Título da oportunidade
- Descrição
- Gênero esperado
- Faixa etária
- Aparência esperada
- Cachê (se mencionado)
- O que levar/apresentar
- Endereço completo
- Datas de inscrição
- Data do teste/audição/gravação
- Link de inscrição
- Link do formulário
- Email de contato
- Localização
- Link para mais detalhes
- Fonte

## Histórico de Alertas

O sistema mantém um histórico em `data/casting_seen.json` para evitar alertar sobre a mesma oportunidade mais de uma vez. Este arquivo é atualizado automaticamente após cada execução.

## Configuração de Email

O sistema utiliza a integração Gmail via MCP (Model Context Protocol) para enviar os emails. A configuração é feita automaticamente através do GitHub Actions.

## Troubleshooting

### Email não está sendo enviado
- Verifique se o Gmail MCP está configurado corretamente
- Verifique se a variável `MONITOR_RECIPIENT` está definida corretamente
- Verifique os logs do GitHub Actions para mensagens de erro

### Oportunidades não estão sendo encontradas
- Verifique se as fontes estão acessíveis
- Verifique se a estrutura HTML das fontes não mudou
- Verifique os logs para mensagens de erro de scraping

### Muitos falsos positivos
- Ajuste os critérios de filtro em `monitor_casting.py`
- Verifique se a extração de idade/aparência está funcionando corretamente

## Desenvolvimento

Para adicionar novas fontes:

1. Crie uma nova função `scrape_<fonte>()` em `monitor_casting.py`
2. Adicione a chamada em `buscar_casting()`
3. Teste localmente antes de fazer commit

## Licença

Este projeto faz parte do sistema Intellicore.

## Suporte

Para problemas ou sugestões, entre em contato com o administrador do repositório.
