# CLAUDE.md — Ollama Manager

Kontekst projektu dla asystentów AI (Claude / Continue). Trzymaj ten plik w katalogu głównym repo.

## Czym jest projekt

Prosta aplikacja desktopowa **PyQt6 pod KDE** do zarządzania lokalną instancją **Ollamy**:
uruchamianie/zatrzymywanie usługi, autostart przy starcie systemu oraz zarządzanie
modelami AI (lista, pobieranie, usuwanie). Narzędzie osobiste, uruchamiane w domowym
środowisku (workstation + serwer BC-250), całość w LAN — nic nie wychodzi na zewnątrz.

## Założenia projektu

- **Prostota ponad wszystko** — to ma być czytelne, łatwe do rozbudowy narzędzie, a nie framework.
- **Prywatność** — działa lokalnie, żadnych zależności od chmury ani telemetrii.
- **Rozdział warstw** — logika sieci i sterowanie usługą są oddzielone od GUI, żeby okno  
  zostało czyste i żeby dało się je rozwijać bez rozgrzebywania wszystkiego naraz.
- **Nieblokujące GUI** — każda operacja sieciowa lub wymagająca uprawnień idzie w osobnym  
  wątku (`QThread`); okno nigdy się nie zawiesza.
- **Natywność KDE** — wygląd Breeze, ikony z motywu systemowego, uprawnienia przez polkit  
  (graficzny dialog), bez wymagania terminala.

## Stack i zależności

- Python 3, **PyQt6**, **requests**
- Wymaga **systemd** + **polkit (pkexec)** — standard na KDE/Debian
- Ollama zainstalowana jako usługa systemd o nazwie `ollama`

Instalacja i uruchomienie:

```
pip install PyQt6 requests
python3 ollama_manager.py
```

## Architektura kodu

Wszystko w jednym pliku `ollama_manager.py`, podzielone na wyraźne bloki:

- **Funkcje sterowania usługą** (`usluga_start`, `usluga_stop`, `usluga_autostart`,  
  `_pkexec`, `_systemctl_query`) — obsługa systemd. Zapytania o stan (`is-active`,  
  `is-enabled`) nie wymagają roota; akcje (start/stop/enable/disable) idą przez `pkexec`.
- `OllamaClient` — cienka nakładka na REST API Ollamy (status, lista modeli,  
  pobieranie strumieniowe, usuwanie). Cała sieć siedzi tutaj.
- **Wątki robocze** (`RefreshWorker`, `PullWorker`, `ActionWorker`) — długie/blokujące  
  operacje w tle, komunikacja z GUI przez sygnały.
- `MainWindow` — całe GUI i spinanie logiki.

## Kluczowe decyzje i ograniczenia

- **Sterowanie usługą zawsze dotyczy LOKALNEGO systemd.** Nawet jeśli `OLLAMA_URL`  
  wskazuje serwer zdalny (BC-250), start/stop/autostart działają na lokalnej maszynie —  
  bo nie da się zdalnie startować usługi przez `systemctl`. Operacje na modelach  
  (lista/pobierz/usuń) idą tam, gdzie wskazuje `OLLAMA_URL`.
- `OLLAMA_URL` **i** `SERVICE_NAME` **to stałe na górze pliku** — jedno miejsce do zmiany,  
  gdy chcesz wskazać BC-250 (`http://192.168.0.236:11434`) albo usługę o innej nazwie.
- **Jedno pobieranie modelu naraz** — świadome uproszczenie, nie obciąża łącza/dysku podwójnie.
- `pkexec` **zamiast sudo** — pokazuje graficzny dialog polkit KDE zamiast wymagać terminala.
- **Log pisze tylko zmiany statusu pobierania** — strumień `/api/pull` sypie dziesiątkami  
  linii na sekundę, więc do logu trafiają tylko nowe etapy, a procent idzie na pasek.

## Konwencje kodu (WAŻNE — trzymaj się ich)

- **Dwa poziomy komentarzy w każdym kodzie**: krótki **WHAT** (do szybkiego skanowania)  
  oraz **WHY** dla nieoczywistych decyzji. Krytyczne fragmenty wyjaśnij jak koledze,  
  zanim uznam kod za gotowy.
- **Komentarze i interfejs po polsku.**
- **Dostarczaj kompletne bloki kodu gotowe do pełnej podmiany** — bez fragmentów typu  
  „tu zmień ręcznie”. Cały plik do wklejenia.
- **Plain language** — prosto, bez zbędnego żargonu.
- **Weryfikuj aktualność (rok 2026)** — nazwy pakietów, wersje, linki i repozytoria  
  sprawdzaj, że nadal istnieją i działają; nie podawaj ich z pamięci historycznej.
- Diagramy/schematy (jeśli potrzebne) proponuj jako **draw.io w PlainText**.

## Struktura plików

```
.
├── ollama_manager.py   # cała aplikacja
├── CLAUDE.md           # ten plik — kontekst dla asystentów AI
└── README.md           # (do zrobienia) opis dla ludzi
```

