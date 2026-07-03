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
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import requests  # WHY: czytelniejsze od urllib przy strumieniowaniu /api/pull

from PyQt6.QtCore import QThread, pyqtSignal, QTimer, QUrl
from PyQt6.QtGui import QIcon, QDesktopServices
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QListWidget, QProgressBar, QTabWidget,
    QGroupBox,
    QPlainTextEdit, QComboBox, QMessageBox, QCheckBox,
    QDialog, QFormLayout, QLineEdit, QDialogButtonBox,
)

# --- Konfiguracja ---------------------------------------------------------
# WHAT: wersja aplikacji - widoczna w tytule okna.
# WHY:  ostatnia cyfra rośnie przy każdym commicie; pierwsze dwie zmieniają się
#       tylko na wyraźne polecenie (patrz CLAUDE.md, sekcja "Wersjonowanie").
WERSJA = "0.3.2"

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

# WHAT: modele podpowiadane w rozwijanej liście pobierania.
# WHY:  przekrój popularnych rodzin (Llama, Gemma, Mistral, Phi, DeepSeek, Qwen) -
#       użytkownik wybiera z listy, zamiast wpisywać nazwy z palca. Pole jest
#       edytowalne, więc dowolny inny model z ollama.com/library da się wpisać ręcznie.
POLECANE_MODELE = [
    "llama3.2",          # Meta - lekki, uniwersalny (domyślnie 3B)
    "llama3.1:8b",       # Meta - solidny model ogólnego przeznaczenia
    "gemma3",            # Google - domyślnie 4B, mieści się na jednym GPU
    "gemma2:9b",         # Google - poprzednia generacja
    "mistral",           # Mistral AI - klasyczny 7B
    "phi4",              # Microsoft - 14B, mocny w rozumowaniu
    "deepseek-r1:8b",    # DeepSeek - model z "myśleniem" (reasoning)
    "qwen3:8b",          # Qwen - ogólny model najnowszej generacji
    "qwen2.5-coder:7b",  # Qwen - do kodu, wspiera tool-calling
    "qwen2.5-coder:14b",
    "qwen2.5-coder:1.5b",
    "nomic-embed-text",  # embeddingi (RAG, wyszukiwanie semantyczne)
]


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


def _pkexec(args):
    # WHAT: uruchamia polecenie jako root przez polkit.
    # WHY:  pkexec pokazuje graficzny dialog KDE z prośbą o hasło - nie potrzeba
    #       terminala ani sudo. Rzucamy wyjątek przy błędzie, żeby worker go złapał.
    r = subprocess.run(["pkexec"] + args, capture_output=True, text=True)
    if r.returncode != 0:
        # WHY: kod 126 = użytkownik anulował/brak uprawnień, 127 = błąd autoryzacji.
        raise RuntimeError(r.stderr.strip() or f"pkexec: kod wyjścia {r.returncode}")


def usluga_start():
    _pkexec(["systemctl", "start", SERVICE_NAME])


def usluga_stop():
    _pkexec(["systemctl", "stop", SERVICE_NAME])


def usluga_autostart(wlacz):
    # WHAT: włącza lub wyłącza automatyczny start po restarcie systemu.
    akcja = "enable" if wlacz else "disable"
    _pkexec(["systemctl", akcja, SERVICE_NAME])


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


def _wyciagnij_host(adres):
    # WHAT: z adresu (sam host, host:port albo pełny URL) wyciąga goły hostname/IP.
    # WHY:  użytkownik naturalnie wpisze adres w stylu OLLAMA_URL
    #       (np. "http://192.168.0.236:11434"), a biała lista OLLAMA_REMOTES
    #       chce samego hosta, bez schematu i portu.
    adres = adres.strip()
    if "://" not in adres:
        adres = "http://" + adres  # WHY: urlparse potrzebuje schematu, żeby rozdzielić host od portu
    host = urlparse(adres).hostname
    if not host:
        raise ValueError(f"Nie rozpoznaję hosta w adresie: {adres}")
    return host


def _obecne_remote_hosty():
    # WHAT: czyta aktualną białą listę OLLAMA_REMOTES z uruchomionej usługi.
    # WHY:  nieuprzywilejowane zapytanie (jak _systemctl_query) - potrzebne,
    #       żeby nie dublować hosta i nie restartować usługi bez potrzeby.
    r = subprocess.run(
        ["systemctl", "show", SERVICE_NAME, "--property=Environment"],
        capture_output=True, text=True, timeout=3,
    )
    dopasowanie = re.search(r"OLLAMA_REMOTES=(\S+)", r.stdout)
    return dopasowanie.group(1).split(",") if dopasowanie else []


def _stats_zdalne_hosty():
    # WHAT: hosty z OLLAMA_REMOTES minus "ollama.com" - to domyślny wpis
    #       whitelisty, nie prawdziwy serwer Ollamy w LAN, więc nie ma go co pytać o VRAM.
    return [h for h in _obecne_remote_hosty() if h and h != "ollama.com"]


