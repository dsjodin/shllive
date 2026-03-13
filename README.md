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

Appen hämtar data från [stats.swehockey.se](https://stats.swehockey.se) –
ingen registrering eller API-nyckel behövs.

```bash
docker compose up --build
```

Öppna sedan [http://localhost:8080](http://localhost:8080).

## Konfiguration

| Variabel       | Beskrivning                                | Default  |
|----------------|--------------------------------------------|----------|
| `SWE_GROUP_ID` | swehockey.se grupp-id för säsongen         | `18263`  |
| `SHL_SEASON`   | Visas i UI:t (säsongsår)                   | `2025`   |
| `PORT`         | Exponerad port                             | `8080`   |

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
                          │ stats.swehockey.se│
                          └──────────────────┘
```

## Hur livetabellen fungerar

1. Grundtabellen hämtas från swehockey.se (cachas 5 min)
2. Pågående matcher identifieras (cachas 30 s)
3. För varje livematch projiceras resultatet:
   - Ledande lag → +3 poäng (regulationstvinst)
   - Oavgjort → +1 poäng vardera (förlängning garanterad)
4. Tabellen sorteras om och visas med liveindikator
