# Configuração do Workflow de Automação

## Passo único: Criar o arquivo de workflow no GitHub

Para ativar o alerta diário automático, é necessário criar o arquivo de workflow diretamente no GitHub (o token de integração não tem permissão para criar arquivos de workflow por razões de segurança).

### Como fazer (menos de 1 minuto):

1. Acesse: https://github.com/contatohb/casting-alerts
2. Clique em **"Add file"** → **"Create new file"**
3. No campo de nome do arquivo, digite exatamente: `.github/workflows/daily-casting-alert.yml`
4. Cole o conteúdo abaixo no editor:

```yaml
name: Daily Casting Alert

on:
  schedule:
    - cron: '0 13 * * *'  # 10:00 BRT (13:00 UTC)
  workflow_dispatch: {}

permissions:
  contents: write

jobs:
  casting-alert:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          token: ${{ secrets.GITHUB_TOKEN }}

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          python -m pip install requests beautifulsoup4 python-dotenv

      - name: Install manus-mcp-cli
        run: |
          pip install manus-mcp-cli || echo "manus-mcp-cli not available via pip"

      - name: Send daily casting alert
        env:
          MONITOR_RECIPIENT: huddsong@gmail.com
          GMAIL_MCP_TOKEN: ${{ secrets.GMAIL_MCP_TOKEN }}
        run: |
          cd scripts && python alerta_casting.py

      - name: Commit updated seen history
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add data/casting_seen.json || true
          git diff --cached --quiet || git commit -m "chore: update casting seen history [skip ci]"
          git push || true

      - name: Notify on failure
        if: failure()
        run: |
          echo "Casting alert job failed at $(date)"
          exit 1
```

5. Clique em **"Commit new file"**

### Verificar se está funcionando:

Após criar o arquivo, vá em **Actions** → **Daily Casting Alert** e clique em **"Run workflow"** para testar manualmente.

---

> **Nota:** O workflow está configurado para rodar todos os dias às **10h00 (horário de Brasília)**.
> O histórico de oportunidades já alertadas é salvo automaticamente em `data/casting_seen.json`.