def pobierz_vram_zdalnego(host):
    # WHAT: pyta zdalny host o /api/ps (modele załadowane do jego VRAM/RAM).
    # WHY:  modele zdalne (RemoteHost) liczą się na GPU tamtej maszyny (np. BC-250) -
    #       pasek statystyk pokazuje to osobno od VRAM-u lokalnego.
    # UWAGA: zakłada domyślny port Ollamy (11434) na hoście zdalnym.
    try:
        r = requests.get(f"http://{host}:11434/api/ps", timeout=2)
        r.raise_for_status()
        return r.json().get("models", [])
    except requests.RequestException:
        return None  # WHY: None = host nieosiągalny, odróżniamy od "0 modeli w pamięci"


def usluga_dodaj_remote_host(host):
    # WHAT: dopisuje host do białej listy OLLAMA_REMOTES usługi i ją restartuje.
    # WHY:  bez wpisu na liście lokalna Ollama odrzuci proxowanie do zdalnego
    #       hosta (domyślnie dozwolony jest tylko ollama.com) - patrz CLAUDE.md.
    if not re.fullmatch(r"[A-Za-z0-9.\-]+", host):
        raise ValueError(f"Nieprawidłowa nazwa hosta: {host}")

    obecne = _obecne_remote_hosty()
    if host in obecne:
        return  # WHY: już na liście - nie ma co restartować usługi bez potrzeby

    tresc = f'[Service]\nEnvironment="OLLAMA_REMOTES={",".join(obecne + [host])}"\n'
    with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as f:
        f.write(tresc)
        tmp_path = f.name
    try:
        docelowy = f"/etc/systemd/system/{SERVICE_NAME}.service.d/override.conf"
        # WHY: jeden pkexec zamiast trzech osobnych (install/reload/restart) -
        #      mniej okienek z hasłem. $1/$2 to argumenty powłoki, a nie
        #      interpolacja stringów, więc dane od użytkownika (host) nie
        #      trafiają bezpośrednio do treści polecenia - trafiają tylko
        #      do zawartości pliku tymczasowego, którą i tak zwalidowano wyżej.
        skrypt = f'install -Dm644 "$1" "$2" && systemctl daemon-reload && systemctl restart {SERVICE_NAME}'
        _pkexec(["sh", "-c", skrypt, "_", tmp_path, docelowy])
    finally:
        os.remove(tmp_path)


