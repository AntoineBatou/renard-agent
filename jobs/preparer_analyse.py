#!/usr/bin/env python3
"""Script de la tâche Hermes de 8 h (« veille-emploi ») : sa sortie est donnée à Renard avec la consigne.

1. Met à jour la copie de cv/ (git pull, clé de lecture seule) : l'agent a le profil à jour de l'utilisateur (dépôt privé, clé de déploiement en lecture seule).
2. Affiche les offres du jour avec leur annonce complète (déposées à 7 h par veille-emploi.sh).
"""

import json
import os
import subprocess
from datetime import date

CV = "/opt/data/cv"
JOUR = f"/opt/data/emploi/jour/{date.today().isoformat()}.json"
MAX_ANNONCE = 3500        # extrait maximal par offre…
ENVELOPPE = 90000         # …dans une enveloppe totale (≈ 25 000 tokens) : beaucoup d'offres = extraits plus courts
MIN_ANNONCE = 600


def maj_cv():
    r = subprocess.run(["git", "-C", CV, "pull", "--ff-only", "-q"], capture_output=True, text=True, timeout=120)
    etat = "à jour" if r.returncode == 0 else f"ÉCHEC du git pull ({r.stderr.strip()[:150]}) : copie de la veille utilisée"
    dernier = subprocess.run(["git", "-C", CV, "log", "-1", "--format=%cs %s"], capture_output=True, text=True).stdout.strip()
    print(f"Copie de cv/ : {etat} (dernier changement : {dernier})")


def offre_complete(n):
    """--offre N : affiche l'annonce complète de l'offre n° N du jour (pour approfondir)."""
    o = json.load(open(JOUR, encoding="utf-8"))["offres"][n - 1]
    print(f"[{o.get('source')}] {o.get('titre')} — {o.get('details', '')}\n{o.get('lien_final') or o.get('lien')}\n")
    print(o.get("annonce") or f"Annonce non lue : {o.get('annonce_erreur')}")


def main():
    import sys
    if "--offre" in sys.argv:
        return offre_complete(int(sys.argv[sys.argv.index("--offre") + 1]))
    maj_cv()
    if not os.path.exists(JOUR):
        print(f"\nERREUR : fichier du jour absent ({JOUR}) → la chaîne de 7 h n'a pas tourné. Le signaler à l'utilisateur.")
        return
    d = json.load(open(JOUR, encoding="utf-8"))
    if d.get("erreur"):
        print("\nERREUR de collecte :", d["erreur"])
    lec = d.get("lecture", {})
    print(f"\nOffres nouvelles : {len(d.get('offres', []))} — annonces lues : {lec.get('lues', 0)}"
          + (f" — lecture arrêtée : {lec['arret']}" if lec.get("arret") else ""))
    for cle in ("muettes", "non_lus", "incomplets", "voir_toutes"):
        for x in d.get(cle, []):
            print(f"[état des sources / {cle}] {x}")
    lues = sum(1 for o in d.get("offres", []) if o.get("annonce"))
    limite = max(MIN_ANNONCE, min(MAX_ANNONCE, ENVELOPPE // max(1, lues)))
    print(f"Extraits limités à {limite} caractères par annonce. Annonce complète d'une offre : "
          f"python3 /opt/data/scripts/preparer_analyse.py --offre <N>")
    for n, o in enumerate(d.get("offres", []), 1):
        print(f"\n=== OFFRE {n} [{o.get('source')}] {o.get('titre')}")
        print(f"Détails : {o.get('details', '')}")
        print(f"Lien : {o.get('lien_final') or o.get('lien')}")
        if o.get("annonce"):
            a = o["annonce"]
            if len(a) > limite:
                print(f"Lu : extrait ({limite} car. sur {len(a)}) — annonce entière : --offre {n}")
            else:
                print("Lu : annonce entière")
            print("Annonce :\n" + a[:limite] + (" […]" if len(a) > limite else ""))
        else:
            print(f"Lu : titre seul ({o.get('annonce_erreur', 'raison inconnue')})")


if __name__ == "__main__":
    main()
