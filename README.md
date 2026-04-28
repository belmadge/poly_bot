# Polymarket Market Maker Bot

Bot simples para um unico `TOKEN_ID` no Polymarket CLOB.

## Objetivo

Versao enxuta para producao com menos pontos de falha:

- um mercado fixo
- spread fixo
- API sempre como source of truth
- validacoes fail-closed
- cancelamento e recriacao de ordens apenas quando necessario
- `DRY_RUN` para teste seco sem enviar ou cancelar ordens

## Fluxo

1. Sincronizar ordens abertas com a API
2. Validar estado local
3. Confirmar saldo de collateral e token
4. Validar preco e spread do book
5. Cancelar ordens se estiverem duplicadas ou fora do alvo
6. Criar ordens `buy` e `sell` simples, apenas com saldo confirmado

## Garantias de seguranca

- nunca cria ordem com saldo nao confirmado
- nunca usa estado local como fonte primaria sem sync recente
- falha fechado em saldo, sync, book invalido ou estado inconsistente
- para o bot apos erros criticos repetidos
- nao faz auto-hedge, auto-selecao de mercado ou logica ambigua

## Estrutura

- `app/config.py`: configuracao via `.env`
- `app/polymarket.py`: wrapper simples do cliente CLOB
- `app/bot.py`: fluxo principal do market maker
- `app/state.py`: persistencia minima do estado
- `app/logging_config.py`: logging em `text` ou `json`
- `main.py`: ponto de entrada

## Requisitos

- Python 3.10+

## Instalacao

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Configuracao

1. Copie o arquivo:

```bash
copy .env.example .env
```

2. Preencha:

- `PK`
- `TOKEN_ID`

## Variaveis principais

- Estrategia: `SPREAD`, `SIZE`, `LOOP_INTERVAL_SECONDS`, `PRICE_TOLERANCE`
- Saldo e risco: `MIN_COLLATERAL_BUFFER`, `MAX_BALANCE_USAGE_PCT`, `MIN_BALANCE_THRESHOLD`
- Validacao de mercado: `MIN_PRICE_BOUND`, `MAX_PRICE_BOUND`, `MAX_BOOK_SPREAD_PCT`, `MAX_MIDPOINT_DEVIATION_RATIO`
- Resiliencia: `MAX_RETRIES`, `RETRY_DELAY_SECONDS`, `MAX_API_FAILURE_STREAK`, `MAX_CONSECUTIVE_ERRORS`, `MAX_SYNC_AGE_SECONDS`
- Operacao: `STATE_FILE`, `KILL_SWITCH_FLAG_FILE`, `LOG_LEVEL`, `LOG_FORMAT`, `DRY_RUN`

## Execucao

```bash
python main.py
```

## Testes

```bash
python -m pytest -q
```

## Kill switch

Se o arquivo configurado em `KILL_SWITCH_FLAG_FILE` existir, o bot encerra o loop.

## Dry run

Com `DRY_RUN=true`, o bot continua lendo book, saldo e ordens, mas nao envia novas ordens nem cancela ordens existentes.
