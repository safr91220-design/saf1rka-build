import psutil
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget
from . import monitor, shell
from .config import Config
from .engine import BoostEngine
from .game_detector import GameDetector
from .logger import log

WARN = ("Во время отключения Explorer исчезнут панель задач, рабочий стол и Проводник.\n\n"
        "Запущенная игра продолжит работать. Alt+Tab может работать не на всех версиях Windows.\n"
        "Вернуть рабочий стол: RESTORE DESKTOP, выход из игры или программы, либо Ctrl+Alt+Shift+R.\n\n"
        "Продолжить?")

STYLE = """
QWidget#menu{background:#000000;border:1px solid #2a2b2f}
QLabel{background:transparent;color:#ffffff;font-family:'Segoe UI'}
QLabel#title{font-size:18px;font-weight:600}
QLabel#small{color:#8b8f99;font-size:12px}
QPushButton{background:#141414;color:#ffffff;border:1px solid #232428;border-radius:6px;font-family:'Segoe UI';font-size:15px}
QPushButton:hover{background:#1f2023}
QPushButton:disabled{color:#5b6372}
QPushButton[on="true"]{border:1px solid #19e6a7;color:#19e6a7}
QPushButton[danger="true"]{border:1px solid #ff5c6c;color:#ff5c6c}
QPushButton#close{background:transparent;border:none;font-size:15px;color:#e6e6e6}
QPushButton#close:hover{color:#ff5c6c}
QMessageBox{background:#0a0a0a}
"""

class MenuWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("menu"); self.setWindowTitle("saf1rka_boost")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)   # сплошной чёрный фон
        self.setFixedSize(280, 370); self.setStyleSheet(STYLE)
        self.cfg = Config(); self.engine = BoostEngine(self.cfg); self.detector = GameDetector(self.cfg)
        self._drag, self._prompted, self._last, self._n = None, None, None, 0
        monitor.cpu()

        title = QLabel("saf1rka_boost"); title.setObjectName("title"); title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        x = QPushButton("X"); x.setObjectName("close"); x.setFixedSize(26, 26); x.clicked.connect(self.close)
        top = QHBoxLayout(); top.addSpacing(26); top.addWidget(title, 1); top.addWidget(x)
        self.btn_pm = QPushButton("Performance Mode"); self.btn_pm.clicked.connect(self.toggle_pm)
        self.btn_shell = QPushButton("Full Desktop Suspend"); self.btn_shell.clicked.connect(self.suspend_shell)
        self.btn_restore = QPushButton("RESTORE DESKTOP"); self.btn_restore.clicked.connect(self.restore_all)
        for b in (self.btn_pm, self.btn_shell, self.btn_restore):
            b.setFixedHeight(48)
        self.shell_lbl = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.stats = QLabel(alignment=Qt.AlignmentFlag.AlignCenter); self.stats.setObjectName("small")
        hint = QLabel("Аварийное восстановление: Ctrl+Alt+Shift+R", alignment=Qt.AlignmentFlag.AlignCenter); hint.setObjectName("small")
        root = QVBoxLayout(self); root.setContentsMargins(22, 12, 22, 16); root.setSpacing(12)
        root.addLayout(top)
        for w in (self.btn_pm, self.btn_shell, self.btn_restore, self.shell_lbl, self.stats, hint):
            root.addWidget(w)
        root.addStretch()

        self.gpu = monitor.GpuPoller(); self.gpu.start()
        self.timer = QTimer(self); self.timer.timeout.connect(self._tick); self.timer.start(1000)
        self._state(); log.info("saf1rka_boost: меню запущено")

    @staticmethod
    def _prop(b, k, v):
        b.setProperty(k, v); b.style().unpolish(b); b.style().polish(b)

    def _state(self):
        pm, sh, st = self.engine.enabled, self.engine.shell_suspended, shell.state()
        if (pm, sh, st) == self._last:
            return
        self._last = (pm, sh, st)
        self.btn_pm.setText("Performance Mode  •  ON" if pm else "Performance Mode"); self._prop(self.btn_pm, "on", pm)
        self.btn_shell.setText("Full Desktop Suspend  •  ON" if sh else "Full Desktop Suspend")
        self.btn_shell.setEnabled(not sh); self._prop(self.btn_shell, "danger", sh)
        self.btn_restore.setEnabled(pm or sh or st == "SUSPENDED")
        self.shell_lbl.setText(f"Explorer: {st}")
        self.shell_lbl.setStyleSheet("font-size:14px;font-weight:600;color:%s" % ("#19e6a7" if st == "RUNNING" else "#ff5c6c"))

    def toggle_pm(self):
        self.engine.disable(restore_shell=False) if self.engine.enabled else self.engine.enable()
        self._state()

    def suspend_shell(self):
        B = QMessageBox.StandardButton
        if QMessageBox.warning(self, "saf1rka_boost", WARN, B.Yes | B.No, B.No) != B.Yes:
            return
        if not self.engine.shell_suspend():
            QMessageBox.information(self, "saf1rka_boost", "Не удалось отключить Explorer (подробности в логе).")
        self._state()

    def restore_all(self):
        self.engine.disable(); self._state()

    def _tick(self):
        self._n += 1
        try:
            self.engine.shell_watch(); self._state()
            c, (pct, _, _), g = monitor.cpu(), monitor.ram(), self.gpu.value
            self.stats.setText(f"CPU {c:.0f}%   RAM {pct:.0f}%   GPU " + (f"{g:.0f}%" if g is not None else "н/д"))
            if self._n % 2 == 0:
                self._check_game()
        except Exception:
            log.exception("Ошибка цикла")

    def _check_game(self):
        g = self.engine.game
        if g:
            try:
                alive = g.proc.is_running()
            except psutil.Error:
                alive = False
            if not alive:
                log.info("Игра закрыта: %s — восстанавливаю всё", g.name)
                self.engine.game = None; self._prompted = None
                if self.engine.enabled or self.engine.shell_suspended:
                    self.engine.disable(); self._state()
            return
        f = self.detector.find()
        if not f:
            return
        self.engine.attach_game(f); log.info("Обнаружена игра: %s (%s)", f.name, f.source)
        if self.engine.enabled:
            return
        if self.cfg["auto_enable"]:
            self.engine.enable(); self._state()
        elif self.cfg["ask_on_game"] and self._prompted != f.pid:
            self._prompted = f.pid
            if QMessageBox.question(self, "saf1rka_boost", f"Обнаружена игра {f.name}.\nВключить Performance Mode?") == QMessageBox.StandardButton.Yes:
                self.engine.enable(); self._state()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def closeEvent(self, e):
        self.timer.stop(); self.gpu.stop(); self.engine.disable(); log.info("saf1rka_boost закрыт"); e.accept()
