# Keyselector Dataset

Narzędzie do przeglądania i anotacji sekwencji angiografii wieńcowej. Projekt składa się z backendu FastAPI obsługującego DICOM/PNG oraz frontendu React/Vite wyświetlanego przez Nginx w kontenerze.

## Struktura projektu

- `backend/` – API FastAPI, skanowanie danych DICOM/PNG, autoryzacja, SQLite
- `frontend/` – aplikacja React do przeglądania sekwencji i anotacji klatek
- `docker-compose.yml` – uruchomienie całego stosu lokalnie
- `.env.example` – wzorzec zmiennych środowiskowych dla Compose i kontenerów
- `backend/.env.example` – wzorzec zmiennych środowiskowych dla lokalnego uruchamiania backendu
- `start.sh` – szybki start w trybie deweloperskim bez Dockera

Frontend nie wymaga osobnego pliku env w obecnej konfiguracji. W developmentcie używa proxy Vite do backendu, a w kontenerze produkcyjnym proxy Nginx.

## Wymagania

- Docker + Docker Compose
- `uv` dla backendu Python przy lokalnym uruchamianiu backendu bez Dockera
- Node.js 20+ dla frontendu przy lokalnym developmentcie bez Dockera

## Szybki start z Docker Compose

1. Skopiuj konfigurację środowiska:

   ```bash
   cp .env.example .env
   ```

2. Ustaw bezpieczne `JWT_SECRET` w `.env`.

3. Uruchom stos:

   ```bash
   ./start.sh
   ```

   Skrypt uruchamia `docker compose up --build` z katalogu głównego projektu.

4. Otwórz aplikację:

   - frontend: `http://localhost:3000`
   - backend API: `http://localhost:8000`

Domyślnie dane wejściowe są montowane z `backend/data/`, a baza SQLite jest trzymana w wolumenie Dockera `db-data`.

Compose buduje obrazy natywnie dla architektury hosta. Na Apple Silicon daje to obrazy `arm64`, a na typowym serwerze Ubuntu obrazy `amd64`.

Jeśli z Apple Silicon chcesz celowo zbudować obraz pod Ubuntu `x86_64`, uruchom build z wymuszoną platformą:

```bash
DOCKER_DEFAULT_PLATFORM=linux/amd64 docker compose build
```

Analogicznie możesz uruchomić cały stos:

```bash
DOCKER_DEFAULT_PLATFORM=linux/amd64 ./start.sh
```

## Lokalne uruchamianie bez Dockera

### Backend

1. Skopiuj plik środowiskowy:

   ```bash
   cp backend/.env.example backend/.env
   ```

2. Zainstaluj zależności przez `uv`:

   ```bash
   cd backend
   uv sync
   ```

### Frontend

1. Zainstaluj zależności:

   ```bash
   cd frontend
   npm ci
   ```

### Start obu usług lokalnie

Uruchom backend w jednym terminalu:

```bash
cd backend
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Uruchom frontend w drugim terminalu:

```bash
cd frontend
npm run dev -- --host 0.0.0.0 --port 5173
```

## Zarządzanie zależnościami backendu

Backend używa `uv` i plików:

- `backend/pyproject.toml` – manifest zależności
- `backend/uv.lock` – lockfile używany także podczas budowy obrazu Dockera

Po zmianie zależności zaktualizuj lockfile:

```bash
cd backend
uv lock
```

## Zmienne środowiskowe

Najważniejsze zmienne używane przez kontenery i backend:

- `BACKEND_PORT` – port wystawiany lokalnie dla API
- `FRONTEND_PORT` – port wystawiany lokalnie dla frontendu
- `HOST_DATA_DIR` – katalog z danymi DICOM/PNG po stronie hosta
- `CONTAINER_DATA_DIR` – ścieżka danych wewnątrz kontenera backendu
- `CONTAINER_DB_PATH` – ścieżka do pliku SQLite wewnątrz kontenera
- `JWT_SECRET` – sekret do podpisywania tokenów JWT
- `JWT_EXPIRY_HOURS` – czas ważności tokenów

Zmienne architektury obrazu nie są ustawiane w `.env.example`. Domyślny build używa architektury hosta, a cross-build pod `amd64` najlepiej wymuszać jednorazowo przez `DOCKER_DEFAULT_PLATFORM=linux/amd64`.

## Kontrola wersji

Repozytorium jest przygotowane do pracy z Git:

- `.gitignore` ignoruje lokalne dane, bazy SQLite, środowiska wirtualne, cache i buildy
- `backend/.dockerignore` i `frontend/.dockerignore` ograniczają kontekst buildu kontenerów do niezbędnych plików
- przykładowe pliki env pozwalają trzymać sekrety poza repozytorium