#!/bin/bash

# Скрипт мониторинга Telegram бота

BOT_SERVICE="telegram-bot"
LOG_FILE="/var/log/telegram-bot-monitor.log"
DATE=$(date '+%Y-%m-%d %H:%M:%S')

echo "[$DATE] Проверка состояния бота..." >> $LOG_FILE

# Функция логирования
log() {
    echo "[$DATE] $1" >> $LOG_FILE
    echo "$1"
}

# Проверяем статус сервиса
if systemctl is-active --quiet $BOT_SERVICE; then
    log "✅ Сервис $BOT_SERVICE работает"
else
    log "❌ Сервис $BOT_SERVICE не работает! Перезапускаем..."
    sudo systemctl restart $BOT_SERVICE
    sleep 5
    
    if systemctl is-active --quiet $BOT_SERVICE; then
        log "✅ Сервис успешно перезапущен"
    else
        log "❌ Не удалось перезапустить сервис!"
        # Отправляем уведомление админу
        # Здесь можно добавить отправку email или Telegram уведомления
    fi
fi

# Проверяем использование памяти
MEMORY_USAGE=$(ps -o pid,ppid,%mem,command -C python3 | grep main.py | awk '{print $3}' | head -1)
if [ ! -z "$MEMORY_USAGE" ]; then
    if (( $(echo "$MEMORY_USAGE > 80" | bc -l) )); then
        log "⚠️ Высокое использование памяти: ${MEMORY_USAGE}%"
    else
        log "📊 Использование памяти: ${MEMORY_USAGE}%"
    fi
fi

# Проверяем размер лог файла
if [ -f "bot.log" ]; then
    LOG_SIZE=$(du -h bot.log | cut -f1)
    log "📁 Размер лога: $LOG_SIZE"
    
    # Если лог больше 100MB, архивируем его
    if [ $(du -m bot.log | cut -f1) -gt 100 ]; then
        log "🗃️ Архивируем большой лог файл..."
        mv bot.log "bot.log.$(date +%Y%m%d_%H%M%S)"
        touch bot.log
    fi
fi

# Проверяем доступность веб-панели (если настроена)
WEB_PORT=$(grep WEB_PORT .env 2>/dev/null | cut -d'=' -f2 | tr -d ' ')
if [ ! -z "$WEB_PORT" ]; then
    if curl -f -s "http://localhost:$WEB_PORT" > /dev/null; then
        log "✅ Веб-панель доступна на порту $WEB_PORT"
    else
        log "❌ Веб-панель недоступна на порту $WEB_PORT"
    fi
fi

# Проверяем место на диске
DISK_USAGE=$(df -h . | tail -1 | awk '{print $5}' | sed 's/%//')
if [ $DISK_USAGE -gt 85 ]; then
    log "⚠️ Мало места на диске: ${DISK_USAGE}% используется"
else
    log "💾 Место на диске: ${DISK_USAGE}% используется"
fi

echo "" >> $LOG_FILE