## API Ollamy używane w projekcie

- `GET  /api/tags`   — lista zainstalowanych modeli / ping serwera
- `POST /api/pull`   — pobieranie modelu (stream JSON: `status`, `total`, `completed`)
- `DELETE /api/delete` — usunięcie modelu (body: `{"model": "<nazwa>"}`)

## Uwagi dot. Ollamy (przydatne przy rozwoju)

- **Okno kontekstu**: Ollama domyślnie odpala modele z oknem 4k, co psuje pracę agentową  
  w Continue/OpenCode. Ustawiane globalnie zmienną `OLLAMA_CONTEXT_LENGTH` po stronie  
  serwera (u nas 32768 na BC-250).
- **Modele agentowe muszą wspierać tool-calling** — rodzina Qwen (np. `qwen2.5-coder`)  
  tak; modele bez tego (Mistral Nemo, Granite) nie utworzą/nie zmienią plików.
- Docelowe modele stacku: `qwen2.5-coder:7b`, `qwen2.5-coder:14b`, `nomic-embed-text`.

## Modele zdalne (RemoteHost / RemoteModel)

Ollama potrafi natywnie proxować inferencję na inny serwer Ollama: tworzysz lokalny
„model-skrót”, który przekazuje zapytania na zdalny host (u nas BC-250), a liczy je
zdalne GPU. Model widać lokalnie w `ollama list` / `/api/tags`, więc VS Code (Continue)
celuje tylko w `localhost`. To jest docelowy sposób, żeby mieć WSZYSTKIE modele
„pod jedną lokalną Ollamą”, a ciężki 14B i tak liczył się na 16 GB BC-250.

Jak to działa ręcznie (do zautomatyzowania w aplikacji):

1. Whitelist zdalnego hosta — zmienna usługi: `OLLAMA_REMOTES=ollama.com,192.168.0.236`. Bez tego lokalna Ollama odrzuci połączenie (domyślnie whitelista = tylko `ollama.com`).
2. Modelfile:

   ```
   FROM ollama/base
   REMOTE_HOST http://192.168.0.236:11434
   REMOTE_MODEL qwen2.5-coder:14b
   ```
3. `ollama create qwen-14b-bc250 -f Modelfile`

Uwagi: to nowsza funkcja Ollamy (weryfikuj `ollama --version`); `REMOTE_MODEL` musi
DOKŁADNIE odpowiadać nazwie na zdalnym hoście; Ollama nie ma autoryzacji — tylko LAN.

## Roadmap / planowane funkcje

- [x] **Dodawanie modeli zdalnych (RemoteHost) z poziomu okna** — kreator: użytkownik podaje adres zdalnego hosta (np. BC-250) + nazwę zdalnego modelu, a aplikacja: dopisuje host do `OLLAMA_REMOTES` (przez pkexec/systemctl), generuje Modelfile i woła `ollama create`. Cel: wszystkie modele widoczne pod jedną lokalną Ollamą, do której podpina się VS Code.
      Zaimplementowane: `DialogModelZdalny` + funkcje `usluga_dodaj_remote_host`/`dodaj_model_zdalny`.
      UWAGA: nieprzetestowane end-to-end na prawdziwym KDE/BC-250 (środowisko deweloperskie
      nie miało PyQt6 ani Ollamy) — przejść raz ręcznie, zanim uzna się za w pełni gotowe.
- [x] Podgląd modeli aktualnie załadowanych do pamięci (`/api/ps`) — widać zużycie VRAM.
      Zaimplementowane: `OllamaClient.list_loaded()` + lista „Załadowane do pamięci (VRAM)”
      pod listą zainstalowanych modeli, odświeżana razem z resztą stanu (co 5 s).
      UWAGA: jak wyżej — nieprzetestowane end-to-end (brak PyQt6/Ollamy w środowisku deweloperskim).
- [ ] Przełącznik localhost ↔ BC-250 z poziomu okna (bez edycji stałej)
- [ ] Ikona w zasobniku systemowym (tray) z szybkim start/stop
- [ ] README.md dla ludzi
- [ ] **Integracja z Open WebUI (panel czatu w przeglądarce)** — uruchamiany lokalnie
      przez `pip install open-webui` (Python 3.11/3.12), BEZ Dockera — prościej,
      spójne z resztą projektu (jeden Python, żadnej dodatkowej infrastruktury).
      Aplikacja mogłaby dorzucić przycisk „Uruchom WebUI”, który odpala je jako
      proces w tle i otwiera przeglądarkę na `localhost:8080`.
      UWAGA: opcjonalne funkcje (głos, dokumenty w RAG, kompresja) wymagają
      systemowych binarek, których `pip` NIE doinstaluje (Docker image je ma
      w środku, goły `pip install` - nie) — trzeba dociągnąć osobno z apt:
      `sudo apt install ffmpeg pandoc zstd`. Bez tego WebUI działa, ale bez
      transkrypcji audio i importu dokumentów (PDF/Word) do RAG.