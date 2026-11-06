# Запуск бота на сервере (VPS Hostinger, Ubuntu)

Инструкция рассчитана на человека, который не администрирует серверы. Каждую команду
копируйте целиком, вставляйте в терминал и нажимайте Enter. После каждого шага написано,
что вы должны увидеть.

Время: примерно 30–40 минут.

---

## Шаг 0. Что подготовить заранее

1. **Сервер** — VPS на Hostinger с Ubuntu 22.04 или 24.04. В панели Hostinger найдите
   IP-адрес сервера и пароль root (или SSH-ключ).
2. **Аккаунт Telegram**, с которого вы будете администрировать бота.
3. **Канал** barcelona.ru в Telegram (уже существует).
4. **Адрес репозитория** с кодом (на GitHub), например
   `https://github.com/ваш-аккаунт/barcelona-bot`.

---

## Шаг 1. Подготовка в Telegram

### 1.1. Создать бота и получить токен

1. Откройте в Telegram **@BotFather**, нажмите «Start».
2. Отправьте `/newbot`.
3. Введите имя бота, например `barcelona.ru — недвижимость`.
4. Введите username бота, он должен заканчиваться на `bot`, например `barcelona_ru_bot`.
5. BotFather пришлёт **токен** — строку вида `1234567890:AAH...`. Сохраните её.
   ⚠️ Токен — это пароль от бота. Никому не пересылайте и не публикуйте.

### 1.2. Узнать свой Telegram ID

1. Откройте **@userinfobot**, нажмите «Start».
2. Он пришлёт ваш `Id` — число, например `123456789`. Сохраните.
   Если администраторов несколько — каждый делает то же самое.

### 1.3. Создать группу для заявок

1. В Telegram: «Новая группа» → название, например «Заявки barcelona.ru».
2. Добавьте в группу своего бота (по username из шага 1.1) и коллег, которые обрабатывают заявки.
3. ID группы узнаем на шаге 6 — после запуска бота.

### 1.4. Сделать бота администратором канала

Это нужно, чтобы бот мог проверять, подписан ли человек на канал.

1. Откройте канал → «Управление каналом» → «Администраторы» → «Добавить администратора».
2. Найдите бота по username и добавьте. Все права можно **выключить** — для проверки
   подписки достаточно самого статуса администратора.

---

## Шаг 2. Подключиться к серверу

На компьютере откройте терминал:
- **Windows** — «PowerShell» (через меню Пуск);
- **macOS** — программа «Терминал».

Выполните (вместо `IP_СЕРВЕРА` — IP из панели Hostinger):

```bash
ssh root@IP_СЕРВЕРА
```

При первом подключении спросят `Are you sure you want to continue connecting?` — напишите
`yes` и Enter. Затем введите пароль (символы при вводе не отображаются — это нормально).

✅ Ожидаемый результат: строка приглашения вида `root@srv123456:~#`.

> Все следующие команды выполняются **на сервере**, в этом окне.

---

## Шаг 3. Установить Docker

```bash
apt update && apt upgrade -y
```

✅ Идут строки установки обновлений, в конце снова приглашение `root@...:~#`.
Если появится розовое окно с вопросом о перезапуске служб — нажмите Enter.

```bash
curl -fsSL https://get.docker.com | sh
```

✅ В конце установки появится текст про `docker` (несколько строк, без слова `ERROR`).

Проверьте:

```bash
docker --version && docker compose version
```

✅ Две строки вида `Docker version 27...` и `Docker Compose version v2...`.

Включите автозапуск Docker после перезагрузки сервера:

```bash
systemctl enable docker
```

```bash
systemctl is-enabled docker
```

✅ Ответ: `enabled`.

---

## Шаг 4. Скачать код бота

```bash
apt install -y git
```

```bash
git clone https://github.com/ВАШ-АККАУНТ/barcelona-bot.git /opt/barcelona-bot
```

Если репозиторий приватный, GitHub спросит логин и пароль. Вместо пароля нужен
**токен доступа**: GitHub → Settings → Developer settings → Personal access tokens →
Fine-grained tokens → Generate new token → доступ «Contents: Read-only» только к этому
репозиторию. Вставьте токен вместо пароля.

```bash
cd /opt/barcelona-bot
```

✅ Приглашение стало `root@...:/opt/barcelona-bot#`.

---

## Шаг 5. Заполнить настройки

### 5.1. Секреты — файл `.env`

```bash
cp .env.example .env
```

```bash
nano .env
```

Откроется редактор. Стрелками перемещайтесь по файлу и заполните:

