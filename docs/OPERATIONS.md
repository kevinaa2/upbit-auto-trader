# 24/7 Operations

This project is designed to run continuously on a Linux VPS with systemd.

## Recommended Architecture

- GitHub stores the source code.
- A VPS runs the bot with systemd.
- systemd restarts the bot if it crashes or the server reboots.
- Secrets are stored in `/etc/upbit-auto-trader.env`, not in GitHub.
- Alerts are sent through Telegram or a generic webhook.

## Server Setup

Example on Ubuntu:

```bash
sudo apt update
sudo apt install -y git python3
sudo git clone https://github.com/kevinaa2/upbit-auto-trader.git /opt/upbit-auto-trader
cd /opt/upbit-auto-trader
sudo bash deploy/install-systemd.sh /opt/upbit-auto-trader
sudo nano /etc/upbit-auto-trader.env
sudo systemctl start upbit-auto-trader
```

Check status:

```bash
sudo systemctl status upbit-auto-trader
journalctl -u upbit-auto-trader -f
```

Stop:

```bash
sudo systemctl stop upbit-auto-trader
```

Restart after code updates:

```bash
cd /opt/upbit-auto-trader
sudo git pull
sudo systemctl restart upbit-auto-trader
```

## Telegram Alerts

Set these values in `/etc/upbit-auto-trader.env`:

```bash
ALERTS_ENABLED=true
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

Test locally:

```bash
python3 -m upbit_bot test-alert --message "server alert test"
```

## Runtime Arguments

Change `AUTO_TRADER_ARGS` in `/etc/upbit-auto-trader.env`.

Conservative example:

```bash
AUTO_TRADER_ARGS=run-auto --live --yes --allow-full-balance --cash-usage-percent 25 --interval-seconds 600 --use-openai-info --alert-heartbeat-cycles 6
```

Full-balance example:

```bash
AUTO_TRADER_ARGS=run-auto --live --yes --allow-full-balance --cash-usage-percent 100 --interval-seconds 600 --use-openai-info --alert-heartbeat-cycles 6
```

## Log Rotation

Optional:

```bash
sudo cp /opt/upbit-auto-trader/deploy/upbit-auto-trader.logrotate /etc/logrotate.d/upbit-auto-trader
```
