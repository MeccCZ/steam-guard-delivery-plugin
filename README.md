# 🔐 Steam Guard Delivery Plugin

Плагин для FunPay Cardinal, который автоматически выдает Steam Guard коды покупателям Steam аккаунтов с ограничением доступа по времени.

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/)
[![FunPay Cardinal](https://img.shields.io/badge/FunPay%20Cardinal-v1.0+-green.svg)](https://github.com/sidor0912/FunPayCardinal)

## ✨ Возможности

- ✅ **Автоматическая выдача** Steam Guard кода при покупке
- ✅ **Запрос кода по команде** `!code` в любое время
- ✅ **Ограничение доступа** на 30 дней (настраивается)
- ✅ **База данных покупателей** с отслеживанием срока доступа
- ✅ **Управление через Telegram** - добавление аккаунтов, просмотр статистики
- ✅ **Поддержка множества аккаунтов** - неограниченное количество
- ✅ **Безопасность** - коды генерируются локально, shared_secret не передается

## 📦 Установка

### Шаг 1: Скачайте плагин

```bash
# Скачайте steam_guard_delivery.py
wget https://raw.githubusercontent.com/MeccCZ/steam-guard-delivery-plugin/main/steam_guard_delivery.py

# Или клонируйте репозиторий
git clone https://github.com/MeccCZ/steam-guard-delivery-plugin.git
```

### Шаг 2: Установите в FunPay Cardinal

```bash
# Скопируйте файл в папку plugins
cp steam_guard_delivery.py /path/to/FunPayCardinal/plugins/

# Перезапустите Cardinal
python main.py
```

### Шаг 3: Настройте через Telegram

1. Откройте Telegram бота FunPay Cardinal
2. Перейдите: **Плагины** → **Steam Guard Delivery** → **⚙️ Настройки**
3. Нажмите **➕ Добавить аккаунт**
4. Отправьте: `НазваниеАккаунта|shared_secret`

**Пример:**
```
MyAccount|wB2k7j9L3mN5pQ8rT1vX4yZ6aB2cD4eF=
```

## 🚀 Быстрый старт

### Получение shared_secret

1. Откройте ваш `.maFile` (файл Steam Desktop Authenticator)
2. Найдите строку: `"shared_secret": "ваш_ключ"`
3. Скопируйте значение

**Пример maFile:**
```json
{
  "shared_secret": "wB2k7j9L3mN5pQ8rT1vX4yZ6aB2cD4eF=",
  "account_name": "MyAccount"
}
```

### Добавление аккаунта

Через Telegram бота:
```
Плагины → Steam Guard Delivery → ⚙️ Настройки → ➕ Добавить аккаунт
```

Отправьте:
```
MyAccount|wB2k7j9L3mN5pQ8rT1vX4yZ6aB2cD4eF=
```

### Настройка лота на FunPay

Добавьте название аккаунта в описание лота:
```
Steam аккаунт MyAccount
Уровень: 50
Prime статус
```

## 💡 Как это работает

### 1. Покупка
```
Покупатель оформляет заказ
    ↓
Плагин определяет аккаунт по названию
    ↓
Генерирует Steam Guard код
    ↓
Отправляет код автоматически
    ↓
Сохраняет доступ на 30 дней
```

### 2. Запрос кода
```
Покупатель пишет: !code
    ↓
Плагин проверяет доступ
    ↓
Генерирует актуальный код
    ↓
Отправляет код с информацией о сроке
```

### 3. Истечение срока
```
Прошло 30 дней
    ↓
Покупатель пишет: !code
    ↓
Плагин сообщает об истечении доступа
    ↓
Для получения кодов нужна повторная покупка
```

## ⚙️ Настройки

| Параметр | Описание | По умолчанию |
|----------|----------|--------------|
| `enabled` | Включен ли плагин | `true` |
| `access_days` | Срок доступа (дней) | `30` |
| `auto_send_on_purchase` | Авто-отправка при покупке | `true` |
| `command` | Команда запроса кода | `!code` |

Все настройки доступны через Telegram бота.

## 📋 Примеры сообщений

### Успешная выдача кода
```
🔐 Steam Guard код для аккаунта MyAccount:

AB123

⏰ Код действителен 30 секунд
📅 Доступ истекает через 25 дн. (22.05.2026)

💡 Для получения нового кода напишите: !code
```

### Истек срок доступа
```
❌ Ваш доступ истек 22.05.2026.
Для получения кодов необходимо повторно приобрести товар.
```

## 🛠️ Утилиты

### test_steam_guard.py
Тестирование генерации кодов перед добавлением в плагин.

```bash
python utils/test_steam_guard.py
```

### import_mafiles.py
Массовый импорт аккаунтов из папки с maFiles.

```bash
python utils/import_mafiles.py
```

## 📁 Структура файлов

```
storage/plugins/steam_guard/
├── settings.json      # Настройки плагина
├── accounts.json      # База Steam аккаунтов
└── buyers.json        # База покупателей
```

## 🔐 Безопасность

⚠️ **ВАЖНО:**
- Храните `shared_secret` в безопасности
- Не передавайте `shared_secret` третьим лицам
- Регулярно делайте резервные копии базы данных
- Используйте сложные пароли для доступа к серверу

**Резервная копия:**
```bash
cp -r storage/plugins/steam_guard/ backups/
```

## 🐛 Решение проблем

### Код не совпадает с Steam Mobile Authenticator
**Решение:** Синхронизируйте время на сервере
```bash
sudo ntpdate -s time.nist.gov
```

### Покупатель не получает код автоматически
**Решение:** Убедитесь, что название аккаунта есть в описании лота

### Команда !code не работает
**Решение:** Проверьте логи
```bash
grep "steam_guard" logs/FPC.log | tail -20
```

## 📚 Документация

- [Подробная инструкция](docs/INSTALLATION.md)
- [API документация](docs/API.md)
- [Часто задаваемые вопросы](docs/FAQ.md)

## 🤝 Вклад в проект

Приветствуются любые улучшения! Создавайте Pull Request или Issue.

## 📞 Поддержка

- **Telegram:** @MeccCZ
- **Email:** your@email.com
- **Issues:** [GitHub Issues](https://github.com/MeccCZ/steam-guard-delivery-plugin/issues)

## 📄 Лицензия

MIT License - см. файл [LICENSE](LICENSE)

## ⭐ Благодарности

- [FunPay Cardinal](https://github.com/sidor0912/FunPayCardinal) - за отличный фреймворк
- [Steam Desktop Authenticator](https://github.com/Jessecar96/SteamDesktopAuthenticator) - за вдохновение

## 📊 Статистика

![GitHub stars](https://img.shields.io/github/stars/MeccCZ/steam-guard-delivery-plugin?style=social)
![GitHub forks](https://img.shields.io/github/forks/MeccCZ/steam-guard-delivery-plugin?style=social)

---

**Версия:** 1.0.0  
**Дата:** 27.04.2026  
**Автор:** @MeccCZ

🎉 **Готово к использованию! Удачных продаж!** 🚀
