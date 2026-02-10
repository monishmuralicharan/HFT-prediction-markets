# Deployment Guide

## Prerequisites

### 1. Supabase Setup

1. Create a Supabase project at https://supabase.com
2. Run the migration script:
   - Go to SQL Editor in Supabase dashboard
   - Copy contents of `migrations/001_initial_schema.sql`
   - Execute the script
3. Get your project URL and API key:
   - Settings -> API -> Project URL
   - Settings -> API -> `anon/public` key
4. Save these credentials for later

### 2. Kalshi Account

1. Create a Kalshi account at https://kalshi.com
2. Generate API keys in Settings -> API Keys
3. Save the API key ID and RSA private key PEM file
4. Fund your account with USD
5. **Start small**: Begin with $100-500 for testing

### 3. Email Setup (Gmail)

1. Enable 2-factor authentication on Gmail
2. Generate an app password:
   - Google Account -> Security -> 2-Step Verification -> App passwords
   - Create password for "Mail"
3. Save the 16-character password

### 4. AWS Account (for EC2 deployment)

1. Create AWS account if needed
2. Create IAM user with EC2 permissions
3. Generate access keys

## Local Development

### 1. Install Dependencies

```bash
# Install Poetry if not already installed
curl -sSL https://install.python-poetry.org | python3 -

# Install project dependencies
poetry install
```

### 2. Configure Secrets

```bash
# Copy example secrets file
cp config/secrets.env.example config/secrets.env

# Edit secrets.env with your credentials
nano config/secrets.env
```

Fill in:
- `KALSHI_API_KEY_ID` - Your Kalshi API key ID
- `KALSHI_PRIVATE_KEY_PATH` - Path to your RSA private key PEM file
- `SUPABASE_URL` - Your Supabase project URL
- `SUPABASE_KEY` - Your Supabase API key
- `SMTP_HOST` - smtp.gmail.com
- `SMTP_PORT` - 587
- `SMTP_USER` - Your Gmail address
- `SMTP_PASSWORD` - Your Gmail app password
- `ALERT_EMAIL` - Email to receive alerts

### 3. Configure Strategy (Optional)

Edit `config/config.yaml` to adjust:
- Trading thresholds
- Position sizes
- Risk limits
- `use_demo: true` for demo API (recommended for testing)

### 4. Test Configuration

```bash
# Test that configuration loads
poetry run python -c "from src.config import get_config; config = get_config(); print('Config loaded successfully')"
```

### 5. Run Tests

```bash
# Run unit tests
poetry run pytest tests/unit/ -v

# Run all tests
poetry run pytest -v
```

### 6. Run Bot Locally

```bash
# Run in development mode
poetry run python -m src.main
```

Press Ctrl+C to stop.

## Production Deployment (AWS EC2)

### 1. Launch EC2 Instance

1. Go to AWS EC2 Console
2. Launch Instance:
   - **AMI**: Ubuntu 22.04 LTS
   - **Instance Type**: t3.small (or t3.medium for better performance)
   - **Storage**: 20 GB GP3
   - **Security Group**:
     - Allow SSH (port 22) from your IP
     - Allow HTTP (port 8080) from your IP (for health checks)
     - Allow outbound HTTPS (port 443) - default
3. Create or select key pair for SSH access
4. Launch instance

### 2. Connect to Instance

```bash
# SSH into instance
ssh -i your-key.pem ubuntu@<instance-public-ip>
```

### 3. Upload Code

Option A: Using git (if your repo is on GitHub)
```bash
ssh ubuntu@<instance-ip>
git clone <your-repo-url>
cd HFT-prediction-markets
```

Option B: Using scp
```bash
# From your local machine
scp -i your-key.pem -r HFT-prediction-markets ubuntu@<instance-ip>:~/
```

### 4. Upload Private Key

```bash
# Copy your Kalshi private key to the server
scp -i your-key.pem kalshi_private_key.pem ubuntu@<instance-ip>:~/.kalshi/
ssh ubuntu@<instance-ip> "chmod 600 ~/.kalshi/kalshi_private_key.pem"
```

### 5. Run Setup Script

```bash
cd ~/HFT-prediction-markets
chmod +x deploy/setup_ec2.sh
./deploy/setup_ec2.sh
```

### 6. Configure Secrets

```bash
nano ~/HFT-prediction-markets/config/secrets.env
```

Enter all your credentials. Save and exit (Ctrl+X, Y, Enter).

### 7. Start Service

```bash
# Start the service
sudo systemctl start hft-bot

# Check status
sudo systemctl status hft-bot

# View logs
sudo journalctl -u hft-bot -f
```

### 8. Verify Deployment

```bash
# Check health endpoint
curl http://localhost:8080/health

# Should return: {"status": "healthy", "uptime": <seconds>}

# Check detailed status
curl http://localhost:8080/status
```

### 9. Monitor Initial Run

Monitor the bot for at least 30 minutes:

```bash
# Watch logs
sudo journalctl -u hft-bot -f

# Watch for:
# - "Bot started successfully"
# - WebSocket connection established
# - Market opportunities detected (if any)
# - No error messages
```