| Строка | Что вписать |
|---|---|
| `BOT_TOKEN=` | токен из шага 1.1 |
| `ADMIN_IDS=` | ваш ID из шага 1.2; несколько — через запятую без пробелов: `123,456` |
| `ADMIN_CHAT_ID=` | пока оставьте как есть — заполним на шаге 6 |
| `CHANNEL_ID=` | `@username` канала, например `@barcelona_ru` |
| `DATABASE_URL=` | **не меняйте** |
| `LOG_LEVEL=` | `INFO` |

Сохранить: `Ctrl+O`, затем Enter. Выйти: `Ctrl+X`.

### 5.2. Обычные настройки — `config/settings.local.yaml`

Свои значения храним в отдельном файле `settings.local.yaml` — его не трогают обновления
из git. Создайте его копией основного:

```bash
cp config/settings.yaml config/settings.local.yaml && nano config/settings.local.yaml
```

Заполните:
- `channel.url` — публичная ссылка на канал, например `https://t.me/barcelona_ru`;
- `legal.privacy_policy_url` — адрес страницы политики конфиденциальности на сайте;
- `legal.controller` — реквизиты юрлица (наименование, NIF/CIF, адрес, e-mail для запросов
  по персональным данным).

Сохранить и выйти: `Ctrl+O`, Enter, `Ctrl+X`.

---

## Шаг 6. Первый запуск

```bash
docker compose up -d --build
```

Первая сборка занимает 1–3 минуты.

✅ В конце строки вида:
```
 ✔ Container barcelona-bot-bot-1  Started
```

Проверьте, что бот работает:

```bash
docker compose ps
```

✅ В колонке `STATUS` у сервиса `bot` написано `Up ...` (а не `Restarting`).

```bash
docker compose logs --tail 20 bot
```

✅ Среди строк есть `"event": "bot_starting"` и username вашего бота.

**Проверка в Telegram:** откройте бота, нажмите «Start» — должно прийти приветствие и
меню из шести кнопок.

### 6.1. Подключить группу для заявок

1. В группе из шага 1.3 отправьте команду `/chatid`
   (если в группе несколько ботов — `/chatid@username_вашего_бота`).
2. Бот ответит `Chat ID: -100...`. Скопируйте число **вместе с минусом**.
3. На сервере:

```bash
nano .env
```

Впишите число в строку `ADMIN_CHAT_ID=`, сохраните (`Ctrl+O`, Enter, `Ctrl+X`) и примените:

```bash
docker compose up -d --force-recreate bot
```

✅ `Container barcelona-bot-bot-1  Started`.

**Проверка:** в личке с ботом нажмите «✉️ Связаться» → «Согласен» → «📱 Отправить телефон».
В группу заявок должна прийти карточка «🔔 Новая заявка». Затем отправьте боту `/stats`
и `/leads_csv` — придут статистика и CSV-файл.

---

## Шаг 7. Проверить автозапуск после перезагрузки

```bash
reboot
```

Соединение оборвётся — это нормально. Подождите 1–2 минуты, подключитесь снова
(шаг 2) и выполните:

```bash
cd /opt/barcelona-bot && docker compose ps
```

✅ Сервис `bot` в статусе `Up`. Бот отвечает в Telegram.

---

## Шаг 8 (рекомендуется). Закрыть лишние порты

Боту входящие порты не нужны (он сам ходит в Telegram). Оставим открытым только SSH:

```bash
ufw allow OpenSSH && ufw --force enable
```

✅ `Firewall is active and enabled on system startup`.

> ⚠️ Если на сервере уже работают другие сайты (как у вас), **пропустите этот шаг** — он закроет
> их порты. Квиз портов не требует: он работает через Cloudflare Tunnel (см. ниже).

---

# Квиз «Знаешь ли ты жильё в Барселоне?» (мини-апп)

Квиз открывается из бота как мини-приложение по адресу `https://barcelona.daagamma.com`.
Порты на сервере **не открываются**: программа `cloudflared` сама подключается к Cloudflare
(Cloudflare Tunnel), поэтому ваши сайты на портах 80/443 не затрагиваются, а HTTPS-сертификат
выдаёт Cloudflare.

Время: около 20 минут.

## К1. Создать туннель в Cloudflare

1. Если вы уже создали DNS-запись `barcelona` (A → 187.7.71.226) — **удалите её**:
   Cloudflare → daagamma.com → **DNS** → запись `barcelona` → Delete. Туннель создаст свою.
2. Откройте https://one.dash.cloudflare.com → **Networks** → **Tunnels** → **Create a tunnel**.
3. Тип — **Cloudflared**, имя — `barcelona-bot` → **Save tunnel**.
4. На экране установки выберите **Docker**. Там будет команда вида
   `docker run cloudflare/cloudflared:latest tunnel ... run --token eyJhIjoi...`.
   Скопируйте **только длинную строку после `--token`** — это токен туннеля.
   ⚠️ Токен — секрет, никому не пересылайте. Команду на сервере запускать **не нужно**.
