#!/usr/bin/env python3
"""Collecte quotidienne des offres d'emploi arrivées par e-mail dans la boîte de Renard.

Zéro token : tout le tri est fait ici, sans IA. La sortie est le message Telegram du matin.

Garanties :
  1. relit TOUS les messages + le spam sur une fenêtre glissante de 3 jours (jamais « non lus ») ;
  2. un plantage ne perd rien : le passage suivant relit les mêmes jours ;
  3. un e-mail d'un site d'emploi dont on ne tire aucune offre est signalé, jamais jeté ;
  4. chien de garde : une source muette trop longtemps est signalée ;
  5. le nombre d'offres annoncé par l'e-mail est comparé au nombre lu.

Lecture seule sur la boîte (readonly). État : /opt/data/emploi/etat.json.

Usage :
  python3 collecte_emploi.py           # message du jour + mise à jour de l'état
  python3 collecte_emploi.py --essai   # tout afficher, détail par e-mail, état NON modifié
"""

import email
import hashlib
import imaplib
import json
import os
import re
import sys
import unicodedata
from datetime import date, timedelta
from email.header import decode_header, make_header
from email.utils import parseaddr
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

SECRET = "/opt/data/.mail-secrets"
DOSSIER = "/opt/data/emploi"
ETAT = os.path.join(DOSSIER, "etat.json")
FENETRE_JOURS = 3
OUBLI_JOURS = 90          # une offre vue il y a plus de 90 jours peut revenir
MAX_PAR_FAMILLE = 8       # au-delà : « + N autres » (la liste complète va dans le fichier du jour)
MAX_MESSAGE = 3800        # Telegram coupe à 4096 caractères

# Expéditeur (morceau d'adresse) -> (nom de la source, nb de jours de silence toléré)
SOURCES = {
    "jobalerts-noreply@linkedin.com": ("LinkedIn", 2),
    "jobs-noreply@linkedin.com": ("LinkedIn", 2),
    "jobs-listings@linkedin.com": ("LinkedIn", 2),
    "jobalert.indeed.com": ("Indeed", 2),
    "emails.hellowork.com": ("HelloWork", 2),
    "notification.prod.jobteaser.net": ("JobTeaser", 8),
    "welcometothejungle": ("WTTJ", 0),          # 0 = pas de chien de garde (alertes suspendues)
}
# Expéditeurs d'emploi qui n'envoient pas d'offres (codes, bienvenue…) : ignorés sans alerte
IGNORES = ["login@indeed.com", "no-reply@indeed.com", "noreply@jobteaser.com",
           "francetravail", "apprentissage.beta.gouv.fr"]
# Objet d'un e-mail inconnu qui ressemble à de l'emploi -> signalé
MOTS_EMPLOI = re.compile(r"offre|emploi|\bjob|alternance|recrut|candidat|poste à pourvoir|mission",
                         re.I)

# Familles, dans l'ordre de priorité (une offre va dans la 1re famille qui correspond)
FAMILLES = [
    ("🪙 Crypto / Web3", r"crypto|blockchain|web3|web 3|defi\b|\btoken|stablecoin|bitcoin|ethereum"
                        r"|\bnft|digital asset|actifs? numériques?|\bpsan\b|\bcasp\b|\bmica\b|wallet"
                        r"|tokeni[sz]"),
    ("🛡️ Conformité KYC / LCB-FT", r"\bkyc\b|\baml\b|lcb|compliance|conformit|financial crime|fraud"
                                   r"|sanction|anti.?blanchiment|\bkyb\b|regulatory|réglementaire|reglementaire|legal counsel"),
    ("🎓 Alternance data / product / IA", r"__ALTERNANCE__"),
    ("✍️ Rédaction / contenu", r"rédact|redact|content|contenu|writer|copywrit|éditorial|editorial"
                              r"|journalis|pigiste"),
    # Sans les intitulés de vente que l'utilisateur écarte : banquier, conseiller
    # clientèle, gestion privée, assurance-vie… « patrimo » reste : une structure digitale ou une mission
    # freelance peut porter ce titre, il faut lire l'annonce pour le savoir.
    ("💼 Patrimoine / fiscalité / conseil", r"patrimo|fiscal|wealth|\bcgp\b|\bcif\b|family office"
                                          r"|chargée? d.affaires|invest|succession|gestion de fortune"
                                          r"|\basset\b|private equity|financ|juriste|notari|advis[eo]r"),
    ("🧭 Transverse (product, data, BA, projet)", r"product|produit|business analyst|data|chef de projet"
                                                 r"|project manager|\bpmo\b|consultant|analyst|\bia\b|\bai\b"
                                                 r"|donnée|donnee|statisti|analytics|genai|\bllm|machine learning"
                                                 r"|intelligence artificielle"),
]
ALTERNANCE = re.compile(r"alternan|apprenti|work.?study|contrat pro", re.I)
ALT_METIER = re.compile(r"data|product|produit|\bia\b|\bai\b|intelligence artificielle|analyst"
                        r"|no.?code|business", re.I)


