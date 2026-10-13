#!/usr/bin/with-contenv bashio
# Herstart bij een crash (bijv. na een verbroken websocket) na 30 seconden.
cd /opt/sat_autotune
while true; do
  python3 -m app.main
  code=$?
  # exitcode 0 = bewuste stop (bijv. testmodus afgerond), 64 = niet herstarten
  if [ "$code" -eq 64 ]; then exit 0; fi
  bashio::log.warning "SAT Autotune gestopt (code ${code}); herstart over 30 s"
  sleep 30
done
