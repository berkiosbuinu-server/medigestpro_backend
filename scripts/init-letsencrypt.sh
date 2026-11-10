#!/bin/sh
# Premier déploiement : obtient le certificat Let's Encrypt pour api.medigestpro.net
# Prérequis : le DNS (enregistrement A) de api.medigestpro.net pointe vers ce VPS,
# les ports 80 et 443 sont ouverts, et .env contient LETSENCRYPT_EMAIL.
set -e
cd "$(dirname "$0")/.."

DOMAIN="api.medigestpro.net"
COMPOSE="docker compose -f Docker-compose.prod.yml"
EMAIL=$(grep -E '^LETSENCRYPT_EMAIL=' .env | cut -d= -f2-)
[ -n "$EMAIL" ] || { echo "LETSENCRYPT_EMAIL manquant dans .env"; exit 1; }

echo "### Certificat temporaire pour démarrer nginx..."
$COMPOSE run --rm --entrypoint sh certbot -c "
  mkdir -p /etc/letsencrypt/live/$DOMAIN &&
  openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
    -keyout /etc/letsencrypt/live/$DOMAIN/privkey.pem \
    -out /etc/letsencrypt/live/$DOMAIN/fullchain.pem -subj '/CN=localhost'"

echo "### Démarrage de la stack..."
$COMPOSE up -d --build

echo "### Demande du vrai certificat..."
$COMPOSE run --rm --entrypoint sh certbot -c "
  rm -rf /etc/letsencrypt/live/$DOMAIN /etc/letsencrypt/archive/$DOMAIN /etc/letsencrypt/renewal/$DOMAIN.conf"
$COMPOSE run --rm --entrypoint certbot certbot certonly --webroot -w /var/www/certbot \
  -d "$DOMAIN" --email "$EMAIL" --agree-tos --no-eff-email --non-interactive

echo "### Rechargement de nginx..."
$COMPOSE exec nginx nginx -s reload
echo "OK : https://$DOMAIN/health"
