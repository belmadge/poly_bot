# Polymarket Market Maker Bot (Python)

Bot market maker para Polymarket CLOB usando `py-clob-client-v2`.

## Recursos de produção

- Logging estruturado (JSON) para observabilidade.
- Retry básico para falhas transitórias de API.
- Tratamento robusto de erros no loop e no bootstrap.
- Controle de saldo (collateral/token).
- Cancelamento de ordens antigas, deduplicação e rastreio de fills.

## Estrutura

- `app/config.py`: leitura e validação de configuração via `.env`.
- `app/logging_config.py`: configuração de logging (json/text).
- `app/polymarket.py`: wrapper de integração CLOB.
- `app/bot.py`: estratégia market maker e loop.
- `main.py`: ponto de entrada da aplicação.

## Requisitos

- Python 3.10+
- Dependências em `requirements.txt`

## Instalação

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuração

1. Copie o exemplo:

```bash
cp .env.example .env
```

2. Preencha no `.env`:
   - `PK` (chave privada)
   - `TOKEN_ID`
   - (opcional) `CLOB_API_KEY`, `CLOB_SECRET`, `CLOB_PASS_PHRASE`

> Segurança: `.env` está no `.gitignore` para evitar exposição de segredo.

## Execução

```bash
python main.py
```

## Variáveis importantes

- Estratégia: `SPREAD`, `SIZE`, `LOOP_INTERVAL_SECONDS`, `PRICE_TOLERANCE`
- Risco/saldo: `MIN_COLLATERAL_BUFFER`
- Resiliência: `MAX_RETRIES`, `RETRY_DELAY_SECONDS`
- Logs: `LOG_LEVEL`, `LOG_FORMAT` (`json` ou `text`)

## Deploy (VPS)

Sugestão: executar com `systemd` ou `supervisor`, com restart automático e coleta de logs stdout.
