"""Optionele uitleg via de Claude API. Stuurt alleen berekende cijfers, nooit ruwe tijdreeksen,
en logt de sleutel nooit. De uitleg beslist niets."""
from __future__ import annotations

import json
import logging

import aiohttp

log = logging.getLogger("advisor")

PROMPT = (
    "Je helpt een huiseigenaar met zijn cv-ketel (Intergas, OpenTherm, SAT-regeling). "
    "Geef in maximaal 5 korte zinnen, in gewoon Nederlands, uitleg bij onderstaande weekcijfers "
    "en bij het voorstel. Verzin geen cijfers en bedenk geen andere wijzigingen; de beslissing "
    "ligt vast in regels en grenzen die jij niet kunt aanpassen. De gasmeter bevat ook warm water "
    "en koken; een basislast is daarvoor afgetrokken."
)


async def explain(api_key, model, numbers: dict):
    if not api_key:
        return None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": model, "max_tokens": 400, "system": PROMPT,
                      "messages": [{"role": "user", "content": json.dumps(numbers, ensure_ascii=False)}]},
                timeout=aiohttp.ClientTimeout(total=45),
            ) as r:
                if r.status != 200:
                    log.warning("Claude API gaf HTTP %s", r.status)
                    return None
                j = await r.json()
                return "".join(b.get("text", "") for b in j.get("content", [])).strip() or None
    except Exception as e:
        log.warning("Claude-uitleg mislukt: %s", type(e).__name__)
        return None
