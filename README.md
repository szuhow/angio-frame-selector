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
- `uv` dla backendu Python
- Node.js 20+ dla frontendu przy lokalnym developmentcie

## Szybki start z Docker Compose

1. Skopiuj konfigurację środowiska:

   ```bash
   cp .env.example .env
   ```

2. Ustaw bezpieczne `JWT_SECRET` w `.env`.

3. Uruchom stos:

   ```bash
   docker compose up --build
   ```

4. Otwórz aplikację:

   - frontend: `http://localhost:3000`
   - backend API: `http://localhost:8000`

Domyślnie dane wejściowe są montowane z `backend/data/`, a baza SQLite jest trzymana w wolumenie Dockera `db-data`.

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

### Start obu usług

Z katalogu głównego projektu:

```bash
./start.sh
```

Skrypt ładuje opcjonalnie `.env` z katalogu głównego i `backend/.env`, uruchamia backend przez `uv run` oraz frontend przez `vite`.

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

## Kontrola wersji

Repozytorium jest przygotowane do pracy z Git:

- `.gitignore` ignoruje lokalne dane, bazy SQLite, środowiska wirtualne, cache i buildy
- `backend/.dockerignore` i `frontend/.dockerignore` ograniczają kontekst buildu kontenerów do niezbędnych plików
- przykładowe pliki env pozwalają trzymać sekrety poza repozytorium