# Phystech Netstatus Zabbix Bot

Автоматизация мониторинга Zabbix через Telegram.

## 📖 Описание

Бот интегрируется с Zabbix API и позволяет в Telegram-чатах:

* Получать сводку открытых проблем `/status`
* Пинговать хосты `/ping <host>`
* Список хостов с действиями `/hosts`
* Строить графики метрик `/graph <itemid> [минут]`
* Получать уведомления от Zabbix через вебхук

## 🚀 Возможности

* **/status** — сводка текущих проблем с кнопкой подробностей
* **/ping** — проверка доступности хоста (ICMP)
* **/hosts** — выбор хоста и быстрые действия
* **/graph** — построение PNG-графика метрики за указанный период
* **Webhook** — отправка алертов Zabbix в Telegram

## 📋 Структура проекта

```
Phystech-Zabbix-Bot/
├── .env.example   # Пример переменных окружения
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── main.py        # Логика бота и FastAPI
├── zbx.py         # Обёртка для Zabbix API
└── __init__.py
```

## 🛠️ Быстрый старт

### 1. Клонирование репозитория

```bash
git clone https://github.com/NeoHikaru/Phystech-Zabbix-Bot.git
cd Phystech-Zabbix-Bot
```

### 2. Настройка переменных окружения

Скопируйте файл `.env.example` в `.env` и заполните параметры:

```dotenv
BOT_TOKEN=ваш_токен_бота_telegram
ADMIN_CHAT_IDS=ID_чата1,ID_чата2  # перечислите ID через запятую
ZABBIX_URL=https://zabbix.example.com/api_jsonrpc.php
ZABBIX_USER=api_user
ZABBIX_PASS=api_password
ZABBIX_TOKEN=      # если используете API-токен
ZABBIX_VERIFY_SSL=true  # отключите для самоподписанных сертификатов
ZABBIX_WEB=https://zabbix.example.com
```

### 3. Запуск с Docker Compose

```bash
docker compose up -d --build
```

После старта:

* HTTP сервер бота будет доступен на `http://localhost:8000`
* Telegram-подключение через long polling

## 🎛️ Использование команд

### /status

Отправляет сводку по количеству открытых проблем в каждой категории. Для просмотра полного списка нажмите кнопку «Показать проблемы» под ответом бота.

### /ping <host>

Пингуем `<host>` 4 раза и выводим результат.

```text
/ping example.com
```

### /hosts

Выводит список всех хостов в виде кнопок для выбора. Нажмите на хост, чтобы выполнить пинг или посмотреть его проблемы.

### /graph <itemid> \[минут]

Строим график метрики с идентификатором `<itemid>` за последние `[минут]` (по умолчанию 60).

```text
/graph 1810864 1440
```

## 🔧 Настройка вебхука Zabbix

1. В интерфейсе Zabbix: «Оповещения» → «Способы оповещения» → добавьте способ оповещения:

   * URL: `http://<bot-host>:8000/zabbix`
   * Метод: POST
2. В действиях (Actions) укажите отправку на ваш способ оповещения и параметры:

   * `subject`: `{EVENT.NAME}`
   * `message`: `{EVENT.STATUS}: {TRIGGER.NAME}`

Бот будет принимать JSON по POST и пересылать в Telegram.

## 🐞 Отладка и проблемы

* Логи контейнера:

  ```bash
  docker compose logs -f zabbixbot
  ```
* Проверьте доступность Zabbix API:

  ```bash
  curl -k -H 'Content-Type: application/json' \
    -d '{"jsonrpc":"2.0","method":"apiinfo.version","params":[],"id":1}' \
    $ZABBIX_URL
  ```

## 📜 Лицензия

MIT © 2025 Отдел информационных технологий Физико-Технического Колледжа
