#!/bin/bash

# EC2 Setup Script for Kalshi HFT Bot
# Run this script on a fresh Ubuntu EC2 instance (t3.small or larger)

set -e

echo "=== Kalshi HFT Bot - EC2 Setup ==="
echo

# Update system
echo "Updating system packages..."
sudo apt-get update
sudo apt-get upgrade -y

# Install required packages
echo "Installing required packages..."
sudo apt-get install -y \
    python3.11 \
    python3.11-venv \
    python3-pip \
    git \
    curl \
    build-essential \
    libssl-dev \
    libffi-dev

# Install Poetry
echo "Installing Poetry..."
curl -sSL https://install.python-poetry.org | python3 -
export PATH="/home/ubuntu/.local/bin:$PATH"
echo 'export PATH="/home/ubuntu/.local/bin:$PATH"' >> ~/.bashrc

# Clone repository (or you can scp the code)
echo "Setting up application directory..."
cd ~
if [ -d "HFT-prediction-markets" ]; then
    echo "Directory already exists, pulling latest changes..."
    cd HFT-prediction-markets
    git pull
else
    echo "Please upload your code to ~/HFT-prediction-markets"
    mkdir -p HFT-prediction-markets
    cd HFT-prediction-markets
fi

# Install Python dependencies
echo "Installing Python dependencies..."
poetry install --only main

# Setup configuration
echo "Setting up configuration..."
if [ ! -f "config/secrets.env" ]; then
    echo "Creating secrets.env from template..."
    cp config/secrets.env.example config/secrets.env
    echo
    echo "IMPORTANT: Edit config/secrets.env with your credentials!"
    echo "   Run: nano config/secrets.env"
    echo
fi

# Create directory for Kalshi private key
mkdir -p ~/.kalshi
echo "Place your Kalshi RSA private key at ~/.kalshi/kalshi_private_key.pem"
echo "   Then set KALSHI_PRIVATE_KEY_PATH in config/secrets.env"

# Install systemd service
echo "Installing systemd service..."
sudo cp deploy/hft-bot.service /etc/systemd/system/
sudo systemctl daemon-reload

# Enable and start service
echo "Enabling and starting service..."
sudo systemctl enable hft-bot.service

echo
echo "=== Setup Complete ==="
echo
echo "Next steps:"
echo "1. Place your RSA key: ~/.kalshi/kalshi_private_key.pem"
echo "2. Edit configuration: nano ~/HFT-prediction-markets/config/secrets.env"
echo "3. Review settings: nano ~/HFT-prediction-markets/config/config.yaml"
echo "4. Start the service: sudo systemctl start hft-bot"
echo "5. Check status: sudo systemctl status hft-bot"
echo "6. View logs: sudo journalctl -u hft-bot -f"
echo "7. Check health: curl http://localhost:8080/health"
echo
echo "To stop the service: sudo systemctl stop hft-bot"
echo "To restart: sudo systemctl restart hft-bot"
echo
