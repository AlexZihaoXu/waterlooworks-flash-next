#!/bin/sh
set -eu
# Only a canonical public DNS hostname and optional TLS port may enter nginx syntax.
# No secrets are accepted or expanded by this entrypoint.
case "${WW_PUBLIC_HOST:-}" in
    ''|*[!a-z0-9.:-]*)
        echo 'Public host configuration unavailable.' >&2
        exit 1
        ;;
esac
if ! printf '%s\n' "$WW_PUBLIC_HOST" | grep -Eq '^[a-z0-9]+([.-][a-z0-9]+)*(:[1-9][0-9]{0,4})?$'; then
    echo 'Public host configuration unavailable.' >&2
    exit 1
fi
umask 077
envsubst '${WW_PUBLIC_HOST}' < /etc/nginx/nginx.conf.template > /tmp/nginx.conf
exec nginx -c /tmp/nginx.conf -g 'daemon off;'
