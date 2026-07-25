#!/usr/bin/env python3
# =============================================================================
#  Ollama Manager - menedżer Ollamy pod KDE (PyQt6)
#
#  WHAT: okno do zarządzania Ollamą:
#        - start / stop usługi systemd (przez pkexec = graficzny polkit KDE),
#        - autostart przy starcie systemu (systemctl enable/disable),
#        - lista zainstalowanych modeli + pobieranie i usuwanie.
#  WHY:  szablon do rozwoju w VCS. Logika sieci (OllamaClient) i sterowanie
#        usługą (funkcje serwisu) są odseparowane od GUI, żeby okno zostało
#        czyste. Dwa poziomy komentarzy: WHAT (skan) + WHY (decyzje).
#
#  Zależności:  PyQt6, requests   ->  pip install PyQt6 requests
#  Wymaga też:  systemd + polkit (pkexec) - standard na KDE/Debian.
#  Uruchomienie: python3 ollama_manager.py
# =============================================================================

import sys
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import requests  # WHY: czytelniejsze od urllib przy strumieniowaniu /api/pull

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QUrl, QSettings
from PyQt6.QtGui import QIcon, QDesktopServices
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QPushButton, QListWidget, QProgressBar, QTabWidget,
    QGroupBox, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QPlainTextEdit, QComboBox, QMessageBox, QCheckBox,
    QDialog, QLineEdit, QScrollArea,
)

# --- Konfiguracja ---------------------------------------------------------
# WHAT: wersja aplikacji - widoczna w tytule okna.
# WHY:  ostatnia cyfra rośnie przy każdym commicie; pierwsze dwie zmieniają się
#       tylko na wyraźne polecenie (patrz CLAUDE.md, sekcja "Wersjonowanie").
WERSJA = "0.4.13"

# WHAT: bazowy adres serwera Ollamy (operacje na modelach).
# WHY:  wydzielony na górę - możesz wskazać BC-250
#       (np. http://192.168.0.236:11434) zamiast localhost.
#       UWAGA: sterowanie USŁUGĄ (start/stop/autostart) dotyczy zawsze
#       LOKALNEGO systemd - nie da się zdalnie startować BC-250 tą drogą.
OLLAMA_URL = "http://localhost:11434"

# WHAT: nazwa usługi systemd. WHY: wydzielona, gdyby u Ciebie nazywała się inaczej.
SERVICE_NAME = "ollama"

# WHAT: adres panelu Open WebUI (domyślny port 'open-webui serve').
# WHY:  wydzielony na górę tak jak reszta adresów - gdybyś kiedyś zmienił port.
WEBUI_URL = "http://localhost:8080"

# WHAT: adres agregatora LiteLLM (domyślny port 'litellm --config ...').
# WHY:  jw. - jedno miejsce do zmiany, gdyby port kolidował z czymś innym.
LITELLM_URL = "http://localhost:4000"

# WHAT: modele (SAME nazwy rodzin, bez rozmiaru) podpowiadane w rozwijanej
#       liście pobierania - rozmiar wybiera się osobno, w polu "Rozmiar"
#       obok, którego podpowiedzi zależą od wybranego modelu (patrz
#       ROZMIARY_WG_MODELU i _aktualizuj_liste_rozmiarow).
# WHY:  przekrój popularnych rodzin (Llama, Gemma, Mistral, Phi, DeepSeek, Qwen) -
#       użytkownik wybiera z listy, zamiast wpisywać nazwy z palca. Pole jest
#       edytowalne, więc dowolny inny model z ollama.com/library da się wpisać ręcznie.
POLECANE_MODELE = [
    "llama3.2",          # Meta - lekki, uniwersalny (domyślnie 3B)
    "llama3.1",          # Meta - solidny model ogólnego przeznaczenia
    "gemma3",            # Google - domyślnie 4B, mieści się na jednym GPU
    "gemma2",            # Google - poprzednia generacja
    "mistral",           # Mistral AI - klasyczny 7B
    "phi4",              # Microsoft - 14B, mocny w rozumowaniu
    "deepseek-r1",       # DeepSeek - model z "myśleniem" (reasoning)
    "qwen3",             # Qwen - ogólny model najnowszej generacji
    "qwen2.5-coder",     # Qwen - do kodu, wspiera tool-calling
    "nomic-embed-text",  # embeddingi (RAG, wyszukiwanie semantyczne)
    "gpt-oss",            # OpenAI - open-weight, reasoning/agentowy
    "deepseek-coder-v2",  # DeepSeek - MoE do kodu, tool-calling
    "ornith",              # agent kodujący, MIT, kontekst 256K
]

# WHAT: RZECZYWISTE rozmiary dostępne dla każdego modelu z POLECANE_MODELE -
#       zweryfikowane wprost na stronach tagów ollama.com/library/<model>/tags
#       (2026-07). Pole "Rozmiar" pokazuje TYLKO te podpowiedzi, gdy wybrany
#       model jest na tej liście (patrz _aktualizuj_liste_rozmiarow).
# WHY:  BUG naprawiony: poprzednia wersja miała jedną wspólną listę podpowiedzi
#       (1b-70b) dla WSZYSTKICH modeli naraz, więc appka sugerowała rozmiary,
#       których dany model w ogóle nie ma (np. Ornith: appka pokazywała 1b-70b,
#       a naprawdę istnieją tylko warianty 9b i 35b - wybranie czegokolwiek
#       innego kończyłoby się błędem pobierania). Rozmiary bez większego
#       znaczenia (Mistral, Phi-4 - jeden rozmiar bazowy) i tak są tu wpisane
#       jawnie, żeby podpowiedź była zgodna z rzeczywistością, a nie pusta.
# UWAGA: ta lista się starzeje, gdy Ollama doda nowe warianty - sprawdzać przy
#        okazji aktualizacji POLECANE_MODELE.
ROZMIARY_WG_MODELU = {
    "llama3.2": ["1b", "3b"],
    "llama3.1": ["8b", "70b", "405b"],
    "gemma3": ["270m", "1b", "4b", "12b", "27b"],
    "gemma2": ["2b", "9b", "27b"],
    "mistral": ["7b"],
    "phi4": ["14b"],
    "deepseek-r1": ["1.5b", "7b", "8b", "14b", "32b", "70b", "671b"],
    "qwen3": ["0.6b", "1.7b", "4b", "8b", "14b", "30b", "32b", "235b"],
    "qwen2.5-coder": ["0.5b", "1.5b", "3b", "7b", "14b", "32b"],
    "gpt-oss": ["20b", "120b"],
    "deepseek-coder-v2": ["16b", "236b"],
    "ornith": ["9b", "35b"],
}

# WHAT: podpowiedzi rozmiaru dla modelu spoza POLECANE_MODELE (wpisanego z ręki) -
#       appka nie zna jego rzeczywistych wariantów, więc dostaje ogólny,
#       orientacyjny przekrój zamiast konkretnej, zweryfikowanej listy.
ROZMIARY_MODELI_OGOLNE = [
    "1b", "1.5b", "3b", "4b", "7b", "8b", "9b", "12b", "14b",
    "20b", "27b", "30b", "32b", "34b", "70b",
]

# WHAT: profil startowy dla przycisku "Zastosuj zalecane wartości" w zakładce
#       Zaawansowane - wypełnia formularz, NIC nie zapisuje samo z siebie.
# WHY:  dobrane pod BC-250 (Vulkan+iGPU włączone, kontekst 16k, keep_alive 30m,
#       jeden model/zapytanie naraz, q8_0 dla mniejszego zużycia VRAM) - to
#       tylko punkt startu do przejrzenia i ewentualnej korekty przed "Zapisz",
#       appka nigdy nie wstrzykuje tych wartości automatycznie/po cichu.
OLLAMA_ZALECANE = {
    "OLLAMA_KEEP_ALIVE": "30m",
    "OLLAMA_CONTEXT_LENGTH": "16384",
    "OLLAMA_MAX_LOADED_MODELS": "1",
    "OLLAMA_NUM_PARALLEL": "1",
    "OLLAMA_FLASH_ATTENTION": "1",
    "OLLAMA_KV_CACHE_TYPE": "q8_0",
    "OLLAMA_VULKAN": "1",
    "OLLAMA_IGPU_ENABLE": "1",
    "OLLAMA_HOST": "",
    "GGML_VK_VISIBLE_DEVICES": "0",
}

# WHAT: WSZYSTKIE zmienne, które mają dedykowane pole w formularzu zakładki
#       Zaawansowane - używane do odróżnienia "znanych" wpisów override.conf
#       od "własnych parametrów" (patrz _odswiez_zakladke_zaawansowane).
# WHY:  to NIE to samo co OLLAMA_ZALECANE.keys() - OLLAMA_GPU_OVERHEAD ma pole
#       w formularzu, ale świadomie NIE ma zalecanej wartości (zależy od tego,
#       czy na danym hoście jest środowisko graficzne, appka tego nie zgaduje).
OLLAMA_ZNANE_ZMIENNE = frozenset(OLLAMA_ZALECANE.keys()) | {"OLLAMA_GPU_OVERHEAD"}


# =============================================================================
#  Tłumaczenia interfejsu (i18n)
# =============================================================================
# WHAT: prosty słownik klucz -> tłumaczenie, jeden plik JSON na język w lang/.
# WHY:  polski tekst w kodzie jest jednocześnie kluczem słownika - brak wpisu
#       w wybranym języku po prostu pokazuje polski oryginał (nigdy pusto/crash).
#       Bez zależności od pyyaml/Qt Linguist (pylupdate6/lrelease) - dodanie
#       kolejnego języka to nowy plik lang/<kod>.json, bez dotykania kodu.
JEZYKI = {
    "pl": "polski", "en": "English", "de": "Deutsch", "es": "español",
    "fr": "français", "pt": "português", "it": "italiano",
}
JEZYK_DOMYSLNY = "pl"
_KATALOG_LANG = Path(__file__).resolve().parent / "lang"
_tlumaczenia = {}