# ---------- boîte e-mail ----------

def identifiants():
    v = {}
    with open(SECRET, encoding="utf-8") as f:
        for ligne in f:
            if "=" in ligne:
                k, val = ligne.strip().split("=", 1)
                v[k.strip()] = val.strip()
    return v["MAIL_USER"], v["MAIL_APP_PASSWORD"].replace(" ", "")


def dossiers(boite):
    """Dossiers Gmail « Tous les messages » (\\All) et « Spam » (\\Junk), noms traduits."""
    _, lignes = boite.list()
    trouves = []
    for attr in ("\\All", "\\Junk"):
        for l in lignes:
            l = l.decode()
            if attr in l:
                trouves.append(l.rsplit(' "/" ', 1)[-1])
                break
    return trouves or ["INBOX"]


def entete(msg, champ):
    return str(make_header(decode_header(msg.get(champ, ""))))


def html_de(msg):
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
    return ""


def source_de(adresse):
    for morceau, (nom, _) in SOURCES.items():
        if morceau in adresse:
            return nom
    return None


# ---------- lecture du HTML : suite de jetons ("a", texte, lien) / ("t", texte, None) ----------

class Jetons(HTMLParser):
    def __init__(self):
        super().__init__()
        self.jetons, self._href, self._txt = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href, self._txt = dict(attrs).get("href", "") or "", []
        elif tag in ("br", "p", "div", "td", "tr", "li") and self._href is None:
            self.jetons.append(("t", "", None))   # séparateur

    def handle_data(self, data):
        if self._href is not None:
            self._txt.append(data)
        else:
            t = propre(data)
            if t:
                self.jetons.append(("t", t, None))

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.jetons.append(("a", propre(" ".join(self._txt)), unescape(self._href)))
            self._href = None


def propre(t):
    t = re.sub(r"\s+", " ", unescape(unescape(t or ""))).strip()
    return "[masqué]" if "token" in t.lower() else t


def jetons_de(html):
    p = Jetons()
    p.feed(html)
    return p.jetons


def details_apres(jetons, i, titre, stop, n=3):
    """Les n premiers textes utiles après le jeton i, jusqu'au prochain jeton qui vérifie stop()."""
    vus = []
    for typ, txt, href in jetons[i + 1:]:
        # s'arrête à la prochaine offre, ou à tout vrai lien (« Voir toutes les offres »…) ;
        # les liens « # » de HelloWork (ville, contrat, salaire) sont des détails, pas des liens
        if stop(typ, txt, href) or (typ == "a" and href and not href.startswith("#")):
            break
        if not txt or txt == titre or len(txt) > 80 or txt in vus:
            continue
        if re.fullmatch(r"(nouveau|new|voir l.offre|postuler|candidature simplifiée|·|-|•"
                        r"|recrutement actif|publié .*|\d+ (relations?|anciens? élèves?))", txt, re.I):
            continue
        vus.append(txt)
        if len(vus) >= n:
            break
    return " · ".join(vus)


# ---------- lecteurs par site ----------

def lire_linkedin(jetons):
    offres, vus = [], set()
    est_offre = lambda typ, txt, href: typ == "a" and href and re.search(r"/jobs/view/\d+", href)
    for i, (typ, txt, href) in enumerate(jetons):
        m = est_offre(typ, txt, href) and re.search(r"/jobs/view/(\d+)", href)
        if not m or not txt or m.group(1) in vus:
            continue
        vus.add(m.group(1))
        offres.append({"titre": txt, "details": details_apres(jetons, i, txt, est_offre),
                       "lien": f"https://www.linkedin.com/jobs/view/{m.group(1)}/",
                       "cle": "linkedin:" + m.group(1)})
    return offres


