# 🔒 PolyBot Hardening - Quick Reference

## 3 Pillars of Final Hardening

### 1️⃣ API SOURCE OF TRUTH
```python
# Run at EVERY cycle start
_sync_orders_with_api()
  ├─ Fetch from API
  ├─ Remove ghost orders (local only)
  └─ Import unknown orders (API only)

# Validate consistency
_validate_state_consistency()
  ├─ Check for ghost orders
  ├─ Check for unknown orders
  ├─ Check price mismatches
  └─ Return False if > 5 ghosts (CRITICAL)

# BEFORE placing ANY order
_pre_order_validation()
  ├─ 1. Sync with API
  ├─ 2. Check state consistency
  ├─ 3. Verify position hedges
  ├─ 4. Confirm balance
  └─ Return False = skip orders
```

### 2️⃣ FAIL-SAFE SYSTEM
```
Consecutive API Errors:
  0 → API call fails → 1
  1 → API call fails → 2
  2 → API call fails → 3 (MAX) → KILL SWITCH

Consecutive Exec Errors:
  0 → Exception → 1
  1 → Exception → 2
  3 → Exception → 4
  5 (MAX) → KILL SWITCH

Reset on Success:
  ✅ Cycle completes → counters = 0
```

**Key Log Events:**
- `api_error` - API call failed
- `unexpected_error` - Execution error
- `kill_switch_api_failures` - API errors exceeded
- `kill_switch_exec_errors` - Exec errors exceeded

### 3️⃣ POSITION SAFETY
```python
# Verify all positions hedged
_validate_position_hedges()
  ├─ LONG position (+) → needs SELL hedge
  ├─ SHORT position (-) → needs BUY hedge
  ├─ If missing → AUTO-CREATE hedge
  └─ Return False if hedge creation fails

# Guarantees:
  ✅ No naked positions
  ✅ All positions hedged
  ✅ Auto-hedge placement
  ✅ Unhedged position detection
```

---

## 📊 Cycle Flow (Hardened)

```
START CYCLE
  ↓
✅ Check kill switch file
  ↓
✅ Sync with API
  ↓
✅ Validate state consistency
  ↓
✅ Check balance safety
  ↓
... build context, check fills ...
  ↓
✅ Validate position hedges
  ↓
... decide if refresh needed ...
  ↓
✅ PRE-ORDER VALIDATION
  ├─ Sync API
  ├─ Check consistency
  ├─ Validate hedges
  └─ Confirm balance
  ↓
LOG EVERY DECISION
  ├─ Duplicate check: ACEITA/REJEITADA
  ├─ Position check: ACEITA/REJEITADA
  ├─ Risk check: ACEITA/REJEITADA
  ├─ Buy balance: ACEITA/REJEITADA
  └─ Place order: ACEITA/REJEITADA
  ↓
✅ Final consistency check
  ↓
✅ Save state
  ↓
END CYCLE
```

---

## 🚨 Error Detection

### API Error Stream
```
Cycle 1: API fails → consecutive_api_errors = 1 → continue
Cycle 2: API fails → consecutive_api_errors = 2 → continue
Cycle 3: API fails → consecutive_api_errors = 3 (MAX)
         → Log: "kill_switch_api_failures"
         → Save state
         → STOP BOT
```

### Execution Error Stream
```
Cycle 1: Exception → consecutive_execution_errors = 1 → continue
Cycle 2: Exception → consecutive_execution_errors = 2 → continue
Cycle 3: Exception → consecutive_execution_errors = 3 → continue
Cycle 4: Exception → consecutive_execution_errors = 4 → continue
Cycle 5: Exception → consecutive_execution_errors = 5 (MAX)
         → Log: "kill_switch_exec_errors"
         → Save state
         → STOP BOT
```

### Recovery
```
Cycle N: ✅ Success
  → consecutive_api_errors = 0
  → consecutive_execution_errors = 0
  → last_api_error_time = 0.0
```

---

## 📝 Decision Logging

Every order decision logged with reason:

```
decision_duplicate_order_check: REJEITADA
  reason: Ordem duplicada detectada no preco alvo

decision_position_check: REJEITADA
  reason: Posicao nao permite este lado

decision_risk_check: REJEITADA
  reason: Falha em verificacao de risco

decision_buy_balance_check: REJEITADA
  reason: Saldo insuficiente para BUY
  required: 45.50
  available: 10.00

decision_sell_balance_check: REJEITADA
  reason: Saldo/posicao insuficiente para SELL
  required: 10
  available: 5

decision_place_order: ACEITA
  reason: Todas as verificacoes passaram
  order_id: abc123
```