def _wczytaj_jezyk(kod):
    # WHAT: ładuje słownik tłumaczeń danego języka do pamięci (moduł _()).
    global _tlumaczenia
    if kod == JEZYK_DOMYSLNY:
        _tlumaczenia = {}  # WHY: polski to sam kod źródłowy - nie potrzeba pliku
        return
    try:
        _tlumaczenia = json.loads((_KATALOG_LANG / f"{kod}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _tlumaczenia = {}


def _(tekst):
    # WHAT: zwraca tłumaczenie danego (polskiego) tekstu w aktualnym języku.
    # WHY:  brak wpisu w słowniku -> pokazujemy oryginał, więc niekompletne
    #       tłumaczenie nigdy nie wywala okna ani nie pokazuje pustego pola.
    return _tlumaczenia.get(tekst, tekst)


def _wczytaj_jezyk_zapisany():
    return _ustawienia().value("jezyk", JEZYK_DOMYSLNY)


def _zapisz_jezyk(kod):
    _ustawienia().setValue("jezyk", kod)


# =============================================================================
#  Serwery Ollama - lista do przełącznika w oknie (localhost / hosty w LAN)
# =============================================================================
# WHAT: edytowalna lista adresów, między którymi można przełączać się z okna,
#       zamiast raz na zawsze edytować stałą OLLAMA_URL.
# WHY:  jedyny realny sposób na "wiele instancji Ollamy" to zwykłe wskazanie
#       klientowi innego adresu API (patrz CLAUDE.md, sekcja "Modele zdalne") -
#       dyrektywy Modelfile typu REMOTE_HOST nie istnieją. Przełącznik dotyczy
#       TYLKO operacji na modelach (lista/pobierz/usuń/VRAM) - sterowanie
#       usługą systemd zawsze zostaje lokalne, więc nie jest tu uwzględnione.
DOMYSLNY_SERWER = {"nazwa": "Lokalny", "adres": OLLAMA_URL}


def _ustawienia():
    # WHAT: jeden wspólny obiekt QSettings (plik INI w ~/.config).
    return QSettings("OllamaManager", "OllamaManager")


def _wczytaj_serwery():
    # WHAT: lista serwerów zapisana jako JSON w ustawieniach.
    # WHY:  QSettings nie zna list słowników wprost - zapisujemy jako tekst.
    surowe = _ustawienia().value("serwery/lista", "")
    if surowe:
        try:
            lista = json.loads(surowe)
            if lista:
                return lista
        except (json.JSONDecodeError, TypeError):
            pass
    return [dict(DOMYSLNY_SERWER)]


def _zapisz_serwery(lista):
    _ustawienia().setValue("serwery/lista", json.dumps(lista))


def _wczytaj_serwer_aktywny(serwery):
    # WHY: jeśli zapisany adres zniknął z listy (usunięty ręcznie), wracamy na pierwszy.
    adres = _ustawienia().value("serwery/aktywny", "")
    return adres if adres in [s["adres"] for s in serwery] else serwery[0]["adres"]


def _zapisz_serwer_aktywny(adres):
    _ustawienia().setValue("serwery/aktywny", adres)


# =============================================================================
#  Sterowanie usługą systemd
# =============================================================================
def _systemctl_query(arg):
    # WHAT: nieuprzywilejowane pytanie o stan usługi (is-active / is-enabled).
    # WHY:  zapytania o stan NIE wymagają roota; wynik jest na stdout nawet
    #       gdy kod wyjścia jest niezerowy (np. "inactive"), więc czytamy stdout.
    try:
        r = subprocess.run(
            ["systemctl", arg, SERVICE_NAME],
            capture_output=True, text=True, timeout=3,
        )
        return r.stdout.strip()
    except Exception:
        return "unknown"


def _pkexec(args, wejscie=None):
    # WHAT: uruchamia polecenie jako root przez polkit.
    # WHY:  pkexec pokazuje graficzny dialog KDE z prośbą o hasło - nie potrzeba
    #       terminala ani sudo. Rzucamy wyjątek przy błędzie, żeby worker go złapał.
    #       Opcjonalne 'wejscie' idzie na STDIN procesu (np. treść pliku do
    #       zapisania) - bezpieczniejsze niż wstrzykiwanie tekstu do argumentów
    #       powłoki, bo w ogóle nie dotyka parsera 'sh'.
    r = subprocess.run(["pkexec"] + args, input=wejscie, capture_output=True, text=True)
    if r.returncode != 0:
        # WHY: kod 126 = użytkownik anulował/brak uprawnień, 127 = błąd autoryzacji.
        raise RuntimeError(r.stderr.strip() or _("pkexec: kod wyjścia {kod}").format(kod=r.returncode))


def usluga_start():
    _pkexec(["systemctl", "start", SERVICE_NAME])


def usluga_stop():
    _pkexec(["systemctl", "stop", SERVICE_NAME])


def usluga_autostart(wlacz):
    # WHAT: włącza lub wyłącza automatyczny start po restarcie systemu.
    akcja = "enable" if wlacz else "disable"
    _pkexec(["systemctl", akcja, SERVICE_NAME])


def _usluga_override_sciezka():
    return Path("/etc/systemd/system") / f"{SERVICE_NAME}.service.d" / "override.conf"


def _usluga_env_wszystkie():
    # WHAT: czyta WSZYSTKIE zmienne środowiskowe zapisane w override.conf.
    # WHY:  sam ODCZYT pliku w /etc nie wymaga roota - tylko jego ZMIANA (patrz
    #       usluga_zapisz_env). BUG naprawiony: poprzednia wersja zakładała,
    #       że plik ZAWSZE ma dokładnie nasz własny format (jedna zmienna na
    #       linię, w cudzysłowach) - ale systemd.exec(5) dopuszcza też WIELE par
    #       KEY=VALUE oddzielonych spacją w jednej linii Environment=, a cudzysłów
    #       może być pojedynczy ('...') albo brak go wcale. Jeśli plik był kiedyś
    #       ręcznie edytowany (np. `sudo systemctl edit ollama` z jakiegoś
    #       poradnika o Vulkanie), stary parser po cichu gubił zmienne (wchłaniał
    #       kolejne pary w wartość poprzedniej) albo dostawał klucz z doklejonym
    #       apostrofem (`'OLLAMA_VULKAN`), który nigdy nie pasował do
    #       "OLLAMA_VULKAN" - checkbox pokazywał zły stan, a "Zastosuj" dopisywał
    #       nowy, poprawny wpis OBOK osieroconego śmiecia zamiast go nadpisać.
    #       shlex.split() parsuje to tak samo, jak zrobiłby to systemd.
    try:
        tresc = _usluga_override_sciezka().read_text()
    except OSError:
        return {}
    zmienne = {}
    for linia in tresc.splitlines():
        linia = linia.strip()
        if not linia.startswith("Environment="):
            continue
        try:
            czesci = shlex.split(linia.removeprefix("Environment="))
        except ValueError:
            continue  # WHY: niesparsowalna linia (np. niedomknięty cudzysłów) - pomijamy
        for czesc in czesci:
            if "=" in czesc:
                klucz, wartosc = czesc.split("=", 1)
                zmienne[klucz] = wartosc
    return zmienne


def usluga_zapisz_env(zmienne):
    # WHAT: nadpisuje CAŁY override.conf zadanym słownikiem zmiennych (jeden
    #       zapis, jedna zmiana pliku) i restartuje usługę.
    # WHY:  zakładka "Zaawansowane" ma jeden przycisk "Zapisz" dla wszystkich
    #       9 zmiennych + własnych parametrów naraz, zamiast osobnego zapisu i
    #       restartu na każde pole (jak w starszych wersjach) - wywołujący
    #       (zapisz_zaawansowane w MainWindow) sam składa kompletny słownik
    #       "co ma się finalnie znaleźć w pliku"; ta funkcja niczego nie scala
    #       z poprzednią zawartością - zrobiła to już strona wywołująca. Gotowa
    #       treść pliku leci na STDIN procesu roota, nie jako argument powłoki -
    #       żaden wpisany tekst nie ma szansy dotknąć parsera 'sh', bo w ogóle
    #       nie trafia do wiersza poleceń.
    tresc = "[Service]\n" + "".join(f'Environment="{k}={v}"\n' for k, v in zmienne.items())
    skrypt = (
        'mkdir -p "$(dirname "$1")" && cat > "$1" && '
        "systemctl daemon-reload && "
        f"systemctl restart {SERVICE_NAME}"
    )
    _pkexec(["sh", "-c", skrypt, "sh", str(_usluga_override_sciezka())], wejscie=tresc)


def ollama_zainstalowana():
    # WHAT: sprawdza, czy binarka 'ollama' w ogóle jest w systemie (PATH).
    # WHY:  usługa systemd może po prostu nie istnieć, gdy Ollama nie jest
    #       zainstalowana - to sprawdzenie działa niezależnie od stanu usługi
    #       i zawsze dotyczy LOKALNEJ maszyny (tak jak reszta sterowania usługą).
    return shutil.which("ollama") is not None


def ollama_zainstaluj():
    # WHAT: pobiera i uruchamia oficjalny skrypt instalacyjny Ollamy z ollama.com.
    # WHY:  robimy to przez pkexec (graficzny polkit), nie przez sudo w terminalu -
    #       skrypt instalacyjny sam wykrywa, że działa jako root, i pomija własne
    #       wywołanie sudo, więc nie pojawi się drugi, tekstowy prompt o hasło.
    # UWAGA: adres to oficjalna, udokumentowana metoda instalacji z ollama.com/download -
    #        warto od czasu do czasu sprawdzić, czy się nie zmieniła.
    _pkexec(["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"])


# =============================================================================
#  Open WebUI - panel czatu w przeglądarce (patrz CLAUDE.md, sekcja roadmapy)
# =============================================================================
def webui_binarka():
    # WHAT: szuka binarki 'open-webui' - w PATH albo pod domyślną ścieżką
    #       'pip install --user' (~/.local/bin).
    # WHY:  aplikacja odpalona ze skrótu na pulpicie może mieć okrojony PATH
    #       bez ~/.local/bin, mimo że pakiet jest już zainstalowany.
    znaleziona = shutil.which("open-webui")
    if znaleziona:
        return znaleziona
    kandydat = Path.home() / ".local" / "bin" / "open-webui"
    return str(kandydat) if kandydat.exists() else None


def webui_zainstalowane():
    return webui_binarka() is not None


def _uv_binarka():
    # WHAT: jak webui_binarka(), tylko dla narzędzia 'uv'.
    znaleziona = shutil.which("uv")
    if znaleziona:
        return znaleziona
    kandydat = Path.home() / ".local" / "bin" / "uv"
    return str(kandydat) if kandydat.exists() else None


def _uv_zapewnij():
    # WHAT: zwraca ścieżkę do 'uv', instalując je w razie potrzeby przez oficjalny
    #       standalone installer Astral (curl | sh), NIE przez 'pip install --user'.
    # WHY:  Debian trixie (i każdy Python 3.11+ zgodny z PEP 668) oznacza systemowego
    #       python3 jako "externally-managed" - 'pip install --user uv' na sys.executable
    #       wywala się błędem "externally-managed-environment", zanim w ogóle dojdzie
    #       do instalacji uv/LiteLLM/WebUI. Installer Astral pobiera gotową binarkę
    #       uv wprost do ~/.local/bin i w ogóle nie dotyka pip ani systemowego
    #       Pythona, więc PEP 668 nie ma tu zastosowania - ten sam wzorzec (curl | sh),
    #       co już używany do instalacji samej Ollamy (patrz ollama_zainstaluj()).
    #       Wspólny helper zamiast duplikowania bootstrapu w webui_zainstaluj()
    #       i litellm_zainstaluj().
    uv = _uv_binarka()
    if uv:
        return uv

    wynik = subprocess.run(
        ["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"],
        capture_output=True, text=True, timeout=None,
    )
    if wynik.returncode != 0:
        raise RuntimeError(wynik.stderr.strip() or _("instalacja uv: nieznany błąd"))

    # WHY: installer dopisuje ~/.local/bin do PATH w plikach powłoki (.bashrc itp.),
    #      ale ten proces (odpalony ze skrótu .desktop) już wystartował ze starym
    #      PATH i tego nie przeczyta - stąd _uv_binarka() sprawdza katalog wprost,
    #      niezależnie od PATH.
    uv = _uv_binarka()
    if not uv:
        raise RuntimeError(_("Zainstalowano 'uv', ale nie widać go w ~/.local/bin."))
    return uv


def webui_zainstaluj():
    # WHAT: instaluje Open WebUI przez 'uv tool install', z Pythonem przypiętym na 3.11.
    # WHY:  Open WebUI (stan na 2026) NIE wspiera jeszcze Pythona 3.13, a to on jest
    #       domyślnym 'python3' na świeżym Debianie/KDE - zwykłe 'pip install' na
    #       systemowym interpreterze kończy się błędem "no matching distribution".
    #       'uv' sam dociąga i zarządza kompatybilnym Pythonem 3.11 (bez apt/roota/
    #       Dockera) i instaluje open-webui w odizolowanym środowisku, wystawiając
    #       binarkę w ~/.local/bin - dokładnie tam, gdzie szuka jej webui_binarka().
    uv = _uv_zapewnij()

    wynik = subprocess.run(
        [uv, "tool", "install", "--python", "3.11", "open-webui"],
        capture_output=True, text=True, timeout=None,  # WHY: pobranie Pythona 3.11 + zależności - może to potrwać
    )
    if wynik.returncode != 0:
        raise RuntimeError(wynik.stderr.strip() or _("uv tool install: nieznany błąd"))


def _webui_dziala():
    try:
        r = requests.get(WEBUI_URL, timeout=1)
        return r.status_code < 500
    except requests.RequestException:
        return False


def webui_uruchom():
    # WHAT: jeśli WebUI już odpowiada - nic nie rób. Jeśli nie - zapisz/odśwież
    #       jego usługę systemd --user i ją wystartuj, potem poczekaj, aż odpowie.
    # WHY:  ta sama usługa --user co przy autostarcie (_zapisz_webui_unit) - jedna
    #       spójna droga uruchamiania, dzięki czemu "Zatrzymaj" (webui_zatrzymaj)
    #       zawsze działa, niezależnie od tego, czy WebUI wystartowało z tego
    #       przycisku, czy z włączonego wcześniej autostartu.
    if _webui_dziala():
        return
    _zapisz_webui_unit()
    _systemctl_user(["start", "open-webui.service"])

    # WHY: 3 minuty zamiast 30 s - pierwsze uruchomienie robi migracje bazy
    #      i potrafi ściągnąć domyślny model embeddingowy do RAG.
    for _ in range(180):
        if _webui_dziala():
            return
        time.sleep(1)

    raise RuntimeError(
        _("WebUI nie odpowiedziało w ciągu 3 minut. Log usługi: journalctl --user -u open-webui -e")
    )


def webui_zatrzymaj():
    # WHAT: zatrzymuje usługę systemd --user Open WebUI. Jeśli systemd nie zna
    #       takiej usługi ("not loaded" - WebUI działa jako goły proces, np.
    #       uruchomiony ręcznie albo starszą wersją tej aplikacji sprzed
    #       przejścia na systemd), dobija proces bezpośrednio po nazwie polecenia.
    # WHY:  bez tego fallbacku przycisk "Zatrzymaj" nic by nie robił w takim przypadku.
    try:
        _systemctl_user(["stop", "open-webui.service"])
        return
    except RuntimeError as e:
        if "not loaded" not in str(e) and "not found" not in str(e):
            raise

    binarka = webui_binarka()
    wzorzec = f"{binarka} serve" if binarka else "open-webui serve"
    wynik = subprocess.run(["pkill", "-f", wzorzec], capture_output=True, text=True, timeout=5)
    if wynik.returncode not in (0, 1):  # WHY: 1 = pkill nie znalazł procesu - i tak już zatrzymane
        raise RuntimeError(wynik.stderr.strip() or _("pkill: nie udało się zatrzymać procesu WebUI"))


def _webui_service_sciezka():
    return Path.home() / ".config" / "systemd" / "user" / "open-webui.service"


def _systemctl_user(args):
    # WHAT: jak _pkexec, tylko dla 'systemctl --user' - podnosi wyjątek z treścią
    #       stderr przy błędzie.
    # WHY:  usługi --user działają w sesji użytkownika, więc NIE wymagają roota
    #       ani pkexec - w przeciwieństwie do systemowej usługi Ollamy.
    r = subprocess.run(
        ["systemctl", "--user"] + args, capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        raise RuntimeError(
            r.stderr.strip()
            or _("systemctl --user {polecenie}: kod wyjścia {kod}").format(polecenie=" ".join(args), kod=r.returncode)
        )


def webui_autostart_wlaczony():
    # WHAT: sprawdza, czy usługa --user open-webui jest włączona (autostart po zalogowaniu).
    # WHY:  nieuprzywilejowane zapytanie, jak _systemctl_query, tylko w trybie --user.
    r = subprocess.run(
        ["systemctl", "--user", "is-enabled", "open-webui.service"],
        capture_output=True, text=True, timeout=3,
    )
    return r.stdout.strip() == "enabled"


def _zapisz_webui_unit():
    # WHAT: (re)zapisuje plik usługi systemd --user dla Open WebUI i przeładowuje systemd.
    # WHY:  wspólne dla ręcznego uruchamiania i włączania autostartu - jedna prawda
    #       o tym, jak wygląda usługa (ExecStart, zmienne środowiskowe), zamiast
    #       dwóch osobnych dróg startowania, które trzeba by osobno zatrzymywać.
    binarka = webui_binarka()
    if not binarka:
        raise RuntimeError(_("Open WebUI nie jest zainstalowane."))
    tresc = (
        "[Unit]\n"
        "Description=Open WebUI\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        f"ExecStart={binarka} serve\n"
        f"Environment=OLLAMA_BASE_URL={OLLAMA_URL}\n"
        "Restart=on-failure\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    sciezka = _webui_service_sciezka()
    sciezka.parent.mkdir(parents=True, exist_ok=True)
    sciezka.write_text(tresc)
    _systemctl_user(["daemon-reload"])


def webui_autostart(wlacz):
    # WHAT: włącza/wyłącza autostart Open WebUI po zalogowaniu przez usługę systemd --user.
    # WHY:  to usługa UŻYTKOWNIKA (nie systemowa jak Ollama) - open-webui i tak żyje
    #       w katalogu domowym (~/.local/bin, dane w ~/.local/share), więc nie ma
    #       powodu prosić o roota. 'enable --now'/'disable --now' od razu też
    #       startuje/zatrzymuje serwer, więc nie trzeba osobno klikać "Uruchom".
    if wlacz:
        _zapisz_webui_unit()
        _systemctl_user(["enable", "--now", "open-webui.service"])
    else:
        _systemctl_user(["disable", "--now", "open-webui.service"])


# =============================================================================
#  LiteLLM - agregator wielu serwerów Ollamy pod jednym API (patrz CLAUDE.md,
#  sekcja roadmapy "Proxy dla modeli zdalnych przez LiteLLM")
# =============================================================================
def litellm_binarka():
    # WHAT: jak webui_binarka() - szuka binarki 'litellm' w PATH albo ~/.local/bin.
    znaleziona = shutil.which("litellm")
    if znaleziona:
        return znaleziona
    kandydat = Path.home() / ".local" / "bin" / "litellm"
    return str(kandydat) if kandydat.exists() else None


def litellm_zainstalowane():
    return litellm_binarka() is not None


def litellm_zainstaluj():
    # WHAT: instaluje LiteLLM (z obsługą proxy) przez 'uv tool install'.
    # WHY:  ten sam wzorzec co Open WebUI (bez Dockera/roota), ale prościej -
    #       LiteLLM (stan na 2026) wspiera Pythona 3.10-3.13, więc w
    #       przeciwieństwie do Open WebUI nie trzeba pinować konkretnej wersji.
    uv = _uv_zapewnij()

    wynik = subprocess.run(
        [uv, "tool", "install", "litellm[proxy]"],
        capture_output=True, text=True, timeout=None,
    )
    if wynik.returncode != 0:
        raise RuntimeError(wynik.stderr.strip() or _("uv tool install: nieznany błąd"))


def _litellm_dziala():
    try:
        r = requests.get(f"{LITELLM_URL}/health/liveliness", timeout=1)
        return r.status_code < 500
    except requests.RequestException:
        return False


def _litellm_config_sciezka():
    return Path.home() / ".config" / "ollama-manager" / "litellm_config.yaml"


def _wykryj_modele_na_serwerach(serwery):
    # WHAT: dla każdego serwera z listy (TA SAMA lista co przełącznik na pasku)
    #       pyta /api/tags o zainstalowane modele.
    # WHY:  jedno źródło prawdy o hostach - żeby dodać model do agregatora,
    #       wystarczy mieć host na liście serwerów, bez osobnej listy do
    #       ręcznego utrzymywania w dwóch miejscach naraz.
    wpisy = []
    for s in serwery:
        adres = s["adres"].rstrip("/")
        try:
            r = requests.get(f"{adres}/api/tags", timeout=3)
            r.raise_for_status()
            modele = [m["name"] for m in r.json().get("models", [])]
        except requests.RequestException:
            continue  # WHY: host akurat nieosiągalny - pomijamy, nie wywalamy całości
        for model in modele:
            wpisy.append((s["nazwa"], model, adres))
    return wpisy


def _zbuduj_config_litellm(serwery):
    # WHAT: generuje treść config.yaml dla LiteLLM - jeden wpis w model_list
    #       na każdy model na każdym hoście.
    # WHY:  format jest prosty (lista słowników o 3 polach tekstowych), więc
    #       piszemy YAML ręcznie zamiast dociągać zależność 'pyyaml'.
    def _yaml_str(tekst):
        return '"' + tekst.replace("\\", "\\\\").replace('"', '\\"') + '"'

    wpisy = _wykryj_modele_na_serwerach(serwery)
    if not wpisy:
        return "model_list: []\n"

    linie = ["model_list:"]
    for _nazwa_hosta, model, adres in wpisy:
        linie.append(f"  - model_name: {_yaml_str(model)}")
        linie.append("    litellm_params:")
        linie.append(f"      model: {_yaml_str('ollama_chat/' + model)}")
        linie.append(f"      api_base: {_yaml_str(adres)}")
    return "\n".join(linie) + "\n"


def litellm_zapisz_config(serwery):
    sciezka = _litellm_config_sciezka()
    sciezka.parent.mkdir(parents=True, exist_ok=True)
    sciezka.write_text(_zbuduj_config_litellm(serwery))


def _zbuduj_config_continue(modele):
    # WHAT: generuje treść config.yaml dla Continue.dev (VS Code) - jeden wpis
    #       w 'models' na każdy UNIKALNY model wykryty w agregatorze LiteLLM.
    # WHY:  Continue rozmawia tylko z LiteLLM (jeden endpoint zgodny z OpenAI),
    #       więc nie musi wiedzieć, na jakim hoście faktycznie liczy się model -
    #       liczy się tylko jego nazwa, identyczna z tą, którą LiteLLM wystawia
    #       pod /v1/models (stąd 'modele' to lista nazw, nie krotki host+model
    #       jak w _zbuduj_config_litellm). ŻADNYCH kotwic YAML (&anchor /
    #       <<: *anchor), każdy model rozpisany osobno - Continue parsuje YAML
    #       1.2, gdzie merge keys bez jawnego nagłówka "%YAML 1.1" po cichu się
    #       nie stosują i model znika z selektora klienta (potwierdzone w
    #       praktyce - patrz pamięć/notatki z testów BC-250 + Continue).
    if not modele:
        return "models: []\n"

    def _yaml_str(tekst):
        return '"' + tekst.replace("\\", "\\\\").replace('"', '\\"') + '"'

    linie = ["name: Local LiteLLM", "version: 1.0.0", "schema: v1", "", "models:"]
    for model in modele:
        linie.append(f"  - name: {_yaml_str(model)}")
        linie.append("    provider: openai")
        linie.append(f"    model: {_yaml_str(model)}")
        linie.append(f"    apiBase: {LITELLM_URL}/v1")
        linie.append("    apiKey: sk-anything")
        linie.append("    roles:")
        linie.append("      - chat")
        linie.append("      - edit")
        linie.append("      - apply")
    return "\n".join(linie) + "\n"
    return sciezka


def _litellm_service_sciezka():
    return Path.home() / ".config" / "systemd" / "user" / "litellm.service"


def _zapisz_litellm_unit():
    # WHAT: (re)zapisuje plik usługi systemd --user dla LiteLLM i przeładowuje systemd.
    # WHY:  ten sam wzorzec co Open WebUI - jedna droga uruchamiania, żeby
    #       "Zatrzymaj" działało niezależnie od tego, skąd LiteLLM wystartował.
    binarka = litellm_binarka()
    if not binarka:
        raise RuntimeError(_("LiteLLM nie jest zainstalowane."))
    sciezka_config = _litellm_config_sciezka()
    tresc = (
        "[Unit]\n"
        "Description=LiteLLM - agregator modeli Ollama\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        f"ExecStart={binarka} --config {sciezka_config}\n"
        "Restart=on-failure\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    sciezka = _litellm_service_sciezka()
    sciezka.parent.mkdir(parents=True, exist_ok=True)
    sciezka.write_text(tresc)
    _systemctl_user(["daemon-reload"])


def litellm_uruchom(serwery):
    # WHAT: generuje świeży config z aktualnej listy serwerów, (re)startuje
    #       usługę systemd --user i czeka, aż API zacznie odpowiadać.
    # WHY:  config generujemy na nowo przy KAŻDYM starcie - lista serwerów
    #       (i modeli na nich) mogła się zmienić od ostatniego uruchomienia.
    litellm_zapisz_config(serwery)
    _zapisz_litellm_unit()
    _systemctl_user(["restart", "litellm.service"])

    for _ in range(60):
        if _litellm_dziala():
            return
        time.sleep(1)

    raise RuntimeError(
        _("LiteLLM nie odpowiedziało w ciągu 60 s. Log usługi: journalctl --user -u litellm -e")
    )


def litellm_zatrzymaj():
    _systemctl_user(["stop", "litellm.service"])


def litellm_autostart_wlaczony():
    r = subprocess.run(
        ["systemctl", "--user", "is-enabled", "litellm.service"],
        capture_output=True, text=True, timeout=3,
    )
    return r.stdout.strip() == "enabled"


def litellm_autostart(wlacz, serwery):
    # WHY: 'enable --now' potrzebuje świeżego configu i unitu - tak samo jak
    #      ręczne uruchomienie, żeby autostart nie odpalił się na nieaktualnej liście.
    if wlacz:
        litellm_zapisz_config(serwery)
        _zapisz_litellm_unit()
        _systemctl_user(["enable", "--now", "litellm.service"])
    else:
        _systemctl_user(["disable", "--now", "litellm.service"])


def _zbuduj_tag_modelu(model, rozmiar, kwantyzacja):
    # WHAT: dokleja rozmiar i/albo wariant kwantyzacji do tagu modelu przed
    #       pobraniem, oddzielone myślnikiem (konwencja Ollamy - np. "8b-q8_0").
    # WHY:  jeśli nazwa modelu ma już tag (po dwukropku, np. wybrany wprost z
    #       listy podpowiedzi "llama3.1:8b"), doklejamy do NIEGO; jeśli nazwa
    #       to sam człon rodziny bez tagu (czyli wskazywałaby "latest"),
    #       rozmiar/kwantyzacja stają się całym tagiem. Nie każdy model
    #       publikuje każdy rozmiar/wariant - to tylko doklejenie tekstu,
    #       appka NIE sprawdza z góry, czy taki tag istnieje w rejestrze
    #       Ollamy (błąd wyjdzie dopiero przy pobieraniu, patrz PullWorker).
    model = model.strip()
    if ":" in model:
        repo, czesci_tagu = model.split(":", 1)
        czesci_tagu = [czesci_tagu]
    else:
        repo, czesci_tagu = model, []
    if rozmiar:
        czesci_tagu.append(rozmiar.strip())
    if kwantyzacja:
        czesci_tagu.append(kwantyzacja)
    if not czesci_tagu:
        return repo
    return f"{repo}:" + "-".join(czesci_tagu)


def _parsuj_rozmiar_parametrow(tekst):
    # WHAT: wyciąga liczbę parametrów (w miliardach) z tekstu typu "8b", "1.5B",
    #       "70b-instruct" albo "llama3.1:8b" (bierze pierwsze dopasowanie "<liczba>b").
    # WHY:  potrzebne do orientacyjnego szacowania zużycia pamięci - działa
    #       zarówno na polu "Rozmiar", jak i na samej nazwie modelu (fallback,
    #       gdy ktoś wpisał rozmiar wprost do nazwy zamiast do osobnego pola).
    dopasowanie = re.search(r"(\d+(?:\.\d+)?)\s*[bB](?![a-zA-Z])", tekst)
    if not dopasowanie:
        return None
    return float(dopasowanie.group(1))


# WHAT: mnożnik GB/mld parametrów (bity na wagę, przeliczone) wg wariantu
#       kwantyzacji - kalkulator pamięci dla modeli LLM (Ollama/llama.cpp).
# WHY:  mapowanie na trzy opcje z combo_kwantyzacja: pusta ("domyślna") to
#       najbliższy odpowiednik tego, co Ollama zwykle ściąga (Q4_K_M), Q8_0
#       i FP16 to bezpośrednie odpowiedniki wybranych wariantów.
_KWANTYZACJA_BPW = {
    "": ("Q4_K_M", 0.61),
    "q8_0": ("Q8_0", 1.06),
    "fp16": ("FP16", 2.00),
}

# WHAT: bajty na element pamięci podręcznej kontekstu (KV cache) wg jej kwantyzacji.
_KVQ_BAJTY_NA_ELEMENT = {
    "f16": 2,
    "q8_0": 1,
    "q4_0": 0.5,
}

# WHAT: przybliżenia architektury (liczba warstw L, liczba głowic KV H_KV) wg
#       wielkości modelu P (mld parametrów) - progi górne przedziałów.
# WHY:  appka nie zna dokładnej architektury modelu, dopóki go nie pobierze -
#       to tabela przybliżeń zamiast prawdziwych metadanych.
_PRZYBLIZENIE_ARCHITEKTURY = [
    (4, 36, 8),
    (9, 32, 8),
    (16, 48, 8),
    (40, 64, 8),
]
_PRZYBLIZENIE_ARCHITEKTURY_MAX = (80, 8)  # P > 40

_TYPOWE_WIELKOSCI_RAM = (8, 16, 32, 48, 64, 96, 128)

# WHAT: kontekst i kwantyzacja cache przyjęte jako STAŁE w kalkulatorze - appka
#       nie daje ich wybrać przy pobieraniu (kontekst ustawia się osobno w
#       zakładce Zaawansowane; 16384 to tamtejsza zalecana wartość - patrz
#       OLLAMA_ZALECANE). q8_0 (nie f16) - bo profil OLLAMA_ZALECANE i tak
#       włącza OLLAMA_FLASH_ATTENTION=1, a bez niego Ollama liczy cache jak
#       f16 mimo wybranej kwantyzacji - stąd stałe ostrzeżenie o tym wymogu
#       w _aktualizuj_szacowana_pamiec, dopóki KVQ != "f16".
_KALKULATOR_CTX = 16384
_KALKULATOR_KVQ = "q8_0"
_KALKULATOR_D = 128  # wymiar głowicy - domyślny


# WHAT: mapowanie PRAWDZIWEJ wartości details.quantization_level z /api/tags
#       (np. "Q4_K_M", "F16") na ten sam mnożnik BPW co _KWANTYZACJA_BPW.
# WHY:  lista zainstalowanych modeli liczy VRAM na faktycznych metadanych
#       Ollamy, nie na zgadywaniu - ale Ollama zna więcej wariantów, niż appka
#       daje wybrać przy pobieraniu, stąd osobna, szersza tabela. Nieznany/
#       nierozpoznany wariant dostaje wartość domyślną (jak Q4_K_M).
_QUANTIZATION_LEVEL_BPW = {
    "FP16": 2.00,
    "F16": 2.00,
    "Q8_0": 1.06,
    "Q6_K": 0.82,
    "Q5_K_M": 0.71,
    "Q4_K_M": 0.61,
    "Q3_K_M": 0.49,
    "Q2_K": 0.42,
}


def _parsuj_parameter_size(tekst):
    # WHAT: parsuje details.parameter_size z /api/tags (np. "8.0B", "134.52M")
    #       na liczbę miliardów parametrów.
    if not tekst:
        return None
    dopasowanie = re.search(r"(\d+(?:\.\d+)?)\s*([BM])\b", tekst, re.IGNORECASE)
    if not dopasowanie:
        return None
    liczba = float(dopasowanie.group(1))
    return liczba / 1000 if dopasowanie.group(2).upper() == "M" else liczba


def _oszacuj_pamiec_bpw(miliardy_parametrow, bpw, etykieta_q):
    # WHAT: kalkulator pamięci dla modelu LLM - wagi + KV cache + bufory,
    #       z zapasem na system - zwraca słownik ze wszystkimi krokami
    #       pośrednimi. Współdzielony rdzeń dla dwóch miejsc w oknie: karty
    #       "Pobierz nowy model" (patrz _oszacuj_pamiec, kwantyzacja z combo)
    #       i listy "Zainstalowane modele" (kwantyzacja z prawdziwych metadanych
    #       API - patrz _QUANTIZATION_LEVEL_BPW).
    # WHY:  poprzednia wersja liczyła tylko "wagi × 1.3" - to dawało zbyt
    #       optymistyczny wynik przy dużym kontekście (KV cache potrafi
    #       przewyższyć same wagi przy mniejszych modelach i długim kontekście).
    #       KV cache liczony jest osobno, na podstawie przybliżonej architektury
    #       (L, H_KV) dobranej wg wielkości P.
    wagi_gb = miliardy_parametrow * bpw

    l, h_kv = _PRZYBLIZENIE_ARCHITEKTURY_MAX
    for prog, prog_l, prog_h_kv in _PRZYBLIZENIE_ARCHITEKTURY:
        if miliardy_parametrow <= prog:
            l, h_kv = prog_l, prog_h_kv
            break

    bajty_na_element = _KVQ_BAJTY_NA_ELEMENT[_KALKULATOR_KVQ]
    bajty_na_token = 2 * l * h_kv * _KALKULATOR_D * bajty_na_element
    kv_cache_gb = (bajty_na_token * _KALKULATOR_CTX) / (1024 ** 3)

    bufory_gb = 0.3 + (miliardy_parametrow * 0.04)

    zajete_realnie_gb = wagi_gb + kv_cache_gb + bufory_gb
    z_zapasem_gb = zajete_realnie_gb / 0.78  # model+cache = maks. 78% pamięci, reszta dla systemu

    rekomendowany_ram = next(
        (r for r in _TYPOWE_WIELKOSCI_RAM if r >= z_zapasem_gb),
        _TYPOWE_WIELKOSCI_RAM[-1],
    )

    return {
        "etykieta_q": etykieta_q,
        "wagi_gb": wagi_gb,
        "kv_cache_gb": kv_cache_gb,
        "bufory_gb": bufory_gb,
        "zajete_realnie_gb": zajete_realnie_gb,
        "z_zapasem_gb": z_zapasem_gb,
        "rekomendowany_ram": rekomendowany_ram,
    }


def _oszacuj_pamiec(miliardy_parametrow, kwantyzacja):
    # WHAT: wariant _oszacuj_pamiec_bpw dla karty "Pobierz nowy model" - bierze
    #       kwantyzację z combo_kwantyzacja ("", "q8_0", "fp16").
    etykieta_q, bpw = _KWANTYZACJA_BPW.get(kwantyzacja, _KWANTYZACJA_BPW[""])
    return _oszacuj_pamiec_bpw(miliardy_parametrow, bpw, etykieta_q)


# =============================================================================
#  Warstwa sieci - odseparowana od GUI
# =============================================================================
class OllamaClient:
    """Cienka nakładka na REST API Ollamy.

    WHAT: lista modeli, strumieniowe pobieranie, usuwanie modelu.
    WHY:  trzymamy sieć poza oknem - łatwiej rozwijać i testować osobno.
    """

    def __init__(self, base_url=OLLAMA_URL):
        # WHY: ucinamy końcowy '/', inaczej powstałby podwójny slash w ścieżkach.
        self.base_url = base_url.rstrip("/")

    def api_dziala(self):
        # WHAT: szybki ping API. WHY: /api/tags odpowiada 200 tylko gdy serwer żyje.
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=2)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def list_models(self):
        # WHAT: lista zainstalowanych modeli - nazwa, rozmiar na dysku (bajty)
        #       i metadane kwantyzacji (details.parameter_size/quantization_level).
        # WHY:  rozmiar do pokazania, ile miejsca zajmuje model na dysku;
        #       parameter_size/quantization_level - do wyliczenia orientacyjnego
        #       VRAM tym samym kalkulatorem co karta "Pobierz nowy model"
        #       (patrz _oszacuj_pamiec_bpw), na PRAWDZIWYCH metadanych modelu,
        #       nie na zgadywaniu rozmiaru z samej nazwy.
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            r.raise_for_status()
            wynik = []
            for m in r.json().get("models", []):
                szczegoly = m.get("details") or {}
                wynik.append({
                    "name": m["name"],
                    "size": m.get("size", 0),
                    "parameter_size": szczegoly.get("parameter_size", ""),
                    "quantization_level": szczegoly.get("quantization_level", ""),
                })
            return wynik
        except requests.RequestException:
            return []

    def list_loaded(self):
        # WHAT: modele aktualnie załadowane do pamięci (RAM/VRAM), z /api/ps.
        # WHY:  pozwala zobaczyć, co faktycznie siedzi teraz na karcie graficznej,
        #       zamiast zgadywać po zużyciu VRAM w innym narzędziu.
        try:
            r = requests.get(f"{self.base_url}/api/ps", timeout=5)
            r.raise_for_status()
            return r.json().get("models", [])
        except requests.RequestException:
            return []

    def delete_model(self, name):
        # WHAT: usuwa model z dysku przez API.
        # WHY:  używamy API (a nie 'ollama rm'), bo działa też gdy OLLAMA_URL
        #       wskazuje zdalny host (BC-250). Rzuca wyjątek przy błędzie.
        r = requests.delete(
            f"{self.base_url}/api/delete",
            json={"model": name},
            timeout=30,
        )
        r.raise_for_status()

    def pull_stream(self, model):
        # WHAT: generator - oddaje kolejne komunikaty postępu pobierania.
        # WHY:  /api/pull zwraca linie JSON z polami status/total/completed;
        #       strumień pozwala pokazać pasek zamiast zamrożonego okna.
        with requests.post(
            f"{self.base_url}/api/pull",
            json={"model": model, "stream": True},
            stream=True,
            timeout=None,  # WHY: pobranie kilku GB trwa - brak limitu czasu
        ) as r:
            r.raise_for_status()
            for linia in r.iter_lines():
                if linia:
                    yield json.loads(linia)


# =============================================================================
#  Wątki robocze - żeby nie blokować GUI
# =============================================================================
class RefreshWorker(QThread):
    """Pobiera w tle stan usługi i listę modeli.

    WHY: zapytania sieciowe i wywołania systemctl nie mogą iść w wątku GUI.
    """
    wynik = pyqtSignal(dict)  # patrz klucze w self.wynik.emit({...}) niżej

    def __init__(self, client):
        super().__init__()
        self.client = client

    def run(self):
        zainstalowana = ollama_zainstalowana()
        active = _systemctl_query("is-active") == "active"
        enabled = _systemctl_query("is-enabled") == "enabled"
        env_ollama = _usluga_env_wszystkie()  # WHY: jedno czytanie override.conf - zakładka "Zaawansowane"
        api = self.client.api_dziala()
        modele = self.client.list_models() if api else []
        zaladowane = self.client.list_loaded() if api else []
        webui = webui_zainstalowane()
        webui_autostart = webui_autostart_wlaczony()
        # WHY: samo "zainstalowane" nie mówi, czy serwer akurat teraz odpowiada -
        #      pasek statystyk ma pokazywać żywy stan, nie tylko obecność binarki.
        webui_dziala = _webui_dziala()

        litellm = litellm_zainstalowane()
        litellm_autostart = litellm_autostart_wlaczony()
        litellm_dziala = _litellm_dziala()

        self.wynik.emit({
            "zainstalowana": zainstalowana,
            "active": active,
            "enabled": enabled,
            "env_ollama": env_ollama,
            "api": api,
            "models": modele,
            "zaladowane": zaladowane,
            "webui": webui,
            "webui_autostart": webui_autostart,
            "webui_dziala": webui_dziala,
            "litellm": litellm,
            "litellm_autostart": litellm_autostart,
            "litellm_dziala": litellm_dziala,
        })


class PullWorker(QThread):
    """Pobiera model w tle wraz z postępem."""
    postep = pyqtSignal(int, str)       # (procent lub -1, opis_statusu)
    zakonczono = pyqtSignal(bool, str)  # (sukces, komunikat)

    def __init__(self, client, model):
        super().__init__()
        self.client = client
        self.model = model

    def run(self):
        try:
            for dane in self.client.pull_stream(self.model):
                status = dane.get("status", "")
                total = dane.get("total")
                completed = dane.get("completed")
                # WHAT: procent liczymy tylko gdy znamy rozmiar całości.
                # WHY:  komunikaty typu "pulling manifest" nie mają total -> -1.
                if total and completed:
                    self.postep.emit(int(completed / total * 100), status)
                else:
                    self.postep.emit(-1, status)
            self.zakonczono.emit(True, _("Pobrano model: {model}").format(model=self.model))
        except Exception as e:
            self.zakonczono.emit(False, _("Błąd pobierania: {blad}").format(blad=e))


class ActionWorker(QThread):
    """Uruchamia dowolną krótką akcję (funkcję) w tle.

    WHY: sterowanie usługą (dialog pkexec) i usuwanie modelu (sieć) są krótkie,
         ale blokujące - nie mogą iść w wątku GUI. Jeden worker na obie rzeczy.
    """
    zakonczono = pyqtSignal(bool, str)

    def __init__(self, funkcja, opis):
        super().__init__()
        self.funkcja = funkcja
        self.opis = opis

    def run(self):
        try:
            self.funkcja()
            self.zakonczono.emit(True, f"{self.opis}: OK")
        except Exception as e:
            self.zakonczono.emit(False, _("{opis}: błąd - {blad}").format(opis=self.opis, blad=e))


class AgregatorWorker(QThread):
    """Odpytuje na żywo hosty z listy serwerów o zainstalowane modele.

    WHY: podgląd "co trafi do configu LiteLLM" wymaga /api/tags na KAŻDYM
         hoście z listy - może potrwać, więc w tle jak każda inna sieć.
    """
    wynik = pyqtSignal(list)  # lista krotek (nazwa_hosta, model, adres)

    def __init__(self, serwery):
        super().__init__()
        self.serwery = serwery

    def run(self):
        self.wynik.emit(_wykryj_modele_na_serwerach(self.serwery))


# =============================================================================
#  Zarządzanie listą serwerów (dialog otwierany z paska w głównym oknie)
# =============================================================================
class DialogZarzadzajSerwerami(QDialog):
    """Dodawanie/usuwanie wpisów z listy serwerów Ollama do przełącznika."""

    def __init__(self, serwery, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Zarządzaj serwerami"))
        self.setMinimumWidth(420)
        # WHY: kopia robocza - lista trafia do wywołującego dopiero po zamknięciu.
        self.serwery = [dict(s) for s in serwery]

        layout = QVBoxLayout(self)

        self.lista = QListWidget()
        layout.addWidget(self.lista)

        pasek_usun = QHBoxLayout()
        pasek_usun.addStretch(1)
        btn_usun = QPushButton(_("Usuń zaznaczony"))
        btn_usun.clicked.connect(self._usun)
        pasek_usun.addWidget(btn_usun)
        layout.addLayout(pasek_usun)

        formularz = QHBoxLayout()
        self.pole_nazwa = QLineEdit()
        self.pole_nazwa.setPlaceholderText(_("Nazwa (np. BC-250)"))
        self.pole_adres = QLineEdit()
        self.pole_adres.setPlaceholderText(_("Adres (np. http://192.168.0.236:11434)"))
        btn_dodaj = QPushButton(_("Dodaj"))
        btn_dodaj.clicked.connect(self._dodaj)
        formularz.addWidget(self.pole_nazwa)
        formularz.addWidget(self.pole_adres, 1)
        formularz.addWidget(btn_dodaj)
        layout.addLayout(formularz)

        btn_zamknij = QPushButton(_("Zamknij"))
        btn_zamknij.clicked.connect(self.accept)
        layout.addWidget(btn_zamknij, alignment=Qt.AlignmentFlag.AlignRight)

        self._odswiez_liste()

    def _odswiez_liste(self):
        self.lista.clear()
        for s in self.serwery:
            self.lista.addItem(f"{s['nazwa']}  —  {s['adres']}")

    def _dodaj(self):
        nazwa = self.pole_nazwa.text().strip()
        adres = self.pole_adres.text().strip().rstrip("/")
        if not nazwa or not adres:
            QMessageBox.warning(self, _("Brak danych"), _("Podaj nazwę i adres serwera."))
            return
        self.serwery.append({"nazwa": nazwa, "adres": adres})
        self.pole_nazwa.clear()
        self.pole_adres.clear()
        self._odswiez_liste()

    def _usun(self):
        wiersz = self.lista.currentRow()
        if wiersz < 0:
            return
        # WHY: przełącznik zawsze potrzebuje przynajmniej jednej pozycji do wyboru.
        if len(self.serwery) <= 1:
            QMessageBox.warning(self, _("Nie można usunąć"), _("Musi zostać co najmniej jeden serwer."))
            return
        del self.serwery[wiersz]
        self._odswiez_liste()


class DialogConfigContinue(QDialog):
    """Podgląd gotowego config.yaml dla Continue.dev - tylko do skopiowania.

    WHY: appka celowo NIGDY nie zapisuje ~/.continue/config.yaml sama - ten
    plik należy do użytkownika i może mieć inne, niezwiązane z Ollamą wpisy;
    automatyczny zapis mógłby je nadpisać. Zamiast tego pokazujemy gotową,
    poprawną treść (patrz _zbuduj_config_continue) do ręcznego wklejenia/scalenia.
    """

    def __init__(self, tresc, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Config dla Continue.dev"))
        self.setMinimumSize(560, 420)

        layout = QVBoxLayout(self)
        lbl_opis = QLabel(
            _("Wklej poniższą treść do pliku ~/.continue/config.yaml (nadpisując "
              "go całkowicie, albo scalając ręcznie z istniejącą zawartością, "
              "jeśli masz tam już inne modele). Appka niczego tu nie zapisuje "
              "automatycznie.")
        )
        lbl_opis.setWordWrap(True)
        layout.addWidget(lbl_opis)

        # WHY: każdy model dostaje tu domyślnie role chat/edit/apply - dobre dla
        #      modeli czatowych/kodujących (np. qwen2.5-coder), ale niepoprawne
        #      dla innych ról z listy docelowych modeli projektu (patrz
        #      CLAUDE.md) - model embeddingowy jak nomic-embed-text (używany w
        #      RAG WebUI) potrzebuje ZAMIAST tego "roles: [embed]". Dopisujemy
        #      to wprost w opisie, żeby nie trzeba było szukać w dokumentacji
        #      Continue, jeśli taki model trafi na listę.
        lbl_role = QLabel(
            _("Każdy model dostaje domyślnie role: chat, edit, apply. Dostępne są "
              "też inne role do ręcznej zmiany w treści poniżej: autocomplete "
              "(podpowiedzi w edytorze), embed (modele embeddingowe, np. "
              "nomic-embed-text - używane w RAG), rerank (przeszukiwanie/ranking "
              "wyników). Model, który nie służy do czatu/edycji kodu, powinien "
              "mieć podmienione roles na odpowiednią z tej listy.")
        )
        lbl_role.setWordWrap(True)
        layout.addWidget(lbl_role)

        self.pole_tresc = QPlainTextEdit()
        self.pole_tresc.setPlainText(tresc)
        self.pole_tresc.setReadOnly(True)
        czcionka = self.pole_tresc.font()
        czcionka.setFamily("monospace")  # WHY: YAML czyta się lepiej ze stałą szerokością znaków
        self.pole_tresc.setFont(czcionka)
        layout.addWidget(self.pole_tresc, 1)

        pasek = QHBoxLayout()
        pasek.addStretch(1)
        btn_kopiuj = QPushButton(_("Kopiuj do schowka"))
        btn_kopiuj.clicked.connect(self._kopiuj)
        pasek.addWidget(btn_kopiuj)
        btn_zamknij = QPushButton(_("Zamknij"))
        btn_zamknij.clicked.connect(self.accept)
        pasek.addWidget(btn_zamknij)
        layout.addLayout(pasek)

    def _kopiuj(self):
        QApplication.clipboard().setText(self.pole_tresc.toPlainText())


# =============================================================================
#  Główne okno
# =============================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.serwery = _wczytaj_serwery()
        self._serwer_aktywny_adres = _wczytaj_serwer_aktywny(self.serwery)
        self.client = OllamaClient(base_url=self._serwer_aktywny_adres)
        self.pull_worker = None
        self.refresh_worker = None
        self._instalacja_worker = None  # WHY: osobne śledzenie, żeby nie odpalić instalacji 2x naraz
        self._webui_worker = None       # WHY: to samo dla instalacji/uruchomienia Open WebUI
        self._webui_zainstalowane = False  # WHY: potrzebne w klik_webui, żeby wiedzieć co robi przycisk
        self._webui_dziala = False         # WHY: jw. - odróżnia "Uruchom" od "Otwórz" w tym samym miejscu
        self._litellm_worker = None     # WHY: to samo dla instalacji/uruchomienia LiteLLM
        self._litellm_zainstalowany = False  # WHY: potrzebne w klik_litellm, żeby wiedzieć co robi przycisk
        self._agregator_worker = None   # WHY: osobny wątek na podgląd modeli (zakładka "Agregator modeli")
        self._workers = []            # WHY: trzymamy referencje, by wątki nie zniknęły w trakcie
        self._ostatni_status = None   # WHY: żeby nie spamować logu tym samym statusem pull

        # WHY: język wczytujemy PRZED _buduj_ui() - wszystkie widgety mają
        #      dostać właściwe napisy od razu przy pierwszym budowaniu okna.
        self._jezyk_aktywny = _wczytaj_jezyk_zapisany()
        _wczytaj_jezyk(self._jezyk_aktywny)

        self.setWindowTitle(f"Ollama Manager {WERSJA}")
        # WHY: rosło razem z appką - "Usługi" mieści sterowanie usługą + Open
        #      WebUI jedna pod drugą, "Agregator modeli" dwie karty jedna pod
        #      drugą, "Zaawansowane" to teraz 10-polowy formularz + pole
        #      własnych parametrów + dwa przyciski, a karta "Pobierz nowy
        #      model" pokazuje wieloliniowe wyliczenie pamięci (kalkulator
        #      wagi+KV cache+bufory) pod polami modelu/rozmiaru/kwantyzacji -
        #      przy 760x700 część z tego się już nie mieściła bez przewijania.
        self.setMinimumSize(860, 910)
        self._buduj_ui()

        # WHAT: cykliczne odświeżanie co 10 s.
        # WHY:  stan usługi/API może zmienić się poza aplikacją (terminal, reboot).
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.odswiez)
        self.timer.start(10000)
        self.odswiez()

    # --- Budowa interfejsu ---
    def _buduj_ui(self):
        # WHAT: pasek serwera + pasek statystyk na górze, pod nim 5 zakładek
        #       (Usługi / Modele / Agregator modeli / Zaawansowane / Pomoc),
        #       dziennik na dole na całą szerokość.
        # WHY:  finalny układ po trzech podejściach w Claude Design - zakładki
        #       trzymają wysokość okna w ryzach (jedna zakładka = jeden ekran),
        #       a pasek statystyk daje podgląd stanu bez klikania w ogóle.
        #       Kolory/ramki są natywne (Qt/Breeze, jasny/ciemny wg motywu systemu).
        # WHY self._zaawansowane_wczytane: formularz w zakładce "Zaawansowane"
        #      ma teraz JEDEN przycisk "Zapisz" zamiast osobnego zapisu na
        #      pole - żeby cykliczne odświeżanie co 10 s nie kasowało
        #      niezapisanych zmian w trakcie edycji, pola wypełniamy z
        #      rzeczywistego stanu TYLKO raz po (prze)budowaniu UI i zaraz po
        #      zapisaniu (patrz _odswiez_zakladke_zaawansowane/zapisz_zaawansowane).
        self._zaawansowane_wczytane = False
        centralny = QWidget()
        self.setCentralWidget(centralny)
        layout = QVBoxLayout(centralny)

        # === Pasek serwera ===============================================
        # WHY: przełącza, do którego hosta Ollama idą operacje na modelach
        #      (lista/pobierz/usuń/VRAM) - bez edycji stałej OLLAMA_URL.
        #      Sterowanie USŁUGĄ (start/stop/autostart) zawsze zostaje lokalne.
        pasek_serwer = QHBoxLayout()
        pasek_serwer.addWidget(QLabel(_("Serwer:")))
        self.combo_serwer = QComboBox()
        self.combo_serwer.currentIndexChanged.connect(self._zmien_serwer)
        pasek_serwer.addWidget(self.combo_serwer, 1)
        btn_zarzadzaj_serwerami = QPushButton(_("Zarządzaj serwerami..."))
        btn_zarzadzaj_serwerami.clicked.connect(self._zarzadzaj_serwerami)
        pasek_serwer.addWidget(btn_zarzadzaj_serwerami)
        # WHY: przełącznik języka obok serwerów - jedyne dwa "globalne" ustawienia
        #      okna, więc żyją w tym samym górnym pasku zamiast osobnego wiersza.
        pasek_serwer.addWidget(QLabel(_("Język:")))
        self.combo_jezyk = QComboBox()
        self.combo_jezyk.currentIndexChanged.connect(self._zmien_jezyk)
        pasek_serwer.addWidget(self.combo_jezyk)
        layout.addLayout(pasek_serwer)
        self._wypelnij_combo_serwer()
        self._wypelnij_combo_jezyk()

        # === Pasek statystyk ============================================
        pasek_staty = QHBoxLayout()
        kafelek, self.lbl_stat_ollama = self._kafelek_stat(_("OLLAMA"))
        pasek_staty.addWidget(kafelek)
        kafelek, self.lbl_stat_webui = self._kafelek_stat(_("WEBUI"))
        pasek_staty.addWidget(kafelek)
        kafelek, self.lbl_stat_vram_lokalnie = self._kafelek_stat(_("VRAM"))
        pasek_staty.addWidget(kafelek)
        kafelek, self.lbl_stat_modele = self._kafelek_stat(_("MODELE"))
        pasek_staty.addWidget(kafelek)
        pasek_staty.addStretch(1)
        layout.addLayout(pasek_staty)

        # === Zakładki ====================================================
        zakladki = QTabWidget()
        zakladki.addTab(self._zakladka_uslugi(), _("Usługi"))
        zakladki.addTab(self._zakladka_modele_lokalne(), _("Modele"))
        zakladki.addTab(self._zakladka_agregator(), _("Agregator modeli"))
        zakladki.addTab(self._zakladka_zaawansowane(), _("Zaawansowane"))
        zakladki.addTab(self._zakladka_pomoc(), _("Pomoc"))
        layout.addWidget(zakladki, 1)

        # === Dziennik - na dole, pod zakładkami =========================
        # WHY: log ma być widoczny bez względu na to, którą zakładkę oglądasz
        #      (np. postęp pobierania modelu widać, nawet patrząc na Usługi).
        karta_log = QGroupBox(_("Dziennik"))
        uk_log = QVBoxLayout(karta_log)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)  # WHY: nie rośnij w nieskończoność
        self.log.setMaximumHeight(120)  # WHY: dziennik to podgląd, nie ma zdominować okna
        uk_log.addWidget(self.log)
        layout.addWidget(karta_log)

    def _kafelek_stat(self, podpis):
        # WHAT: mały kafelek "podpis nad wartością" do paska statystyk.
        # WHY:  zwraca też etykietę wartości, żeby _po_odswiezeniu mogło ją podmieniać.
        widget = QWidget()
        v = QVBoxLayout(widget)
        v.setContentsMargins(8, 4, 8, 4)
        lbl_podpis = QLabel(podpis)
        czcionka = lbl_podpis.font()
        czcionka.setPointSize(max(7, czcionka.pointSize() - 2))
        lbl_podpis.setFont(czcionka)
        lbl_wartosc = QLabel("—")
        czcionka_w = lbl_wartosc.font()
        czcionka_w.setBold(True)
        czcionka_w.setPointSize(czcionka_w.pointSize() + 1)
        lbl_wartosc.setFont(czcionka_w)
        v.addWidget(lbl_podpis)
        v.addWidget(lbl_wartosc)
        return widget, lbl_wartosc

    def _naglowek_sekcji(self, tytul, widget_z_prawej=None):
        # WHAT: wspólny nagłówek sekcji - tytuł po lewej, opcjonalny status/przycisk z prawej.
        pasek = QHBoxLayout()
        etykieta = QLabel(tytul)
        czcionka = etykieta.font()
        czcionka.setBold(True)
        czcionka.setPointSize(czcionka.pointSize() + 1)
        etykieta.setFont(czcionka)
        pasek.addWidget(etykieta)
        pasek.addStretch(1)
        if widget_z_prawej is not None:
            pasek.addWidget(widget_z_prawej)
        return pasek

    def _zakladka_uslugi(self):
        # WHAT: dwie kolumny - lewa: Sterowanie i Open WebUI jedna pod drugą
        #       (obie "usługi w tle" tej aplikacji); prawa: Załadowane do VRAM
        #       na całą wysokość zakładki, jako pełnoprawne okno, nie mały podgląd.
        strona = QWidget()
        layout = QVBoxLayout(strona)

        kolumny = QHBoxLayout()
        layout.addLayout(kolumny, 1)

        # --- Lewa kolumna: Ollama + Open WebUI, ta sama "ramka" i styl nagłówka ---
        lewa_kolumna = QVBoxLayout()
        kolumny.addLayout(lewa_kolumna, 1)

        karta_ollama = QGroupBox(_("Ollama"))
        uk_ollama = QVBoxLayout(karta_ollama)
        self.lbl_ollama_status = QLabel(_("sprawdzam..."))
        uk_ollama.addLayout(self._naglowek_sekcji(_("Usługa systemd"), self.lbl_ollama_status))
        pasek_przyciskow = QHBoxLayout()
        self.btn_start = QPushButton(_("Uruchom"))
        self.btn_start.clicked.connect(self.start_uslugi)
        self.btn_stop = QPushButton(_("Zatrzymaj"))
        self.btn_stop.clicked.connect(self.stop_uslugi)
        self.btn_instaluj = QPushButton(_("Zainstaluj Ollama"))
        self.btn_instaluj.setVisible(False)  # WHY: widoczny tylko gdy wykryjemy brak instalacji
        self.btn_instaluj.clicked.connect(self.zainstaluj_ollame)
        pasek_przyciskow.addWidget(self.btn_start)
        pasek_przyciskow.addWidget(self.btn_stop)
        pasek_przyciskow.addWidget(self.btn_instaluj)
        uk_ollama.addLayout(pasek_przyciskow)
        self.chk_autostart = QCheckBox(_("Uruchamiaj automatycznie przy starcie systemu"))
        self.chk_autostart.toggled.connect(self.przelacz_autostart)
        uk_ollama.addWidget(self.chk_autostart)

        lewa_kolumna.addWidget(karta_ollama)

        # --- Open WebUI, w takiej samej ramce (QGroupBox) jak Sterowanie ---
        karta_webui = QGroupBox("Open WebUI")
        uk_webui = QVBoxLayout(karta_webui)
        self.lbl_webui_status = QLabel(_("sprawdzam..."))
        uk_webui.addLayout(self._naglowek_sekcji(_("Panel czatu w przeglądarce"), self.lbl_webui_status))
        pasek_webui = QHBoxLayout()
        self.btn_webui = QPushButton(_("sprawdzam..."))
        self.btn_webui.clicked.connect(self.klik_webui)
        self.btn_webui_stop = QPushButton(_("Zatrzymaj"))
        self.btn_webui_stop.setEnabled(False)  # WHY: aktywny dopiero gdy WebUI faktycznie działa
        self.btn_webui_stop.clicked.connect(self.zatrzymaj_webui)
        pasek_webui.addWidget(self.btn_webui)
        pasek_webui.addWidget(self.btn_webui_stop)
        uk_webui.addLayout(pasek_webui)
        # WHY: usługa --user, więc to osobny checkbox od autostartu Ollamy (systemowego)
        self.chk_webui_autostart = QCheckBox(_("Uruchamiaj automatycznie po zalogowaniu"))
        self.chk_webui_autostart.setEnabled(False)  # WHY: bez sensu, dopóki WebUI nie jest zainstalowane
        self.chk_webui_autostart.toggled.connect(self.przelacz_webui_autostart)
        uk_webui.addWidget(self.chk_webui_autostart)
        lewa_kolumna.addWidget(karta_webui)

        lewa_kolumna.addStretch(1)  # WHY: karty trzymają się góry lewej kolumny

        # --- Prawa kolumna: Załadowane do pamięci (VRAM) ---
        karta_vram = QGroupBox(_("Załadowane do pamięci (VRAM)"))
        uk_vram = QVBoxLayout(karta_vram)
        self.lista_zaladowane = QListWidget()  # WHY: cała kolumna - niech się rozciąga, bez limitu wysokości
        uk_vram.addWidget(self.lista_zaladowane)
        kolumny.addWidget(karta_vram, 1)

        return strona

    def _zakladka_modele_lokalne(self):
        # WHAT: dwie kolumny - lewa: pobieranie nowych modeli; prawa:
        #       zainstalowane modele (lista + usuwanie) na całą wysokość zakładki.
        strona = QWidget()
        layout = QVBoxLayout(strona)

        kolumny = QHBoxLayout()
        layout.addLayout(kolumny, 1)

        # --- Lewa kolumna: Pobierz nowy model ---
        karta_pobierz = QGroupBox(_("Pobierz nowy model"))
        uk_pobierz = QVBoxLayout(karta_pobierz)
        pasek_pull = QHBoxLayout()
        self.combo_modele = QComboBox()
        self.combo_modele.setEditable(True)  # WHY: pozwól wpisać też dowolną nazwę
        # WHY: domyślne maxVisibleItems (10) obcinało listę POLECANE_MODELE
        #      (13 pozycji) i wymuszało przewijanie rozwiniętej listy - z
        #      zapasem, żeby kolejne dopisane modele też mieściły się od razu.
        self.combo_modele.setMaxVisibleItems(20)
        self.combo_modele.addItems(POLECANE_MODELE)
        # WHY: podpowiedzi na liście to tylko wybór - pełna baza jest na ollama.com,
        #      placeholder przypomina o tym, gdy pole jest puste.
        self.combo_modele.lineEdit().setPlaceholderText(
            _("wpisz nazwę modelu lub wybierz z listy")
        )
        self.btn_pull = QPushButton(_("Pobierz"))
        self.btn_pull.clicked.connect(self.pobierz_model)
        pasek_pull.addWidget(self.combo_modele, 1)
        pasek_pull.addWidget(self.btn_pull)
        uk_pobierz.addLayout(pasek_pull)

        # WHAT: opcjonalny rozmiar (parametry) do doklejenia do nazwy modelu -
        #       osobne pole zamiast pisania "rodzina:rozmiar" ręcznie za każdym
        #       razem. Puste = bez zmian (nazwa z pola wyżej idzie 1:1). Lista
        #       podpowiedzi zależy od wybranego modelu (patrz
        #       _aktualizuj_liste_rozmiarow, ROZMIARY_WG_MODELU) - appka NIE
        #       sugeruje już rozmiarów, których dany model w ogóle nie ma.
        pasek_rozmiar = QHBoxLayout()
        pasek_rozmiar.addWidget(QLabel(_("Rozmiar:")))
        self.combo_rozmiar = QComboBox()
        self.combo_rozmiar.setEditable(True)
        # WHY: ROZMIARY_MODELI_OGOLNE (fallback dla modeli spoza listy) ma 15
        #      pozycji - tyle samo z zapasem, żeby też nie wymuszało przewijania.
        self.combo_rozmiar.setMaxVisibleItems(20)
        self.combo_rozmiar.setCurrentText("")
        self.combo_rozmiar.lineEdit().setPlaceholderText(_("opcjonalnie, np. 8b"))
        pasek_rozmiar.addWidget(self.combo_rozmiar, 1)
        uk_pobierz.addLayout(pasek_rozmiar)

        # WHAT: wybór kwantyzacji do pobrania - Ollama bez dopisku zwykle
        #       ściąga Q4_0, appka dokleja wariant do tagu (patrz _zbuduj_tag_modelu).
        # WHY:  Q8_0/F16 to większe pliki, ale mniejsza strata jakości - warto
        #       dać wybór z okna, zamiast zmuszać do ręcznego dopisywania tagu.
        pasek_kwantyzacja = QHBoxLayout()
        pasek_kwantyzacja.addWidget(QLabel(_("Kwantyzacja:")))
        self.combo_kwantyzacja = QComboBox()
        for etykieta, wartosc in [
            (_("domyślna (zwykle Q4_0)"), ""),
            ("Q8_0", "q8_0"),
            (_("F16 (pełna precyzja)"), "fp16"),
        ]:
            self.combo_kwantyzacja.addItem(etykieta, wartosc)
        pasek_kwantyzacja.addWidget(self.combo_kwantyzacja, 1)
        uk_pobierz.addLayout(pasek_kwantyzacja)

        # WHAT: orientacyjne zużycie pamięci dla aktualnie wpisanego rozmiaru
        #       + kwantyzacji - aktualizowane na żywo (patrz _aktualizuj_szacowana_pamiec).
        # WHY:  jawnie podpisane jako przybliżenie (patrz _oszacuj_pamiec_gb) -
        #       appka nie zna architektury modelu przed pobraniem, więc nie
        #       udaje precyzji, której nie ma.
        self.lbl_szacowana_pamiec = QLabel("")
        self.lbl_szacowana_pamiec.setWordWrap(True)
        uk_pobierz.addWidget(self.lbl_szacowana_pamiec)
        self.combo_modele.currentTextChanged.connect(self._zmien_model_pobierania)
        self.combo_rozmiar.currentTextChanged.connect(self._aktualizuj_szacowana_pamiec)
        self.combo_kwantyzacja.currentIndexChanged.connect(self._aktualizuj_szacowana_pamiec)
        self._aktualizuj_liste_rozmiarow()
        self._aktualizuj_szacowana_pamiec()

        # WHAT: krótkie wyjaśnienie, skąd wziąć nazwę modelu do pobrania.
        # WHY:  powyższa lista to tylko garść popularnych modeli - reszta jest
        #       na ollama.com/library, skąd można skopiować dowolną nazwę. Nie
        #       każdy model ma opublikowany każdy wariant kwantyzacji - jeśli
        #       wybranego nie ma, pobieranie zakończy się błędem z Ollamy
        #       (manifest nieznaleziony) - warto sprawdzić dostępne tagi na stronie.
        lbl_link = QLabel(
            _('Sprawdź dostępne modele (i warianty kwantyzacji) na '
              '<a href="https://ollama.com/library">ollama.com/library</a>, '
              "wpisz nazwę w polu powyżej i kliknij Pobierz.")
        )
        lbl_link.setWordWrap(True)
        lbl_link.setOpenExternalLinks(True)
        uk_pobierz.addWidget(lbl_link)

        self.pasek_postepu = QProgressBar()
        self.pasek_postepu.setVisible(False)
        uk_pobierz.addWidget(self.pasek_postepu)
        uk_pobierz.addStretch(1)  # WHY: karta trzyma się góry lewej kolumny
        kolumny.addWidget(karta_pobierz, 1)

        # --- Prawa kolumna: Zainstalowane modele ---
        karta_zainstalowane = QGroupBox(_("Zainstalowane modele"))
        uk_zainstalowane = QVBoxLayout(karta_zainstalowane)
        self.btn_usun = QPushButton(_("Usuń zaznaczony"))
        self.btn_usun.setEnabled(False)  # WHY: aktywny dopiero po zaznaczeniu modelu
        self.btn_usun.clicked.connect(self.usun_model)
        pasek_usun = QHBoxLayout()
        pasek_usun.addStretch(1)
        pasek_usun.addWidget(self.btn_usun)
        uk_zainstalowane.addLayout(pasek_usun)
        # WHAT: tabela zamiast zwykłej listy - trzy kolumny: nazwa, rozmiar na
        #       dysku (dokładny, z /api/tags) i szacowane VRAM (patrz
        #       _po_odswiezeniu: realne zużycie, jeśli model jest akurat
        #       załadowany, w przeciwnym razie wyliczenie tym samym kalkulatorem
        #       co karta "Pobierz nowy model" - _oszacuj_pamiec_bpw).
        self.lista_modeli = QTableWidget(0, 3)
        self.lista_modeli.setHorizontalHeaderLabels([_("Model"), _("Rozmiar na dysku"), _("VRAM")])
        self.lista_modeli.horizontalHeader().setStretchLastSection(True)
        self.lista_modeli.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.lista_modeli.verticalHeader().setVisible(False)
        self.lista_modeli.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.lista_modeli.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.lista_modeli.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.lista_modeli.itemSelectionChanged.connect(self._aktualizuj_przycisk_usun)
        uk_zainstalowane.addWidget(self.lista_modeli)
        kolumny.addWidget(karta_zainstalowane, 1)

        return strona

    def _zakladka_agregator(self):
        # WHAT: karta sterowania usługą LiteLLM (instalacja/start/stop/autostart)
        #       + karta z podglądem modeli, które trafią do jej configu.
        # WHY:  hosty biorą się WPROST z listy serwerów (pasek u góry, ten sam
        #       `self.serwery` co przełącznik) - żadnej drugiej listy hostów
        #       do ręcznego utrzymywania w dwóch miejscach.
        strona = QWidget()
        layout = QVBoxLayout(strona)

        karta_litellm = QGroupBox("LiteLLM")
        uk_litellm = QVBoxLayout(karta_litellm)
        self.lbl_litellm_status = QLabel(_("sprawdzam..."))
        uk_litellm.addLayout(
            self._naglowek_sekcji(_("Jeden adres dla modeli z wielu serwerów Ollamy"), self.lbl_litellm_status)
        )
        opis_litellm = QLabel(
            _("Wystawia jeden endpoint (zgodny z API OpenAI), za którym LiteLLM "
              "kieruje zapytania do modeli na hostach z listy serwerów (pasek u "
              "góry okna). Dzięki temu np. VS Code/Continue może korzystać z "
              "modeli na kilku komputerach naraz, wskazując tylko na ten jeden adres.")
        )
        opis_litellm.setWordWrap(True)
        uk_litellm.addWidget(opis_litellm)
        # WHAT: adres, który trzeba wpisać w kliencie (VS Code/Continue itp.),
        #       zaznaczalny myszką do skopiowania - bez grzebania w kodzie po LITELLM_URL.
        # WHY:  to jedyne miejsce w oknie, gdzie ten adres jest w ogóle pokazany -
        #       bez niego użytkownik nie wie, gdzie właściwie podłączyć klienta.
        lbl_litellm_adres = QLabel(
            _("Adres dla klientów (np. Continue): <b>{adres}</b>").format(adres=f"{LITELLM_URL}/v1")
        )
        lbl_litellm_adres.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        uk_litellm.addWidget(lbl_litellm_adres)
        pasek_litellm = QHBoxLayout()
        self.btn_litellm = QPushButton(_("sprawdzam..."))
        self.btn_litellm.clicked.connect(self.klik_litellm)
        self.btn_litellm_stop = QPushButton(_("Zatrzymaj"))
        self.btn_litellm_stop.setEnabled(False)  # WHY: aktywny dopiero gdy LiteLLM faktycznie działa
        self.btn_litellm_stop.clicked.connect(self.zatrzymaj_litellm)
        pasek_litellm.addWidget(self.btn_litellm)
        pasek_litellm.addWidget(self.btn_litellm_stop)
        uk_litellm.addLayout(pasek_litellm)
        # WHY: usługa --user, więc osobny checkbox od autostartu Ollamy (systemowego)
        self.chk_litellm_autostart = QCheckBox(_("Uruchamiaj automatycznie po zalogowaniu"))
        self.chk_litellm_autostart.setEnabled(False)  # WHY: bez sensu, dopóki LiteLLM nie jest zainstalowane
        self.chk_litellm_autostart.toggled.connect(self.przelacz_litellm_autostart)
        uk_litellm.addWidget(self.chk_litellm_autostart)
        layout.addWidget(karta_litellm)

        karta_agregator = QGroupBox(_("Modele w agregatorze"))
        uk_agregator = QVBoxLayout(karta_agregator)
        pasek_agregator = QHBoxLayout()
        lbl_agregator = QLabel(
            _("Podgląd modeli i hostów, które trafią do configu LiteLLM - na "
              "podstawie listy serwerów u góry okna.")
        )
        lbl_agregator.setWordWrap(True)
        pasek_agregator.addWidget(lbl_agregator, 1)
        self.btn_odswiez_agregator = QPushButton(_("Odśwież listę"))
        self.btn_odswiez_agregator.clicked.connect(self.odswiez_liste_agregatora)
        pasek_agregator.addWidget(self.btn_odswiez_agregator)
        self.btn_config_continue = QPushButton(_("Config dla Continue..."))
        self.btn_config_continue.clicked.connect(self.generuj_config_continue)
        pasek_agregator.addWidget(self.btn_config_continue)
        uk_agregator.addLayout(pasek_agregator)
        self.lista_agregator = QListWidget()
        uk_agregator.addWidget(self.lista_agregator)
        layout.addWidget(karta_agregator, 1)

        return strona

    def _zakladka_zaawansowane(self):
        # WHAT: zmienne środowiskowe usługi Ollama - kompaktowy formularz
        #       "nazwa + pole/checkbox", bez opisów (te są w osobnej zakładce
        #       "Pomoc", patrz _zakladka_pomoc) i bez osobnego zapisu na każde
        #       pole - jeden przycisk "Zapisz" na dole stosuje WSZYSTKO naraz.
        # WHY:  osobna zakładka zamiast kolejnych pól w zakładce "Usługi" -
        #       to ustawienia dla kogoś, kto świadomie tuninguje sprzęt (np.
        #       BC-250 z ograniczonym VRAM), nie coś, co widać na pierwszy rzut
        #       oka. Wcześniejsza wersja miała osobny opis + przycisk "Zastosuj"
        #       przy KAŻDYM z 9 pól - zajmowało to mnóstwo miejsca (dużo
        #       przewijania) i każda zmiana osobno restartowała usługę. Jeden
        #       formularz + jeden zapis = jeden restart, nawet przy zmianie
        #       kilku pól naraz. Dotyczą WYŁĄCZNIE lokalnej Ollamy - tak jak
        #       reszta sterowania usługą.
        strona = QWidget()
        layout = QVBoxLayout(strona)

        karta = QGroupBox(_("Ollama - zmienne środowiskowe usługi"))
        uk = QVBoxLayout(karta)
        lbl_wstep = QLabel(
            _("Opis każdej zmiennej: zakładka \"Pomoc\". \"Zapisz\" stosuje "
              "WSZYSTKIE pola naraz i restartuje usługę Ollama (wymaga uprawnień "
              "administratora - załadowane modele na chwilę znikną z pamięci). "
              "Puste pole = domyślne zachowanie Ollamy.")
        )
        lbl_wstep.setWordWrap(True)
        uk.addWidget(lbl_wstep)

        formularz = QFormLayout()

        self.pole_keep_alive = QLineEdit()
        self.pole_keep_alive.setPlaceholderText("np. 30m")
        formularz.addRow("OLLAMA_KEEP_ALIVE", self.pole_keep_alive)

        self.pole_context_length = QLineEdit()
        self.pole_context_length.setPlaceholderText("np. 16384")
        formularz.addRow("OLLAMA_CONTEXT_LENGTH", self.pole_context_length)

        self.pole_max_loaded = QLineEdit()
        self.pole_max_loaded.setPlaceholderText("np. 1")
        formularz.addRow("OLLAMA_MAX_LOADED_MODELS", self.pole_max_loaded)

        self.pole_num_parallel = QLineEdit()
        self.pole_num_parallel.setPlaceholderText("np. 1")
        formularz.addRow("OLLAMA_NUM_PARALLEL", self.pole_num_parallel)

        self.combo_flash_attention = QComboBox()
        for etykieta, wartosc in [(_("domyślne (auto)"), ""), (_("włączone"), "1"), (_("wyłączone"), "0")]:
            self.combo_flash_attention.addItem(etykieta, wartosc)
        formularz.addRow("OLLAMA_FLASH_ATTENTION", self.combo_flash_attention)

        self.combo_kv_cache = QComboBox()
        for etykieta, wartosc in [(_("domyślne (f16)"), ""), ("q8_0", "q8_0"), ("q4_0", "q4_0")]:
            self.combo_kv_cache.addItem(etykieta, wartosc)
        formularz.addRow("OLLAMA_KV_CACHE_TYPE", self.combo_kv_cache)

        self.chk_vulkan = QCheckBox()
        formularz.addRow("OLLAMA_VULKAN", self.chk_vulkan)

        self.chk_igpu = QCheckBox()
        formularz.addRow("OLLAMA_IGPU_ENABLE", self.chk_igpu)

        self.chk_host = QCheckBox()
        formularz.addRow("OLLAMA_HOST", self.chk_host)

        self.pole_ggml_vk_visible_devices = QLineEdit()
        self.pole_ggml_vk_visible_devices.setPlaceholderText("np. 0")
        formularz.addRow("GGML_VK_VISIBLE_DEVICES", self.pole_ggml_vk_visible_devices)

        # WHAT: ograniczony wybór (nie wolne pole) - wartość w bajtach zależy
        #       tylko od tego, czy na tym hoście działa środowisko graficzne.
        #       "Pulpit" jako PIERWSZA pozycja (domyślnie wybrana w świeżo
        #       zbudowanym formularzu) - appka ma GUI, więc najczęściej i tak
        #       działa na maszynie z pulpitem, nie na czystym serwerze bez X.
        #       Realny stan wczytany z override.conf (patrz
        #       _odswiez_zakladke_zaawansowane) nadpisuje to i tak, jeśli
        #       zmienna była już kiedyś świadomie ustawiona.
        # WHY:  serwer bez X (np. BC-250 jako czysty backend) nie potrzebuje
        #       żadnej rezerwacji; stacja robocza z pulpitem (GNOME/KDE) dzieli
        #       tę samą pulę VRAM z kompozytorem, więc bez rezerwacji Ollama
        #       mogłaby zająć całość i zdusić pulpit. 500 MB (dokładnie 512 MiB)
        #       to bezpieczny, orientacyjny punkt startu - sam kompozytor/sterownik
        #       i tak rezerwuje coś jeszcze OSOBNO, poza kontrolą appki (patrz
        #       rozmowa o OLLAMA_GPU_OVERHEAD vs. rezerwacja systemowa/sterownika) -
        #       do korekty przez pole "własne parametry" niżej, jeśli 500 MB
        #       okaże się za mało/za dużo w praktyce.
        # WHY jawne "0" zamiast pustego: obie opcje muszą dawać się rozróżnić
        #     po zapisie i przeładowaniu (tak jak Vulkan/iGPU wcześniej) - gdyby
        #     "serwer" zapisywał puste (czyli w ogóle nie dopisywał klucza), nie
        #     dałoby się potem odróżnić "świadomie wybrał serwer" od "nigdy nie
        #     dotknął tego pola", i po każdym przeładowaniu wracałoby do "pulpit".
        self.combo_gpu_overhead = QComboBox()
        for etykieta, wartosc in [
            (_("pulpit (GNOME/KDE)"), "536870912"),
            (_("serwer (bez X-ów)"), "0"),
        ]:
            self.combo_gpu_overhead.addItem(etykieta, wartosc)
        formularz.addRow("OLLAMA_GPU_OVERHEAD", self.combo_gpu_overhead)

        uk.addLayout(formularz)

        lbl_wlasne = QLabel(_("Własne parametry (jeden na linię, KLUCZ=WARTOŚĆ):"))
        uk.addWidget(lbl_wlasne)
        self.pole_wlasne_zmienne = QPlainTextEdit()
        self.pole_wlasne_zmienne.setPlaceholderText("OLLAMA_...=...")
        self.pole_wlasne_zmienne.setMaximumHeight(100)
        uk.addWidget(self.pole_wlasne_zmienne)

        pasek_przyciskow = QHBoxLayout()
        self.btn_zalecane_zaawansowane = QPushButton(_("Zastosuj zalecane wartości"))
        self.btn_zalecane_zaawansowane.clicked.connect(self.zastosuj_zalecane_zaawansowane)
        pasek_przyciskow.addWidget(self.btn_zalecane_zaawansowane)
        pasek_przyciskow.addStretch(1)
        self.btn_zapisz_zaawansowane = QPushButton(_("Zapisz"))
        self.btn_zapisz_zaawansowane.clicked.connect(self.zapisz_zaawansowane)
        pasek_przyciskow.addWidget(self.btn_zapisz_zaawansowane)
        uk.addLayout(pasek_przyciskow)

        layout.addWidget(karta)
        layout.addStretch(1)

        przewijanie = QScrollArea()
        przewijanie.setWidget(strona)
        przewijanie.setWidgetResizable(True)
        przewijanie.setFrameShape(QScrollArea.Shape.NoFrame)  # WHY: bez podwójnej ramki (QScrollArea + karta)
        return przewijanie

    def _zakladka_pomoc(self):
        # WHAT: pełne opisy zmiennych środowiskowych Ollamy z zakładki
        #       "Zaawansowane" - wydzielone tutaj, żeby tamta zakładka mogła
        #       być krótkim formularzem (nazwa + pole), bez ściany tekstu.
        # WHY:  9 opisów naraz w jednej zakładce z polami wymuszało mnóstwo
        #       przewijania - rozdzielenie "co ustawić" (Zaawansowane) od
        #       "co to znaczy" (tu) trzyma obie zakładki czytelne.
        strona = QWidget()
        layout = QVBoxLayout(strona)

        karta = QGroupBox(_("Opis zmiennych środowiskowych Ollamy"))
        uk = QVBoxLayout(karta)

        opisy = [
            ("OLLAMA_KEEP_ALIVE", _("Jak długo model zostaje w pamięci po ostatnim zapytaniu, zanim zostanie "
                                     "wyładowany (domyślnie kilka minut). np. 30m, 1h, -1 (zawsze), 0 (od razu).")),
            ("OLLAMA_CONTEXT_LENGTH", _("Rozmiar okna kontekstu w tokenach (domyślnie 4096 - za mało do pracy "
                                         "agentowej w Continue/OpenCode).")),
            ("OLLAMA_MAX_LOADED_MODELS", _("Ile modeli może być jednocześnie załadowanych do pamięci (domyślnie "
                                            "3x liczba GPU).")),
            ("OLLAMA_NUM_PARALLEL", _("Ile równoległych zapytań obsłuży jeden załadowany model naraz.")),
            ("OLLAMA_FLASH_ATTENTION", _("Zmniejsza zużycie pamięci przy dłuższym kontekście, jeśli model i "
                                          "sprzęt to wspierają.")),
            ("OLLAMA_KV_CACHE_TYPE", _("Kwantyzacja pamięci podręcznej kontekstu - q8_0 to ok. -50% zużycia "
                                        "VRAM przy pomijalnej stracie jakości.")),
            ("OLLAMA_VULKAN", _("Backend Vulkan zamiast ROCm - szersza kompatybilność z kartami AMD "
                                 "bez pełnego wsparcia ROCm (np. BC-250).")),
            ("OLLAMA_IGPU_ENABLE", _("Czy Ollama może korzystać ze zintegrowanego GPU (iGPU) - domyślnie "
                                      "włączone. Wyłącz (w zakładce Zaawansowane), żeby wymusić pominięcie iGPU "
                                      "(np. przy problemach na jednolitej architekturze CPU+GPU jak BC-250).")),
            ("OLLAMA_HOST", _("Domyślnie Ollama nasłuchuje tylko lokalnie (127.0.0.1) - inne urządzenia "
                               "w sieci nie mogą się połączyć. Włącz (w zakładce Zaawansowane), żeby "
                               "nasłuchiwała na wszystkich interfejsach (0.0.0.0) i była dostępna spoza tego "
                               "komputera (np. z innego PC albo dla BC-250 pracującego jako klient).")),
            ("GGML_VK_VISIBLE_DEVICES", _("Który indeks urządzenia Vulkan ma być widoczny dla backendu "
                                           "(0 = pierwsze) - niezbędne na niektórych APU (np. BC-250), gdzie "
                                           "Vulkan wylicza kilka urządzeń naraz i bez tego Ollama może wybrać "
                                           "złe. Działa tylko razem z włączonym OLLAMA_VULKAN.")),
            ("OLLAMA_GPU_OVERHEAD", _("Ile pamięci GPU (w bajtach) zarezerwować z dala od Ollamy, dla reszty "
                                       "systemu - istotne na APU z pamięcią współdzieloną z systemem RAM. "
                                       "Serwer bez środowiska graficznego zwykle nie potrzebuje rezerwacji; "
                                       "stacja robocza z pulpitem (KDE/GNOME) dzieli tę samą pulę VRAM z "
                                       "kompozytorem, więc bez rezerwacji Ollama mogłaby zająć całość.")),
        ]
        for nazwa, opis in opisy:
            lbl_nazwa = QLabel(nazwa)
            czcionka = lbl_nazwa.font()
            czcionka.setBold(True)
            lbl_nazwa.setFont(czcionka)
            uk.addWidget(lbl_nazwa)
            lbl_opis = QLabel(opis)
            lbl_opis.setWordWrap(True)
            uk.addWidget(lbl_opis)

        layout.addWidget(karta)
        layout.addStretch(1)

        przewijanie = QScrollArea()
        przewijanie.setWidget(strona)
        przewijanie.setWidgetResizable(True)
        przewijanie.setFrameShape(QScrollArea.Shape.NoFrame)
        return przewijanie

    # --- Przełącznik serwera ---
    def _wypelnij_combo_serwer(self):
        # WHY: blokujemy sygnały na czas wypełniania - inaczej samo dodawanie
        #      pozycji odpaliłoby _zmien_serwer() i niepotrzebny wpis w logu.
        self.combo_serwer.blockSignals(True)
        self.combo_serwer.clear()
        aktywny_index = 0
        for i, s in enumerate(self.serwery):
            self.combo_serwer.addItem(f"{s['nazwa']} ({s['adres']})", s["adres"])
            if s["adres"] == self._serwer_aktywny_adres:
                aktywny_index = i
        self.combo_serwer.setCurrentIndex(aktywny_index)
        self.combo_serwer.blockSignals(False)

    def _zmien_serwer(self, index):
        if index < 0:
            return
        adres = self.combo_serwer.itemData(index)
        if not adres or adres == self.client.base_url:
            return
        self.client.base_url = adres
        self._serwer_aktywny_adres = adres
        _zapisz_serwer_aktywny(adres)
        self.wpis_log(_("Przełączono na serwer: {serwer}").format(serwer=self.combo_serwer.itemText(index)))
        self.odswiez()

    def _zarzadzaj_serwerami(self):
        dialog = DialogZarzadzajSerwerami(self.serwery, self)
        dialog.exec()
        self.serwery = dialog.serwery
        _zapisz_serwery(self.serwery)
        # WHY: jeśli aktywny adres zniknął z listy (usunięty w dialogu), wracamy na pierwszy.
        if self._serwer_aktywny_adres not in [s["adres"] for s in self.serwery]:
            self._serwer_aktywny_adres = self.serwery[0]["adres"]
            self.client.base_url = self._serwer_aktywny_adres
            _zapisz_serwer_aktywny(self._serwer_aktywny_adres)
        self._wypelnij_combo_serwer()
        self.odswiez()

    # --- Przełącznik języka ---
    def _wypelnij_combo_jezyk(self):
        self.combo_jezyk.blockSignals(True)
        self.combo_jezyk.clear()
        for i, (kod, nazwa) in enumerate(JEZYKI.items()):
            self.combo_jezyk.addItem(nazwa, kod)
            if kod == self._jezyk_aktywny:
                self.combo_jezyk.setCurrentIndex(i)
        self.combo_jezyk.blockSignals(False)

    def _zmien_jezyk(self, index):
        # WHY: przebudowujemy CAŁE okno tą samą funkcją co przy starcie
        #      (_buduj_ui) - żywa zmiana języka bez restartu aplikacji, bo
        #      widgety i tak zawsze budujemy od zera z aktualnych danych
        #      (self.serwery, self._serwer_aktywny_adres, ...).
        kod = self.combo_jezyk.itemData(index)
        if not kod or kod == self._jezyk_aktywny:
            return
        self._jezyk_aktywny = kod
        _wczytaj_jezyk(kod)
        _zapisz_jezyk(kod)
        self._buduj_ui()
        self.odswiez()

    # --- Pomocnicze ---
    def wpis_log(self, tekst):
        self.log.appendPlainText(tekst)

    def _uruchom_akcje(self, funkcja, opis):
        # WHAT: uruchamia akcję w tle i po zakończeniu loguje wynik + odświeża.
        # WHY:  wspólna droga dla start/stop/autostart/usuwania - jeden mechanizm.
        worker = ActionWorker(funkcja, opis)
        self._workers.append(worker)

        def _sprzatnij(sukces, komunikat, w=worker):
            self.wpis_log(komunikat)
            if w in self._workers:
                self._workers.remove(w)
            # WHY: krótkie opóźnienie - stan usługi ustala się chwilę po komendzie.
            QTimer.singleShot(1200, self.odswiez)

        worker.zakonczono.connect(_sprzatnij)
        worker.start()
        return worker

    # --- Odświeżanie stanu ---
    def odswiez(self):
        if self.refresh_worker and self.refresh_worker.isRunning():
            return
        self.refresh_worker = RefreshWorker(self.client)
        self.refresh_worker.wynik.connect(self._po_odswiezeniu)
        self.refresh_worker.start()

    def _ustaw_combo_wg_wartosci(self, combo, wartosc):
        indeks = combo.findData(wartosc)
        combo.setCurrentIndex(indeks if indeks >= 0 else 0)

    def _odswiez_zakladke_zaawansowane(self, stan):
        # WHAT: blokuje formularz, gdy Ollama nie jest zainstalowana (restart
        #       nie ma sensu) - to dzieje się przy KAŻDYM odświeżeniu (co 10 s).
        #       Rzeczywisty stan override.conf wczytujemy do pól TYLKO raz
        #       (patrz self._zaawansowane_wczytane, ustawiane w _buduj_ui i
        #       zerowane w zapisz_zaawansowane) - inaczej cykliczne odświeżanie
        #       nadpisywałoby to, co user właśnie wpisuje, zanim zdąży kliknąć
        #       "Zapisz".
        for widget in (
            self.pole_keep_alive, self.pole_context_length, self.pole_max_loaded,
            self.pole_num_parallel, self.combo_flash_attention, self.combo_kv_cache,
            self.chk_vulkan, self.chk_igpu, self.chk_host, self.pole_ggml_vk_visible_devices,
            self.combo_gpu_overhead,
            self.pole_wlasne_zmienne, self.btn_zalecane_zaawansowane, self.btn_zapisz_zaawansowane,
        ):
            widget.setEnabled(stan["zainstalowana"])

        if self._zaawansowane_wczytane:
            return
        self._zaawansowane_wczytane = True

        env = stan["env_ollama"]
        self.pole_keep_alive.setText(env.get("OLLAMA_KEEP_ALIVE", ""))
        self.pole_context_length.setText(env.get("OLLAMA_CONTEXT_LENGTH", ""))
        self.pole_max_loaded.setText(env.get("OLLAMA_MAX_LOADED_MODELS", ""))
        self.pole_num_parallel.setText(env.get("OLLAMA_NUM_PARALLEL", ""))
        self._ustaw_combo_wg_wartosci(self.combo_flash_attention, env.get("OLLAMA_FLASH_ATTENTION", ""))
        self._ustaw_combo_wg_wartosci(self.combo_kv_cache, env.get("OLLAMA_KV_CACHE_TYPE", ""))
        self.chk_vulkan.setChecked(env.get("OLLAMA_VULKAN") == "1")
        # WHY: IGPU_ENABLE domyślnie WŁĄCZONE - checkbox zaznaczony, dopóki nie
        #      ma jawnego wyłączenia. Akceptujemy zarówno "0" (nowy, jawny
        #      zapis - patrz zapisz_zaawansowane) jak i "false" (stary format
        #      sprzed przebudowy tej zakładki na jeden formularz).
        self.chk_igpu.setChecked(env.get("OLLAMA_IGPU_ENABLE") not in ("0", "false"))
        self.chk_host.setChecked(bool(env.get("OLLAMA_HOST")))
        self.pole_ggml_vk_visible_devices.setText(env.get("GGML_VK_VISIBLE_DEVICES", ""))
        self._ustaw_combo_wg_wartosci(self.combo_gpu_overhead, env.get("OLLAMA_GPU_OVERHEAD", ""))

        # WHY: pola/checkboxy wyżej pokrywają "znane" zmienne (OLLAMA_ZNANE_ZMIENNE) -
        #      wszystko inne, co siedzi w override.conf (np. dopisane ręcznie
        #      kiedyś przez systemctl edit), ląduje w polu "własne parametry",
        #      żeby formularz niczego nie gubił przy następnym zapisie.
        wlasne = [f"{k}={v}" for k, v in env.items() if k not in OLLAMA_ZNANE_ZMIENNE]
        self.pole_wlasne_zmienne.setPlainText("\n".join(wlasne))

    def _po_odswiezeniu(self, stan):
        # WHAT: przełóż stan usługi/API na wygląd okna.
        # WHY: ten sam wzorzec statusu instalacji co przy karcie Open WebUI -
        #      spójny styl obu kart w zakładce "Usługi".
        self.lbl_ollama_status.setText(
            _("zainstalowana ✓") if stan["zainstalowana"] else _("nie zainstalowana ✗")
        )
        if not stan["zainstalowana"]:
            # WHY: bez binarki 'ollama' sterowanie usługą nie ma sensu - chowamy
            #      Start/Stop/autostart i pokazujemy tylko przycisk instalacji.
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(False)
            self.chk_autostart.setEnabled(False)
            self.btn_instaluj.setVisible(True)
            instalacja_w_toku = bool(self._instalacja_worker and self._instalacja_worker.isRunning())
            self.btn_instaluj.setEnabled(not instalacja_w_toku)
        else:
            self.btn_instaluj.setVisible(False)
            self.chk_autostart.setEnabled(True)

            active = stan["active"]
            self.btn_start.setEnabled(not active)  # nie startuj czegoś, co już działa
            self.btn_stop.setEnabled(active)

            # WHY: blokujemy sygnały, żeby programowe ustawienie checkboxa
            #      nie wywołało przypadkiem enable/disable.
            self.chk_autostart.blockSignals(True)
            self.chk_autostart.setChecked(stan["enabled"])
            self.chk_autostart.blockSignals(False)

        self._odswiez_zakladke_zaawansowane(stan)

        # Odśwież tabelę modeli, zachowując zaznaczenie jeśli się da
        # WHY: kolumna "VRAM" pokazuje REALNE zużycie (z /api/ps), jeśli model
        #      jest akurat załadowany, albo orientacyjne wyliczenie tym samym
        #      kalkulatorem co karta "Pobierz nowy model" - na podstawie
        #      prawdziwych details.parameter_size/quantization_level z
        #      /api/tags, nie zgadywania rozmiaru z nazwy.
        vram_wg_nazwy = {m.get("name", "?"): m.get("size_vram", 0) for m in stan["zaladowane"]}
        zaznaczony = self._wybrany_model()
        self.lista_modeli.setRowCount(0)
        for m in stan["models"]:
            nazwa = m["name"]
            rozmiar_gb = m.get("size", 0) / (1024 ** 3)

            if nazwa in vram_wg_nazwy:
                vram_tekst = _("{gb:.1f} GB (załadowany)").format(gb=vram_wg_nazwy[nazwa] / (1024 ** 3))
            else:
                miliardy = _parsuj_parameter_size(m.get("parameter_size", ""))
                if miliardy is None:
                    vram_tekst = "—"
                else:
                    bpw = _QUANTIZATION_LEVEL_BPW.get(
                        (m.get("quantization_level") or "").upper(), _KWANTYZACJA_BPW[""][1]
                    )
                    oszacowanie = _oszacuj_pamiec_bpw(miliardy, bpw, "")
                    vram_tekst = _("~{gb:.1f} GB").format(gb=oszacowanie["zajete_realnie_gb"])

            wiersz = self.lista_modeli.rowCount()
            self.lista_modeli.insertRow(wiersz)
            self.lista_modeli.setItem(wiersz, 0, QTableWidgetItem(nazwa))
            self.lista_modeli.setItem(wiersz, 1, QTableWidgetItem(f"{rozmiar_gb:.1f} GB"))
            self.lista_modeli.setItem(wiersz, 2, QTableWidgetItem(vram_tekst))
        if zaznaczony:
            for i in range(self.lista_modeli.rowCount()):
                if self.lista_modeli.item(i, 0).text() == zaznaczony:
                    self.lista_modeli.setCurrentCell(i, 0)
                    break

        # Odśwież listę modeli załadowanych do pamięci (VRAM)
        self.lista_zaladowane.clear()
        for m in stan["zaladowane"]:
            nazwa = m.get("name", "?")
            vram_gb = m.get("size_vram", 0) / (1024 ** 3)
            self.lista_zaladowane.addItem(f"{nazwa}  —  {vram_gb:.1f} GB VRAM")

        # WHAT: jeden przycisk, trzy znaczenia - "Zainstaluj" / "Uruchom" / "Otwórz",
        #       zależnie od tego, czy binarka jest zainstalowana i czy WebUI
        #       już odpowiada. Drugi przycisk ("Zatrzymaj") aktywny tylko gdy działa.
        self._webui_zainstalowane = stan["webui"]
        self._webui_dziala = stan["webui_dziala"]
        self.lbl_webui_status.setText(_("zainstalowane ✓") if stan["webui"] else _("nie zainstalowane ✗"))
        webui_w_toku = bool(self._webui_worker and self._webui_worker.isRunning())
        if webui_w_toku:
            self.btn_webui.setEnabled(False)
            self.btn_webui_stop.setEnabled(False)
        else:
            if not stan["webui"]:
                self.btn_webui.setText(_("Zainstaluj WebUI"))
            elif stan["webui_dziala"]:
                self.btn_webui.setText(_("Otwórz WebUI"))
            else:
                self.btn_webui.setText(_("Uruchom WebUI"))
            self.btn_webui.setEnabled(True)
            self.btn_webui_stop.setEnabled(stan["webui"] and stan["webui_dziala"])

        # WHY: bez sensu włączać autostart czegoś, co nie jest zainstalowane;
        #      blokujemy sygnały jak przy chk_autostart, żeby nie odpalić
        #      przypadkiem enable/disable przy samym odświeżeniu.
        self.chk_webui_autostart.setEnabled(stan["webui"])
        self.chk_webui_autostart.blockSignals(True)
        self.chk_webui_autostart.setChecked(stan["webui_autostart"])
        self.chk_webui_autostart.blockSignals(False)

        # WHAT: jeden przycisk, dwa znaczenia - "Zainstaluj" / "Uruchom" - w
        #       przeciwieństwie do WebUI nie ma tu trzeciego stanu "Otwórz",
        #       bo LiteLLM to sam endpoint API, nie strona do oglądania.
        self._litellm_zainstalowany = stan["litellm"]
        self.lbl_litellm_status.setText(_("zainstalowane ✓") if stan["litellm"] else _("nie zainstalowane ✗"))
        litellm_w_toku = bool(self._litellm_worker and self._litellm_worker.isRunning())
        if litellm_w_toku:
            self.btn_litellm.setEnabled(False)
            self.btn_litellm_stop.setEnabled(False)
        else:
            if not stan["litellm"]:
                self.btn_litellm.setText(_("Zainstaluj LiteLLM"))
            elif stan["litellm_dziala"]:
                self.btn_litellm.setText(_("Uruchom ponownie"))
            else:
                self.btn_litellm.setText(_("Uruchom LiteLLM"))
            self.btn_litellm.setEnabled(True)
            self.btn_litellm_stop.setEnabled(stan["litellm"] and stan["litellm_dziala"])

        self.chk_litellm_autostart.setEnabled(stan["litellm"])
        self.chk_litellm_autostart.blockSignals(True)
        self.chk_litellm_autostart.setChecked(stan["litellm_autostart"])
        self.chk_litellm_autostart.blockSignals(False)

        # === Pasek statystyk ============================================
        # WHY: to jedyne miejsce w oknie ze statusem Ollamy - usunięty osobny
        #      pill w zakładce "Usługi", żeby nie dublować tej samej informacji.
        if not stan["zainstalowana"]:
            self.lbl_stat_ollama.setText(_("nie zainstalowana ✗"))
        elif stan["active"]:
            self.lbl_stat_ollama.setText(_("działa ✓"))
        elif stan["api"]:
            self.lbl_stat_ollama.setText("API")
        else:
            self.lbl_stat_ollama.setText(_("zatrzymana ✗"))

        self.lbl_stat_webui.setText(_("działa ✓") if stan["webui_dziala"] else _("zatrzymane ✗"))

        vram_lokalnie_gb = sum(m.get("size_vram", 0) for m in stan["zaladowane"]) / (1024 ** 3)
        self.lbl_stat_vram_lokalnie.setText(f"{vram_lokalnie_gb:.1f} GB ({len(stan['zaladowane'])})")

        self.lbl_stat_modele.setText(str(len(stan["models"])))

    # --- Instalacja ---
    def zainstaluj_ollame(self):
        if self._instalacja_worker and self._instalacja_worker.isRunning():
            self.wpis_log(_("Instalacja już trwa - poczekaj na zakończenie."))
            return
        # WHY: pobieranie i uruchamianie skryptu z internetu jako root - wymagamy
        #      świadomej zgody, tak jak przy usuwaniu modelu.
        odp = QMessageBox.question(
            self, _("Zainstaluj Ollama"),
            _("Zainstalować Ollamę teraz?\n\n"
              "Pobierze i uruchomi oficjalny skrypt instalacyjny z ollama.com\n"
              "(wymaga uprawnień administratora i połączenia z internetem)."),
        )
        if odp != QMessageBox.StandardButton.Yes:
            return
        self.wpis_log(_("Instaluję Ollamę - to może potrwać kilka minut..."))
        self.btn_instaluj.setEnabled(False)
        self._instalacja_worker = self._uruchom_akcje(ollama_zainstaluj, _("Instalacja Ollamy"))

    def klik_webui(self):
        if self._webui_worker and self._webui_worker.isRunning():
            self.wpis_log(_("Operacja na WebUI już trwa - poczekaj na zakończenie."))
            return

        if not self._webui_zainstalowane:
            # WHY: pip ściąga sporo zależności z internetu - jak przy Ollamie,
            #      wymagamy świadomej zgody zamiast robić to bez pytania.
            odp = QMessageBox.question(
                self, _("Zainstaluj Open WebUI"),
                _("Zainstalować Open WebUI teraz?\n\n"
                  "Zainstaluje pakiet 'open-webui' przez pip, dla bieżącego użytkownika\n"
                  "(bez Dockera, bez uprawnień administratora - wymaga internetu)."),
            )
            if odp != QMessageBox.StandardButton.Yes:
                return
            self.wpis_log(_("Instaluję Open WebUI - to może potrwać kilka minut..."))
            self.btn_webui.setEnabled(False)
            self._webui_worker = self._uruchom_akcje(webui_zainstaluj, _("Instalacja Open WebUI"))
            return

        if self._webui_dziala:
            # WHY: trzecia funkcja przycisku - WebUI już działa, więc w tym
            #      miejscu tylko otwiera przeglądarkę, nic nie uruchamiamy.
            QDesktopServices.openUrl(QUrl(WEBUI_URL))
            return

        # WHY: druga funkcja przycisku - uruchom serwer w tle. NIE otwieramy
        #      już automatycznie przeglądarki po starcie - użytkownik czasem
        #      chce tylko odpalić WebUI w tle, bez nowej karty w przeglądarce
        #      za każdym razem; otwiera ją sam przyciskiem "Otwórz WebUI".
        self.wpis_log(_("Uruchamiam Open WebUI..."))
        self.btn_webui.setEnabled(False)
        worker = ActionWorker(webui_uruchom, _("Uruchomienie Open WebUI"))
        self._webui_worker = worker
        self._workers.append(worker)

        def _po_uruchomieniu(sukces, komunikat, w=worker):
            self.wpis_log(komunikat)
            if w in self._workers:
                self._workers.remove(w)
            QTimer.singleShot(1200, self.odswiez)

        worker.zakonczono.connect(_po_uruchomieniu)
        worker.start()

    def zatrzymaj_webui(self):
        if self._webui_worker and self._webui_worker.isRunning():
            self.wpis_log(_("Operacja na WebUI już trwa - poczekaj na zakończenie."))
            return
        self.wpis_log(_("Zatrzymuję Open WebUI..."))
        self._webui_worker = self._uruchom_akcje(webui_zatrzymaj, _("Zatrzymanie Open WebUI"))

    # --- Sterowanie usługą ---
    def start_uslugi(self):
        self.wpis_log(_("Uruchamiam usługę Ollama..."))
        self._uruchom_akcje(usluga_start, _("Start usługi"))

    def stop_uslugi(self):
        self.wpis_log(_("Zatrzymuję usługę Ollama..."))
        self._uruchom_akcje(usluga_stop, _("Stop usługi"))

    def przelacz_autostart(self, wlacz):
        # WHAT: reakcja na kliknięcie checkboxa autostartu.
        opis = _("Włączenie autostartu") if wlacz else _("Wyłączenie autostartu")
        self.wpis_log(opis + "...")
        self._uruchom_akcje(lambda: usluga_autostart(wlacz), opis)

    def zastosuj_zalecane_zaawansowane(self):
        # WHAT: wypełnia formularz zalecanym profilem (OLLAMA_ZALECANE) - NIC
        #       nie zapisuje na dysk. To tylko punkt startu do przejrzenia i
        #       ewentualnej poprawki przed kliknięciem "Zapisz".
        z = OLLAMA_ZALECANE
        self.pole_keep_alive.setText(z["OLLAMA_KEEP_ALIVE"])
        self.pole_context_length.setText(z["OLLAMA_CONTEXT_LENGTH"])
        self.pole_max_loaded.setText(z["OLLAMA_MAX_LOADED_MODELS"])
        self.pole_num_parallel.setText(z["OLLAMA_NUM_PARALLEL"])
        self._ustaw_combo_wg_wartosci(self.combo_flash_attention, z["OLLAMA_FLASH_ATTENTION"])
        self._ustaw_combo_wg_wartosci(self.combo_kv_cache, z["OLLAMA_KV_CACHE_TYPE"])
        self.chk_vulkan.setChecked(z["OLLAMA_VULKAN"] == "1")
        self.chk_igpu.setChecked(z["OLLAMA_IGPU_ENABLE"] == "1")
        self.chk_host.setChecked(bool(z["OLLAMA_HOST"]))
        self.pole_ggml_vk_visible_devices.setText(z["GGML_VK_VISIBLE_DEVICES"])
        self.wpis_log(_('Wypełniono formularz zalecanymi wartościami - sprawdź i kliknij "Zapisz", żeby je zastosować.'))

    def zapisz_zaawansowane(self):
        # WHAT: składa WSZYSTKIE pola formularza (9 znanych zmiennych + własne
        #       parametry) w jeden słownik i zapisuje go JEDNYM wywołaniem
        #       usluga_zapisz_env - jeden zapis, jeden restart Ollamy,
        #       niezależnie od tego, ile pól zmieniłeś naraz.
        zmienne = {}
        keep_alive = self.pole_keep_alive.text().strip()
        if keep_alive:
            zmienne["OLLAMA_KEEP_ALIVE"] = keep_alive
        context_length = self.pole_context_length.text().strip()
        if context_length:
            zmienne["OLLAMA_CONTEXT_LENGTH"] = context_length
        max_loaded = self.pole_max_loaded.text().strip()
        if max_loaded:
            zmienne["OLLAMA_MAX_LOADED_MODELS"] = max_loaded
        num_parallel = self.pole_num_parallel.text().strip()
        if num_parallel:
            zmienne["OLLAMA_NUM_PARALLEL"] = num_parallel
        flash_attention = self.combo_flash_attention.currentData()
        if flash_attention:
            zmienne["OLLAMA_FLASH_ATTENTION"] = flash_attention
        kv_cache = self.combo_kv_cache.currentData()
        if kv_cache:
            zmienne["OLLAMA_KV_CACHE_TYPE"] = kv_cache
        # WHY: Vulkan i iGPU zawsze dostają jawną wartość "1"/"0" (nie "puste =
        #      domyślne") - łatwiej odczytać z pliku, co faktycznie jest
        #      ustawione, niż zgadywać ze samego braku wpisu.
        zmienne["OLLAMA_VULKAN"] = "1" if self.chk_vulkan.isChecked() else "0"
        zmienne["OLLAMA_IGPU_ENABLE"] = "1" if self.chk_igpu.isChecked() else "0"
        # WHY: HOST to adres, nie zwykły przełącznik 0/1 - odznaczenie po
        #      prostu nie dopisuje klucza (powrót do wbudowanego domyślnego
        #      "127.0.0.1:11434", czyli tylko lokalnie).
        if self.chk_host.isChecked():
            zmienne["OLLAMA_HOST"] = "0.0.0.0:11434"
        ggml_vk_visible_devices = self.pole_ggml_vk_visible_devices.text().strip()
        if ggml_vk_visible_devices:
            zmienne["GGML_VK_VISIBLE_DEVICES"] = ggml_vk_visible_devices
        # WHY: zawsze jawna wartość ("0" albo rozmiar w bajtach), nigdy pusta -
        #      patrz WHY przy budowie combo_gpu_overhead w _zakladka_zaawansowane.
        zmienne["OLLAMA_GPU_OVERHEAD"] = self.combo_gpu_overhead.currentData()

        for numer, linia in enumerate(self.pole_wlasne_zmienne.toPlainText().splitlines(), start=1):
            linia = linia.strip()
            if not linia:
                continue
            if "=" not in linia:
                QMessageBox.warning(
                    self, _("Błąd we własnych parametrach"),
                    _('Linia {numer} ("{linia}") nie ma postaci KLUCZ=WARTOŚĆ - popraw ją przed zapisem.').format(
                        numer=numer, linia=linia
                    ),
                )
                return
            klucz, wartosc = linia.split("=", 1)
            klucz = klucz.strip()
            if klucz:
                zmienne[klucz] = wartosc.strip()

        odp = QMessageBox.question(
            self, _("Zapisanie ustawień Ollamy"),
            _("Zapisać wszystkie pola z tej zakładki i zrestartować usługę Ollama?") + "\n\n" + _(
                "Wymaga uprawnień administratora - aktualnie załadowane\n"
                "modele zostaną na chwilę wyładowane z pamięci."
            ),
        )
        if odp != QMessageBox.StandardButton.Yes:
            return
        self.wpis_log(_("Zapisuję ustawienia Ollamy..."))
        self._zaawansowane_wczytane = False  # WHY: po restarcie odśwież formularz faktycznie zapisanym stanem
        self._uruchom_akcje(lambda: usluga_zapisz_env(zmienne), _("Zapisanie ustawień Ollamy"))

    def przelacz_webui_autostart(self, wlacz):
        # WHAT: reakcja na kliknięcie checkboxa autostartu WebUI.
        # WHY:  usługa --user, nie systemowa - ale i tak idzie przez _uruchom_akcje
        #       (w tle, bo dopisanie pliku .service + systemctl --user to blokujące I/O).
        opis = _("Włączenie autostartu WebUI") if wlacz else _("Wyłączenie autostartu WebUI")
        self.wpis_log(opis + "...")
        self._uruchom_akcje(lambda: webui_autostart(wlacz), opis)

    # --- Agregator modeli (LiteLLM) ---
    def klik_litellm(self):
        if self._litellm_worker and self._litellm_worker.isRunning():
            self.wpis_log(_("Operacja na LiteLLM już trwa - poczekaj na zakończenie."))
            return

        if not self._litellm_zainstalowany:
            odp = QMessageBox.question(
                self, _("Zainstaluj LiteLLM"),
                _("Zainstalować LiteLLM teraz?\n\n"
                  "Zainstaluje pakiet 'litellm[proxy]' przez uv, dla bieżącego użytkownika\n"
                  "(bez Dockera, bez uprawnień administratora - wymaga internetu)."),
            )
            if odp != QMessageBox.StandardButton.Yes:
                return
            self.wpis_log(_("Instaluję LiteLLM - to może potrwać kilka minut..."))
            self.btn_litellm.setEnabled(False)
            self._litellm_worker = self._uruchom_akcje(litellm_zainstaluj, _("Instalacja LiteLLM"))
            return

        # WHY: config.yaml generujemy tuż przed (re)startem z AKTUALNEJ listy
        #      serwerów - dodanie/usunięcie hosta widać dopiero po tym kliknięciu.
        self.wpis_log(_("Uruchamiam LiteLLM..."))
        self.btn_litellm.setEnabled(False)
        worker = ActionWorker(lambda: litellm_uruchom(self.serwery), _("Uruchomienie LiteLLM"))
        self._litellm_worker = worker
        self._workers.append(worker)

        def _po_uruchomieniu(sukces, komunikat, w=worker):
            self.wpis_log(komunikat)
            if sukces:
                # WHY: przypomnienie w dzienniku, gdzie podłączyć klienta -
                #      łatwiej znaleźć w logu niż wracać do zakładki po adres.
                self.wpis_log(_("Adres dla klientów (np. Continue): {adres}").format(adres=f"{LITELLM_URL}/v1"))
            if w in self._workers:
                self._workers.remove(w)
            QTimer.singleShot(1200, self.odswiez)

        worker.zakonczono.connect(_po_uruchomieniu)
        worker.start()

    def zatrzymaj_litellm(self):
        if self._litellm_worker and self._litellm_worker.isRunning():
            self.wpis_log(_("Operacja na LiteLLM już trwa - poczekaj na zakończenie."))
            return
        self.wpis_log(_("Zatrzymuję LiteLLM..."))
        self._litellm_worker = self._uruchom_akcje(litellm_zatrzymaj, _("Zatrzymanie LiteLLM"))

    def przelacz_litellm_autostart(self, wlacz):
        # WHY: jw. co przy WebUI - usługa --user, ale i tak przez _uruchom_akcje (blokujące I/O).
        opis = _("Włączenie autostartu LiteLLM") if wlacz else _("Wyłączenie autostartu LiteLLM")
        self.wpis_log(opis + "...")
        self._uruchom_akcje(lambda: litellm_autostart(wlacz, self.serwery), opis)

    def odswiez_liste_agregatora(self):
        # WHAT: na żądanie (nie co 10 s razem z resztą) odpytuje /api/tags na
        #       każdym hoście z listy serwerów - pokazuje, co realnie trafi
        #       do configu LiteLLM przy następnym uruchomieniu.
        # WHY:  osobny przycisk zamiast robić to w RefreshWorker - inaczej
        #       każde odświeżenie stanu (co 10 s) pytałoby wszystkie hosty,
        #       nawet gdy nikt nie patrzy akurat na tę zakładkę.
        if self._agregator_worker and self._agregator_worker.isRunning():
            return
        self.lista_agregator.clear()
        self.lista_agregator.addItem(_("Sprawdzam hosty..."))
        self.btn_odswiez_agregator.setEnabled(False)
        worker = AgregatorWorker(self.serwery)
        self._agregator_worker = worker

        def _po_sprawdzeniu(wpisy, w=worker):
            self.btn_odswiez_agregator.setEnabled(True)
            self.lista_agregator.clear()
            if not wpisy:
                self.lista_agregator.addItem(_("Brak dostępnych modeli (hosty nieosiągalne albo puste)."))
                return
            for nazwa_hosta, model, adres in wpisy:
                self.lista_agregator.addItem(f"{model}  —  {nazwa_hosta} ({adres})")

        worker.wynik.connect(_po_sprawdzeniu)
        worker.start()

    def generuj_config_continue(self):
        # WHAT: odpytuje hosty (jak odswiez_liste_agregatora) i z wykrytych
        #       modeli buduje gotowy config.yaml dla Continue.dev, pokazany
        #       do skopiowania w DialogConfigContinue.
        # WHY:  osobne, świadome kliknięcie zamiast automatycznego zapisu przy
        #       starcie LiteLLM - appka nie ma dotykać ~/.continue/config.yaml
        #       (patrz DialogConfigContinue). Model może wystąpić na kilku
        #       hostach naraz (LiteLLM sam routuje) - Continue widzi tylko
        #       nazwę modelu, więc listę deduplikujemy.
        if self._agregator_worker and self._agregator_worker.isRunning():
            return
        self.btn_config_continue.setEnabled(False)
        worker = AgregatorWorker(self.serwery)
        self._agregator_worker = worker

        def _po_sprawdzeniu(wpisy, w=worker):
            self.btn_config_continue.setEnabled(True)
            modele = sorted({model for _nazwa_hosta, model, _adres in wpisy})
            if not modele:
                QMessageBox.information(
                    self, _("Brak modeli"),
                    _("Brak dostępnych modeli (hosty nieosiągalne albo puste) - "
                      "sprawdź listę serwerów u góry okna i spróbuj ponownie."),
                )
                return
            DialogConfigContinue(_zbuduj_config_continue(modele), self).exec()

        worker.wynik.connect(_po_sprawdzeniu)
        worker.start()

    # --- Modele ---
    def _wybrany_model(self):
        wiersz = self.lista_modeli.currentRow()
        if wiersz < 0:
            return None
        item = self.lista_modeli.item(wiersz, 0)
        return item.text() if item else None

    def _aktualizuj_przycisk_usun(self):
        # WHY: usuwać można tylko gdy coś jest zaznaczone.
        self.btn_usun.setEnabled(self._wybrany_model() is not None)

    def usun_model(self):
        model = self._wybrany_model()
        if not model:
            return
        # WHY: usuwanie jest nieodwracalne - wymagamy potwierdzenia.
        odp = QMessageBox.question(
            self, _("Usuń model"),
            _("Na pewno usunąć model:\n\n{model}\n\nTej operacji nie da się cofnąć.").format(model=model),
        )
        if odp != QMessageBox.StandardButton.Yes:
            return
        self.wpis_log(_("Usuwam model: {model}").format(model=model))
        self._uruchom_akcje(
            lambda: self.client.delete_model(model), _("Usunięcie {model}").format(model=model)
        )

    def _aktualizuj_liste_rozmiarow(self):
        # WHAT: podmienia podpowiedzi w polu "Rozmiar" na te NAPRAWDĘ dostępne
        #       dla wybranego modelu (ROZMIARY_WG_MODELU), albo ogólny przekrój
        #       (ROZMIARY_MODELI_OGOLNE), jeśli model nie jest na tej liście.
        # WHY:  BUG naprawiony - wcześniej appka pokazywała TE SAME podpowiedzi
        #       (1b-70b) dla każdego modelu, więc sugerowała rozmiary, których
        #       dany model wcale nie ma (np. Ornith ma tylko 9b/35b). Nie
        #       czyścimy przy tym tego, co user już wpisał ręcznie do pola -
        #       zmieniają się tylko podpowiedzi w rozwijanej liście.
        model = self.combo_modele.currentText().strip()
        rozmiary = ROZMIARY_WG_MODELU.get(model, ROZMIARY_MODELI_OGOLNE)
        wpisany_tekst = self.combo_rozmiar.currentText()
        self.combo_rozmiar.blockSignals(True)
        self.combo_rozmiar.clear()
        self.combo_rozmiar.addItems(rozmiary)
        self.combo_rozmiar.setCurrentText(wpisany_tekst)
        self.combo_rozmiar.blockSignals(False)

    def _zmien_model_pobierania(self):
        self._aktualizuj_liste_rozmiarow()
        self._aktualizuj_szacowana_pamiec()

    def _aktualizuj_szacowana_pamiec(self):
        # WHAT: przelicza i pokazuje orientacyjne zużycie pamięci dla aktualnie
        #       wpisanego rozmiaru + kwantyzacji - na żywo, przy każdej zmianie.
        # WHY:  rozmiar bierzemy najpierw z osobnego pola "Rozmiar"; jeśli puste,
        #       próbujemy go wyciągnąć z samej nazwy modelu (np. gdy ktoś wybrał
        #       z listy gotowe "llama3.1:8b" bez użycia pola Rozmiar).
        miliardy = _parsuj_rozmiar_parametrow(self.combo_rozmiar.currentText())
        if miliardy is None:
            miliardy = _parsuj_rozmiar_parametrow(self.combo_modele.currentText())
        if miliardy is None:
            self.lbl_szacowana_pamiec.setText(
                _("Szacowane zużycie pamięci: podaj rozmiar modelu (np. 8b), żeby zobaczyć wyliczenie.")
            )
            return
        kwantyzacja = self.combo_kwantyzacja.currentData()
        w = _oszacuj_pamiec(miliardy, kwantyzacja)

        tresc = _(
            "Model: {p:.0f}B {kwant}, kontekst {ctx} ({kvq})\n"
            "Wagi: {wagi:.1f} GB\n"
            "KV cache: {kv_cache:.1f} GB\n"
            "Bufory: {bufory:.1f} GB\n"
            "Realnie zajęte: {zajete:.1f} GB\n"
            "Z zapasem: {z_zapasem:.1f} GB\n"
            "Rekomendowany RAM: {rekomendowany} GB"
        ).format(
            p=miliardy, kwant=w["etykieta_q"], ctx=_KALKULATOR_CTX, kvq=_KALKULATOR_KVQ,
            wagi=w["wagi_gb"], kv_cache=w["kv_cache_gb"], bufory=w["bufory_gb"],
            zajete=w["zajete_realnie_gb"], z_zapasem=w["z_zapasem_gb"],
            rekomendowany=w["rekomendowany_ram"],
        )
        if w["kv_cache_gb"] > w["wagi_gb"]:
            tresc += "\n" + _(
                "Uwaga: cache kontekstu przewyższa wagi modelu — rozważ mniejszy "
                "kontekst lub kwantyzację cache."
            )
        if _KALKULATOR_KVQ != "f16":
            tresc += "\n" + _(
                "Cache {kvq} działa TYLKO z OLLAMA_FLASH_ATTENTION=1 (zakładka Zaawansowane) - "
                "bez tego Ollama i tak liczy cache jak f16, czyli więcej niż wyliczono wyżej."
            ).format(kvq=_KALKULATOR_KVQ)
        tresc += "\n" + _(
            "Na APU / pamięci współdzielonej zarezerwuj pamięć dla systemu przez OLLAMA_GPU_OVERHEAD."
        )
        self.lbl_szacowana_pamiec.setText(tresc)

    def pobierz_model(self):
        model_wpisany = self.combo_modele.currentText().strip()
        if not model_wpisany:
            return
        model = _zbuduj_tag_modelu(
            model_wpisany, self.combo_rozmiar.currentText(), self.combo_kwantyzacja.currentData()
        )
        # WHY: jedno pobieranie naraz - prościej i nie obciąża łącza/dysku podwójnie.
        if self.pull_worker and self.pull_worker.isRunning():
            self.wpis_log(_("Pobieranie już trwa - poczekaj na zakończenie."))
            return

        self.btn_pull.setEnabled(False)
        self.pasek_postepu.setVisible(True)
        self.pasek_postepu.setRange(0, 100)
        self.pasek_postepu.setValue(0)
        self._ostatni_status = None
        self.wpis_log(_("Rozpoczynam pobieranie: {model}").format(model=model))

        self.pull_worker = PullWorker(self.client, model)
        self.pull_worker.postep.connect(self._postep_pull)
        self.pull_worker.zakonczono.connect(self._koniec_pull)
        self.pull_worker.start()

    def _postep_pull(self, procent, status):
        # WHAT: aktualizuj pasek; do logu pisz tylko przy ZMIANIE statusu.
        # WHY:  strumień sypie dziesiątkami linii/s - log dostaje tylko nowe etapy.
        if procent < 0:
            self.pasek_postepu.setRange(0, 0)  # tryb nieokreślony
        else:
            self.pasek_postepu.setRange(0, 100)
            self.pasek_postepu.setValue(procent)

        if status != self._ostatni_status:
            self.wpis_log(f"  {status}")
            self._ostatni_status = status

    def _koniec_pull(self, sukces, komunikat):
        self.pasek_postepu.setVisible(False)
        self.btn_pull.setEnabled(True)
        self.wpis_log(komunikat)
        self.odswiez()  # WHY: świeżo pobrany model powinien pojawić się na liście


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Ollama Manager")
    # WHY: ikona z motywu systemowego - spójny wygląd w Breeze, bez własnych zasobów.
    app.setWindowIcon(QIcon.fromTheme("applications-system"))
    okno = MainWindow()
    okno.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
