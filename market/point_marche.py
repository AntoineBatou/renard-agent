#!/usr/bin/env python3
"""Point marché crypto : récupère les prix sur CoinGecko et CALCULE les signaux.

Le script donne les chiffres exacts ; l'agent (le LLM) ne fait que les commenter.
Aucune clé API, aucune bibliothèque à installer (uniquement la bibliothèque standard de Python).
Usage : python3 point_marche.py
"""

import json
import sys
import urllib.request
from datetime import datetime, timezone

# --- Réglages (à modifier librement) -------------------------------------------
# Identifiant CoinGecko → symbole affiché
CRYPTOS = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
}
SEUIL_24H = 5.0  # en %, à la hausse comme à la baisse

URL = (
    "https://api.coingecko.com/api/v3/coins/markets"
    "?vs_currency=usd"
    "&ids=" + ",".join(CRYPTOS)
    + "&price_change_percentage=24h,7d"
)


def recuperer():
    """Interroge CoinGecko et renvoie la liste des cryptos (format JSON décodé)."""
    requete = urllib.request.Request(URL, headers={"User-Agent": "point-marche/1.0"})
    with urllib.request.urlopen(requete, timeout=20) as reponse:
        return json.load(reponse)


def prix_lisible(prix):
    """Plus de décimales pour les petits prix (0,1494 $) que pour les gros (74 285 $)."""
    if prix >= 100:
        return f"{prix:,.0f} $".replace(",", " ")
    if prix >= 1:
        return f"{prix:,.2f} $".replace(",", " ")
    return f"{prix:.4f} $"


def signe(valeur):
    """+1.2 % / -3.4 % ; « n.d. » si CoinGecko ne fournit pas la donnée."""
    return "n.d." if valeur is None else f"{valeur:+.1f} %"


def main():
    try:
        donnees = recuperer()
    except Exception as erreur:  # réseau coupé, CoinGecko indisponible, limite atteinte…
        print(f"ERREUR : impossible de joindre CoinGecko ({erreur}). Ne rien inventer.")
        sys.exit(1)

    # Du plus en baisse au plus en hausse sur 24 h
    donnees.sort(key=lambda c: c.get("price_change_percentage_24h_in_currency") or 0)

    maintenant = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"POINT MARCHÉ — données CoinGecko du {maintenant}")
    print(f"Seuil d'alerte : ±{SEUIL_24H:.0f} % sur 24 h\n")

    alertes = []
    for c in donnees:
        symbole = CRYPTOS.get(c["id"], c["symbol"].upper())
        v24 = c.get("price_change_percentage_24h_in_currency")
        v7j = c.get("price_change_percentage_7d_in_currency")
        depuis_ath = c.get("ath_change_percentage")

        drapeau = ""
        if v24 is not None and abs(v24) >= SEUIL_24H:
            drapeau = "🔻 ALERTE BAISSE" if v24 < 0 else "🔺 ALERTE HAUSSE"
            alertes.append(f"{symbole} {signe(v24)}")

        print(
            f"{symbole:<5} {prix_lisible(c['current_price']):>12} | "
            f"24 h {signe(v24):>8} | 7 j {signe(v7j):>8} | "
            f"vs plus haut historique {signe(depuis_ath):>8}  {drapeau}"
        )

    print()
    print("ALERTES : " + (", ".join(alertes) if alertes else "aucune (aucun mouvement au-delà du seuil)"))


if __name__ == "__main__":
    main()