def lire_indeed(jetons):
    offres, vus = [], set()
    est_offre = lambda typ, txt, href: typ == "a" and href and re.search(r"indeed\.com/(rc|pagead)/clk", href)
    for i, (typ, txt, href) in enumerate(jetons):
        if not est_offre(typ, txt, href) or not txt:
            continue
        jk = parse_qs(urlparse(href).query).get("jk", [""])[0]
        details = details_apres(jetons, i, txt, est_offre)
        cle = "indeed:" + (jk or empreinte(txt, details))
        if cle in vus:
            continue
        vus.add(cle)
        lien = f"https://fr.indeed.com/viewjob?jk={jk}" if jk else href
        offres.append({"titre": txt, "details": details, "lien": lien, "cle": cle})
    return offres


def lire_hellowork(jetons):
    """Bloc d'une offre = lien titre (…/clic/…), puis textes « # » (ville, contrat, salaire), puis « Voir l'offre »."""
    offres = []
    est_clic = lambda typ, txt, href: typ == "a" and href and "hellowork.com/clic/" in href
    voir = lambda x: re.match(r"voir l.offre", x or "", re.I)
    for i, (typ, txt, href) in enumerate(jetons):
        if not est_clic(typ, txt, href) or not txt or voir(txt):
            continue
        # c'est un titre seulement si le PROCHAIN lien de suivi est un « Voir l'offre »
        suivant = next((x for t, x, h in jetons[i + 1:] if est_clic(t, x, h)), None)
        if not voir(suivant):
            continue
        details = details_apres(jetons, i, txt,
                                lambda t, x, h: t == "a" and h and "hellowork.com/clic/" in h)
        offres.append({"titre": txt, "details": details, "lien": href,
                       "cle": "hellowork:" + empreinte(txt, details)})
    return offres


def lire_generique(jetons):
    """JobTeaser / WTTJ : format pas encore vu. Rien plutôt que du faux : l'e-mail sera signalé."""
    return []


def voir_tout_linkedin(jetons):
    """Lien « Voir toutes les offres » d'une alerte LinkedIn, nettoyé de ses jetons de suivi.
    C'est une recherche (mots-clés de l'alerte, zone, « publiées depuis la dernière alerte ») que le navigateur
    connecté peut ouvrir pour trouver les offres absentes de l'e-mail (vu le 2026-11-21 : 6 dans l'e-mail sur 40)."""
    from urllib.parse import urlsplit, parse_qs, urlencode
    for typ, txt, href in jetons:
        if typ == "a" and href and "/jobs/search-results/" in href:
            q = parse_qs(urlsplit(href).query)
            garde = {k: q[k][0] for k in ("keywords", "f_TPR", "geoId", "origin") if k in q}
            if "keywords" in garde:
                return "https://www.linkedin.com/jobs/search-results/?" + urlencode(garde)
    return None


LECTEURS = {"LinkedIn": lire_linkedin, "Indeed": lire_indeed, "HelloWork": lire_hellowork}


def empreinte(*morceaux):
    t = unicodedata.normalize("NFKD", " ".join(morceaux).lower())
    t = re.sub(r"[^a-z0-9]+", " ", t.encode("ascii", "ignore").decode()).strip()
    return hashlib.sha1(t.encode()).hexdigest()[:12]


def annonce(html, objet=""):
    """Nombre d'offres annoncé (« 12 nouvelles offres », objet LinkedIn « X et 30 autres offres »…)."""
    texte = re.sub(r"<[^>]+>", " ", html) + " " + objet
    nombres = [int(n) for n in re.findall(
        r"(\d{1,4})\s+(?:nouve(?:lles?|aux?)\s+|autres\s+)?(?:offres|emplois|jobs|postes)", texte, re.I)]
    # « Une offre … et 30 autres offres » = 31
    autres = re.search(r"et\s+(\d{1,4})\s+autres\s+offres", texte, re.I)
    if autres:
        nombres.append(int(autres.group(1)) + 1)
    return max(nombres) if nombres else None


# ---------- tri ----------

def famille_de(offre):
    t = f"{offre['titre']} {offre['details']}"
    for nom, motif in FAMILLES:
        if motif == "__ALTERNANCE__":
            if ALTERNANCE.search(t) and ALT_METIER.search(t):
                return nom
        elif re.search(motif, t, re.I):
            return nom
    return None


