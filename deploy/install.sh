#!/usr/bin/env bash
# One-shot droplet setup for activist-watch.
#
#   ssh root@DROPLET
#   git clone https://github.com/<user>/activist-watch.git /opt/activist-watch
#   # then copy .env from your Mac (it is NOT in the repo — it holds the bot token):
#   #   scp .env root@DROPLET:/opt/activist-watch/.env
#   bash /opt/activist-watch/deploy/install.sh
#
# Idempotent: safe to re-run.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/activist-watch}"
PY="$(command -v python3)"

echo "==> deps"
if command -v apt-get >/dev/null; then
    apt-get update -qq
    apt-get install -y -qq python3-pip git >/dev/null
fi
pip3 install --quiet --break-system-packages -r "$APP_DIR/requirements.txt" 2>/dev/null \
    || pip3 install --quiet -r "$APP_DIR/requirements.txt"

echo "==> checking secrets"
if [ ! -f "$APP_DIR/.env" ]; then
    echo "!! $APP_DIR/.env is missing."
    echo "   It is deliberately NOT in the repo (it holds the Telegram bot token)."
    echo "   From your Mac:  scp .env root@\$DROPLET:$APP_DIR/.env"
    exit 1
fi
chmod 600 "$APP_DIR/.env"
grep -q "AW_TELEGRAM_CHAT_ID=[0-9]" "$APP_DIR/.env" \
    || { echo "!! AW_TELEGRAM_CHAT_ID is empty in .env"; exit 1; }

echo "==> warmup (records current state, sends nothing)"
cd "$APP_DIR"
if [ ! -f "$APP_DIR/data/activist_watch.db" ]; then
    "$PY" watch.py --once
else
    echo "    database already present, skipping warmup"
fi

echo "==> systemd"
install -m 644 "$APP_DIR/deploy/activist-watch.service" /etc/systemd/system/
sed -i "s|__APP_DIR__|$APP_DIR|g; s|__PY__|$PY|g" /etc/systemd/system/activist-watch.service
systemctl daemon-reload
systemctl enable --now activist-watch

echo "==> auto-update + daily health (cron)"
CRON_TMP="$(mktemp)"
crontab -l 2>/dev/null | grep -v "activist-watch" > "$CRON_TMP" || true
cat >> "$CRON_TMP" <<EOF
*/10 * * * * $APP_DIR/deploy/update.sh >> $APP_DIR/data/update.log 2>&1
0 6 * * * cd $APP_DIR && $PY watch.py --health >> $APP_DIR/data/health.log 2>&1
EOF
crontab "$CRON_TMP"
rm -f "$CRON_TMP"
chmod +x "$APP_DIR/deploy/update.sh"

echo
echo "==> done"
systemctl --no-pager status activist-watch | head -5
echo
echo "logs:    journalctl -u activist-watch -f"
echo "update:  every 10 min via cron (git pull -> restart only if changed)"
echo "health:  daily 06:00 to Telegram"