def dodaj_model_zdalny(host, remote_host_url, remote_model, nazwa_lokalna):
    # WHAT: pełny przepis z sekcji "Modele zdalne" w CLAUDE.md, zautomatyzowany:
    #       1) dopisuje host do OLLAMA_REMOTES i restartuje usługę (wymaga roota),
    #       2) generuje Modelfile i tworzy lokalny model-skrót przez 'ollama create'
    #          (nie wymaga roota - rozmawia z lokalnym demonem jako zwykły użytkownik).
    usluga_dodaj_remote_host(host)

    # WHY: usługa mogła się właśnie zrestartować - dajemy jej chwilę na start,
    #      zanim 'ollama create' spróbuje się z nią połączyć.
    for _ in range(10):
        if _systemctl_query("is-active") == "active":
            break
        time.sleep(1)

    modelfile = (
        "FROM ollama/base\n"
        f"REMOTE_HOST {remote_host_url}\n"
        f"REMOTE_MODEL {remote_model}\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".Modelfile", delete=False) as f:
        f.write(modelfile)
        tmp_path = f.name
    try:
        wynik = subprocess.run(
            ["ollama", "create", nazwa_lokalna, "-f", tmp_path],
            capture_output=True, text=True, timeout=120,
        )
        if wynik.returncode != 0:
            raise RuntimeError(wynik.stderr.strip() or "ollama create: nieznany błąd")
    finally:
        os.remove(tmp_path)


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


def webui_zainstaluj():
    # WHAT: instaluje Open WebUI przez 'uv tool install', z Pythonem przypiętym na 3.11.
    # WHY:  Open WebUI (stan na 2026) NIE wspiera jeszcze Pythona 3.13, a to on jest
    #       domyślnym 'python3' na świeżym Debianie/KDE - zwykłe 'pip install' na
    #       systemowym interpreterze kończy się błędem "no matching distribution".
    #       'uv' sam dociąga i zarządza kompatybilnym Pythonem 3.11 (bez apt/roota/
    #       Dockera) i instaluje open-webui w odizolowanym środowisku, wystawiając
    #       binarkę w ~/.local/bin - dokładnie tam, gdzie szuka jej webui_binarka().
    uv = _uv_binarka()
    if not uv:
        wynik = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--user", "uv"],
            capture_output=True, text=True, timeout=None,
        )
        if wynik.returncode != 0:
            raise RuntimeError(wynik.stderr.strip() or "instalacja uv: nieznany błąd")
        uv = _uv_binarka()
        if not uv:
            raise RuntimeError("Zainstalowano 'uv', ale nie widać go w ~/.local/bin.")

    wynik = subprocess.run(
        [uv, "tool", "install", "--python", "3.11", "open-webui"],
        capture_output=True, text=True, timeout=None,  # WHY: pobranie Pythona 3.11 + zależności - może to potrwać
    )
    if wynik.returncode != 0:
        raise RuntimeError(wynik.stderr.strip() or "uv tool install: nieznany błąd")


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
        "WebUI nie odpowiedziało w ciągu 3 minut. Log usługi: "
        "journalctl --user -u open-webui -e"
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
        raise RuntimeError(wynik.stderr.strip() or "pkill: nie udało się zatrzymać procesu WebUI")


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
        raise RuntimeError(r.stderr.strip() or f"systemctl --user {' '.join(args)}: kod wyjścia {r.returncode}")


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
        raise RuntimeError("Open WebUI nie jest zainstalowane.")
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
        # WHAT: lista nazw zainstalowanych modeli.
        # WHY:  bierzemy tylko 'name' - reszta metadanych tu zbędna.
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
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

    def show_model(self, name):
        # WHAT: szczegóły pojedynczego modelu - m.in. czy ma ustawiony REMOTE_HOST.
        # WHY:  /api/tags nie mówi, które modele są zdalnymi proxy; trzeba spytać
        #       /api/show osobno dla każdego. Krótki timeout - to leci N razy pod rząd.
        try:
            r = requests.post(f"{self.base_url}/api/show", json={"model": name}, timeout=3)
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            return {}

    def list_remote_models(self):
        # WHAT: filtruje zainstalowane modele do tych stworzonych przez
        #       REMOTE_HOST/REMOTE_MODEL (patrz CLAUDE.md, sekcja "Modele zdalne").
        # WHY:  osobna zakładka w GUI pokazuje tylko te, nie wszystkie modele.
        zdalne = []
        for nazwa in self.list_models():
            info = self.show_model(nazwa)
            host = info.get("remote_host")
            if host:
                zdalne.append((nazwa, host, info.get("remote_model", "?")))
        return zdalne

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
        api = self.client.api_dziala()
        modele = self.client.list_models() if api else []
        zaladowane = self.client.list_loaded() if api else []
        webui = webui_zainstalowane()
        webui_autostart = webui_autostart_wlaczony()
        # WHY: samo "zainstalowane" nie mówi, czy serwer akurat teraz odpowiada -
        #      pasek statystyk ma pokazywać żywy stan, nie tylko obecność binarki.
        webui_dziala = _webui_dziala()

        # WHAT: modele z REMOTE_HOST (osobna zakładka) - wymaga /api/show na
        #       każdym zainstalowanym modelu, więc robimy to tylko gdy API żyje.
        zdalne_modele = self.client.list_remote_models() if api else []

        # WHAT: pasek statystyk - VRAM zdalny to suma z /api/ps na KAŻDYM
        #       hoście z białej listy OLLAMA_REMOTES (poza ollama.com).
        # WHY:  osobne od VRAM-u lokalnego - to zużycie karty na innej maszynie (BC-250).
        vram_zdalnie = 0
        zaladowane_zdalnie = 0
        hosty_nieosiagalne = 0
        zdalne_hosty = _stats_zdalne_hosty()
        for host in zdalne_hosty:
            wynik_hosta = pobierz_vram_zdalnego(host)
            if wynik_hosta is None:
                hosty_nieosiagalne += 1
                continue
            zaladowane_zdalnie += len(wynik_hosta)
            vram_zdalnie += sum(m.get("size_vram", 0) for m in wynik_hosta)

        self.wynik.emit({
            "zainstalowana": zainstalowana,
            "active": active,
            "enabled": enabled,
            "api": api,
            "models": modele,
            "zaladowane": zaladowane,
            "webui": webui,
            "webui_autostart": webui_autostart,
            "webui_dziala": webui_dziala,
            "zdalne_modele": zdalne_modele,
            "zdalne_hosty_liczba": len(zdalne_hosty),
            "zdalne_hosty_nieosiagalne": hosty_nieosiagalne,
            "vram_zdalnie_bajty": vram_zdalnie,
            "zaladowane_zdalnie_liczba": zaladowane_zdalnie,
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
            self.zakonczono.emit(True, f"Pobrano model: {self.model}")
        except Exception as e:
            self.zakonczono.emit(False, f"Błąd pobierania: {e}")


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
            self.zakonczono.emit(False, f"{self.opis}: błąd - {e}")


# =============================================================================
#  Kreator dodawania modelu zdalnego (RemoteHost/RemoteModel)
# =============================================================================
class DialogModelZdalny(QDialog):
    """Formularz z trzema polami potrzebnymi do przepisu z CLAUDE.md:
    adres zdalnej Ollamy, nazwa modelu na niej i lokalna nazwa-skrót.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Dodaj model zdalny")
        layout = QFormLayout(self)

        self.pole_adres = QLineEdit("http://192.168.0.236:11434")
        self.pole_model = QLineEdit()
        self.pole_model.setPlaceholderText("np. qwen2.5-coder:14b")
        self.pole_nazwa = QLineEdit()
        self.pole_nazwa.setPlaceholderText("np. qwen-14b-bc250")

        layout.addRow("Adres zdalnego hosta:", self.pole_adres)
        layout.addRow("Nazwa modelu na zdalnym hoście:", self.pole_model)
        layout.addRow("Nazwa lokalna (skrót):", self.pole_nazwa)

        przyciski = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        przyciski.accepted.connect(self.accept)
        przyciski.rejected.connect(self.reject)
        layout.addRow(przyciski)

    def dane(self):
        return (
            self.pole_adres.text().strip(),
            self.pole_model.text().strip(),
            self.pole_nazwa.text().strip(),
        )


# =============================================================================
#  Główne okno
# =============================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.client = OllamaClient()
        self.pull_worker = None
        self.refresh_worker = None
        self._instalacja_worker = None  # WHY: osobne śledzenie, żeby nie odpalić instalacji 2x naraz
        self._webui_worker = None       # WHY: to samo dla instalacji/uruchomienia Open WebUI
        self._webui_zainstalowane = False  # WHY: potrzebne w klik_webui, żeby wiedzieć co robi przycisk
        self._workers = []            # WHY: trzymamy referencje, by wątki nie zniknęły w trakcie
        self._ostatni_status = None   # WHY: żeby nie spamować logu tym samym statusem pull

        self.setWindowTitle(f"Ollama Manager {WERSJA}")
        # WHY: zakładka "Usługi" mieści sterowanie usługą + Open WebUI jedna pod
        #      drugą, więc potrzeba nieco więcej wysokości niż przy samych kartach.
        self.setMinimumSize(760, 600)
        self._buduj_ui()

        # WHAT: cykliczne odświeżanie co 10 s.
        # WHY:  stan usługi/API może zmienić się poza aplikacją (terminal, reboot).
        #       10 s zamiast 5 s - odświeżenie robi teraz więcej zapytań
        #       sieciowych (/api/show na każdy model, /api/ps na każdy zdalny host).
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.odswiez)
        self.timer.start(10000)
        self.odswiez()

    # --- Budowa interfejsu ---
    def _buduj_ui(self):
        # WHAT: pasek statystyk na górze, pod nim 3 zakładki (Usługi / Modele
        #       lokalne / Modele zdalne), dziennik na dole na całą szerokość.
        # WHY:  finalny układ po trzech podejściach w Claude Design - zakładki
        #       trzymają wysokość okna w ryzach (jedna zakładka = jeden ekran),
        #       a pasek statystyk daje podgląd stanu bez klikania w ogóle.
        #       Kolory/ramki są natywne (Qt/Breeze, jasny/ciemny wg motywu systemu).
        centralny = QWidget()
        self.setCentralWidget(centralny)
        layout = QVBoxLayout(centralny)

        # === Pasek statystyk ============================================
        pasek_staty = QHBoxLayout()
        kafelek, self.lbl_stat_ollama = self._kafelek_stat("OLLAMA")
        pasek_staty.addWidget(kafelek)
        kafelek, self.lbl_stat_webui = self._kafelek_stat("WEBUI")
        pasek_staty.addWidget(kafelek)
        kafelek, self.lbl_stat_vram_lokalnie = self._kafelek_stat("VRAM LOKALNIE")
        pasek_staty.addWidget(kafelek)
        kafelek, self.lbl_stat_vram_zdalnie = self._kafelek_stat("VRAM ZDALNIE")
        pasek_staty.addWidget(kafelek)
        kafelek, self.lbl_stat_modele = self._kafelek_stat("MODELE")
        pasek_staty.addWidget(kafelek)
        pasek_staty.addStretch(1)
        layout.addLayout(pasek_staty)

        # === Zakładki ====================================================
        zakladki = QTabWidget()
        zakladki.addTab(self._zakladka_uslugi(), "Usługi")
        zakladki.addTab(self._zakladka_modele_lokalne(), "Modele lokalne")
        zakladki.addTab(self._zakladka_modele_zdalne(), "Modele zdalne")
        layout.addWidget(zakladki, 1)

        # === Dziennik - na dole, pod zakładkami =========================
        # WHY: log ma być widoczny bez względu na to, którą zakładkę oglądasz
        #      (np. postęp pobierania modelu widać, nawet patrząc na Usługi).
        karta_log = QGroupBox("Dziennik")
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

        karta_ollama = QGroupBox("Ollama")
        uk_ollama = QVBoxLayout(karta_ollama)
        self.lbl_ollama_status = QLabel("sprawdzam...")
        uk_ollama.addLayout(self._naglowek_sekcji("Usługa systemd", self.lbl_ollama_status))
        pasek_przyciskow = QHBoxLayout()
        self.btn_start = QPushButton("Uruchom")
        self.btn_start.clicked.connect(self.start_uslugi)
        self.btn_stop = QPushButton("Zatrzymaj")
        self.btn_stop.clicked.connect(self.stop_uslugi)
        self.btn_instaluj = QPushButton("Zainstaluj Ollama")
        self.btn_instaluj.setVisible(False)  # WHY: widoczny tylko gdy wykryjemy brak instalacji
        self.btn_instaluj.clicked.connect(self.zainstaluj_ollame)
        pasek_przyciskow.addWidget(self.btn_start)
        pasek_przyciskow.addWidget(self.btn_stop)
        pasek_przyciskow.addWidget(self.btn_instaluj)
        uk_ollama.addLayout(pasek_przyciskow)
        self.chk_autostart = QCheckBox("Uruchamiaj automatycznie przy starcie systemu")
        self.chk_autostart.toggled.connect(self.przelacz_autostart)
        uk_ollama.addWidget(self.chk_autostart)
        lewa_kolumna.addWidget(karta_ollama)

        # --- Open WebUI, w takiej samej ramce (QGroupBox) jak Sterowanie ---
        karta_webui = QGroupBox("Open WebUI")
        uk_webui = QVBoxLayout(karta_webui)
        self.lbl_webui_status = QLabel("sprawdzam...")
        uk_webui.addLayout(self._naglowek_sekcji("Panel czatu w przeglądarce", self.lbl_webui_status))
        pasek_webui = QHBoxLayout()
        self.btn_webui = QPushButton("sprawdzam...")
        self.btn_webui.clicked.connect(self.klik_webui)
        self.btn_webui_stop = QPushButton("Zatrzymaj")
        self.btn_webui_stop.setEnabled(False)  # WHY: aktywny dopiero gdy WebUI faktycznie działa
        self.btn_webui_stop.clicked.connect(self.zatrzymaj_webui)
        pasek_webui.addWidget(self.btn_webui)
        pasek_webui.addWidget(self.btn_webui_stop)
        uk_webui.addLayout(pasek_webui)
        # WHY: usługa --user, więc to osobny checkbox od autostartu Ollamy (systemowego)
        self.chk_webui_autostart = QCheckBox("Uruchamiaj automatycznie po zalogowaniu")
        self.chk_webui_autostart.setEnabled(False)  # WHY: bez sensu, dopóki WebUI nie jest zainstalowane
        self.chk_webui_autostart.toggled.connect(self.przelacz_webui_autostart)
        uk_webui.addWidget(self.chk_webui_autostart)
        lewa_kolumna.addWidget(karta_webui)

        lewa_kolumna.addStretch(1)  # WHY: karty trzymają się góry lewej kolumny

        # --- Prawa kolumna: Załadowane do pamięci (VRAM) ---
        karta_vram = QGroupBox("Załadowane do pamięci (VRAM)")
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
        karta_pobierz = QGroupBox("Pobierz nowy model")
        uk_pobierz = QVBoxLayout(karta_pobierz)
        pasek_pull = QHBoxLayout()
        self.combo_modele = QComboBox()
        self.combo_modele.setEditable(True)  # WHY: pozwól wpisać też dowolną nazwę
        self.combo_modele.addItems(POLECANE_MODELE)
        # WHY: podpowiedzi na liście to tylko wybór - pełna baza jest na ollama.com,
        #      placeholder przypomina o tym, gdy pole jest puste.
        self.combo_modele.lineEdit().setPlaceholderText(
            "wpisz nazwę modelu lub wybierz z listy"
        )
        self.btn_pull = QPushButton("Pobierz")
        self.btn_pull.clicked.connect(self.pobierz_model)
        pasek_pull.addWidget(self.combo_modele, 1)
        pasek_pull.addWidget(self.btn_pull)
        uk_pobierz.addLayout(pasek_pull)

        # WHAT: link do pełnej listy modeli na stronie Ollamy.
        # WHY:  powyższa lista to tylko garść popularnych modeli - reszta jest
        #       na ollama.com/library, skąd można skopiować dowolną nazwę.
        lbl_link = QLabel(
            'Więcej modeli: <a href="https://ollama.com/library">ollama.com/library</a>'
        )
        lbl_link.setOpenExternalLinks(True)
        uk_pobierz.addWidget(lbl_link)

        self.pasek_postepu = QProgressBar()
        self.pasek_postepu.setVisible(False)
        uk_pobierz.addWidget(self.pasek_postepu)
        uk_pobierz.addStretch(1)  # WHY: karta trzyma się góry lewej kolumny
        kolumny.addWidget(karta_pobierz, 1)

        # --- Prawa kolumna: Zainstalowane modele ---
        karta_zainstalowane = QGroupBox("Zainstalowane modele")
        uk_zainstalowane = QVBoxLayout(karta_zainstalowane)
        self.btn_usun = QPushButton("Usuń zaznaczony")
        self.btn_usun.setEnabled(False)  # WHY: aktywny dopiero po zaznaczeniu modelu
        self.btn_usun.clicked.connect(self.usun_model)
        pasek_usun = QHBoxLayout()
        pasek_usun.addStretch(1)
        pasek_usun.addWidget(self.btn_usun)
        uk_zainstalowane.addLayout(pasek_usun)
        self.lista_modeli = QListWidget()
        self.lista_modeli.itemSelectionChanged.connect(self._aktualizuj_przycisk_usun)
        uk_zainstalowane.addWidget(self.lista_modeli)
        kolumny.addWidget(karta_zainstalowane, 1)

        return strona

    def _zakladka_modele_zdalne(self):
        # WHAT: dwie kolumny - lewa: dodawanie modeli zdalnych (RemoteHost);
        #       prawa: lista tych już utworzonych, na całą wysokość zakładki.
        # WHY:  lista jest PRZEFILTROWANA (tylko modele z ustawionym REMOTE_HOST,
        #       sprawdzanym przez /api/show) - odróżnia je od zwykłych, lokalnych
        #       modeli w zakładce "Modele lokalne".
        strona = QWidget()
        layout = QVBoxLayout(strona)

        kolumny = QHBoxLayout()
        layout.addLayout(kolumny, 1)

        # --- Lewa kolumna: Dodaj model zdalny ---
        karta_dodaj = QGroupBox("Dodaj model zdalny")
        uk_dodaj = QVBoxLayout(karta_dodaj)
        opis_remote = QLabel("Podepnij zdalny host (np. BC-250) pod lokalną Ollamę.")
        opis_remote.setWordWrap(True)
        uk_dodaj.addWidget(opis_remote)
        self.btn_dodaj_remote = QPushButton("Dodaj model zdalny...")
        self.btn_dodaj_remote.clicked.connect(self.dodaj_model_zdalny_dialog)
        uk_dodaj.addWidget(self.btn_dodaj_remote)
        uk_dodaj.addStretch(1)  # WHY: karta trzyma się góry lewej kolumny
        kolumny.addWidget(karta_dodaj, 1)

        # --- Prawa kolumna: Modele z REMOTE_HOST ---
        karta_lista = QGroupBox("Modele z REMOTE_HOST")
        uk_lista = QVBoxLayout(karta_lista)
        self.lista_modele_zdalne = QListWidget()
        uk_lista.addWidget(self.lista_modele_zdalne)
        kolumny.addWidget(karta_lista, 1)

        return strona

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

    def _po_odswiezeniu(self, stan):
        # WHAT: przełóż stan usługi/API na wygląd okna.
        # WHY: ten sam wzorzec statusu instalacji co przy karcie Open WebUI -
        #      spójny styl obu kart w zakładce "Usługi".
        self.lbl_ollama_status.setText(
            "zainstalowana ✓" if stan["zainstalowana"] else "nie zainstalowana ✗"
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

        # Odśwież listę modeli, zachowując zaznaczenie jeśli się da
        zaznaczony = self._wybrany_model()
        self.lista_modeli.clear()
        self.lista_modeli.addItems(stan["models"])
        if zaznaczony:
            for i in range(self.lista_modeli.count()):
                if self.lista_modeli.item(i).text() == zaznaczony:
                    self.lista_modeli.setCurrentRow(i)
                    break

        # Odśwież listę modeli załadowanych do pamięci (VRAM)
        self.lista_zaladowane.clear()
        for m in stan["zaladowane"]:
            nazwa = m.get("name", "?")
            vram_gb = m.get("size_vram", 0) / (1024 ** 3)
            self.lista_zaladowane.addItem(f"{nazwa}  —  {vram_gb:.1f} GB VRAM")

        # WHAT: jeden przycisk, dwa znaczenia - "Zainstaluj" albo "Uruchom",
        #       zależnie od tego, czy binarka open-webui już tu jest. Drugi
        #       przycisk ("Zatrzymaj") aktywny tylko gdy WebUI faktycznie działa.
        self._webui_zainstalowane = stan["webui"]
        self.lbl_webui_status.setText("zainstalowane ✓" if stan["webui"] else "nie zainstalowane ✗")
        webui_w_toku = bool(self._webui_worker and self._webui_worker.isRunning())
        if webui_w_toku:
            self.btn_webui.setEnabled(False)
            self.btn_webui_stop.setEnabled(False)
        else:
            self.btn_webui.setText("Uruchom WebUI" if stan["webui"] else "Zainstaluj WebUI")
            self.btn_webui.setEnabled(not stan["webui_dziala"])
            self.btn_webui_stop.setEnabled(stan["webui"] and stan["webui_dziala"])

        # WHY: bez sensu włączać autostart czegoś, co nie jest zainstalowane;
        #      blokujemy sygnały jak przy chk_autostart, żeby nie odpalić
        #      przypadkiem enable/disable przy samym odświeżeniu.
        self.chk_webui_autostart.setEnabled(stan["webui"])
        self.chk_webui_autostart.blockSignals(True)
        self.chk_webui_autostart.setChecked(stan["webui_autostart"])
        self.chk_webui_autostart.blockSignals(False)

        # Odśwież listę modeli zdalnych (REMOTE_HOST) w zakładce "Modele zdalne"
        self.lista_modele_zdalne.clear()
        for nazwa, host, model_zdalny in stan["zdalne_modele"]:
            self.lista_modele_zdalne.addItem(f"{nazwa}  →  {model_zdalny} @ {host}")

        # === Pasek statystyk ============================================
        # WHY: to jedyne miejsce w oknie ze statusem Ollamy - usunięty osobny
        #      pill w zakładce "Usługi", żeby nie dublować tej samej informacji.
        if not stan["zainstalowana"]:
            self.lbl_stat_ollama.setText("nie zainstalowana ✗")
        elif stan["active"]:
            self.lbl_stat_ollama.setText("działa ✓")
        elif stan["api"]:
            self.lbl_stat_ollama.setText("API")
        else:
            self.lbl_stat_ollama.setText("zatrzymana ✗")

        self.lbl_stat_webui.setText("działa ✓" if stan["webui_dziala"] else "zatrzymane ✗")

        vram_lokalnie_gb = sum(m.get("size_vram", 0) for m in stan["zaladowane"]) / (1024 ** 3)
        self.lbl_stat_vram_lokalnie.setText(f"{vram_lokalnie_gb:.1f} GB ({len(stan['zaladowane'])})")

        if stan["zdalne_hosty_liczba"] == 0:
            self.lbl_stat_vram_zdalnie.setText("brak hostów")
        elif stan["zdalne_hosty_nieosiagalne"] == stan["zdalne_hosty_liczba"]:
            self.lbl_stat_vram_zdalnie.setText("niedostępne")
        else:
            vram_zdalnie_gb = stan["vram_zdalnie_bajty"] / (1024 ** 3)
            self.lbl_stat_vram_zdalnie.setText(
                f"{vram_zdalnie_gb:.1f} GB ({stan['zaladowane_zdalnie_liczba']})"
            )

        self.lbl_stat_modele.setText(str(len(stan["models"])))

    # --- Instalacja ---
    def zainstaluj_ollame(self):
        if self._instalacja_worker and self._instalacja_worker.isRunning():
            self.wpis_log("Instalacja już trwa - poczekaj na zakończenie.")
            return
        # WHY: pobieranie i uruchamianie skryptu z internetu jako root - wymagamy
        #      świadomej zgody, tak jak przy usuwaniu modelu.
        odp = QMessageBox.question(
            self, "Zainstaluj Ollama",
            "Zainstalować Ollamę teraz?\n\n"
            "Pobierze i uruchomi oficjalny skrypt instalacyjny z ollama.com\n"
            "(wymaga uprawnień administratora i połączenia z internetem).",
        )
        if odp != QMessageBox.StandardButton.Yes:
            return
        self.wpis_log("Instaluję Ollamę - to może potrwać kilka minut...")
        self.btn_instaluj.setEnabled(False)
        self._instalacja_worker = self._uruchom_akcje(ollama_zainstaluj, "Instalacja Ollamy")

    def klik_webui(self):
        if self._webui_worker and self._webui_worker.isRunning():
            self.wpis_log("Operacja na WebUI już trwa - poczekaj na zakończenie.")
            return

        if not self._webui_zainstalowane:
            # WHY: pip ściąga sporo zależności z internetu - jak przy Ollamie,
            #      wymagamy świadomej zgody zamiast robić to bez pytania.
            odp = QMessageBox.question(
                self, "Zainstaluj Open WebUI",
                "Zainstalować Open WebUI teraz?\n\n"
                "Zainstaluje pakiet 'open-webui' przez pip, dla bieżącego użytkownika\n"
                "(bez Dockera, bez uprawnień administratora - wymaga internetu).",
            )
            if odp != QMessageBox.StandardButton.Yes:
                return
            self.wpis_log("Instaluję Open WebUI - to może potrwać kilka minut...")
            self.btn_webui.setEnabled(False)
            self._webui_worker = self._uruchom_akcje(webui_zainstaluj, "Instalacja Open WebUI")
            return

        # WHY: druga funkcja przycisku - uruchom serwer (jeśli jeszcze nie działa)
        #      i otwórz go w przeglądarce. Nie idzie przez _uruchom_akcje, bo
        #      potrzebujemy dodatkowego kroku (otwarcie przeglądarki) po sukcesie.
        self.wpis_log("Uruchamiam Open WebUI...")
        self.btn_webui.setEnabled(False)
        worker = ActionWorker(webui_uruchom, "Uruchomienie Open WebUI")
        self._webui_worker = worker
        self._workers.append(worker)

        def _po_uruchomieniu(sukces, komunikat, w=worker):
            self.wpis_log(komunikat)
            if w in self._workers:
                self._workers.remove(w)
            if sukces:
                QDesktopServices.openUrl(QUrl(WEBUI_URL))
            QTimer.singleShot(1200, self.odswiez)

        worker.zakonczono.connect(_po_uruchomieniu)
        worker.start()

    def zatrzymaj_webui(self):
        if self._webui_worker and self._webui_worker.isRunning():
            self.wpis_log("Operacja na WebUI już trwa - poczekaj na zakończenie.")
            return
        self.wpis_log("Zatrzymuję Open WebUI...")
        self._webui_worker = self._uruchom_akcje(webui_zatrzymaj, "Zatrzymanie Open WebUI")

    # --- Sterowanie usługą ---
    def start_uslugi(self):
        self.wpis_log("Uruchamiam usługę Ollama...")
        self._uruchom_akcje(usluga_start, "Start usługi")

    def stop_uslugi(self):
        self.wpis_log("Zatrzymuję usługę Ollama...")
        self._uruchom_akcje(usluga_stop, "Stop usługi")

    def przelacz_autostart(self, wlacz):
        # WHAT: reakcja na kliknięcie checkboxa autostartu.
        opis = "Włączenie autostartu" if wlacz else "Wyłączenie autostartu"
        self.wpis_log(opis + "...")
        self._uruchom_akcje(lambda: usluga_autostart(wlacz), opis)

    def przelacz_webui_autostart(self, wlacz):
        # WHAT: reakcja na kliknięcie checkboxa autostartu WebUI.
        # WHY:  usługa --user, nie systemowa - ale i tak idzie przez _uruchom_akcje
        #       (w tle, bo dopisanie pliku .service + systemctl --user to blokujące I/O).
        opis = "Włączenie autostartu WebUI" if wlacz else "Wyłączenie autostartu WebUI"
        self.wpis_log(opis + "...")
        self._uruchom_akcje(lambda: webui_autostart(wlacz), opis)

    # --- Modele ---
    def _wybrany_model(self):
        item = self.lista_modeli.currentItem()
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
            self, "Usuń model",
            f"Na pewno usunąć model:\n\n{model}\n\nTej operacji nie da się cofnąć.",
        )
        if odp != QMessageBox.StandardButton.Yes:
            return
        self.wpis_log(f"Usuwam model: {model}")
        self._uruchom_akcje(lambda: self.client.delete_model(model), f"Usunięcie {model}")

    def pobierz_model(self):
        model = self.combo_modele.currentText().strip()
        if not model:
            return
        # WHY: jedno pobieranie naraz - prościej i nie obciąża łącza/dysku podwójnie.
        if self.pull_worker and self.pull_worker.isRunning():
            self.wpis_log("Pobieranie już trwa - poczekaj na zakończenie.")
            return

        self.btn_pull.setEnabled(False)
        self.pasek_postepu.setVisible(True)
        self.pasek_postepu.setRange(0, 100)
        self.pasek_postepu.setValue(0)
        self._ostatni_status = None
        self.wpis_log(f"Rozpoczynam pobieranie: {model}")

        self.pull_worker = PullWorker(self.client, model)
        self.pull_worker.postep.connect(self._postep_pull)
        self.pull_worker.zakonczono.connect(self._koniec_pull)
        self.pull_worker.start()

    def dodaj_model_zdalny_dialog(self):
        # WHAT: kreator RemoteHost/RemoteModel - patrz sekcja "Modele zdalne" w CLAUDE.md.
        dialog = DialogModelZdalny(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        adres, model_zdalny, nazwa_lokalna = dialog.dane()
        if not (adres and model_zdalny and nazwa_lokalna):
            QMessageBox.warning(self, "Brak danych", "Wypełnij wszystkie pola.")
            return
        try:
            host = _wyciagnij_host(adres)
        except ValueError as e:
            QMessageBox.warning(self, "Błędny adres", str(e))
            return

        # WHY: to może zrestartować lokalną usługę Ollama (jeśli host jest
        #      nowy na białej liście) - wymagamy świadomej zgody.
        odp = QMessageBox.question(
            self, "Dodaj model zdalny",
            f"Dodać model zdalny?\n\n"
            f"  Zdalny host:   {adres}\n"
            f"  Model zdalny:  {model_zdalny}\n"
            f"  Nazwa lokalna: {nazwa_lokalna}\n\n"
            "Jeśli ten host nie jest jeszcze na białej liście OLLAMA_REMOTES,\n"
            "lokalna usługa Ollama zostanie zrestartowana.",
        )
        if odp != QMessageBox.StandardButton.Yes:
            return

        self.wpis_log(f"Dodaję model zdalny: {nazwa_lokalna} -> {model_zdalny}@{host}")
        self._uruchom_akcje(
            lambda: dodaj_model_zdalny(host, adres, model_zdalny, nazwa_lokalna),
            f"Dodanie modelu zdalnego {nazwa_lokalna}",
        )

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
