# Ollama Manager

Prosta aplikacja desktopowa (PyQt6) do zarządzania lokalną instancją [Ollamy](https://ollama.com)
pod KDE — bez terminala, bez Dockera. Wszystko działa lokalnie, w Twoim LAN.

## Wymagania

- Python 3 + **PyQt6**, **requests**
- **systemd** + **polkit** (`pkexec`) — standard na KDE/Debian
- Ollama (jeśli jej nie masz, aplikacja sama ją zainstaluje jednym przyciskiem)
- Opcjonalnie: **uv** (do instalacji Open WebUI i LiteLLM — aplikacja doinstaluje je sama w razie potrzeby)
- Opcjonalnie: `ffmpeg`, `pandoc`, `zstd` (pełna funkcjonalność Open WebUI — głos, dokumenty w RAG)

## Instalacja i uruchomienie

**Jako aplikacja z menu, bez roota (zalecane)** — instaluje zależności przez pip,
kopiuje apkę do `~/.local/share/ollama-manager` i dodaje wpis w menu (sekcja Narzędzia):

```
./install.sh
```

**Jako pakiet `.deb` (Debian/Ubuntu)** — zależności z `apt`, łatwe odinstalowanie:

```
./build-deb.sh
sudo apt install ./ollama-manager_*_all.deb
```

**Ręcznie, do dewelopowania** — bez kopiowania i wpisu w menu:

```
pip install PyQt6 requests
python3 ollama_manager.py
```

## Funkcje

**Usługa Ollama**
- Start / stop usługi systemd, wykrywanie stanu na bieżąco
- Autostart przy starcie systemu
- Wykrywanie braku instalacji + przycisk instalującej ją jednym kliknięciem

**Modele**
- Lista zainstalowanych modeli + usuwanie
- Pobieranie nowych modeli z podpowiedziami popularnych (Llama, Gemma, Mistral, Phi, DeepSeek, Qwen) i paskiem postępu
- Podgląd modeli aktualnie załadowanych do pamięci (VRAM)

**Open WebUI**
- Instalacja panelu czatu w przeglądarce jednym przyciskiem (bez Dockera)
- Start / stop, autostart po zalogowaniu
- Przycisk "Otwórz WebUI" otwiera panel w przeglądarce (bez automatycznego otwierania)

**Przełącznik serwera**
- Wybór hosta Ollama (localhost albo dowolny w LAN, np. BC-250) dla operacji na modelach
- Dodawanie/usuwanie serwerów z poziomu okna, zapamiętywane między uruchomieniami

**Agregator modeli (LiteLLM)**
- Instalacja, start/stop i autostart LiteLLM jednym przyciskiem (bez Dockera)
- Wystawia jeden endpoint (zgodny z API OpenAI) łączący modele ze WSZYSTKICH
  serwerów z listy przełącznika — VS Code/Continue wskazuje tylko na ten adres
- Podgląd, jakie modele i hosty trafią do configu, przed uruchomieniem

**Zaawansowane (zmienne środowiskowe Ollamy)**
- `OLLAMA_KEEP_ALIVE` — jak długo model zostaje w pamięci po ostatnim zapytaniu
- `OLLAMA_CONTEXT_LENGTH` — rozmiar okna kontekstu (domyślne 4096 za mało do pracy agentowej)
- `OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_NUM_PARALLEL`, `OLLAMA_FLASH_ATTENTION`, `OLLAMA_KV_CACHE_TYPE`
- `OLLAMA_VULKAN` — backend Vulkan zamiast ROCm (przydatne na kartach AMD bez pełnego wsparcia ROCm, np. BC-250)
- `OLLAMA_IGPU_ENABLE` — czy Ollama może korzystać ze zintegrowanego GPU (domyślnie włączone)
- Każda zmiana zapisuje override systemd i restartuje usługę — z poziomu okna, bez edycji plików ręcznie

**Pasek statystyk**
- Status Ollamy i Open WebUI
- Zużycie VRAM na aktualnie wybranym serwerze
- Liczba zainstalowanych modeli

**Dziennik zdarzeń** — log wszystkich operacji, zawsze widoczny na dole okna.

**Język interfejsu** — polski, angielski, niemiecki, hiszpański, francuski, portugalski i włoski, przełączany z poziomu okna, zapamiętywany między uruchomieniami.

## Uwagi

- Sterowanie usługą Ollama zawsze dotyczy lokalnej maszyny — nawet jeśli w oknie
  wybrany jest zdalny serwer (np. BC-250), start/stop/autostart działają lokalnie.
