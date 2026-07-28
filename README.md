# activist-watch

Мониторинг публикаций активистов-шортселлеров с двухступенчатым пушем в Telegram.

## Зачем

Не новостной бот. Это **инструмент live-shadow** для проверки единственной гипотезы,
пережившей исследование: у издателей среднего калибра пост-публикационный дрейф
существует (Bleecker Street: mn4 +5.6%, hit 79%, p=0.053 на 24 событиях), но ячейка
выбрана задним числом — значит нужны живые непредвзятые данные.

Поэтому система **логирует всё**, включая то, по чему не алертит. База `data/activist_watch.db` —
это и есть накапливаемый датасет.

## Двухступенчатый пуш

```
публикация → детект (2–3 мин)
   ⚡ FLASH   контора · ТИКЕР · ссылка          ← сразу, без LLM/брокера
              «ПРОВЕРЬ ЛОКЕЙТ В TWS»
   📊 FULL    суть · борроу · история конторы   ← +1–2 мин
              запись в paper-ledger
   ⚠️ ОТБОЙ   если оказался follow-up            ← отменяет FLASH
```

Логика: дефицитный ресурс — не котировка, а **локейт борроу**, который пересыхает
за часы после отчёта. FLASH существует, чтобы ты успел его занять.

## Установка

```bash
pip install -r requirements.txt
export AW_TELEGRAM_BOT_TOKEN=...
export AW_TELEGRAM_CHAT_ID=...
```

Токен бота — у @BotFather, chat_id — у @userinfobot.

## Запуск

```bash
python watch.py --probe    # проверить все источники, ничего не менять
python watch.py --once     # один свип (первый = прогрев, без алертов)
python watch.py            # бесконечный цикл
python watch.py --health   # дайджест здоровья в телеграм
python watch.py --reset    # сбросить seen-set (заново прогреться)
```

**Первый запуск — прогрев:** всё уже опубликованное помечается как виденное,
пуши не идут. Алерты начинаются только с реально новых публикаций.

## Деплой (systemd)

`/etc/activist-watch.env`:
```
AW_TELEGRAM_BOT_TOKEN=...
AW_TELEGRAM_CHAT_ID=...
```

`/etc/systemd/system/activist-watch.service`:
```ini
[Unit]
Description=activist-watch
After=network-online.target

[Service]
WorkingDirectory=/opt/activist-watch
EnvironmentFile=/etc/activist-watch.env
ExecStart=/usr/bin/python3 watch.py
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable --now activist-watch
journalctl -u activist-watch -f
```

Ежедневный health-дайджест:
```
0 6 * * * cd /opt/activist-watch && /usr/bin/python3 watch.py --health
```

## Покрытие (проверено 27.07.2026)

**Работают 19 из 24:** Morpheus, Gotham, ShadowFall, Snowcap, QCM, Petrus, Spruce Point,
Bleecker Street, Viceroy, Grizzly, Muddy Waters, Fuzzy Panda, Bonitas, Kerrisdale,
Scorpion, Wolfpack, Blue Orca, Night Market, GMT, Bear Cave.

**Заблокированы Cloudflare (403):** Jehoshaphat, Ningi, Iceberg, Culper.
Обход протестирован и **не работает** (27.07.2026): plain requests, curl_cffi
со всеми профилями TLS (chrome/124/131, safari, firefox; пути /, /feed/, /sitemap.xml),
headless Chromium, headless Chromium + stealth — везде 403 либо вечное
«Performing security verification», куки не выдаются. Проходит только настоящий
не-headless браузер с живым профилем. Модуль `cf_bypass.py` оставлен: он бесплатный
и самоотключается (если Playwright не установлен — просто не вызывается).
Практический путь к этим четырём — Фаза 2 (X через Grok, ~$13/мес);
двое из них (Jehoshaphat, Ningi) — Tier A, самые торгуемые цели каталога.

Адаптеры подбираются автоматически: WordPress REST → Squarespace JSON → RSS →
Wix → sitemap → HTML. Рабочая стратегия кэшируется в `source_health`.

## Тиры

`tier` — это **гипотеза, а не фильтр**:
- **A** — каталог считает цели ликвидными и шортабельными на IBKR
- **B** — смешанно
- **C** — обычно нешортабельны (микрокапы, HK designated list, research-only)

Алерты идут по A и B, tier C только логируется. **Важно:** бэктест нашёл лучший
дрейф именно в tier-B конторе (Bleecker), что и есть открытый вопрос,
ради которого монитор существует. Тир пишется в каждое событие — через 20–30
событий сравним по факту.

## Фазы

- **1 (сделано)** — веб-коллекторы, дедуп, двухступенчатый пуш, ledger, health
- **2** — X через Grok (закрывает 4 заблокированные конторы, ~$13/мес)
- **3** — LLM-извлечение (тикер там, где нет в заголовке; суть; качество улик).
  Решения не принимает — копит разметку для слепой проверки
- **4** — IBKR read-only: shortable shares + fee rate в FULL-пуш
- **5** — сверка ledger с ценами, месячный tearsheet против ожиданий бэктеста

## Что осознанно НЕ делается

- **Никакого автоматического размещения ордеров.** Система только уведомляет.
- Никаких решений на основе LLM — только разметка для последующей проверки.
