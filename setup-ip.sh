#!/bin/bash

# Скрипт быстрой настройки Telegram бота для работы по IP
# Запускать с правами root

set -e

echo "🌐 Настройка Telegram бота для работы по IP..."

# Получаем IP адрес сервера
VPS_IP=$(curl -s ifconfig.me 2>/dev/null || curl -s ipinfo.io/ip 2>/dev/null || echo "127.0.0.1")
echo "📍 IP адрес сервера: $VPS_IP"

# Создаем пользователя botuser если не существует
if ! id "botuser" &>/dev/null; then
    echo "👤 Создаем пользователя botuser..."
    useradd -m -s /bin/bash botuser
    usermod -aG sudo botuser
fi

# Переключаемся на пользователя botuser
echo "📁 Настраиваем проект..."
sudo -u botuser bash << EOF
cd /home/botuser

# Создаем .env файл с IP адресом
cat > telegram-bot/.env << EOL
BOT_TOKEN=YOUR_BOT_TOKEN_HERE
ADMIN_PASSWORD=secure_password_123
ADMIN_IDS=123456789,987654321
WEB_PORT=5000
WEB_BASE_URL=http://$VPS_IP:5000
DATABASE_TYPE=duckdb
DATABASE_PATH=/home/botuser/telegram-bot/data/applications.duckdb
BROADCAST_RATE_PER_SEC=8
BROADCAST_MAX_RETRIES=3
BROADCAST_RETRY_BASE_DELAY=1.0
EOL

echo "✅ Файл .env создан с IP: $VPS_IP"
echo "📝 Не забудьте заменить BOT_TOKEN и ADMIN_IDS в файле .env!"
EOF

# Настраиваем файрвол
echo "🔥 Настраиваем файрвол..."
ufw --force enable
ufw allow ssh
ufw allow 80
ufw allow 5000
ufw status

# Настраиваем Nginx
echo "🌐 Настраиваем Nginx..."
if [ -f "/home/botuser/telegram-bot/nginx-telegram-bot.conf" ]; then
    cp /home/botuser/telegram-bot/nginx-telegram-bot.conf /etc/nginx/sites-available/telegram-bot
    ln -sf /etc/nginx/sites-available/telegram-bot /etc/nginx/sites-enabled/
    rm -f /etc/nginx/sites-enabled/default
    nginx -t
    systemctl restart nginx
    echo "✅ Nginx настроен"
else
    echo "⚠️ Файл nginx-telegram-bot.conf не найден"
fi

# Настраиваем systemd сервис
echo "⚙️ Настраиваем автозапуск..."
if [ -f "/home/botuser/telegram-bot/telegram-bot.service" ]; then
    cp /home/botuser/telegram-bot/telegram-bot.service /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable telegram-bot
    echo "✅ Сервис настроен"
else
    echo "⚠️ Файл telegram-bot.service не найден"
fi

echo ""
echo "🎉 Настройка завершена!"
echo ""
echo "📋 Следующие шаги:"
echo "1. Отредактируйте файл /home/botuser/telegram-bot/.env"
echo "2. Замените BOT_TOKEN на токен от @BotFather"
echo "3. Замените ADMIN_IDS на ваши Telegram ID"
echo "4. Запустите: sudo systemctl start telegram-bot"
echo "5. Проверьте: sudo systemctl status telegram-bot"
echo ""
echo "🌐 Веб-админка будет доступна по адресу:"
echo "   http://$VPS_IP:5000"
echo "   или через Nginx: http://$VPS_IP"
echo ""
echo "📊 Для мониторинга:"
echo "   sudo journalctl -u telegram-bot -f"
