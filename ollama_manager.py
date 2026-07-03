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
from urllib.parse import urlparse

import requests  # WHY: czytelniejsze od urllib przy strumieniowaniu /api/pull

from PyQt6.QtCore import QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QListWidget, QProgressBar,
    QPlainTextEdit, QComboBox, QMessageBox, QFrame, QCheckBox,
    QDialog, QFormLayout, QLineEdit, QDialogButtonBox,
)

# --- Konfiguracja ---------------------------------------------------------
# WHAT: bazowy adres serwera Ollamy (operacje na modelach).
# WHY:  wydzielony na górę - możesz wskazać BC-250
#       (np. http://192.168.0.236:11434) zamiast localhost.
#       UWAGA: sterowanie USŁUGĄ (start/stop/autostart) dotyczy zawsze
#       LOKALNEGO systemd - nie da się zdalnie startować BC-250 tą drogą.
OLLAMA_URL = "http://localhost:11434"

# WHAT: nazwa usługi systemd. WHY: wydzielona, gdyby u Ciebie nazywała się inaczej.
SERVICE_NAME = "ollama"

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
    wynik = pyqtSignal(dict)  # {'zainstalowana','active','enabled','api','models','zaladowane'}

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
        self.wynik.emit({
            "zainstalowana": zainstalowana,
            "active": active,
            "enabled": enabled,
            "api": api,
            "models": modele,
            "zaladowane": zaladowane,
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
        self._workers = []            # WHY: trzymamy referencje, by wątki nie zniknęły w trakcie
        self._ostatni_status = None   # WHY: żeby nie spamować logu tym samym statusem pull

        self.setWindowTitle("Ollama Manager")
        self.setMinimumSize(580, 680)
        self._buduj_ui()

        # WHAT: cykliczne odświeżanie co 5 s.
        # WHY:  stan usługi/API może zmienić się poza aplikacją (terminal, reboot).
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.odswiez)
        self.timer.start(5000)
        self.odswiez()

    # --- Budowa interfejsu ---
    def _buduj_ui(self):
        centralny = QWidget()
        self.setCentralWidget(centralny)
        layout = QVBoxLayout(centralny)

        # Sekcja statusu + sterowanie usługą
        pasek_status = QHBoxLayout()
        self.lbl_status = QLabel("sprawdzam...")
        self.btn_start = QPushButton("Uruchom")
        self.btn_start.clicked.connect(self.start_uslugi)
        self.btn_stop = QPushButton("Zatrzymaj")
        self.btn_stop.clicked.connect(self.stop_uslugi)
        self.btn_instaluj = QPushButton("Zainstaluj Ollama")
        self.btn_instaluj.setVisible(False)  # WHY: widoczny tylko gdy wykryjemy brak instalacji
        self.btn_instaluj.clicked.connect(self.zainstaluj_ollame)
        pasek_status.addWidget(QLabel("Usługa:"))
        pasek_status.addWidget(self.lbl_status, 1)
        pasek_status.addWidget(self.btn_start)
        pasek_status.addWidget(self.btn_stop)
        pasek_status.addWidget(self.btn_instaluj)
        layout.addLayout(pasek_status)

        # Autostart przy starcie systemu
        self.chk_autostart = QCheckBox("Uruchamiaj automatycznie przy starcie systemu")
        self.chk_autostart.toggled.connect(self.przelacz_autostart)
        layout.addWidget(self.chk_autostart)

        # Separator
        linia = QFrame()
        linia.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(linia)

        # Lista modeli + przycisk usuwania
        naglowek_modeli = QHBoxLayout()
        naglowek_modeli.addWidget(QLabel("Zainstalowane modele:"))
        naglowek_modeli.addStretch(1)
        self.btn_usun = QPushButton("Usuń zaznaczony")
        self.btn_usun.setEnabled(False)  # WHY: aktywny dopiero po zaznaczeniu modelu
        self.btn_usun.clicked.connect(self.usun_model)
        naglowek_modeli.addWidget(self.btn_usun)
        layout.addLayout(naglowek_modeli)

        self.lista_modeli = QListWidget()
        self.lista_modeli.itemSelectionChanged.connect(self._aktualizuj_przycisk_usun)
        layout.addWidget(self.lista_modeli, 1)

        # Modele aktualnie załadowane do pamięci (RAM/VRAM) - podgląd z /api/ps
        layout.addWidget(QLabel("Załadowane do pamięci (VRAM):"))
        self.lista_zaladowane = QListWidget()
        self.lista_zaladowane.setMaximumHeight(90)  # WHY: to tylko podgląd, nie ma zajmować pół okna
        layout.addWidget(self.lista_zaladowane)

        # Sekcja pobierania
        layout.addWidget(QLabel("Pobierz nowy model:"))
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
        layout.addLayout(pasek_pull)

        # WHAT: link do pełnej listy modeli na stronie Ollamy.
        # WHY:  powyższa lista to tylko garść popularnych modeli - reszta jest
        #       na ollama.com/library, skąd można skopiować dowolną nazwę.
        lbl_link = QLabel(
            'Więcej modeli: <a href="https://ollama.com/library">ollama.com/library</a>'
        )
        lbl_link.setOpenExternalLinks(True)
        layout.addWidget(lbl_link)

        # Model zdalny (RemoteHost) - proxy do np. BC-250 pod jedną lokalną Ollamą
        pasek_remote = QHBoxLayout()
        pasek_remote.addWidget(QLabel("Model zdalny (np. z BC-250):"))
        pasek_remote.addStretch(1)
        self.btn_dodaj_remote = QPushButton("Dodaj model zdalny...")
        self.btn_dodaj_remote.clicked.connect(self.dodaj_model_zdalny_dialog)
        pasek_remote.addWidget(self.btn_dodaj_remote)
        layout.addLayout(pasek_remote)

        self.pasek_postepu = QProgressBar()
        self.pasek_postepu.setVisible(False)
        layout.addWidget(self.pasek_postepu)

        # Log zdarzeń
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)  # WHY: nie rośnij w nieskończoność
        layout.addWidget(self.log)

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
        if not stan["zainstalowana"]:
            # WHY: bez binarki 'ollama' sterowanie usługą nie ma sensu - chowamy
            #      Start/Stop/autostart i pokazujemy tylko przycisk instalacji.
            self.lbl_status.setText("Ollama nie jest zainstalowana \u2717")
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
            if active:
                self.lbl_status.setText("działa \u2713")
            elif stan["api"]:
                # WHY: API odpowiada, choć systemd nie widzi usługi jako active -
                #      typowe gdy Ollama chodzi ręcznie ('ollama serve') lub zdalnie.
                self.lbl_status.setText("API odpowiada (poza usługą systemd)")
            else:
                self.lbl_status.setText("zatrzymana \u2717")

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
