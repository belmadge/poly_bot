# PolyBot Refactoring Guide - All 7 Critical Improvements

## 📋 Overview
This document summarizes all 7 critical improvements implemented for production-grade safety, API synchronization, and performance tracking.

---

## 🔥 PROMPT 1: API SYNCHRONIZATION (CRÍTICO)

### Implementation: `_sync_orders_with_api()`
**Location:** `app/bot.py` - Called in `run_once()` FIRST

```python
def _sync_orders_with_api(self) -> None:
    """
    CRITICAL: Synchronize known_orders with API.
    Remove local orders that no longer exist on API.
    This ensures API is the source of truth.
    """
    # Fetches all open orders from API
    # Removes orders from local state that aren't on API
    # Updates known_orders with latest API data
    # Logs all sync events
```

**Key Points:**
- ✅ Always fetch from API before decisions
- ✅ Remove stale local orders
- ✅ API is source of truth
- ✅ Called at `run_once()` start

**Configuration:** No new config needed - always enabled.

---

## 🔥 PROMPT 2: SMART CANCELLATION

### Implementation: `_should_refresh_orders_smart()`
**Location:** `app/bot.py` - Used in `run_once()`

**Smart Logic:**
1. Only refresh if price moved above `REPOSITION_PRICE_THRESHOLD`
2. Check for price/size mismatches
3. Avoid unnecessary cancel/recreate cycles

**Configuration:**
```bash
REPOSITION_PRICE_THRESHOLD=0.001  # 0.1% default
```

**Key Points:**
- ✅ Reduce order churn
- ✅ Smart price threshold
- ✅ Detailed logging
- ✅ No duplicate orders

---

## 🔥 PROMPT 3: EXECUTION CONTROL (ESSENCIAL)

### Implementation: Enhanced `_check_fills()`
**Location:** `app/bot.py`

**Features:**
1. **Partial Fill Detection:**
   ```python
   is_partial = float(filled_size) < float(original_size)
   ```

2. **Hedge Position Tracking:**
   - Ensures BUY creates matching SELL hedge
   - Ensures SELL creates matching BUY hedge

3. **Detailed Logging:**
   - `filled_size`, `original_size`, `is_partial`
   - Net position after fill

**Key Points:**
- ✅ Detect partial execution
- ✅ Update positions proportionally
- ✅ Ensure hedges created
- ✅ Clear fill logs

---

## 🔥 PROMPT 4: RISK MANAGEMENT (PRODUÇÃO)

### Implementation: Multiple Enhanced Methods
**Location:** `app/bot.py`

**Balance Validation:**
```python
def _has_buy_balance(self, quote: Quote) -> bool
def _has_sell_balance(self, quote: Quote) -> bool
def _has_buy_capacity(self, quote: Quote) -> bool
def _has_sell_capacity(self, quote: Quote, token_balance: float | None) -> bool
```

**Position Control:**
```python
def _within_max_exposure(self, token_id: str, quote: Quote) -> bool
def _position_allows_side(self, side: str, token_balance: float | None) -> bool
```

**Configuration:**
```bash
MAX_POSITION_SIZE=10
MAX_BALANCE_USAGE_PCT=0.9
MIN_COLLATERAL_BUFFER=1.0
MIN_BALANCE_THRESHOLD=10.0
```

**Key Points:**
- ✅ Block orders if max_position_size reached
- ✅ Validate balance before each order
- ✅ Implement capital usage limit
- ✅ Prevent unilateral exposure

---

## 🔥 PROMPT 5: KILL SWITCH (MUITO IMPORTANTE)

### Implementation: Multiple Safety Methods

#### 1. Manual Kill Switch: `_check_kill_switch()`
```python
def _check_kill_switch(self) -> bool:
    """Check if kill switch flag file exists"""
    flag_file_path = Path(self.settings.kill_switch_flag_file)
    if flag_file_path.exists():
        logger.critical("KILL SWITCH: Flag file detected")
        return True
```

**Usage:** Create `.kill_switch` file to stop bot immediately

#### 2. Balance Safety: `_check_balance_safety()`
```python
def _check_balance_safety(self) -> bool:
    """Check if balance above minimum threshold"""
    collateral = self._with_retry(self.client.get_collateral_balance)
    is_safe = collateral >= self.settings.min_balance_threshold
```

