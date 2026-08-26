# Mac Mini recovery runbook

The live stack is `docker-compose.real.yml`. Its persistent data is held in the
named volumes `econest-real_mysql-data`, `econest-real_arcadedb-data`, and
`econest-real_ollama-data`.

1. Start Docker Desktop and Tailscale. Confirm Home Assistant is running.
2. Confirm `.env` contains `HA_TOKEN`, `ARCADEDB_USER`, and
   `ARCADEDB_PASSWORD`. Set `HA_URL` only when Home Assistant is not on the
   Mac host; the default is `http://host.docker.internal:8123`.
3. Start safely: `docker compose -f docker-compose.real.yml up -d`.
4. Check `docker compose -f docker-compose.real.yml ps` and
   `curl http://localhost:8001/recovery/status`.
5. Inspect logs with `docker compose -f docker-compose.real.yml logs -f orchestrator`.

Use `stop`/`start` for normal recovery. `down` is safe only **without** `-v`.
Never run `docker compose ... down -v` against the real stack: it deletes the
authoritative database volumes. Before maintenance, create MySQL and ArcadeDB
backups and record their timestamps.

Remote Tailnet UIs: orchestrator/MySQL monitor `http://<mac-tailscale-ip>:8001/monitor`;
ArcadeDB Studio `http://<mac-tailscale-ip>:2481`.

The scheduled graph sync is separate from HA graph bootstrap. Check
`/recovery/status` for its last result; a healthy sync gives graph records their
MySQL identities and current `SensorReading` vertices. Do not perform a HA
service call as a recovery check without an explicitly approved safe entity.
