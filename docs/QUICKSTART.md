# Quick Start Guide

Get your Kalshi HFT bot running in 5 minutes.

## Prerequisites

- Python 3.11+
- Kalshi account with API access
- Supabase account (free tier)
- Gmail account

## Step 1: Install Dependencies

```bash
# Install Poetry
curl -sSL https://install.python-poetry.org | python3 -

# Install project dependencies
poetry install
```

## Step 2: Set Up Supabase

1. Create account at https://supabase.com
2. Create new project
3. Go to SQL Editor
4. Copy and run `migrations/001_initial_schema.sql`
5. Get your credentials:
   - Project URL: Settings -> API -> URL
   - API Key: Settings -> API -> `anon` key

## Step 3: Set Up Kalshi API Keys

1. Log into your Kalshi account
2. Go to Settings -> API Keys
3. Create a new API key (generates a key ID and RSA private key PEM)
4. Save the private key PEM file securely (e.g., `~/.kalshi/private_key.pem`)

## Step 4: Configure Secrets

```bash
# Copy template
cp config/secrets.env.example config/secrets.env

# Edit with your credentials
nano config/secrets.env
```

Fill in:
```env
KALSHI_API_KEY_ID=your_api_key_id
KALSHI_PRIVATE_KEY_PATH=/path/to/your/kalshi_private_key.pem
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_KEY=your_supabase_key
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your@gmail.com
SMTP_PASSWORD=your_gmail_app_password
ALERT_EMAIL=your@email.com
ENVIRONMENT=development
```

## Step 5: Adjust Configuration (Optional)

Edit `config/config.yaml` to adjust:
- `api.use_demo: true` - Start with demo mode (recommended)
- `positions.min_position_size: 50` - Minimum trade size
- `positions.max_position_size: 1000` - Maximum trade size
- `strategy.entry_threshold: 0.85` - Minimum probability (85%)

## Step 6: Run Tests

```bash
poetry run pytest tests/unit/ -v
```

All tests should pass.

## Step 7: Start the Bot

```bash
poetry run python -m src.main
```

You should see:
```
[INFO] Starting Kalshi HFT Bot
[INFO] Loaded initial markets
[INFO] All components initialized
[INFO] Bot started successfully
[INFO] Connected to WebSocket
[INFO] Subscribed to Kalshi channels
```

## Step 8: Verify It's Working

In another terminal:

```bash
# Check health
curl http://localhost:8080/health

# Check detailed status
curl http://localhost:8080/status
```

## What Happens Next?

The bot will:
1. Connect to Kalshi WebSocket (demo or production)
2. Monitor prediction markets in real-time
3. Filter for opportunities (85%+ probability, good liquidity)
4. Execute trades when criteria met
5. Send email alerts for positions opened/closed
6. Send daily summary email

## Monitoring

- **Logs**: Watch the console output
- **Health**: Check http://localhost:8080/status
- **Email**: You'll receive alerts for all trades
- **Database**: Query Supabase to see trades

## Safety Features Active

- Circuit breaker at -5% daily loss
- Max 10 concurrent positions
- Max 30% total exposure
- Stop loss at -1%
- Take profit at +2%
- Auto-exit after 2 hours

## Stop the Bot

Press `Ctrl+C` in the terminal.

The bot will:
1. Stop accepting new signals
2. Cancel all pending orders
3. Disconnect WebSocket
4. Save final account snapshot
5. Shut down gracefully

## Next Steps

**For Local Development:**
- See `README.md` for detailed docs
- See `RUNBOOK.md` for operations

**For Production Deployment:**
- See `DEPLOYMENT.md` for EC2 setup
- Set `api.use_demo: false` in config for live trading
- Start with small position sizes ($50-100)
- Monitor closely for first 24 hours

## Troubleshooting

**Bot won't start?**
```bash
# Check logs for errors
cat logs/bot.log

# Verify configuration
poetry run python -c "from src.config import get_config; config = get_config()"
```

**No trades executing?**
- Check if any markets meet criteria (85%+ probability)
- Verify account has sufficient balance
- Check logs for circuit breaker messages

**Emails not sending?**
- Verify Gmail app password (not regular password)
- Check SMTP settings in secrets.env

## Important Notes

- **Start with Demo**: Use `use_demo: true` to test against Kalshi's demo API first
- **Start Small**: Begin with $100-500 total capital when going live
- **Monitor Closely**: Check daily for first week
- **Risk Warning**: Trading involves risk of loss

## Support

- Check `RUNBOOK.md` for detailed troubleshooting
- Review logs in console and Supabase
- Check circuit breaker status in `/status` endpoint