**Usage:** Bot stops if balance drops below threshold

#### 3. Error Tracking in `run_forever()`
```python
# Consecutive API errors
if self.consecutive_api_errors >= self.settings.max_api_failure_streak:
    logger.critical("KILL SWITCH: Max API failures exceeded")
    self._save_state()
    break

# Consecutive execution errors
if self.consecutive_execution_errors >= self.settings.max_consecutive_errors:
    logger.critical("KILL SWITCH: Max execution errors exceeded")
    self._save_state()
    break
```

**Configuration:**
```bash
MAX_CONSECUTIVE_ERRORS=5
MIN_BALANCE_THRESHOLD=10.0
MAX_API_FAILURE_STREAK=3
KILL_SWITCH_FLAG_FILE=.kill_switch
```

**Key Points:**
- ✅ X consecutive errors → stop
- ✅ Balance below threshold → stop
- ✅ API fails repeatedly → stop
- ✅ Manual flag file → stop

---

## 🔥 PROMPT 6: REPOSITIONING CONTROL

### Implementation: `_should_refresh_orders_smart()`
**Location:** `app/bot.py` - Used in `run_once()`

**Smart Logic:**
```python
# Only reposition if price moved above threshold
if context.price_change_ratio >= self.settings.reposition_price_threshold:
    return True

# Check for price/size mismatches
for side, quote in context.quotes.items():
    if not self._has_matching_open_order(open_orders, quote):
        return True

# Otherwise keep existing orders
return False
```

**Configuration:**
```bash
REPOSITION_PRICE_THRESHOLD=0.001  # 0.1% triggers reposition
```

**Key Points:**
- ✅ Only recreate if price moves significantly
- ✅ Avoid unnecessary cancel/recreate
- ✅ Implement price tolerance
- ✅ Reduce order churn

---

## 🔥 PROMPT 7: PERFORMANCE LOGGING

### Implementation: `_log_performance_metrics()`
**Location:** `app/bot.py` - Called in `run_once()`

**Metrics Logged Periodically (every `METRICS_LOG_INTERVAL_SECONDS`):**

```python
logger.info(
    "Performance Metrics",
    extra={
        "event": "performance_metrics",
        "trades_executed": self.trades_executed,
        "total_pnl_realized": round(self.total_pnl_realized, 4),
        "estimated_unrealized_pnl": round(estimated_pnl, 4),
        "execution_rate_per_hour": round(execution_rate, 2),
        "open_orders_count": len(self.known_orders),
        "tracked_positions_count": len(self.net_positions),
        "consecutive_api_errors": self.consecutive_api_errors,
        "consecutive_execution_errors": self.consecutive_execution_errors,
        "time_since_last_api_error": round(current_time - self.last_api_error_time, 1),
    },
)
```

**Configuration:**
```bash
METRICS_LOG_INTERVAL_SECONDS=3600  # Log every hour
```

**Metrics:**
1. **PnL Estimated** - Based on positions and prices
2. **Trades Executed** - Total count
3. **Execution Rate** - Trades per hour
4. **Open Orders** - Current count
5. **Error Tracking** - Consecutive errors and timing

**Key Points:**
- ✅ PnL estimated
- ✅ Trades executed count
- ✅ Order execution rate
- ✅ Time-weighted metrics
- ✅ Periodic logging to avoid spam

---

## 📊 State Tracking

### New BotState Fields
```python
@dataclass
class BotState:
    known_orders: dict[str, dict[str, Any]]
    net_positions: dict[str, float]
    last_market_prices: dict[str, float]
    # NEW:
    trades_executed: int = 0
    total_pnl_realized: float = 0.0
    last_metrics_log_time: float = 0.0
    consecutive_api_errors: int = 0
    last_api_error_time: float = 0.0
```

**Persistence:** All metrics saved to `STATE_FILE` and restored on restart.

---

## 🔧 Configuration Summary