5. **Next** → вкладка **Public Hostname** (или **Published application routes**) → **Add**:
   - Subdomain: `barcelona`
   - Domain: `daagamma.com`
   - Service: Type **HTTP**, URL `api:8000`
   → **Save**.

✅ В списке туннелей появился `barcelona-bot` (статус станет HEALTHY после шага К2).

## К2. Включить квиз на сервере

```bash
cd /opt/barcelona-bot && nano .env
```

Добавьте (или заполните, если строки уже есть) три строки:

```
WEBAPP_DOMAIN=barcelona.daagamma.com
CLOUDFLARE_TUNNEL_TOKEN=токен_из_шага_К1
COMPOSE_PROFILES=webapp
```

Сохраните (`Ctrl+O`, Enter, `Ctrl+X`) и запустите:

```bash
git pull && docker compose up -d --build
```

Проверьте, что работают три сервиса:

```bash
docker compose ps
```

✅ `bot`, `api` и `cloudflared` в статусе `Up`.

Откройте в браузере https://barcelona.daagamma.com/api/health

✅ Видно `{"status":"ok"}`. (Сама страница игры в браузере напишет «Откройте игру в
Telegram» — так и должно быть: игра работает только внутри Telegram.)

## К3. Зарегистрировать мини-апп в @BotFather

1. **@BotFather** → `/newapp` → выберите своего бота.
2. **Title:** `Квиз: жильё в Барселоне`
3. **Description:** `25 вопросов о покупке, аренде и налогах в Барселоне. Пройдите до конца — получите бесплатную nota simple.`
4. **Photo:** отправьте картинку 640×360 — файл `quiz-cover-640x360.png` (прислал разработчик).
5. **GIF:** отправьте `/empty`.
6. **Web App URL:** `https://barcelona.daagamma.com/`
7. **Short name:** `quiz` (именно так — оно используется в ссылках «Поделиться»).

✅ BotFather пришлёт ссылку вида `t.me/ваш_бот/quiz`.

## К4. Проверить

1. Откройте бота, отправьте `/start` → кнопка **«🎮 Квиз…»** в меню, а рядом с полем ввода — кнопка **«🎮 Квиз»**.
2. Сыграйте: после каждого ответа — пояснение; 3 ошибки — конец игры.
3. Победитель нажимает **«Получить приз»** → бот проверяет подписку на канал и просит контакт →
   в группу заявок приходит карточка «Квиз (приз)».
4. `/stats` показывает «Сыграно в квиз: N, прошли до конца: M».

Для повторной проверки в тот же день (одна попытка в сутки) можно временно поставить
`attempts_per_day: 10` в `config/settings.local.yaml` (раздел `quiz:`) и выполнить
`docker compose restart api`.

## Изменить вопросы квиза

Вопросы — в файле `config/quiz.yaml` (на русском и английском, правильный ответ — `correct`,
три неверных — `wrong`). После правки:

```bash
docker compose restart api
```

# Повседневные операции

Все команды выполняются на сервере из папки бота. Сначала:

```bash
cd /opt/barcelona-bot
```

## Обновить бота после изменений в коде

```bash
git pull && docker compose up -d --build
```

✅ `Container barcelona-bot-bot-1  Started`. Миграции базы данных применяются автоматически
при запуске.

## Подключить оплату nota simple (Stripe)

Бот поддерживает два способа:

- **Ссылка Stripe (Payment Link) — включена сейчас.** Ссылка указана в `config/settings.yaml` →
  `nota_simple.payment_link_url`. Клиент нажимает «Заказать», бот создаёт заказ (в группу приходит
  заявка «⏳ ожидает оплаты» с номером вида `bcnbot-lead-12`) и даёт кнопку «Оплатить».
  Как проверить оплату: Stripe → **Payments** → откройте платёж → поле **Client reference ID**
  содержит тот же номер `bcnbot-lead-12`. После проверки менеджер заказывает nota simple.
- **Оплата внутри Telegram** — если в @BotFather → Bot Settings → Payments появится Stripe.
  Тогда бот сам видит оплату и отмечает заявку «✅ оплачено». Включается токеном (ниже) и
  имеет приоритет над ссылкой.

Без ссылки и без токена кнопка «Nota simple» создаёт обычную заявку, счёт выставляет менеджер.

### Оплата внутри Telegram (если Stripe есть в BotFather)
С токеном клиент платит прямо в Telegram (карта, Apple Pay, Google Pay), а в группу приходит
заявка с отметкой «✅ оплачено».

**1. Сначала тестовый режим (деньги не списываются).**

1. Откройте **@BotFather** → `/mybots` → выберите бота → **Bot Settings** → **Payments**.
2. Выберите **Stripe TEST** (или «Stripe» → «Connect Stripe Test»). Telegram откроет бот Stripe —
   войдите в свой аккаунт Stripe и подтвердите подключение.
