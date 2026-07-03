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
import subprocess

import requests  # WHY: czytelniejsze od urllib przy strumieniowaniu /api/pull

from PyQt6.QtCore import QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QListWidget, QProgressBar,
    QPlainTextEdit, QComboBox, QMessageBox, QFrame, QCheckBox,
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
# WHY:  pod Twój stack (Continue) - szybki dostęp bez wpisywania nazw z palca.
POLECANE_MODELE = [
    "qwen2.5-coder:7b",
    "qwen2.5-coder:14b",
    "qwen2.5-coder:1.5b",
    "nomic-embed-text",
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
    wynik = pyqtSignal(dict)  # {'active','enabled','api','models'}

    def __init__(self, client):
        super().__init__()
        self.client = client

    def run(self):
        active = _systemctl_query("is-active") == "active"
        enabled = _systemctl_query("is-enabled") == "enabled"
        api = self.client.api_dziala()
        modele = self.client.list_models() if api else []
        self.wynik.emit({
            "active": active,
            "enabled": enabled,
            "api": api,
            "models": modele,
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
#  Główne okno
# =============================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.client = OllamaClient()
        self.pull_worker = None
        self.refresh_worker = None
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
        pasek_status.addWidget(QLabel("Usługa:"))
        pasek_status.addWidget(self.lbl_status, 1)
        pasek_status.addWidget(self.btn_start)
        pasek_status.addWidget(self.btn_stop)
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

        # Sekcja pobierania
        layout.addWidget(QLabel("Pobierz nowy model:"))
        pasek_pull = QHBoxLayout()
        self.combo_modele = QComboBox()
        self.combo_modele.setEditable(True)  # WHY: pozwól wpisać też dowolną nazwę
        self.combo_modele.addItems(POLECANE_MODELE)
        self.btn_pull = QPushButton("Pobierz")
        self.btn_pull.clicked.connect(self.pobierz_model)
        pasek_pull.addWidget(self.combo_modele, 1)
        pasek_pull.addWidget(self.btn_pull)
        layout.addLayout(pasek_pull)

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

    # --- Odświeżanie stanu ---
    def odswiez(self):
        if self.refresh_worker and self.refresh_worker.isRunning():
            return
        self.refresh_worker = RefreshWorker(self.client)
        self.refresh_worker.wynik.connect(self._po_odswiezeniu)
        self.refresh_worker.start()

    def _po_odswiezeniu(self, stan):
        # WHAT: przełóż stan usługi/API na wygląd okna.
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
