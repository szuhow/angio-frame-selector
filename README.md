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

Domyślnie baza SQLite jest trzymana w wolumenie Dockera `db-data`, wgrane zbiory danych w wolumenie `library-data` (`/app/library`), a wersjonowane eksporty w `exports-data` (`/app/exports`). Dane nie są już montowane z hosta – nowe zbiory dodaje administrator z poziomu interfejsu aplikacji.

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
- `LIBRARY_DIR` – katalog biblioteki zbiorów wewnątrz kontenera (domyślnie `/app/library`)
- `EXPORTS_DIR` – katalog przechowujący wersjonowane eksporty (domyślnie `/app/exports`)
- `MAX_UPLOAD_BYTES` – maksymalny rozmiar wgrywanego ZIP-a ze zbiorem (domyślnie 10 GiB)
- `CONTAINER_DB_PATH` – ścieżka do pliku SQLite wewnątrz kontenera
- `JWT_SECRET` – sekret do podpisywania tokenów JWT
- `JWT_EXPIRY_HOURS` – czas ważności tokenów

Zmienne architektury obrazu nie są ustawiane w `.env.example`. Domyślny build używa architektury hosta, a cross-build pod `amd64` najlepiej wymuszać jednorazowo przez `DOCKER_DEFAULT_PLATFORM=linux/amd64`.

## Zarządzanie zbiorami danych (admin)

Administrator zarządza zbiorami danych z panelu w aplikacji (zakładka **Zbiory danych**):

- **Rejestracja istniejącego katalogu** – wskazujesz folder leżący bezpośrednio w `LIBRARY_DIR` (np. skopiowany tam przez operatora infrastruktury). Backend wykryje nieprzypisane podkatalogi i udostępni je na liście.
- **Upload archiwum ZIP** – wgrywasz plik ZIP przez interfejs. Archiwum jest weryfikowane (zip-slip, dozwolone rozszerzenia `.dcm`, `.png`, limit `MAX_UPLOAD_BYTES`) i rozpakowywane do `LIBRARY_DIR/<slug>`.
- **Usunięcie zbioru** – odpina wszystkie przypisania i kasuje pliki na dysku.

Każdy zbiór ma unikalny `slug` używany w ścieżkach eksportów i katalogów.

### Duże zbiory – bind-mount katalogu hosta

Jeśli masz wiele GB DICOM-ów, nie musisz ich kopiować do kontenera ani przesyłać ZIP-em. Ustaw w `.env`:

```env
HOST_LIBRARY_DIR=/absolute/path/to/dicom-library
# opcjonalnie, żeby eksporty też trafiały na hosta:
HOST_EXPORTS_DIR=/absolute/path/to/exports
```

Struktura katalogu: `HOST_LIBRARY_DIR/<nazwa-zbioru>/...pliki DICOM...`. Po `docker compose up -d` w panelu admina → **Zbiory danych** → sekcja „Biblioteka (niezarejestrowane)” pojawią się podfoldery do jednoklikowej rejestracji. Pliki pozostają na hoście, kontener czyta je in-place. Jeśli nie ustawisz tych zmiennych, używany jest nazwany wolumen Dockera i dane wgrywa się wyłącznie przez ZIP.

### Przypisywanie zbiorów użytkownikom

W zakładce **Przypisania** admin przypisuje zbiory danych do konkretnych kont:

- Użytkownicy widzą wyłącznie zbiory, do których zostali przypisani.
- Administratorzy widzą wszystkie zbiory.
- Wszystkie endpointy API wymagają parametru/ciała `dataset_id`; próba dostępu do nieprzypisanego zbioru zwraca `404` (ukrywamy samo istnienie zbioru).
- Adnotacje i skipy są zakresowane po zbiorach – `UNIQUE(dataset_id, patient_id, sequence_id, user_id)`.

### Wersjonowane eksporty

W zakładce **Wersje eksportu** admin tworzy migawki adnotacji dla wybranego zbioru:

- Formaty: `annotations-json` oraz `coco`.
- Wersja musi pasować do `^[A-Za-z0-9][A-Za-z0-9._\-]{0,63}$` (np. `v1.0.0`).
- Plik jest serializowany kanonicznie (`sort_keys=True`) i hashowany SHA-256; hash jest zwracany w nagłówku `ETag` przy pobraniu.
- Próba utworzenia wersji o tej samej nazwie i formacie dla tego samego zbioru zwraca `409`.
- Fizyczny plik ląduje w `EXPORTS_DIR/<slug>/<version>/<format>.json`. Jeśli zostanie ręcznie usunięty, pobranie zwraca `410`.

Kluczowe endpointy:

- `POST /api/export/versions` – utworzenie wersji (admin)
- `GET /api/export/versions?dataset_id=…` – lista wersji widocznych dla użytkownika
- `GET /api/export/versions/{id}/download` – pobranie migawki (z `ETag`)
- `DELETE /api/export/versions/{id}` – usunięcie wersji (admin)

### Metadane sekwencji (kąt obrazowania i inne)

Panel „Metadane” w widoku sekwencji wyświetla wartości tagów DICOM wyciągane z pliku `.dcm` lub z sidecara DICOM-JSON (`foo.dcm.json` obok/w folderze sekwencji PNG). Dostępny przycisk <kbd>i</kbd> w pasku narzędzi pokazuje się tylko gdy sekwencja posiada choć jeden skonfigurowany tag.

- Domyślna lista zawiera `PositionerPrimaryAngle (0018,1510)`, `PositionerSecondaryAngle (0018,1511)`, `KVP`, `XRayTubeCurrent`, `FrameTime`, `StudyDate`.
- Kąty są formatowane jako `RAO/LAO` i `CAU/CRA` na podstawie znaku (np. `-24 → RAO 24°`).
- Admin zarządza listą w zakładce **Metadane** panelu administratora: dodawanie tagów w formacie `00181510` / `0018,1510` / `(0018,1510)`, nadpisywanie etykiet, zmiana kolejności, przywracanie wartości domyślnych.

Endpointy:

- `GET /api/metadata/config` – bieżąca konfiguracja (każdy zalogowany)
- `PUT /api/metadata/config` – aktualizacja (admin)
- `GET /api/patients/{pid}/sequences/{sid}/metadata?dataset_id=…` – wartości dla sekwencji

## Kontrola wersji

Repozytorium jest przygotowane do pracy z Git:

- `.gitignore` ignoruje lokalne dane, bazy SQLite, środowiska wirtualne, cache i buildy
- `backend/.dockerignore` i `frontend/.dockerignore` ograniczają kontekst buildu kontenerów do niezbędnych plików
- przykładowe pliki env pozwalają trzymać sekrety poza repozytorium
