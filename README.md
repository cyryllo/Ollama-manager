# Ollama Manager

Prosta aplikacja desktopowa (PyQt6) do zarządzania lokalną instancją [Ollamy](https://ollama.com)
pod KDE — bez terminala, bez Dockera. Wszystko działa lokalnie, w Twoim LAN.

## Wymagania

- Python 3 + **PyQt6**, **requests**
- **systemd** + **polkit** (`pkexec`) — standard na KDE/Debian
- Ollama (jeśli jej nie masz, aplikacja sama ją zainstaluje jednym przyciskiem)
- Opcjonalnie: **uv** (do instalacji Open WebUI — aplikacja doinstaluje je sama w razie potrzeby)
- Opcjonalnie: `ffmpeg`, `pandoc`, `zstd` (pełna funkcjonalność Open WebUI — głos, dokumenty w RAG)

## Instalacja i uruchomienie

**Jako aplikacja z menu (zalecane)** — instaluje zależności, kopiuje apkę do
`~/.local/share/ollama-manager` i dodaje wpis w menu (sekcja Narzędzia), bez roota:

```
./install.sh
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

**Modele lokalne**
- Lista zainstalowanych modeli + usuwanie
- Pobieranie nowych modeli z podpowiedziami popularnych (Llama, Gemma, Mistral, Phi, DeepSeek, Qwen) i paskiem postępu
- Podgląd modeli aktualnie załadowanych do pamięci (VRAM)

**Modele zdalne**
- Kreator dodawania modeli zdalnych (RemoteHost) — np. podpięcie serwera BC-250 pod jedną lokalną Ollamę
- Osobna lista modeli utworzonych w ten sposób

**Open WebUI**
- Instalacja panelu czatu w przeglądarce jednym przyciskiem (bez Dockera)
- Start / stop, autostart po zalogowaniu
- Otwiera się automatycznie w przeglądarce po uruchomieniu

**Pasek statystyk**
- Status Ollamy i Open WebUI
- Zużycie VRAM lokalnie i na hostach zdalnych
- Liczba zainstalowanych modeli

**Dziennik zdarzeń** — log wszystkich operacji, zawsze widoczny na dole okna.

## Uwagi

- Sterowanie usługą Ollama zawsze dotyczy lokalnej maszyny — nawet jeśli aplikacja
  wskazuje na zdalny serwer (np. BC-250), start/stop/autostart działają lokalnie.
- Adres serwera Ollamy i nazwa usługi to stałe na górze `ollama_manager.py`.