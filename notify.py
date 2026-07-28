"""Two-stage Telegram push.

FLASH  — fires the moment a publication is detected. Regex ticker only, no LLM,
         no broker call. Its single job: get Pavel into TWS to check the borrow
         locate, which is the resource that actually dries up.
FULL   — follows ~1-2 min later with enrichment, borrow and the paper-ledger
         entry. It may also RETRACT the flash if the item turns out to be a
         follow-up or media clip.
"""
from __future__ import annotations
import html
import requests

import config as C

API = "https://api.telegram.org/bot{token}/sendMessage"


def _send(text: str, silent: bool = False) -> bool:
    if not (C.TG_TOKEN and C.TG_CHAT):
        print("[notify] telegram not configured — printing:\n" + text + "\n")
        return False
    try:
        r = requests.post(API.format(token=C.TG_TOKEN), timeout=20, json={
            "chat_id": C.TG_CHAT, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": False, "disable_notification": silent})
        if r.status_code != 200:
            print(f"[notify] telegram {r.status_code}: {r.text[:200]}")
        return r.status_code == 200
    except Exception as e:
        print(f"[notify] telegram error: {e}")
        return False


def esc(s) -> str:
    return html.escape(str(s or ""), quote=False)


def flash(firm: dict, item, ticker: str | None, unsure: bool) -> bool:
    """Stage 1 — minimal and immediate."""
    head = f"⚡ <b>{esc(firm['name'])}</b> — новая публикация"
    if ticker:
        tick = f"\n<b>${esc(ticker)}</b>   ({esc(firm.get('region',''))})"
        action = "\n\n👉 <b>ПРОВЕРЬ ЛОКЕЙТ В TWS</b>"
    else:
        tick = f"\n<i>тикер не распознан — см. ссылку</i>   ({esc(firm.get('region',''))})"
        action = "\n\n👉 <b>открой отчёт, тикер в тексте</b>"
    warn = "\n<i>⚠️ тип не подтверждён — возможен follow-up</i>" if unsure else ""
    body = (f"{head}{tick}\n"
            f"tier {esc(firm['tier'])}{warn}\n\n"
            f"{esc(item.title)[:220]}\n"
            f"{esc(item.url)}{action}")
    return _send(body)


def full(firm: dict, item, ticker, enrichment: dict, history: dict | None,
         ledger_id: int | None) -> bool:
    """Stage 2 — analysis, borrow, ledger."""
    lines = [f"📊 <b>{esc(firm['name'])}</b> → <b>${esc(ticker or '—')}</b>"]

    llm = (enrichment or {}).get("llm")
    if llm:
        lines.append(f"\n{esc(llm.get('thesis',''))[:300]}")
        lines.append(f"Тип: {esc(llm.get('allegation',''))} · улики: {esc(llm.get('evidence',''))}/5")
    else:
        lines.append(f"\n{esc(item.title)[:280]}")

    borrow = (enrichment or {}).get("borrow")
    if borrow:
        avail = borrow.get("shares")
        fee = borrow.get("fee")
        mark = "✅" if (avail or 0) > 0 else "🚫"
        lines.append(f"\n{mark} Борроу: {avail:,} акц. @ {fee:.1f}%" if avail else f"\n{mark} Борроу недоступен")
    else:
        lines.append("\n<i>борроу: проверь в TWS (авто-слой в фазе 4)</i>")

    if history and history.get("n"):
        lines.append(f"\n📈 История конторы: {history['n']} отчётов, "
                     f"медиана 4д {history['mn4']*100:+.1f}%, hit {history['hit']*100:.0f}%")

    if ledger_id:
        lines.append(f"\n📒 Ledger #{ledger_id}: план — вход в сессию, "
                     f"хедж лонг SPY, выход MOC день+4, сайз 1–2%")
    lines.append("\n<i>⚠️ research trigger, не ордер</i>")
    return _send("\n".join(lines))


def retract(firm: dict, ticker: str | None, reason: str) -> bool:
    return _send(f"⚠️ <b>ОТБОЙ</b> по <b>${esc(ticker or '—')}</b> ({esc(firm['name'])})\n"
                 f"{esc(reason)}\nПозиция в ledger не открыта.")


def digest(text: str) -> bool:
    return _send(text, silent=True)
