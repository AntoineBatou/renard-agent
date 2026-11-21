#!/bin/sh
# Chaîne du matin, partie « serveur » (lancée par root à 7 h 00, /etc/cron.d/veille-emploi).
#   1. collecte des offres dans la boîte de Renard (conteneur hermes, utilisateur 10000)
#   2. lecture de l'annonce complète de chaque offre par le Chromium connecté (conteneur li-nav)
#   3. dépôt du résultat dans /srv/hermes/data/emploi/jour/AAAA-MM-JJ.json (lu par Renard à 8 h)
# Si la lecture échoue, on dépose quand même les offres sans annonce : rien n'est perdu.
# À installer : sudo install -m 750 veille-emploi.sh /usr/local/sbin/
set -u
JOUR=$(date +%F)
DOS=/srv/hermes/data/emploi/jour
SORTIE="$DOS/$JOUR.json"
COMPOSE="docker compose -f /srv/hermes/compose.yaml"
TMP=$(mktemp)
LUES=$(mktemp)

mkdir -p "$DOS"

# Le navigateur doit tourner (après un redémarrage du serveur, par exemple)
if [ "$(docker inspect -f '{{.State.Running}}' li-nav 2>/dev/null)" != "true" ]; then
  docker start li-nav >/dev/null 2>&1
  sleep 25
fi

# Arguments éventuels transmis à la collecte (ex. : « veille-emploi.sh --jours 7 » pour le premier passage)
if ! $COMPOSE exec -T -u 10000 hermes python3 /opt/data/scripts/collecte_emploi.py --json "$@" > "$TMP" 2>"$TMP.err"; then
  printf '{"date":"%s","erreur":"collecte en échec : %s","offres":[]}\n' "$JOUR" \
    "$(tr '\n"' ' ' < "$TMP.err" | cut -c1-300)" > "$TMP"
fi

if docker exec -i li-nav python3 /li/lire_annonces.py < "$TMP" > "$LUES" 2>"$LUES.err" && [ -s "$LUES" ]; then
  cp "$LUES" "$SORTIE"
else
  # lecture impossible : offres sans annonce, avec la raison (Renard le signalera)
  python3 - "$TMP" "$LUES.err" > "$SORTIE" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
err = open(sys.argv[2]).read().strip().splitlines()[-1:] or ["?"]
d["lecture"] = {"lues": 0, "total": len(d.get("offres", [])), "arret": "navigateur en échec : " + err[0][:200]}
print(json.dumps(d, ensure_ascii=False))
EOF
fi

chown 10000:10000 "$DOS" "$SORTIE" && chmod 600 "$SORTIE"

# Offres ajoutées par le navigateur (pages « voir toutes » des alertes LinkedIn) : les marquer comme vues
$COMPOSE exec -T -u 10000 hermes python3 /opt/data/scripts/collecte_emploi.py --marquer < "$SORTIE" >/dev/null 2>&1
rm -f "$TMP" "$TMP.err" "$LUES" "$LUES.err"

# Offres bloquées par un contrôle anti-robot : 2e essai 20 minutes plus tard (le contrôle retombe souvent).
# Ce qui reste bloqué va dans « À LIRE » de l'e-mail de Renard, avec le lien, en tête.
for S in Indeed HelloWork; do
  if grep -q "\"source\": \"$S\"[^}]*anti-robot" "$SORTIE" 2>/dev/null; then
    sleep 1200
    python3 /usr/local/sbin/relire_source.py "$S" "$JOUR" >/dev/null 2>&1
  fi
done