# ---------- état ----------

def lire_etat():
    try:
        with open(ETAT, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"vues": {}}


def ecrire_etat(etat):
    os.makedirs(DOSSIER, mode=0o700, exist_ok=True)
    limite = (date.today() - timedelta(days=OUBLI_JOURS)).isoformat()
    etat["vues"] = {k: v for k, v in etat["vues"].items() if v >= limite}
    tmp = ETAT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(etat, f, ensure_ascii=False, indent=0)
    os.replace(tmp, ETAT)       # écriture atomique : jamais d'état à moitié écrit


# ---------- programme ----------

def marquer():
    """--marquer : ajoute à l'état les offres trouvées après coup par le navigateur (pages « voir toutes »),
    pour ne pas les reproposer demain. Lit le JSON final de la chaîne sur l'entrée standard."""
    etat = lire_etat()
    jour = date.today().isoformat()
    n = 0
    for o in json.load(sys.stdin).get("offres", []):
        if o.get("cle") and o["cle"] not in etat["vues"]:
            etat["vues"][o["cle"]] = jour
            n += 1
    ecrire_etat(etat)
    print(f"{n} offre(s) ajoutée(s) à l'état")


def main():
    if "--marquer" in sys.argv:
        return marquer()
    essai = "--essai" in sys.argv
    etat = lire_etat()
    boite = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=60)
    boite.login(*identifiants())

    # --jours N : fenêtre plus large (premier passage) ; sinon FENETRE_JOURS
    jours = int(sys.argv[sys.argv.index("--jours") + 1]) if "--jours" in sys.argv else FENETRE_JOURS
    depuis = (date.today() - timedelta(days=jours)).strftime("%d-%b-%Y")
    offres, non_lus, incomplets, deja = [], [], [], set()
    recherches = []      # pages « voir toutes les offres » des alertes LinkedIn incomplètes
    for nom_dossier in dossiers(boite):
        boite.select(nom_dossier, readonly=True)
        _, ids = boite.search(None, f"(SINCE {depuis})")
        for num in ids[0].split():
            _, data = boite.fetch(num, "(BODY.PEEK[])")
            msg = email.message_from_bytes(data[0][1])
            mid = msg.get("Message-ID", "") or num.decode()
            if mid in deja:          # même e-mail vu dans deux dossiers
                continue
            deja.add(mid)
            adresse = parseaddr(entete(msg, "From"))[1].lower()
            objet = propre(entete(msg, "Subject"))[:90]
            src = source_de(adresse)
            if src is None:
                if not any(i in adresse for i in IGNORES) and MOTS_EMPLOI.search(objet):
                    non_lus.append(f"{adresse} : {objet}")
                continue
            if re.search(r"candidature a été envoyée|déposez votre candidature|vous avez postulé", objet, re.I):
                continue    # suivi d'une candidature de l'utilisateur, pas une offre nouvelle
            html = html_de(msg)
            lues = LECTEURS.get(src, lire_generique)(jetons_de(html))
            if essai:
                print(f"[{src}] {objet} → {len(lues)} offre(s)")
            if not lues:
                non_lus.append(f"{src} : {objet}")
                continue
            n = annonce(html, entete(msg, "Subject"))
            if n and n > len(lues):
                url = voir_tout_linkedin(jetons_de(html)) if src == "LinkedIn" else None
                if url:
                    recherches.append({"url": url, "annoncees": n, "alerte": objet[:60]})
                else:
                    incomplets.append(f"{src} : {len(lues)} lues sur {n} annoncées ({objet[:50]})")
            for o in lues:
                o["source"] = src
                offres.append(o)

    # chien de garde : chaque source doit avoir écrit dans sa période de tolérance (tous dossiers)
    muettes = []
    par_source = {}
    for morceau, (nom, tolere) in SOURCES.items():
        if tolere:
            par_source.setdefault((nom, tolere), []).append(morceau)
    for (nom, tolere), morceaux in par_source.items():
        limite = (date.today() - timedelta(days=tolere)).strftime("%d-%b-%Y")
        trouve = False
        for nom_dossier in dossiers(boite):
            boite.select(nom_dossier, readonly=True)
            for m in morceaux:
                if boite.search(None, f'(FROM "{m}" SINCE {limite})')[1][0].split():
                    trouve = True
            if trouve:
                break
        if not trouve:
            muettes.append(f"{nom} muet depuis plus de {tolere} jour(s)")
    boite.logout()

    # dédoublonnage : déjà envoyées, et même offre venue de deux e-mails
    nouvelles, cles = [], set()
    for o in offres:
        if o["cle"] in cles or (not essai and o["cle"] in etat["vues"]):
            continue
        cles.add(o["cle"])
        nouvelles.append(o)

    rangees = {}
    hors_cible = []
    for o in nouvelles:
        f = famille_de(o)
        (rangees.setdefault(f, []) if f else hors_cible).append(o)

    if "--json" in sys.argv:
        # Pour la chaîne du matin : toutes les nouvelles offres (y compris hors familles) + l'état des sources.
        for o in nouvelles:
            o["famille"] = famille_de(o) or "hors familles"
        # deja_vus : identifiants LinkedIn déjà proposés, pour que le navigateur ne les rajoute pas
        vus_li = sorted({k.split(":", 1)[1] for k in list(etat["vues"]) + [o["cle"] for o in nouvelles]
                         if k.startswith("linkedin:")})
        print(json.dumps({"date": date.today().isoformat(), "offres": nouvelles, "non_lus": non_lus,
                          "incomplets": incomplets, "muettes": muettes, "recherches": recherches,
                          "deja_vus_linkedin": vus_li}, ensure_ascii=False))
    else:
        print(composer(rangees, hors_cible, non_lus, incomplets, muettes, len(nouvelles)))

    if not essai:
        aujourdhui = date.today().isoformat()
        for o in nouvelles:
            etat["vues"][o["cle"]] = aujourdhui
        ecrire_etat(etat)
        sauver_liste_complete(rangees, hors_cible, aujourdhui)


