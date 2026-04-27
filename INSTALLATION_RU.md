# 🚀 Установка Steam Guard Delivery Plugin

## Требования

- Python 3.7+
- FunPay Cardinal v1.0+
- Telegram бот (настроенный в Cardinal)

## Быстрая установка

### 1. Скачайте плагин

```bash
# Скачайте файл напрямую
wget https://raw.githubusercontent.com/MeccCZ/steam-guard-delivery-plugin/main/steam_guard_delivery.py

# Или клонируйте весь репозиторий
git clone https://github.com/MeccCZ/steam-guard-delivery-plugin.git
```

### 2. Установите в Cardinal

```bash
# Скопируйте файл в папку plugins
cp steam_guard_delivery.py /path/to/FunPayCardinal/plugins/

# Перезапустите Cardinal
cd /path/to/FunPayCardinal
python main.py
```

### 3. Проверьте установку

В логах должна появиться строка:
```
[INFO] Плагин Steam Guard Delivery v1.0.0 инициализирован
```

## Настройка

### Шаг 1: Получите shared_secret

1. Откройте папку Steam Desktop Authenticator
2. Найдите файл `.maFile` вашего аккаунта
3. Откройте его в текстовом редакторе
4. Найдите строку `"shared_secret"`
5. Скопируйте значение

**Пример maFile:**
```json
{
  "shared_secret": "wB2k7j9L3mN5pQ8rT1vX4yZ6aB2cD4eF=",
  "account_name": "MyAccount",
  "identity_secret": "...",
  ...
}
```

### Шаг 2: Добавьте аккаунт через Telegram

1. Откройте Telegram бота FunPay Cardinal
2. Перейдите: **Плагины** → **Steam Guard Delivery**
3. Нажмите **⚙️ Настройки**
4. Нажмите **➕ Добавить аккаунт**
5. Отправьте: `НазваниеАккаунта|shared_secret`

**Пример:**
```
MyAccount|wB2k7j9L3mN5pQ8rT1vX4yZ6aB2cD4eF=
```

### Шаг 3: Настройте лот на FunPay

Добавьте название аккаунта в описание лота:

```
Steam аккаунт MyAccount
Уровень: 50
Prime статус
Игры: CS2, Dota 2
```

**Важно:** Название аккаунта должно точно совпадать с тем, что вы указали в плагине.

## Проверка работы

### Тест 1: Проверка кода

1. В Telegram боте откройте **Плагины** → **Steam Guard Delivery** → **⚙️ Настройки**
2. Нажмите **📋 Список аккаунтов**
3. Проверьте, что код генерируется и совпадает с Steam Mobile Authenticator

### Тест 2: Команда !code

1. Создайте тестовый заказ или вручную добавьте покупателя в базу
2. Напишите в чат с покупателем: `!code`
3. Проверьте, что бот отправил код

## Структура файлов

После установки будет создана следующая структура:

```
FunPayCardinal/
├── plugins/
│   └── steam_guard_delivery.py          # Основной файл плагина
│
└── storage/
    └── plugins/
        └── steam_guard/
            ├── settings.json            # Настройки (создается автоматически)
            ├── accounts.json            # База аккаунтов (создается автоматически)
            └── buyers.json              # База покупателей (создается автоматически)
```

## Настройки

Все настройки доступны через Telegram бота:

| Настройка | Описание | По умолчанию |
|-----------|----------|--------------|
| Плагин включен | Включить/выключить плагин | ✅ Включен |
| Срок доступа | Количество дней доступа к кодам | 30 дней |
| Авто-отправка | Автоматическая отправка при покупке | ✅ Включена |
| Команда | Команда для запроса кода | `!code` |

## Решение проблем

### Плагин не загружается

**Проблема:** В логах ошибка "невалидный UUID"

**Решение:** Убедитесь, что вы скачали последнюю версию плагина

---

### Код не совпадает с Steam Mobile Authenticator

**Проблема:** Генерируемый код не совпадает с кодом в приложении

**Решение:** Синхронизируйте время на сервере
```bash
sudo ntpdate -s time.nist.gov
```

---

### Покупатель не получает код автоматически

**Проблема:** При покупке код не отправляется

**Решение:**
1. Проверьте, что название аккаунта есть в описании лота
2. Проверьте, что аккаунт добавлен в базу (📋 Список аккаунтов)
3. Проверьте логи: `grep "steam_guard" logs/FPC.log`

---

### Команда !code не работает

**Проблема:** Покупатель пишет `!code`, но ответа нет

**Решение:**
1. Проверьте, что плагин включен
2. Проверьте, что у покупателя есть доступ (👥 Список покупателей)
3. Проверьте логи: `grep "steam_guard" logs/FPC.log`

## Безопасность

⚠️ **ВАЖНО:**

1. **Храните shared_secret в безопасности**
   - Не передавайте третьим лицам
   - Не публикуйте в открытом доступе

2. **Регулярно делайте резервные копии**
   ```bash
   cp -r storage/plugins/steam_guard/ backups/steam_guard_$(date +%Y%m%d)/
   ```

3. **Ограничьте доступ к файлам**
   ```bash
   chmod 600 storage/plugins/steam_guard/*.json
   ```

4. **Используйте сложные пароли**
   - Для доступа к серверу
   - Для Telegram бота

## Обновление

### Обновление до новой версии

1. **Сделайте резервную копию**
   ```bash
   cp -r storage/plugins/steam_guard/ backups/
   ```

2. **Скачайте новую версию**
   ```bash
   wget https://raw.githubusercontent.com/MeccCZ/steam-guard-delivery-plugin/main/steam_guard_delivery.py -O plugins/steam_guard_delivery.py
   ```

3. **Перезапустите Cardinal**
   ```bash
   python main.py
   ```

4. **Проверьте версию в логах**
   ```
   [INFO] Плагин Steam Guard Delivery v1.X.X инициализирован
   ```

## Удаление

### Полное удаление плагина

1. **Удалите файл плагина**
   ```bash
   rm plugins/steam_guard_delivery.py
   ```

2. **Удалите данные (опционально)**
   ```bash
   rm -rf storage/plugins/steam_guard/
   ```

3. **Перезапустите Cardinal**
   ```bash
   python main.py
   ```

## Поддержка

Если у вас возникли проблемы:

1. **Проверьте документацию:** [README.md](README.md)
2. **Проверьте логи:** `grep "steam_guard" logs/FPC.log`
3. **Создайте Issue:** [GitHub Issues](https://github.com/MeccCZ/steam-guard-delivery-plugin/issues)
4. **Telegram:** @MeccCZ

## Дополнительно

- [Changelog](CHANGELOG.md) - История изменений
- [License](LICENSE) - Лицензия MIT
- [Contributing](CONTRIBUTING.md) - Как внести вклад

---

**Версия:** 1.0.0  
**Дата:** 27.04.2026  

🎉 **Готово! Удачных продаж!** 🚀
