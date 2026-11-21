#!/usr/bin/env python3
"""Lit l'annonce complète de chaque offre (liste JSON sur l'entrée standard) avec le Chromium connecté.

Entrée : {"offres": [{"cle", "source", "titre", "details", "lien"}, …], …} (sortie de collecte_emploi.py --json)
Sortie : le même JSON, chaque offre enrichie de "annonce" (texte) ou "annonce_erreur" (raison).

Rythme humain : une page à la fois, 15 s de pause entre deux pages. S'ARRÊTE au premier signe de
méfiance de LinkedIn (vérification, mur de connexion) : les offres restantes sont marquées non lues.
Les liens Indeed / HelloWork sont tentés aussi ; s'ils sont bloqués, l'offre est marquée non lue.

Usage : sudo docker exec -i li-nav python3 /li/lire_annonces.py < offres.json > offres_lues.json
"""

import json
import re
import sys
import time

from playwright.sync_api import sync_playwright

PAUSE = 15
PAUSE_SOURCE = {"Indeed": 40}   # Indeed se méfie après ~30 pages rapprochées (2026-11-20) : on espace
MAX_CAR = 8000
MAX_AJOUTS = 80      # offres ajoutées par jour depuis les pages « voir toutes » (ménager le compte LinkedIn)
ALERTE = re.compile(r"checkpoint|authwall|/login|challenge|captcha|cloudflare", re.I)
DEBUT = re.compile(r"À propos de l.offre d.emploi|About the job|Descriptif du poste|Description du poste"
                   r"|Détails de l.emploi|Le poste|Missions", re.I)
# Page de contrôle anti-robot (Indeed passe par Cloudflare) : ce n'est PAS une annonce
BLOCAGE = re.compile(r"Additional Verification Required|Vérification supplémentaire|Just a moment|Un instant"
                     r"|Verify you are human|Vérifiez que vous êtes|hCaptcha|Ray ID|Security Check"
                     # mur de connexion Indeed (session absente ou expirée)
                     r"|Ready to take the next step|Create an account or sign in|Créez un compte ou connectez-vous", re.I)
# Titres qu'on n'ouvre PAS : seulement l'évidemment hors profil (développement logiciel, technique, administratif).
# Tout le reste est ouvert : dans le doute, on lit l'annonce (retour d'expérience du 2026-11-21).
EXCLUS = re.compile(r"d[ée]veloppeu|developer|software engineer|full.?stack|front.?end|back.?end|devops"
                    r"|\bjava\b|\.net\b|\bc#|angular|django|network|t[ée]l[ée]com|scada|technicien|technician"
                    r"|assembly|assistant\(?e?\)? administrati|copropri[ée]t", re.I)
FIN = re.compile(r"\n(Afficher moins|Voir moins|Show less|À propos de l.entreprise|About the company"
                 r"|Relancez Premium|Réessayez Premium|Essai gratuit|Premium\n)")


def extraire(texte, source):
    m = DEBUT.search(texte)
    if not m:
        return None if source == "LinkedIn" else texte[:MAX_CAR]
    bloc = texte[m.start():m.start() + MAX_CAR + 2000]
    f = FIN.search(bloc)
    return (bloc[:f.start()] if f else bloc)[:MAX_CAR]


