#!/usr/bin/env python3
"""Relit avec le navigateur li-nav les annonces d'une source mal lues dans le fichier du jour, puis les réintègre.

Cas d'usage : Indeed affichait un mur de connexion ; une fois l'utilisateur connecté dans li-nav, on relit seulement
ces offres-là (les autres ne sont pas rouvertes). Ensuite : « hermes cron run <ID> » pour que Renard refasse l'analyse.

Usage (root, sur le serveur) : sudo python3 relire_source.py Indeed [AAAA-MM-JJ]
"""

import json
import os
import re
import subprocess
import sys
from datetime import date

source = sys.argv[1]
jour = sys.argv[2] if len(sys.argv) > 2 else date.today().isoformat()
F = f"/srv/hermes/data/emploi/jour/{jour}.json"
MUR = re.compile(r"Ready to take the next step|Create an account or sign in|Créez un compte ou connectez-vous"
                 r"|Just a moment|Additional Verification Required|Vérification supplémentaire", re.I)

d = json.load(open(F, encoding="utf-8"))
a_relire = [dict(o) for o in d["offres"]
            if o["source"] == source
            and (not o.get("annonce") or MUR.search(o["annonce"][:3000]))
            and "hors profil" not in o.get("annonce_erreur", "")]
print(f"{len(a_relire)} annonces {source} à relire (environ {len(a_relire) * 22 // 60 + 1} min)…")
for o in a_relire:
    for k in ("annonce", "annonce_erreur", "lien_final"):
        o.pop(k, None)

r = subprocess.run(["docker", "exec", "-i", "li-nav", "python3", "/li/lire_annonces.py"],
                   input=json.dumps({"offres": a_relire}, ensure_ascii=False),
                   capture_output=True, text=True)
if r.returncode or not r.stdout.strip():
    sys.exit("Échec de la lecture, fichier du jour inchangé : " + r.stderr.strip()[-300:])

relues = {o["cle"]: o for o in json.loads(r.stdout)["offres"]}
d["offres"] = [relues.get(o.get("cle"), o) for o in d["offres"]]
d.setdefault("lecture", {})["lues"] = sum(1 for o in d["offres"] if o.get("annonce"))

tmp = F + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False)
os.chown(tmp, 10000, 10000)
os.chmod(tmp, 0o600)
os.replace(tmp, F)

ok = sum(1 for o in relues.values() if o.get("annonce"))
print(f"{ok} lues sur {len(relues)}. Total du jour : {d['lecture']['lues']} annonces lues.")
for o in relues.values():
    if not o.get("annonce"):
        print("  non lue :", o["titre"][:50], "→", o.get("annonce_erreur"))
