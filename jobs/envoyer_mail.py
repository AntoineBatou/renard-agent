#!/usr/bin/env python3
"""Envoie un e-mail depuis la boîte de l'agent vers l'utilisateur (une seule adresse, fixée dans le fichier secret).

Usage (par Renard) : python3 /opt/data/scripts/envoyer_mail.py "Objet" /opt/data/emploi/mail-AAAA-MM-JJ.md
Le destinataire n'est JAMAIS un argument : seulement MAIL_TO dans /opt/data/.mail-secrets (écrit par l'utilisateur, jamais par l'agent).
"""

import smtplib
import sys
from email.message import EmailMessage

SECRET = "/opt/data/.mail-secrets"


def lire_secret():
    v = {}
    with open(SECRET, encoding="utf-8") as f:
        for ligne in f:
            if "=" in ligne:
                k, val = ligne.strip().split("=", 1)
                v[k.strip()] = val.strip()
    return v["MAIL_USER"], v["MAIL_APP_PASSWORD"].replace(" ", ""), v["MAIL_TO"]


def main():
    objet, chemin = sys.argv[1], sys.argv[2]
    user, mdp, dest = lire_secret()
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = user, dest, objet
    msg.set_content(open(chemin, encoding="utf-8").read())
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(user, mdp)
        s.send_message(msg)
    print("E-mail envoyé.")


if __name__ == "__main__":
    main()
