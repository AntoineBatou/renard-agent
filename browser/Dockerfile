# Navigateur LinkedIn « avec écran » pour Renard (test du 2026-11-20).
# Chromium ordinaire de Debian sur un écran virtuel (Xvfb), visible par l'utilisateur via noVNC (tunnel SSH).
# L'utilisateur se connecte lui-même. Aucun déguisement : pas de proxy, pas de mode furtif, pas de solveur de CAPTCHA.
# Construction (sur le serveur) : sudo docker build -t li-nav /srv/li-nav/image
FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive TZ=Europe/Paris

# chromium = le navigateur ; xvfb = écran virtuel ; x11vnc + novnc + websockify = voir cet écran depuis le Mac
# fonts-* = polices (accents, emojis) pour que les pages s'affichent correctement
RUN apt-get update \
 && apt-get install -y --no-install-recommends chromium xvfb x11vnc novnc websockify \
      fonts-liberation fonts-dejavu-core fonts-noto-color-emoji \
 && apt-get clean && rm -r /var/lib/apt/lists/*

# Seulement la bibliothèque Python qui se branche sur le Chromium déjà ouvert (aucun navigateur téléchargé)
RUN pip install --no-cache-dir playwright==1.49.0

# Utilisateur dédié, non-root (le dossier /profil du serveur lui appartient)
RUN useradd -u 10001 -m renard
USER renard
CMD ["/li/demarrer_li.sh"]
