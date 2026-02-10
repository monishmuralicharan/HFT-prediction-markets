# Kalshi HFT Bot - Operations Runbook

## Quick Reference

### Health Checks
```bash
# Check service status
sudo systemctl status hft-bot

# Check application health
curl http://localhost:8080/health

# Check detailed status
curl http://localhost:8080/status

# View live logs
sudo journalctl -u hft-bot -f
```

### Common Operations

#### Start/Stop/Restart
```bash
sudo systemctl start hft-bot
sudo systemctl stop hft-bot
sudo systemctl restart hft-bot
```

#### View Logs
```bash
# Live logs
sudo journalctl -u hft-bot -f

# Last 100 lines
sudo journalctl -u hft-bot -n 100

# Logs since today
sudo journalctl -u hft-bot --since today

# Error logs only
sudo journalctl -u hft-bot -p err
```

## Architecture Overview

### Components
1. **Market Monitor** - WebSocket connection to Kalshi
2. **Strategy Engine** - Signal generation
3. **Risk Manager** - Position limits and circuit breakers
4. **Execution Engine** - Order submission and management
5. **Position Tracker** - Track open positions
6. **Email Alerter** - Send email notifications

### Data Flow
1. Market Monitor receives price updates
2. Market Filter checks criteria (probability, liquidity, volume)
3. Strategy Engine generates trading signal
4. Risk Manager validates signal (limits, circuit breakers)
5. Execution Engine submits three orders (entry, stop loss, take profit)
6. Position Tracker monitors for exit conditions

## Circuit Breakers

### Types
1. **Daily Loss** - Triggers at -5% daily loss
2. **Consecutive Losses** - Triggers after 5 consecutive losses
3. **API Error Rate** - Triggers at 10% API error rate
4. **WebSocket Disconnect** - Triggers after 15 seconds disconnect

### When Circuit Breaker Triggers
1. All trading halts immediately
2. Email alert sent
3. No new positions opened
4. Existing positions remain open
5. Manual intervention required

### Reset Circuit Breaker
Circuit breakers reset automatically on new day, or manually via code.

## Troubleshooting

### Bot Won't Start
```bash
# Check logs for errors
sudo journalctl -u hft-bot -n 50

# Common issues:
# 1. Invalid configuration
#    -> Check config/secrets.env
# 2. Missing dependencies
#    -> Run: poetry install
# 3. Port already in use
#    -> Check: sudo lsof -i :8080
# 4. Invalid RSA key
#    -> Verify PEM file format and path
```

### No Trades Executing
```bash
# Check if markets are being detected
curl http://localhost:8080/status

# Possible causes:
# 1. No markets meet criteria
#    -> Check config/config.yaml thresholds
# 2. Circuit breaker active
#    -> Check logs for circuit breaker messages
# 3. Risk limits reached
#    -> Check account balance and exposure
# 4. WebSocket disconnected
#    -> Check logs for connection issues
```

### WebSocket Keeps Disconnecting
```bash
# Check connection logs
sudo journalctl -u hft-bot | grep -i websocket

# Possible causes:
# 1. Network issues
#    -> Check: ping trading-api.kalshi.com
# 2. Firewall blocking
#    -> Check AWS security group allows outbound HTTPS
# 3. Authentication failure
#    -> Verify API key and private key are valid
# 4. Rate limiting
#    -> Check if exceeding connection limits
```

### High API Error Rate
```bash
# Check API error logs
sudo journalctl -u hft-bot | grep -i "api error"

# Possible causes:
# 1. Rate limiting
#    -> Reduce rate limits in config
# 2. Invalid credentials
#    -> Check secrets.env
# 3. API downtime
#    -> Check Kalshi status
```

### Email Alerts Not Sending
```bash
# Check email logs
sudo journalctl -u hft-bot | grep -i email

# Possible causes:
# 1. Invalid SMTP credentials
#    -> Check secrets.env
# 2. Firewall blocking port 587
#    -> Check AWS security group
# 3. Gmail app password required
#    -> Generate app password in Gmail settings
```

## Monitoring

### Key Metrics to Watch
1. **Daily P&L** - Should stay within acceptable range
2. **Win Rate** - Target >60%
3. **Open Positions** - Should not exceed max_concurrent
4. **API Error Rate** - Should stay <5%
5. **WebSocket Connection** - Should stay connected

### Daily Checklist
- [ ] Check daily P&L email summary
- [ ] Review circuit breaker status
- [ ] Check open positions count
- [ ] Verify WebSocket connected
- [ ] Review error logs
- [ ] Check account balance

### Weekly Checklist
- [ ] Review trade performance in Supabase
- [ ] Analyze win rate and average P&L
- [ ] Check for any API errors
- [ ] Review configuration parameters
- [ ] Update dependencies if needed

## Emergency Procedures

### Emergency Shutdown
```bash
# Stop the bot immediately
sudo systemctl stop hft-bot

# Cancel all active orders (if needed)
# This would require a manual script or API calls
```

### Close All Positions
1. Stop the bot
2. Log into Kalshi manually
3. Cancel all active orders
4. Close all open positions

### Restart After Circuit Breaker
1. Identify root cause in logs
2. Fix the issue (adjust config, add funds, etc.)
3. Reset circuit breaker (requires code change to add admin endpoint)
4. Restart service: `sudo systemctl restart hft-bot`

## Configuration Management

### Update Configuration
```bash
# Edit config
nano ~/HFT-prediction-markets/config/config.yaml

# Restart service to apply
sudo systemctl restart hft-bot
```

### Update Secrets
```bash
# Edit secrets
nano ~/HFT-prediction-markets/config/secrets.env

# Restart service to apply
sudo systemctl restart hft-bot
```

### Deploy Code Updates
```bash
# Stop service
sudo systemctl stop hft-bot

# Pull latest code
cd ~/HFT-prediction-markets
git pull

# Install dependencies
poetry install

# Start service
sudo systemctl start hft-bot

# Check status
sudo systemctl status hft-bot
```

## Database Queries (Supabase)

### Check Recent Trades
```sql
SELECT * FROM trades
ORDER BY entry_time DESC
LIMIT 20;
```

### Daily Performance
```sql
SELECT * FROM daily_performance
ORDER BY trade_date DESC
LIMIT 7;
```

### Active Positions
```sql
SELECT * FROM active_positions;
```

### Recent Errors
```sql
SELECT * FROM recent_errors
LIMIT 50;
```

### Account Snapshots
```sql
SELECT * FROM account_snapshots
ORDER BY created_at DESC
LIMIT 20;
```

## Performance Optimization

### If Latency is High
1. Move to EC2 instance closer to Kalshi servers (us-east-1)
2. Upgrade to larger instance type (t3.medium)
3. Optimize order submission logic
4. Reduce logging verbosity

### If Memory Usage is High
1. Clear completed positions periodically
2. Reduce log retention in memory
3. Upgrade instance memory

## Contacts & Resources

- **Kalshi API Docs**: https://trading-api.readme.io
- **Supabase Dashboard**: https://app.supabase.com
- **AWS Console**: https://console.aws.amazon.com

## Change Log

Track major changes to configuration or deployment here.

| Date | Change | Reason |
|------|--------|--------|
| 2024-01-01 | Initial deployment | First production release |
| 2026-02-09 | Migrated from Polymarket to Kalshi | Platform migration |
