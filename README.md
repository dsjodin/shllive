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

### 1. Skaffa API-nycklar

Registrera dig på [SHL Open API](https://openapi.shl.se) för att få
`client_id` och `client_secret`.

### 2. Konfigurera miljövariabler

```bash
cp .env.example .env
# Redigera .env och fyll i dina API-nycklar
```

### 3. Starta med Docker Compose

```bash
docker compose up --build
```

Öppna sedan [http://localhost:8080](http://localhost:8080).

## Konfiguration

| Variabel            | Beskrivning                         | Default |
|---------------------|-------------------------------------|---------|
| `SHL_CLIENT_ID`     | OAuth2 client id från SHL API       | –       |
| `SHL_CLIENT_SECRET` | OAuth2 client secret från SHL API   | –       |
| `SHL_SEASON`        | Säsongsår (2025 = säsong 2025/26)   | `2025`  |
| `PORT`              | Exponerad port                      | `8080`  |

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
                          ┌────────▼─────────┐
                          │    SHL API        │
                          └──────────────────┘
```

## Hur livetabellen fungerar

1. Grundtabellen hämtas från SHL:s API (cachas 5 min)
2. Pågående matcher identifieras (cachas 30 s)
3. För varje livematch projiceras resultatet:
   - Ledande lag → +3 poäng (regulationstvinst)
   - Oavgjort → +1 poäng vardera (förlängning garanterad)
4. Tabellen sorteras om och visas med liveindikator