## Post-Deployment

### 1. Set Up Monitoring

1. Check health endpoint regularly:
   ```bash
   */5 * * * * curl -f http://localhost:8080/health || echo "Bot is down!" | mail -s "HFT Bot Alert" your@email.com
   ```

2. Monitor email alerts - you should receive:
   - Position opened/closed alerts
   - Daily summary (at configured time)
   - Circuit breaker alerts (if triggered)

### 2. Initial Trading

**Start conservatively:**

1. Use demo mode first (`use_demo: true`)
2. Switch to production with small position sizes ($50-100)
3. Monitor for 24-48 hours
4. Check P&L and performance
5. Gradually increase limits if performing well

### 3. Regular Maintenance

**Daily:**
- Check daily summary email
- Review logs for errors
- Verify bot is running: `sudo systemctl status hft-bot`

**Weekly:**
- Review trade performance in Supabase
- Check win rate and P&L
- Adjust configuration if needed

### 4. Scaling Up

Once comfortable with performance:

1. Increase position sizes gradually
   - Edit `config/config.yaml`
   - Increase `positions.min_position_size` and `max_position_size`
   - Restart: `sudo systemctl restart hft-bot`

2. Increase concurrent positions
   - Edit `positions.max_concurrent`
   - Restart service

3. Add more capital to your Kalshi account

## Troubleshooting

### Bot Won't Start

```bash
# Check logs for specific error
sudo journalctl -u hft-bot -n 100 --no-pager

# Common issues:
# 1. Configuration error - check secrets.env
# 2. Missing dependencies - run: poetry install
# 3. Permission issues - check file ownership
# 4. Invalid RSA key - verify PEM file format
```

### No Trades Executing

Possible reasons:
1. **No markets meet criteria** - Markets must have:
   - Probability >= 85%
   - Liquidity >= $500
   - Volume >= $10,000
   - Spread <= 2%
   - Room for 2% profit before 0.99 ceiling

2. **Circuit breaker active** - Check logs for circuit breaker messages

3. **Insufficient balance** - Check account balance

### Email Alerts Not Working

```bash
# Test email configuration
python3 << EOF
import smtplib
from email.mime.text import MIMEText

msg = MIMEText("Test")
msg['Subject'] = 'Test'
msg['From'] = 'your@gmail.com'
msg['To'] = 'your@gmail.com'

with smtplib.SMTP('smtp.gmail.com', 587) as s:
    s.starttls()
    s.login('your@gmail.com', 'your-app-password')
    s.send_message(msg)
print("Email sent successfully")
EOF
```

## Backup and Recovery

### Backup Configuration

```bash
# Backup configuration files
scp ubuntu@<instance-ip>:~/HFT-prediction-markets/config/secrets.env ./backup/
scp ubuntu@<instance-ip>:~/HFT-prediction-markets/config/config.yaml ./backup/
```

### Database Backup

Supabase provides automatic backups. To export data:

```sql
-- In Supabase SQL Editor
COPY (SELECT * FROM trades) TO '/tmp/trades.csv' WITH CSV HEADER;
COPY (SELECT * FROM account_snapshots) TO '/tmp/snapshots.csv' WITH CSV HEADER;
```

## Updating the Bot

```bash
# SSH into instance
ssh ubuntu@<instance-ip>

# Stop the service
sudo systemctl stop hft-bot

# Update code
cd ~/HFT-prediction-markets
git pull  # or upload new files

# Install any new dependencies
poetry install

# Restart service
sudo systemctl start hft-bot

# Verify
sudo systemctl status hft-bot
sudo journalctl -u hft-bot -f
```

## Uninstall

```bash
# Stop and disable service
sudo systemctl stop hft-bot
sudo systemctl disable hft-bot

# Remove service file
sudo rm /etc/systemd/system/hft-bot.service
sudo systemctl daemon-reload

# Remove application
rm -rf ~/HFT-prediction-markets

# Terminate EC2 instance from AWS Console
```

## Security Best Practices

1. **Never commit secrets.env to git**
   - Already in .gitignore
   - Double check before pushing

2. **Protect your RSA private key**
   - Set file permissions: `chmod 600 kalshi_private_key.pem`
   - Never share private key
   - Store backup in secure location (password manager)

3. **Limit SSH access**
   - Only allow SSH from your IP in security group
   - Use SSH keys, not passwords

4. **Monitor for unusual activity**
   - Check logs daily
   - Review trades in Supabase
   - Set up email alerts

## Cost Estimate

**AWS EC2:**
- t3.small: ~$15-20/month
- t3.medium: ~$30-40/month
- Storage: ~$2/month

**Supabase:**
- Free tier sufficient for starting
- Pro tier: $25/month (if needed)

**Total:** ~$17-67/month depending on configuration

## Support

For issues:
1. Check RUNBOOK.md for troubleshooting
2. Review logs: `sudo journalctl -u hft-bot -f`
3. Check Kalshi API status
4. Review Supabase logs

## Disclaimer

This bot trades real money. Start with small amounts and monitor closely. Trading carries risk of loss. Use at your own risk.
