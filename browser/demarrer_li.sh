#!/bin/sh
# Navigateur LinkedIn « avec écran » sur le serveur (test du 2026-11-20).
# Chromium ORDINAIRE (pas lancé par Playwright → pas de mode automatisation), profil gardé dans /profil.
# L'utilisateur s'y connecte lui-même via noVNC (tunnel SSH) ; lire_annonces.py se branche ensuite dessus.
# Le port de pilotage 9222 n'écoute que dans le conteneur (127.0.0.1) ; aucun port n'est publié.
set -e
# Verrou « profil utilisé par un autre ordinateur » laissé par le conteneur précédent (nom d'hôte différent) :
# sans ce ménage, Chromium affiche une fenêtre d'avertissement au lieu de démarrer.
rm -f /profil/SingletonLock /profil/SingletonSocket /profil/SingletonCookie
# Verrou d'écran virtuel laissé par un démarrage précédent (docker restart garde /tmp) : sinon Xvfb refuse de démarrer.
rm -f /tmp/.X99-lock
Xvfb :99 -screen 0 1280x900x24 -nolisten tcp &
export DISPLAY=:99
sleep 2
x11vnc -display :99 -forever -shared -nopw -localhost -quiet &
websockify --web /usr/share/novnc 6080 localhost:5900 >/dev/null 2>&1 &
# Chromium de Debian. --no-sandbox : son bac à sable interne ne démarre pas dans un conteneur sans privilèges.
# Économie de mémoire (serveur de 2 Go) : 2 processus de rendu max, pas de /dev/shm (64 Mo dans Docker).
# Page de départ vide par défaut (le fil LinkedIn est très lourd) ; pour se reconnecter :
#   docker run … -e START_URL=https://www.linkedin.com/login …
exec chromium --user-data-dir=/profil --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1 \
  --no-first-run --no-default-browser-check --lang=fr-FR --start-maximized --no-sandbox \
  --disable-dev-shm-usage --renderer-process-limit=2 \
  "${START_URL:-about:blank}"