3. BotFather пришлёт токен вида `123456789:TEST:...`. Скопируйте его.
4. На сервере:

```bash
cd /opt/barcelona-bot && nano .env
```

Впишите токен в строку `PAYMENT_PROVIDER_TOKEN=` (если строки нет — добавьте её в конец),
сохраните (`Ctrl+O`, Enter, `Ctrl+X`) и примените:

```bash
docker compose up -d --force-recreate bot
```

5. Проверьте: «🔎 Проверить квартиру» → найдите объект → «📄 Nota simple, 29 €» → «Оплатить».
   Тестовая карта: `4242 4242 4242 4242`, любая будущая дата, любой CVC.
   ✅ Бот пишет «Оплата получена», в группу приходит заявка с «✅ 29,00 EUR», в Stripe
   (режим Test) появляется платёж.

**2. Боевой режим.** Повторите шаги 1–4, но выберите **Stripe** (не TEST) — токен будет вида
`123456789:LIVE:...`. Замените им тестовый в `.env` и снова выполните
`docker compose up -d --force-recreate bot`.

Цена меняется в `config/settings.yaml` → `nota_simple.price_eur` (или напишите разработчику).
Возврат денег — в панели Stripe: Payments → платёж → Refund.

## Проверить связь с кадастром (Catastro)

Команда делает реальные запросы к Catastro (номер из документации, геоточка и три адреса
в Барселоне) и печатает результат:

```bash
docker compose exec bot python -m app.integrations.catastro --quiet
```

✅ Все строки начинаются с `[OK]`, в конце `FAILED: 0`. Если есть `[FAIL] ... unavailable` —
кадастр не отвечает серверу; без `--quiet` команда покажет и сырые ответы.

## Изменить ставки или настройки

Свои значения пишите в `config/*.local.yaml` (например, `config/taxes.local.yaml`):
достаточно указать только изменённые строки, с той же вложенностью, что в основном файле.
Основные файлы (`taxes.yaml`, `settings.yaml`) не правьте — их обновляет `git pull`.

```bash
nano config/taxes.local.yaml
```

Затем перезапустите бота — пересборка не нужна:

```bash
docker compose restart bot
```

## Изменить секреты (`.env`)

После правки `.env`:

```bash
docker compose up -d --force-recreate bot
```

(`restart` не перечитывает `.env` — нужен именно `--force-recreate`.)

## Посмотреть логи

Последние 100 строк:

```bash
docker compose logs --tail 100 bot
```

В реальном времени (выход — `Ctrl+C`):

```bash
docker compose logs -f bot
```

Только ошибки:

```bash
docker compose logs bot | grep -E '"level": "(error|critical)"'
```

Логи хранятся не более 50 МБ (5 файлов по 10 МБ), старые удаляются автоматически.

## Резервная копия базы

```bash
docker compose exec bot python -c "import sqlite3; sqlite3.connect('/data/bot.db').backup(sqlite3.connect('/data/backup.db'))" && docker compose cp bot:/data/backup.db ./backup-$(date +%F).db
```

✅ В папке появился файл `backup-ГГГГ-ММ-ДД.db`. Скачать его на компьютер (команда
выполняется **на компьютере**, не на сервере):

```bash
scp root@IP_СЕРВЕРА:/opt/barcelona-bot/backup-*.db .
```

## Остановить / запустить бота

```bash
docker compose stop bot
```

```bash
docker compose start bot
```

---

# Если что-то не работает

| Симптом | Что проверить |
|---|---|
| `docker compose ps` показывает `Restarting` | `docker compose logs --tail 50 bot`. Если есть `Unauthorized` — неверный `BOT_TOKEN` в `.env`. |
| Бот не отвечает, в логах ошибок нет | Не запущен ли тот же бот (тот же токен) где-то ещё — у Telegram может быть только одна копия. |
| Карточки заявок не приходят в группу | `ADMIN_CHAT_ID` с минусом? Бот состоит в группе? В логах ищите `lead_notify_failed`. |
| `/stats` отвечает «только администраторам» | Ваш ID есть в `ADMIN_IDS`? После правки `.env` — `docker compose up -d --force-recreate bot`. |
| Квиз не открывается / «ошибка соединения» | `docker compose ps` — `api` и `cloudflared` в `Up`? В Cloudflare туннель HEALTHY? `docker compose logs --tail 30 cloudflared` |
| Кнопка «Квиз» пишет «скоро» | Не заполнен `WEBAPP_DOMAIN` в `.env` → заполнить и `docker compose up -d --force-recreate bot` |
| Проверка подписки всегда «не подписан» | Бот — администратор канала? `CHANNEL_ID` верный (`@username` или `-100...`)? В логах — `subscription_check_failed`. |