---

## 🛡️ Hedge Auto-Creation

### Detection
```
net_position > 0 (LONG):
  Check: Is there a SELL order for this token?
  If NO → Unhedged
  
net_position < 0 (SHORT):
  Check: Is there a BUY order for this token?
  If NO → Unhedged
```

### Auto-Placement
```
Get market price: $0.50
Place BUY hedge: $0.49 (1 cent worse)
Place SELL hedge: $0.51 (1 cent worse)

Log: "auto_hedge_success"
  token_id: xyz
  hedge_side: buy|sell
  hedge_size: X
  hedge_price: Y
  order_id: abc123
```

---

## 🔍 State Consistency

### Ghost Orders
```
Local memory has order X
API has NO order X
→ Log warning: "state_inconsistency_ghost_orders"
→ Remove order X from local memory

If > 5 ghost orders:
  → Log CRITICAL
  → Return False
  → FAIL-SAFE triggered
```

### Unknown Orders
```
API has order Y
Local memory has NO order Y
→ Log warning: "state_inconsistency_unknown_orders"
→ Import order Y into known_orders
→ Continue normally
```

### Price Mismatches
```
Local: BUY at $0.45
API:   BUY at $0.46 (diff > $0.01)
→ Log warning: "price_mismatch"
→ Price difference: $0.01
→ Note for audit
```

---

## 📋 Configuration

No new config vars required - all use existing settings:

```bash
# Error limits
MAX_CONSECUTIVE_ERRORS=5              # Execution errors
MAX_API_FAILURE_STREAK=3              # API errors

# Safety
MIN_BALANCE_THRESHOLD=10.0            # Kill switch on low balance

# Kill switch
KILL_SWITCH_FLAG_FILE=.kill_switch    # Manual emergency stop

# State
STATE_FILE=bot_state.json             # Persists all metrics
```

---

## 🚀 Deployment

1. **Verify no syntax errors:**
   ```bash
   python -m py_compile app/bot.py
   python -m py_compile app/polymarket.py
   ```

2. **Review configuration:**
   ```bash
   cat .env
   # Check error limits, thresholds
   ```

3. **Start bot:**
   ```bash
   python main.py
   ```

4. **Monitor logs:**
   ```bash
   # Watch for:
   api_sync_complete  ✅ API sync working
   cycle_success      ✅ Cycle completed
   api_error          ⚠️  API problems
   unexpected_error   ⚠️  Execution problems
   kill_switch        🛑  Emergency stop
   ```

---

## 🆘 Emergency Procedures

### Manual Stop
```bash
touch .kill_switch
# Bot stops at next cycle start
```

### Check Status
```bash
# See consecutive errors
grep "consecutive_errors" logs/

# See state consistency
grep "state_inconsistency" logs/

# See hedging
grep "unhedged_position" logs/

# See kill switch
grep "KILL SWITCH" logs/
```

### Inspect State
```bash
cat bot_state.json
# Shows: known_orders, net_positions, trades_executed, etc.
```

---

## ✅ Verification Checklist

- [ ] Bot starts without errors
- [ ] `api_sync_complete` appears in logs
- [ ] `cycle_success` appears hourly
- [ ] No `state_inconsistency_*` errors
- [ ] No `unhedged_position` errors
- [ ] Decision logging works (see `decision_*` events)
- [ ] Error counters reset after `cycle_success`
- [ ] Kill switch file works (create `.kill_switch`)
- [ ] State file updates with metrics
- [ ] Balance safety works (set low balance)

---

## 🎯 Key Guarantees

✅ **API is Source of Truth**
  - Always synced at cycle start
  - Ghost orders removed
  - Unknown orders imported

✅ **No Naked Positions**
  - All positions verified hedged
  - Auto-hedges created if needed
  - Continuous validation

✅ **Automatic Error Detection**
  - Error counters tracked
  - Auto-stop on threshold
  - Clear audit trail

✅ **Complete Decision Logging**
  - Every decision recorded
  - Reason for each rejection
  - Full audit trail for compliance

✅ **Safe Shutdown**
  - State saved in finally block
  - No data loss on errors
  - Metrics persisted

---

**Version:** Final Hardening v1.0
**Date:** 2026-04-27
**Status:** ✅ Production Ready
