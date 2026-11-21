#!/usr/bin/env python3
"""Alertes marché en journée : script de SURVEILLANCE pour `hermes cron create --monitor-script`.

Principe : Hermes lance ce script toutes les 15 minutes et compare sa sortie à la précédente.
- Sortie identique  → l'agent n'est PAS réveillé (zéro token consommé).
- Sortie différente → l'agent est réveillé avec la différence et t'envoie une alerte.

La sortie ne doit donc contenir AUCUN chiffre qui bouge (prix, heure) : seulement des
« paliers » (±5 %, ±10 %, ±15 %…). On n'est réveillé que quand une crypto franchit un palier.

Anti-clignotement : un palier atteint ne se perd que si la variation repasse MARGE point(s)
en dessous (ex. palier 5 % gardé jusqu'à 4 %). Sinon, une crypto qui oscille autour de 5 %
déclencherait une alerte toutes les 15 minutes.

Réutilise les réglages de point_marche.py (même liste de cryptos, même appel CoinGecko).
"""

import json
import os
import sys

DOSSIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DOSSIER)
from point_marche import CRYPTOS, SEUIL_24H, recuperer  # noqa: E402

MARGE = 1.0  # en points de %
ETAT = os.path.join(DOSSIER, ".alertes_marche_etat.json")  # mémoire entre deux passages


def palier_brut(variation):
    """-7,3 → -5 ; +12,1 → +10 ; +3 → 0 (sous le seuil)."""
    p = int(abs(variation) // SEUIL_24H) * int(SEUIL_24H)
    return p if variation >= 0 else -p


def palier(variation, ancien=0):
    """Palier avec marge : on monte dès le seuil, on ne redescend qu'avec MARGE point(s) de recul."""
    nouveau = palier_brut(variation)
    if ancien and (ancien > 0) == (variation > 0) and abs(nouveau) < abs(ancien):
        # On garde l'ancien palier tant qu'on n'est pas repassé sous (ancien - MARGE).
        if abs(variation) > abs(ancien) - MARGE:
            return ancien
        return palier_brut(variation if abs(variation) < abs(ancien) - MARGE else 0)
    return nouveau


def lire_etat():
    try:
        with open(ETAT, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"paliers": {}, "sortie": "Aucune alerte en cours."}


def main():
    etat = lire_etat()
    try:
        donnees = recuperer()
    except Exception:
        # Panne passagère de CoinGecko : on répète la dernière sortie → inchangée → l'agent dort.
        print(etat["sortie"])
        return

    paliers, lignes = {}, []
    for c in sorted(donnees, key=lambda c: CRYPTOS.get(c["id"], c["symbol"])):
        symbole = CRYPTOS.get(c["id"], c["symbol"].upper())
        v24 = c.get("price_change_percentage_24h_in_currency")
        if v24 is None:
            continue
        p = palier(v24, etat["paliers"].get(symbole, 0))
        if p != 0:
            paliers[symbole] = p
            sens = "BAISSE" if p < 0 else "HAUSSE"
            lignes.append(f"{symbole} : {sens} d'au moins {abs(p)} % sur 24 h")

    sortie = "\n".join(lignes) if lignes else "Aucune alerte en cours."
    print(sortie)
    try:
        with open(ETAT, "w", encoding="utf-8") as f:
            json.dump({"paliers": paliers, "sortie": sortie}, f)
    except OSError:
        pass


if __name__ == "__main__":
    main()
