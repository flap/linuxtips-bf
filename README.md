# linuxtips-bf

## Monitoramento do tempo de resposta de https://pimenta.dev

| Peça | Arquivo | O que faz |
|---|---|---|
| Medição | `scripts/check-response.sh` | Faz N requisições com `curl`, mostra DNS, conexão, TLS, TTFB e total em ms, e sai com código 2 se a mediana passar do limite ou se alguma requisição falhar. |
| Resumo | `scripts/summarize.sh` | Lê o CSV e mostra média, p50, p95, máximo e falhas por origem, mais as 5 medições mais lentas. |
| Coleta contínua | `.github/workflows/response-monitor.yml` | Roda a cada 15 min no GitHub Actions e grava em `response-times.csv` na branch `metrics`. Se um limite estourar, o job falha e o GitHub manda e-mail. |
| Agente | `.kiro/agents/response-monitor.md` | Agente do Kiro que faz checagens na hora e analisa o histórico da branch `metrics`. |
| Hook | `.kiro/hooks/response-check-on-start.json` | Ao abrir uma sessão no Kiro, mede o site (3 amostras) e coloca o resultado no contexto do agente. |

### Uso local

```bash
bash scripts/check-response.sh                       # 5 amostras, sem gravar
bash scripts/check-response.sh --samples 10 --csv /tmp/rt.csv
MAX_TTFB_MS=500 bash scripts/check-response.sh       # outro limite

git fetch origin metrics
git show origin/metrics:response-times.csv > /tmp/rt.csv
bash scripts/summarize.sh /tmp/rt.csv --last 480     # ~últimas 24 h
```

Limites padrão: TTFB mediano de 800 ms e total mediano de 1500 ms. Para mudar no workflow, edite o bloco `env` em `.github/workflows/response-monitor.yml`.

### No Kiro Web

Peça, por exemplo, "use o agente response-monitor para checar o site agora" ou "use o agente response-monitor para analisar as últimas 24 h". O Kiro Web usa agentes do projeto como sub-agentes; não dá para escolher um como agente principal da sessão.

### Formato do CSV

`timestamp_utc,source,url,http_code,dns_ms,connect_ms,tls_ms,ttfb_ms,total_ms`

Os tempos são cumulativos desde o início da requisição. `http_code=000` indica erro de rede ou timeout.