### New Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `MAX_CONSECUTIVE_ERRORS` | 5 | Kill switch threshold |
| `MIN_BALANCE_THRESHOLD` | 10.0 | Minimum balance for operation |
| `MAX_API_FAILURE_STREAK` | 3 | Max consecutive API errors |
| `KILL_SWITCH_FLAG_FILE` | .kill_switch | Manual emergency stop file |
| `REPOSITION_PRICE_THRESHOLD` | 0.001 | Price move for reposition (0.1%) |
| `METRICS_LOG_INTERVAL_SECONDS` | 3600 | Metrics logging interval (1 hour) |

### Modified Existing Variables

- `MAX_BALANCE_USAGE_PCT`: Now used in risk validation
- `MAX_POSITION_SIZE`: Now enforced as hard limit
- `PRICE_TOLERANCE`: Used for smart cancellation

---

## 🚀 Execution Flow

```
loop:
  ├─ Check kill switch
  ├─ Try run_once():
  │  ├─ 🔄 Sync with API (source of truth)
  │  ├─ ✅ Check balance safety
  │  ├─ 📊 Build quote context
  │  ├─ ✓ Check fills (partial detection)
  │  ├─ 💰 Get token balance
  │  ├─ 📈 Build execution plan
  │  ├─ 🧹 Cancel inactive markets
  │  ├─ 📋 List open orders (synced)
  │  ├─ 🎯 Smart refresh orders
  │  ├─ 🛡️ Place quotes with risk checks
  │  ├─ 📉 Log performance metrics
  │  └─ 💾 Save state
  ├─ Error handling with tracking
  ├─ Kill switch on thresholds
  └─ Sleep interval
```

---

## ✅ Testing Checklist

- [ ] Bot starts with new config variables
- [ ] `_sync_orders_with_api()` removes stale orders
- [ ] Kill switch flag file stops bot immediately
- [ ] Balance threshold stops bot on low balance
- [ ] Error streak counting triggers kill switch
- [ ] Partial fills detected and logged
- [ ] Smart repositioning reduces API calls
- [ ] Performance metrics appear in logs
- [ ] State file persists metrics across restarts
- [ ] No syntax errors on startup

---

## 🔍 Debugging Tips

### Check API Sync Status
```
event: "api_sync_complete"
api_orders: <count from API>
local_orders: <count in memory>
stale_removed: <removed count>
```

### Monitor Kill Switch Status
```
event: "kill_switch_flag_file"  → Manual trigger
event: "kill_switch_api_failures"  → API errors exceeded
event: "kill_switch_exec_errors"  → Execution errors exceeded
event: "kill_switch_balance"  → Balance too low
```

### View Performance Metrics
```
event: "performance_metrics"
trades_executed: <count>
total_pnl_realized: <value>
estimated_unrealized_pnl: <value>
```

---

## 📝 Log Events Reference

**API Events:**
- `api_sync_complete` - Sync successful
- `api_sync_failed` - Sync error
- `api_sync_stale_removal` - Stale orders removed

**Safety Events:**
- `kill_switch` - Kill switch activated
- `kill_switch_flag_file` - Manual flag detected
- `kill_switch_api_failures` - Too many API errors
- `kill_switch_exec_errors` - Too many exec errors
- `balance_safety_failed` - Balance below minimum

**Order Events:**
- `filled` - Order filled (with `is_partial` flag)
- `order_out_of_range_buy` - Buy outside range
- `order_out_of_range_sell` - Sell outside range

**Repositioning Events:**
- `refresh_price_threshold` - Price moved significantly
- `refresh_price_mismatch` - Order misalignment
- `refresh_keep` - Keep existing orders

**Performance Events:**
- `performance_metrics` - Hourly metrics log

---

## 🎯 Production Checklist

- [ ] All 7 improvements tested and working
- [ ] Configuration values appropriate for your market
- [ ] Monitoring/logging configured
- [ ] Manual kill switch procedure documented
- [ ] Error escalation process defined
- [ ] PnL tracking integrated with accounting
- [ ] State file backup strategy in place

---

## 📞 Support

For questions or issues:
1. Check logs for event types listed above
2. Verify config variables in .env file
3. Ensure API connectivity
4. Check bot_state.json for persisted metrics

---

**Last Updated:** 2026-04-27
**Version:** Production v2.0 - All 7 Critical Improvements
