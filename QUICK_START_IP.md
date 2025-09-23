# 🚀 Быстрый запуск по IP адресу VPS

## 📋 Пошаговая инструкция

### 1️⃣ Подготовка VPS сервера
```bash
# Обновляем систему
sudo apt update && sudo apt upgrade -y

# Устанавливаем необходимые пакеты
sudo apt install python3 python3-pip python3-venv git nginx ufw curl -y
```

### 2️⃣ Загрузка проекта
```bash
# Создаем пользователя
sudo useradd -m -s /bin/bash botuser
sudo usermod -aG sudo botuser

# Переходим к пользователю
sudo su - botuser
cd ~

# Загружаем проект (замените на ваш способ)
# git clone <your-repo> telegram-bot
# или загрузите файлы вручную
cd telegram-bot
```

### 3️⃣ Настройка конфигурации

**Создайте файл `.env`:**
```bash
nano .env
```

**Содержимое файла `.env`:**
```bash
# Замените на ваши данные
BOT_TOKEN=1234567890:YOUR_BOT_TOKEN_FROM_BOTFATHER
ADMIN_PASSWORD=your_secure_password
ADMIN_IDS=123456789,987654321
WEB_PORT=5000
WEB_BASE_URL=http://YOUR_VPS_IP:5000
DATABASE_TYPE=duckdb
DATABASE_PATH=/home/botuser/telegram-bot/data/applications.duckdb
BROADCAST_RATE_PER_SEC=8
```

**Узнать IP сервера:**
```bash
curl ifconfig.me
```

### 4️⃣ Установка и запуск
```bash
# Активируем виртуальное окружение
python3 -m venv venv
source venv/bin/activate

# Устанавливаем зависимости
pip install --upgrade pip
pip install -r requirements.txt

# Создаем папки
mkdir -p data photos exports logs

# Инициализируем базу данных
python3 -c "from database.db_manager import init_database; init_database()"
```

### 5️⃣ Настройка автозапуска
```bash
# Копируем сервис
sudo cp telegram-bot.service /etc/systemd/system/

# Настраиваем автозапуск
sudo systemctl daemon-reload
sudo systemctl enable telegram-bot
sudo systemctl start telegram-bot

# Проверяем статус
sudo systemctl status telegram-bot
```

### 6️⃣ Настройка Nginx (опционально)
```bash
# Копируем конфигурацию
sudo cp nginx-telegram-bot.conf /etc/nginx/sites-available/telegram-bot

# Активируем
sudo ln -s /etc/nginx/sites-available/telegram-bot /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default

# Настраиваем файрвол
sudo ufw enable
sudo ufw allow ssh
sudo ufw allow 80
sudo ufw allow 5000

# Перезапускаем Nginx
sudo nginx -t
sudo systemctl restart nginx
```

---

## 🌐 Доступ к веб-админке

После настройки веб-админка будет доступна по адресам:

- **Прямой доступ**: `http://YOUR_VPS_IP:5000`
- **Через Nginx**: `http://YOUR_VPS_IP` (если настроен)

---

## 🔧 Полезные команды

```bash
# Статус бота
sudo systemctl status telegram-bot

# Перезапуск бота
sudo systemctl restart telegram-bot

# Логи бота
sudo journalctl -u telegram-bot -f

# Логи веб-приложения
tail -f /home/botuser/telegram-bot/bot.log

# Проверка портов
sudo netstat -tulpn | grep :5000

# Проверка доступности
curl http://localhost:5000
```

---

## 🆘 Устранение проблем

### Бот не запускается
```bash
# Проверяем логи
sudo journalctl -u telegram-bot -n 50

# Проверяем токен
grep BOT_TOKEN /home/botuser/telegram-bot/.env
```

### Веб-панель недоступна
```bash
# Проверяем порт
sudo netstat -tulpn | grep :5000

# Проверяем файрвол
sudo ufw status

# Проверяем Nginx
sudo nginx -t
sudo systemctl status nginx
```

### Высокое потребление ресурсов
```bash
# Мониторинг
htop

# Проверка процессов бота
ps aux | grep python3
```

---

## ✅ Чеклист

- [ ] VPS сервер подготовлен
- [ ] Python 3.9+ установлен
- [ ] Пользователь `botuser` создан
- [ ] Проект загружен
- [ ] Файл `.env` настроен с правильным IP
- [ ] Виртуальное окружение создано
- [ ] Зависимости установлены
- [ ] База данных инициализирована
- [ ] Systemd сервис настроен
- [ ] Файрвол настроен
- [ ] Nginx настроен (опционально)
- [ ] Бот запущен и работает
- [ ] Веб-панель доступна

**🎉 Готово! Ваш бот работает по IP адресу VPS!**
