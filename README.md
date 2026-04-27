# Polymarket Market Maker Bot (Python)

Bot market maker para Polymarket CLOB usando `py-clob-client-v2`.

## Recursos de producao

- Logging estruturado (JSON) para observabilidade.
- Retry basico para falhas transitorias de API.
- Tratamento robusto de erros no loop e no bootstrap.
- Controle de saldo (collateral/token).
- Verificacao de ordens executadas com rastreio por `order_id`.
- Controle de posicao para evitar acumular apenas um lado.
- Escolha automatica do mercado com maior liquidez entre os `token_id` candidatos.
- Ajuste dinamico de spread conforme liquidez e spread atual do book.
- Listagem de ordens abertas.
- Cancelamento de todas as ordens abertas antes de recriar cotacoes.
- Protecao contra duplicacao de ordens no preco alvo.

## Estrutura

- `app/config.py`: leitura e validacao de configuracao via `.env`.
- `app/logging_config.py`: configuracao de logging (`json`/`text`).
- `app/polymarket.py`: wrapper de integracao CLOB.
- `app/bot.py`: estrategia market maker e loop.
- `main.py`: ponto de entrada da aplicacao.

## Requisitos

- Python 3.10+
- Dependencias em `requirements.txt`

## Instalacao

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuracao

1. Copie o exemplo:

```bash
cp .env.example .env
```

2. Preencha no `.env`:
- `PK` (chave privada)
- `TOKEN_ID`
- `TOKEN_IDS` opcional, separado por virgula, para o bot comparar mercados candidatos
- opcionalmente `CLOB_API_KEY`, `CLOB_SECRET`, `CLOB_PASS_PHRASE`

`/.env` esta no `.gitignore` para evitar exposicao de segredo.

## Execucao

```bash
python main.py
```

## Variaveis importantes

- Estrategia: `SPREAD`, `SIZE`, `LOOP_INTERVAL_SECONDS`, `PRICE_TOLERANCE`, `AUTO_SELECT_MARKET`, `MARKET_SCAN_LIMIT`
- Spread adaptativo: `DYNAMIC_SPREAD_ENABLED`, `MIN_SPREAD`, `MAX_SPREAD`, `LIQUIDITY_TARGET_LOW`, `LIQUIDITY_TARGET_HIGH`
- Risco/saldo: `MIN_COLLATERAL_BUFFER`, `TARGET_POSITION_SIZE`, `MAX_POSITION_IMBALANCE`
- Resiliencia: `MAX_RETRIES`, `RETRY_DELAY_SECONDS`
- Logs: `LOG_LEVEL`, `LOG_FORMAT` (`json` ou `text`)

## Deploy (VPS)

Sugestao: executar com `systemd` ou `supervisor`, com restart automatico e coleta de logs em stdout.
