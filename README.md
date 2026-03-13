# SHL Live Standings

Containeriserad webbapp som visar SHL-tabellen med **liveopdatering** –
när matcher pågår räknas tabellen om i realtid baserat på aktuell ställning.

## Funktioner

- Fullständig SHL-tabell med alla nyckelstatistik
- **Livetabell** – under pågående matcher projiceras resultaten direkt in i tabellen
- Live-matchpanel med aktuell ställning och period
- WebSocket-uppdateringar (polling var 30 s som fallback)
- Mobilanpassad design med SHL:s färger

## Kom igång

### Snabbstart – utan API-nycklar

Appen fungerar **utan registrering** tack vare en inbyggd reservlösning som
hämtar data från [stats.swehockey.se](https://stats.swehockey.se).

```bash
docker compose up --build
```

Öppna sedan [http://localhost:8080](http://localhost:8080).

### Med officiell SHL Open API (rekommenderat)

Registrera dig på [SHL Open API](https://openapi.shl.se) för fullständig
datakvalitet och mer detaljerade livematchdata.

```bash
cp .env.example .env
# Fyll i SHL_CLIENT_ID och SHL_CLIENT_SECRET
docker compose up --build
```

## Konfiguration

| Variabel            | Beskrivning                                    | Default  |
|---------------------|------------------------------------------------|----------|
| `SHL_CLIENT_ID`     | OAuth2 client id från SHL API (valfritt)       | –        |
| `SHL_CLIENT_SECRET` | OAuth2 client secret från SHL API (valfritt)   | –        |
| `SHL_SEASON`        | Säsongsår (2025 = säsong 2025/26)              | `2025`   |
| `SWE_GROUP_ID`      | swehockey.se grupp-id (reservläge)             | `18263`  |
| `PORT`              | Exponerad port                                 | `8080`   |

## Arkitektur

```
┌─────────────┐   nginx   ┌──────────────────┐
│   Browser   │ ◄───────► │  frontend (nginx) │
└─────────────┘           └────────┬─────────┘
                                   │ /api/* + /ws
                          ┌────────▼─────────┐
                          │ backend (FastAPI) │
                          └────────┬─────────┘
                                   │ HTTPS
                   ┌───────────────┴──────────────────┐
          ┌────────▼─────────┐             ┌──────────▼──────────┐
          │  SHL Open API    │             │  stats.swehockey.se  │
          │  (med API-nyckel)│             │  (reservlösning)     │
          └──────────────────┘             └─────────────────────┘
```

## Datakällor

Appen väljer datakälla automatiskt:

| Situation                        | Datakälla              |
|----------------------------------|------------------------|
| `SHL_CLIENT_ID` + `SECRET` satta | SHL Open API (OAuth2)  |
| Inga API-nycklar konfigurerade   | stats.swehockey.se     |

## Hur livetabellen fungerar

1. Grundtabellen hämtas och cachas (5 min)
2. Pågående matcher identifieras (cachas 30 s)
3. För varje livematch projiceras resultatet:
   - Ledande lag → +3 poäng (regulationstvinst)
   - Oavgjort → +1 poäng vardera (förlängning garanterad)
4. Tabellen sorteras om och visas med liveindikator