def completer_par_recherches(page, donnees, offres):
    """Alertes LinkedIn incomplètes : ouvre la page « voir toutes les offres » de chacune et ajoute les offres
    absentes de l'e-mail (jamais vues avant). Les N premières de la page, N = nombre annoncé par l'e-mail.
    Renvoie un motif d'arrêt si LinkedIn se méfie (alors plus rien n'est lu sur LinkedIn aujourd'hui)."""
    deja = set(donnees.get("deja_vus_linkedin", []))
    ajoutees = 0
    compte = []
    for r in donnees.get("recherches", []):
        if ajoutees >= MAX_AJOUTS:
            compte.append(f"{r['alerte']} : non ouverte (plafond de {MAX_AJOUTS} offres ajoutées atteint)")
            continue
        time.sleep(PAUSE)
        try:
            page.goto(r["url"], wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(6000)
        except Exception as e:
            compte.append(f"{r['alerte']} : page « voir toutes » non chargée ({type(e).__name__})")
            continue
        if ALERTE.search(page.url) or ALERTE.search(page.title()):
            donnees.setdefault("incomplets", []).append("LinkedIn demande une vérification : arrêt")
            return "LinkedIn demande une vérification (page « voir toutes »)"
        ids = []
        for m in re.finditer(r"currentJobId=(\d{8,})|/jobs/view/(\d{8,})", page.content()):
            i = m.group(1) or m.group(2)
            if i not in ids:
                ids.append(i)
        ids = ids[:r["annoncees"]]
        nouveaux = [i for i in ids if i not in deja][:MAX_AJOUTS - ajoutees]
        for i in nouveaux:
            deja.add(i)
            offres.append({"source": "LinkedIn", "titre": "(titre lu sur l'annonce)", "titre_a_lire": True,
                           "details": f"trouvée sur la page « voir toutes » de l'alerte « {r['alerte']} »",
                           "lien": f"https://www.linkedin.com/jobs/view/{i}/", "cle": f"linkedin:{i}",
                           "famille": "?"})
        ajoutees += len(nouveaux)
        compte.append(f"{r['alerte']} : {r['annoncees']} annoncées, {len(ids)} trouvées sur la page, "
                      f"{len(nouveaux)} nouvelles ajoutées")
    if compte:
        donnees["voir_toutes"] = compte
    return None


def main():
    donnees = json.load(sys.stdin)
    offres = donnees.get("offres", [])
    with sync_playwright() as p:
        ctx = p.chromium.connect_over_cdp("http://127.0.0.1:9222").contexts[0]
        page = ctx.new_page()
        bloque = None
        bloques = set()       # sources qui ont montré une page de contrôle : on ne les sollicite plus aujourd'hui
        vus = {}              # lien final → titre : deux offres ne doivent pas mener à la même annonce
        try:
            bloque = completer_par_recherches(page, donnees, offres)
        except Exception as e:      # nouveauté : un échec ici ne doit jamais empêcher la lecture des autres offres
            donnees.setdefault("voir_toutes", []).append(f"pages « voir toutes » en échec ({type(e).__name__})")
        for i, o in enumerate(offres):
            if bloque:
                o["annonce_erreur"] = f"non lue : lecture arrêtée ({bloque})"
                continue
            if o["source"] in bloques:
                o["annonce_erreur"] = f"non lue : {o['source']} a bloqué la lecture plus tôt (à ouvrir soi-même)"
                continue
            if EXCLUS.search(o.get("titre", "")):
                o["annonce_erreur"] = "non ouverte (titre évidemment hors profil : développement, technique…)"
                continue
            if i:
                time.sleep(PAUSE_SOURCE.get(o["source"], PAUSE))
            try:
                page.goto(o["lien"], wait_until="domcontentloaded", timeout=90000)
                page.wait_for_timeout(5000)
            except Exception as e:
                o["annonce_erreur"] = f"page non chargée ({type(e).__name__})"
                continue
            if ALERTE.search(page.url) or ALERTE.search(page.title()):
                msg = f"{o['source']} demande une vérification"
                o["annonce_erreur"] = msg
                if o["source"] == "LinkedIn":
                    bloque = msg      # on n'insiste jamais avec le compte LinkedIn de l'utilisateur
                continue
            if o.pop("titre_a_lire", False):
                morceaux = page.title().split(" | ")   # « Poste | Entreprise | LinkedIn »
                o["titre"] = morceaux[0].strip() or o["titre"]
                if len(morceaux) > 2:
                    o["details"] = morceaux[1].strip() + " — " + o["details"]
            corps = page.inner_text("body")
            if BLOCAGE.search(page.title()) or BLOCAGE.search(corps[:3000]):
                o["annonce_erreur"] = f"non lue : {o['source']} affiche un contrôle anti-robot (à ouvrir soi-même)"
                if o["source"] == "LinkedIn":
                    bloque = o["annonce_erreur"]
                else:
                    bloques.add(o["source"])
                continue
            texte = extraire(corps, o["source"])
            final = page.url.split("?")[0]
            if final in vus:
                # ex. 2026-11-21 : une offre HelloWork expirée redirigée vers celle d'un autre recruteur
                o["annonce_erreur"] = f"le lien mène à l'annonce d'une autre offre ({vus[final][:60]}) : à vérifier à la main"
                continue
            if texte and len(texte) > 300:
                o["annonce"] = texte.strip()
                o["lien_final"] = final
                vus[final] = o.get("titre", "")
            else:
                o["annonce_erreur"] = "description introuvable sur la page"
        page.close()          # ferme l'onglet, pas le navigateur (la session reste)
    donnees["lecture"] = {"lues": sum(1 for o in offres if "annonce" in o), "total": len(offres),
                          "arret": bloque}
    json.dump(donnees, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