def ligne(o):
    d = f" — {o['details']}" if o["details"] else ""
    return f"• {o['titre']}{d} [{o['source']}]\n  {o['lien']}"


def composer(rangees, hors_cible, non_lus, incomplets, muettes, total):
    blocs = [f"💼 Veille emploi : {total} nouvelle(s) offre(s)"]
    for nom, _ in FAMILLES:
        lst = rangees.get(nom)
        if not lst:
            continue
        b = [f"\n{nom} ({len(lst)})"] + [ligne(o) for o in lst[:MAX_PAR_FAMILLE]]
        if len(lst) > MAX_PAR_FAMILLE:
            b.append(f"  + {len(lst) - MAX_PAR_FAMILLE} autres (liste complète sur le serveur)")
        blocs.append("\n".join(b))
    if hors_cible:
        titres = " ; ".join(o["titre"][:45] for o in hors_cible[:6])
        blocs.append(f"\n🗂️ Hors familles ({len(hors_cible)}) : {titres}"
                     + (" …" if len(hors_cible) > 6 else ""))
    etat_lignes = []
    if muettes:
        etat_lignes += [f"⚠️ {m}" for m in muettes]
    if non_lus:
        etat_lignes += [f"❓ E-mail non lu par le script — {x}" for x in non_lus[:8]]
    if incomplets:
        etat_lignes += [f"✂️ {x}" for x in incomplets[:5]]
    blocs.append("\n" + ("\n".join(etat_lignes) if etat_lignes else "✅ Toutes les sources répondent"))
    texte = "\n".join(blocs)
    if len(texte) > MAX_MESSAGE:
        texte = texte[:MAX_MESSAGE].rsplit("\n", 1)[0] + "\n… (message tronqué, liste complète sur le serveur)"
    return texte


def sauver_liste_complete(rangees, hors_cible, jour):
    os.makedirs(DOSSIER, mode=0o700, exist_ok=True)
    with open(os.path.join(DOSSIER, f"{jour}.md"), "a", encoding="utf-8") as f:
        for nom, _ in FAMILLES:
            for o in rangees.get(nom, []):
                f.write(f"{nom}\t{o['source']}\t{o['titre']}\t{o['details']}\t{o['lien']}\n")
        for o in hors_cible:
            f.write(f"hors familles\t{o['source']}\t{o['titre']}\t{o['details']}\t{o['lien']}\n")


if __name__ == "__main__":
    main()
