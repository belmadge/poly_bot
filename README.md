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
4. Validar preco, spread, liquidez, volatilidade e lucro minimo esperado
5. Ajustar quotes com skew simples de inventario
6. Cancelar ordens se estiverem duplicadas ou fora do alvo
7. Criar ordens `buy` e `sell` simples, apenas com saldo confirmado

## Garantias de seguranca

- nunca cria ordem com saldo nao confirmado
- nunca usa estado local como fonte primaria sem sync recente
- falha fechado em saldo, sync, book invalido ou estado inconsistente
- nao opera com spread abaixo do custo estimado, liquidez baixa ou volatilidade alta
- reduz atividade em ciclos sem execucao e pausa em sequencias de erro
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



source .venv/Scripts/activate
python main.py
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
- Protecoes extras: `MIN_OPERABLE_SPREAD`, `MAX_OPERABLE_SPREAD`, `MIN_LIQUIDITY_SCORE`, `MAX_VOLATILITY_RATIO`, `FEE_RATE`, `ESTIMATED_SLIPPAGE_RATE`, `MIN_PROFIT_MARGIN`
- Inventario e protecao: `INVENTORY_TARGET`, `INVENTORY_SOFT_LIMIT`, `INVENTORY_PRICE_ADJUSTMENT`, `PROTECTION_NO_FILL_CYCLES`, `PROTECTION_ERROR_STREAK`, `PROTECTION_PAUSE_SECONDS`, `PROTECTION_SPREAD_MULTIPLIER`, `MAX_CANCELS_PER_MINUTE`, `PAUSE_AFTER_CANCEL_LIMIT_SECONDS`, `MAX_ORDER_AGE_SECONDS`, `MAX_ORDERS_PER_CYCLE`
- Operacao: `STATE_FILE`, `KILL_SWITCH_FLAG_FILE`, `LOG_LEVEL`, `LOG_FORMAT`, `DRY_RUN`

## Execucao

```bash
python main.py
```

## Dashboard web

Para abrir o painel local no navegador:

```bash
python main_web.py
```

Depois acesse:

```text
http://127.0.0.1:8000
```

Voce pode ajustar host e porta com:

- `WEB_HOST`
- `WEB_PORT`

O painel mostra:

- status do bot
- start e stop pelo navegador
- `gross_bought`
- `gross_sold`
- `position_size`
- `realized_pnl`
- pausas de protecao, erros e logs recentes

## Testes

```bash
python -m pytest -q
```

## Kill switch

Se o arquivo configurado em `KILL_SWITCH_FLAG_FILE` existir, o bot encerra o loop.

## Dry run

Com `DRY_RUN=true`, o bot continua lendo book, saldo e ordens, mas nao envia novas ordens nem cancela ordens existentes.
