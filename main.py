#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Popup Browser — всплывающее окно-браузер на движке Chromium (Qt WebEngine).

Возможности:
  • Окно-попап поверх всех окон (frameless, всегда сверху, без панели задач)
  • Режим блокировки: курсор и клики мыши проходят сквозь окно насквозь
  • Блокировка/разблокировка через трей (ПКМ по значку в трее)
  • Адресная строка и кнопки управления появляются только при наведении мыши
  • Постоянный кеш HTTP / cookie / localStorage между запусками
  • Аппаратное ускорение GPU — плавный скролл, анимации и видео без лагов
    (режим максимальной экономии ОЗУ без GPU: запуск с PB_NO_GPU=1)
  • Экономия ОЗУ: 1 renderer-процесс, кеш ограничен
  • ✂ «Вырезать элемент» — клик по любому блоку страницы скрывает его,
    правило сохраняется для сайта и автоматически применяется при загрузке
  • ↩ «Вернуть вырезанное» — любой скрытый ✂-элемент можно вернуть:
    подменю в ПКМ по странице, в меню трея и кнопка ↩ на панели
  • </> «Редактировать HTML» — правка исходного кода страницы вручную

Управление:
  Наведите мышь на окно    — панель: адрес, ↻ обновить, ✂ вырезать,
                             ↩ вернуть, </> HTML, 🔒 блок
  ПКМ по странице          — меню (обновить, ✂ вырезать, ↩ вернуть вырезанное…)
  ПКМ по значку в трее     — меню (разблокировать, обновить, скрыть, кеш, выход)
  ЛКМ по значку в трее     — показать / скрыть окно
  ⠿ на панели или Ctrl+ЛКМ — перетащить окно
  ◣ в правом нижнем углу   — изменить размер окна (когда разблокировано)
"""

import ctypes
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote

# ─────────────────────────────────── Пути ────────────────────────────────────
if getattr(sys, "frozen", False):                      # режим собранного билда
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
RULES_FILE = DATA_DIR / "hidden_rules.json"
STATE_FILE = DATA_DIR / "state.json"

# ────────────────── Настройки Chromium: скорость и память ────────────────────
# Аппаратное ускорение GPU включено по умолчанию: скролл, анимации и видео
# рендерятся видеокартой, без лагов. Память экономят: один renderer-процесс,
# без фоновых сервисов, кеш ограничен.
# Режим максимальной экономии ОЗУ (без GPU): запустите с PB_NO_GPU=1
#   (Windows:  set PB_NO_GPU=1 , затем run_windows.bat)
# или добавьте "--disable-gpu " в начало строки ниже.
CHROMIUM_FLAGS = (
    "--disable-dev-tools "
    "--disable-features=Translate,BackForwardCache,MediaRouter,OptimizationHints "
    "--disable-back-forward-cache "
    "--disable-component-update "
    "--renderer-process-limit=1 "
    "--process-per-site "
    "--autoplay-policy=no-user-gesture-required"
)
if os.environ.get("PB_NO_GPU"):
    CHROMIUM_FLAGS = "--disable-gpu --disable-software-rasterizer " + CHROMIUM_FLAGS
# Ещё меньше памяти (−40…60 МБ, чуть менее стабильно): добавьте в строку выше
# флаг "--single-process".

HTTP_CACHE_MB = 100          # предел HTTP-кеша на диске
DEFAULT_SIZE = (460, 640)

if hasattr(os, "geteuid") and os.geteuid() == 0:
    CHROMIUM_FLAGS += " --no-sandbox"   # только для запуска от root (контейнер/CI)
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", CHROMIUM_FLAGS)

from PySide6.QtCore import Qt, QEvent, QRect, QTimer, QUrl, QObject, QPoint  # noqa: E402
from PySide6.QtGui import (QAction, QColor, QCursor, QFont, QIcon,          # noqa: E402
                           QPainter, QPixmap)
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout,   # noqa: E402
                               QLabel, QLineEdit, QMainWindow, QMenu,
                               QPlainTextEdit, QPushButton, QSystemTrayIcon,
                               QToolButton, QVBoxLayout, QWidget)
from PySide6.QtWebEngineWidgets import QWebEngineView                        # noqa: E402
from PySide6.QtWebEngineCore import (QWebEnginePage, QWebEngineProfile,      # noqa: E402
                                     QWebEngineScript, QWebEngineSettings)

# ───────────────────────────── JS: режим ✂ «вырезать» ────────────────────────
# Внедряется в каждую страницу. window.__picker.activate() включает режим:
# подсветка блока под курсором, клик скрывает элемент и запоминает селектор.
PICKER_JS = r"""
(function () {
  if (window.__picker) return;
  var state = { active: false, picks: [], box: null };

  function ensureBox() {
    if (state.box) return;
    var b = document.createElement('div');
    b.setAttribute('data-popup-picker', '1');
    b.style.cssText = 'position:absolute;pointer-events:none;z-index:2147483647;' +
        'border:2px solid #ff453a;background:rgba(255,69,58,.18);' +
        'box-sizing:border-box;display:none;';
    document.documentElement.appendChild(b);
    state.box = b;
  }
  function onMove(e) {
    if (!state.active) return;
    ensureBox();
    var el = e.target;
    if (!el || el === state.box) return;
    var r = el.getBoundingClientRect();
    state.box.style.display = 'block';
    state.box.style.left = (r.left + window.scrollX) + 'px';
    state.box.style.top = (r.top + window.scrollY) + 'px';
    state.box.style.width = r.width + 'px';
    state.box.style.height = r.height + 'px';
  }
  function onClick(e) {
    if (!state.active) return;
    e.preventDefault();
    e.stopPropagation();
    var el = e.target;
    if (!el || el === state.box) return;
    window.__pbHide(el);
    state.picks.push(cssPath(el));
  }
  function onKey(e) {
    if (state.active && (e.key === 'Escape')) setActive(false);
  }
  function cssPath(el) {
    var parts = [];
    while (el && el.nodeType === 1 && parts.length < 6) {
      var sel = el.nodeName.toLowerCase();
      if (el.id) { parts.unshift(sel + '#' + CSS.escape(el.id)); break; }
      var p = el.parentNode;
      if (p && p.children) {
        var sib = [];
        for (var i = 0; i < p.children.length; i++)
          if (p.children[i].nodeName === el.nodeName) sib.push(p.children[i]);
        if (sib.length > 1) sel += ':nth-of-type(' + (sib.indexOf(el) + 1) + ')';
      }
      parts.unshift(sel);
      el = el.parentElement;
    }
    return parts.join(' > ');
  }
  // Скрытие с пометкой data-pb-hidden: позволяет потом вернуть элемент (↩),
  // восстановив прежнее значение inline-display, если оно было.
  window.__pbHide = function (el) {
    if (!el || el.getAttribute('data-pb-hidden')) return;
    el.setAttribute('data-pb-hidden', '1');
    var prev = el.style.getPropertyValue('display');
    if (prev) el.setAttribute('data-pb-prev', prev);
    el.style.setProperty('display', 'none', 'important');
  };
  window.__pbUnhide = function (el) {
    if (!el || !el.getAttribute('data-pb-hidden')) return;
    var prev = el.getAttribute('data-pb-prev');
    if (prev) el.style.setProperty('display', prev);
    else el.style.removeProperty('display');
    el.removeAttribute('data-pb-hidden');
    el.removeAttribute('data-pb-prev');
  };
  function setActive(on) {
    state.active = on;
    if (state.box) state.box.style.display = 'none';
    var evs = [['mousemove', onMove], ['click', onClick],
               ['mousedown', onClick], ['keydown', onKey]];
    for (var i = 0; i < evs.length; i++) {
      if (on) document.addEventListener(evs[i][0], evs[i][1], true);
      else    document.removeEventListener(evs[i][0], evs[i][1], true);
    }
  }
  window.__picker = {
    get active() { return state.active; },
    activate:   function () { ensureBox(); setActive(true); },
    deactivate: function () { setActive(false); },
    toggle:     function () { state.active ? setActive(false) : setActive(true); },
    collect:    function () { var p = state.picks.slice(); state.picks.length = 0; return p; }
  };
})();
"""

POLL_JS = (
    "(function(){if(!window.__picker){return JSON.stringify({active:false,picks:[]});}"
    "return JSON.stringify({active:window.__picker.active,"
    "picks:window.__picker.collect()});})()"
)


# Хелперы скрытия/возврата для runJavaScript-сниппетов: если PICKER_JS уже
# внедрён — используются его window.__pbHide/__pbUnhide, иначе локальные копии.
_RULE_HELPERS = (
    "var H=window.__pbHide||function(el){"
    "if(!el||el.getAttribute('data-pb-hidden'))return;"
    "el.setAttribute('data-pb-hidden','1');"
    "var p=el.style.getPropertyValue('display');"
    "if(p)el.setAttribute('data-pb-prev',p);"
    "el.style.setProperty('display','none','important');};"
    "var U=window.__pbUnhide||function(el){"
    "if(!el||!el.getAttribute('data-pb-hidden'))return;"
    "var p=el.getAttribute('data-pb-prev');"
    "if(p)el.style.setProperty('display',p);"
    "else el.style.removeProperty('display');"
    "el.removeAttribute('data-pb-hidden');"
    "el.removeAttribute('data-pb-prev');};"
)


def set_rules_js(old_rules, new_rules):
    """JS: вернуть элементы по old_rules и скрыть по new_rules (итог)."""
    return (
        "(function(){" + _RULE_HELPERS +
        "function each(s,fn){try{document.querySelectorAll(s).forEach(fn);}catch(e){}}"
        "var old=" + json.dumps(list(old_rules)) + ";"
        "var neu=" + json.dumps(list(new_rules)) + ";"
        "old.forEach(function(s){each(s,U);});"
        "neu.forEach(function(s){each(s,H);});"
        "})();"
    )


def apply_rules_js(rules):
    """JS: скрыть элементы по CSS-селекторам (с пометкой — можно вернуть ↩)."""
    return set_rules_js([], rules)


def _dbg(*args):
    """Отладочная печать (включается переменной окружения PB_DEBUG=1)."""
    if os.environ.get("PB_DEBUG"):
        print("[PB]", *args, flush=True)


def normalize_url(text):
    t = (text or "").strip()
    if not t:
        return None
    if "://" in t:
        return t
    if t.startswith("localhost") or ("." in t and " " not in t):
        return "https://" + t
    return "https://duckduckgo.com/?q=" + quote(t)


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text("utf-8"))
    except Exception:
        return default


def save_json(path, obj):
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=1), "utf-8")
    except Exception:
        pass


def make_app_icon():
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(22, 25, 31))
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    p.setPen(QColor(102, 204, 255))
    f = QFont()
    f.setBold(True)
    f.setPixelSize(36)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, "P")
    p.end()
    return QIcon(pm)


TOOLBAR_CSS = """
QFrame#toolbar { background: rgba(18,20,24,238); border: 1px solid #3a3f4b;
                 border-radius: 8px; }
QFrame#toolbar QLabel { color: #cfd6e4; background: transparent; }
QFrame#toolbar QLineEdit {
    background: rgba(40,44,54,238); color: #eef1f7; border: 1px solid #3a3f4b;
    border-radius: 5px; padding: 3px 8px; font-size: 12px;
    selection-background-color: #3b82f6; }
QFrame#toolbar QToolButton {
    background: transparent; color: #cfd6e4; border: none;
    border-radius: 5px; padding: 3px 7px; font-size: 13px; }
QFrame#toolbar QToolButton:hover  { background: #2c3340; }
QFrame#toolbar QToolButton:pressed{ background: #3b4252; }
QFrame#toolbar QToolButton:checked{ background: #5a2030; color: #ff9db0; }
"""


class DragGrip(QLabel):
    """«⠿» — перетаскивание окна."""

    def __init__(self, parent=None):
        super().__init__("⠿", parent)
        self.setToolTip("Перетащите, чтобы переместить окно")
        self.setCursor(Qt.SizeAllCursor)
        self._off = QPoint()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._off = e.globalPosition().toPoint() - self.window().frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self.window().move(e.globalPosition().toPoint() - self._off)


class ResizeHandle(QLabel):
    """«◣» — изменение размера окна."""

    def __init__(self, parent=None):
        super().__init__("◢", parent)
        self.setToolTip("Потяните, чтобы изменить размер окна")
        self.setCursor(Qt.SizeFDiagCursor)
        self._press = None
        self._start = None

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.globalPosition().toPoint()
            self._start = self.window().size()

    def mouseMoveEvent(self, e):
        if self._press is not None and (e.buttons() & Qt.LeftButton):
            d = e.globalPosition().toPoint() - self._press
            self.window().resize(max(220, self._start.width() + d.x()),
                                 max(150, self._start.height() + d.y()))

    def mouseReleaseEvent(self, e):
        self._press = None


class WebView(QWebEngineView):
    """Контекстное меню (ПКМ) обрабатывается главным окном;
    ссылки target=_blank открываются в том же окне."""

    def createWindow(self, wtype):
        return self


class DragFilter(QObject):
    """Ctrl + перетаскивание по странице = перемещение окна.

    ВАЖНО: фильтр ставится ТОЛЬКО на виджет рендера страницы (focusProxy),
    а не на всё приложение: глобальные фильтры QApplication вызываются из
    потоков WebEngine и приводят к падению (известная проблема PySide6)."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self._installed = set()
        self.dragging = False
        self.offset = QPoint()

    def install(self):
        targets = [self.win.view]
        fp = self.win.view.focusProxy()
        if fp:
            targets.append(fp)
        for t in targets:
            if t is not None and id(t) not in self._installed:
                t.installEventFilter(self)
                self._installed.add(id(t))

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t not in (QEvent.MouseButtonPress, QEvent.MouseMove,
                     QEvent.MouseButtonRelease):
            return False
        if self.win._locked:
            return False
        if t == QEvent.MouseButtonPress:
            if (ev.button() == Qt.LeftButton
                    and (ev.modifiers() & Qt.ControlModifier)):
                self.dragging = True
                self.offset = (ev.globalPosition().toPoint()
                               - self.win.frameGeometry().topLeft())
                return True
        elif t == QEvent.MouseMove and self.dragging:
            self.win.move(ev.globalPosition().toPoint() - self.offset)
            return True
        elif t == QEvent.MouseButtonRelease and self.dragging:
            self.dragging = False
            return True
        return False


class PopupBrowser(QMainWindow):
    def __init__(self, start_url=None):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setWindowTitle("Popup Browser")
        self.setWindowIcon(make_app_icon())
        self.setMinimumSize(220, 150)
        self.resize(*DEFAULT_SIZE)

        self._locked = False
        self._picking = False
        self._quitting = False
        self._hint_until = 0.0
        self.rules = load_json(RULES_FILE, {})
        self.state = load_json(STATE_FILE, {})

        # ── Профиль Chromium: постоянный кеш и cookie ──
        self.profile = QWebEngineProfile("popup-browser", self)
        self.profile.setPersistentStoragePath(str(DATA_DIR / "profile"))
        self.profile.setCachePath(str(DATA_DIR / "cache"))
        try:
            # Qt < 6.9: PersistentHttpCache, Qt >= 6.9: DiskHttpCache
            cache_enum = getattr(QWebEngineProfile.HttpCacheType, "DiskHttpCache",
                                 QWebEngineProfile.HttpCacheType.PersistentHttpCache)
            self.profile.setHttpCacheType(cache_enum)
        except Exception:
            pass
        self.profile.setHttpCacheMaximumSize(HTTP_CACHE_MB * 1024 * 1024)
        try:
            cookies = getattr(QWebEngineProfile.PersistentCookiesPolicy,
                              "ForcePersistentCookies",
                              QWebEngineProfile.ForcePersistentCookies)
            self.profile.setPersistentCookiesPolicy(cookies)
        except Exception:
            self.profile.setPersistentCookiesPolicy(
                QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        self.profile.setSpellCheckEnabled(False)
        try:
            self.profile.setDownloadDirectory(str(DATA_DIR / "downloads"))
        except Exception:
            pass

        # ── Страница + настройки экономии памяти ──
        self.page = QWebEnginePage(self.profile, self)
        s = self.page.settings()
        for name, val in (
            ("PluginsEnabled", False),
            ("PdfViewerEnabled", False),
            ("ScrollAnimatorEnabled", False),
            ("DnsPrefetchEnabled", False),
            ("HyperlinkAuditingEnabled", False),
            ("PrintElementBackgrounds", False),
            ("LocalStorageEnabled", True),
            ("ErrorPageEnabled", True),
        ):
            attr = getattr(QWebEngineSettings, name, None)
            if attr is not None:
                s.setAttribute(attr, val)

        # Скрипт-пикер внедряется в каждую загрузку страницы
        scr = QWebEngineScript()
        scr.setName("popup-picker")
        scr.setSourceCode(PICKER_JS)
        scr.setInjectionPoint(QWebEngineScript.DocumentCreation)
        scr.setWorldId(QWebEngineScript.MainWorld)
        scr.setRunsOnSubFrames(False)
        self.page.scripts().insert(scr)

        # ── UI ──
        central = QWidget(self)
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        self.view = WebView()
        self.view.setPage(self.page)
        self.view.setContextMenuPolicy(Qt.DefaultContextMenu)
        self.view.contextMenuEvent = self._view_context_menu
        lay.addWidget(self.view)
        self.setCentralWidget(central)

        self._build_toolbar()
        self.resize_handle = ResizeHandle(self)
        self.resize_handle.hide()

        self.page.urlChanged.connect(self._on_url_changed)
        self.page.loadFinished.connect(self._on_load_finished)
        self.page.titleChanged.connect(self.setWindowTitle)

        # ── Трей ──
        self.tray = QSystemTrayIcon(make_app_icon(), self)
        self.tray.setToolTip("Popup Browser — ПКМ: меню управления")
        self._build_tray_menu()
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

        # ── Таймеры ──
        self.hover_timer = QTimer(self, interval=120, timeout=self._check_hover)
        self.hover_timer.start()
        self._pick_timer = QTimer(self, interval=300, timeout=self._poll_picks)

        self.filter = DragFilter(self)
        self.filter.install()
        QTimer.singleShot(0, self.filter.install)   # focusProxy появляется после загрузки

        # ── Восстановление состояния ──
        pos = self.state.get("pos")
        size = self.state.get("size")
        if size and len(size) == 2:
            self.resize(int(size[0]), int(size[1]))
        else:
            g = QApplication.primaryScreen().availableGeometry()
            self.move(g.right() - self.width() - 60, max(60, g.top() + 80))
        if pos and len(pos) == 2:
            self.move(int(pos[0]), int(pos[1]))
            self._restored_pos = True
        else:
            self._restored_pos = False

        url = start_url or self.state.get("url")
        url = normalize_url(url) if url else None
        self.page.load(QUrl(url or "about:blank"))

        if not (self.state.get("url") or start_url):
            # первый запуск — подсказка
            self._hint_until = time.time() + 6

        self.set_locked(bool(self.state.get("locked", False)), save=False)
        self.show()

    # ─────────────────────────────── Панель ───────────────────────────────
    def _build_toolbar(self):
        self.toolbar = QFrame(self)
        self.toolbar.setObjectName("toolbar")
        self.toolbar.setStyleSheet(TOOLBAR_CSS)
        self.toolbar.setFixedHeight(40)
        self.toolbar.hide()

        row = QHBoxLayout(self.toolbar)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(4)

        self.grip = DragGrip()
        row.addWidget(self.grip)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("Вставьте ссылку и нажмите Enter…")
        self.url_edit.returnPressed.connect(self._go)
        row.addWidget(self.url_edit, 1)

        self.btn_reload = self._mk_btn("⟳", "Обновить страницу (Ctrl+R тоже работает в меню ПКМ)",
                                       self.reload)
        self.btn_pick = self._mk_btn("✂", "Режим вырезания: клик по блоку скрывает его "
                                         "(Esc — выход). Правила сохраняются для сайта.",
                                     self.toggle_picker, checkable=True)
        self.btn_restore = self._mk_btn("↩", "Вернуть вырезанные (✂) элементы этого сайта",
                                        self.show_restore_menu)
        self.btn_html = self._mk_btn("</>", "Редактировать HTML страницы", self.edit_html)
        self.btn_lock = self._mk_btn("🔒", "Заблокировать окно: клики проходят насквозь.\n"
                                         "Разблокировка — ПКМ по значку в трее",
                                     lambda: self.set_locked(True))
        for b in (self.btn_reload, self.btn_pick, self.btn_restore,
                  self.btn_html, self.btn_lock):
            row.addWidget(b)

        central = self.centralWidget()
        self.toolbar.setParent(central)
        self.toolbar.raise_()

    def _mk_btn(self, text, tip, cb, checkable=False):
        b = QToolButton(self.toolbar)
        b.setText(text)
        b.setToolTip(tip)
        b.setCheckable(checkable)
        b.clicked.connect(lambda *_, f=cb: f())
        return b

    def _place_overlays(self):
        w, h = self.width(), self.height()
        self.toolbar.setGeometry(6, 6, w - 12, 40)
        self.resize_handle.setGeometry(w - 18, h - 18, 14, 14)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_overlays()

    def showEvent(self, e):
        super().showEvent(e)
        self._place_overlays()

    def _go(self):
        u = normalize_url(self.url_edit.text())
        if u:
            self.page.load(QUrl(u))

    def reload(self):
        self.page.triggerAction(QWebEnginePage.Reload)

    # ─────────────────── Наведение: панель только при hover ───────────────
    def _check_hover(self):
        if not self.isVisible():
            if self.toolbar.isVisible():
                self.toolbar.hide()
            return
        if self._locked:
            if self.toolbar.isVisible():
                self.toolbar.hide()
            return
        gp = self.mapToGlobal(QPoint(0, 0))
        inside = QRect(gp, self.size()).contains(QCursor.pos())
        inside = inside or (time.time() < self._hint_until)
        self.toolbar.setVisible(inside)
        self.resize_handle.setVisible(inside)

    # ────────────────────── Блокировка (click-through) ────────────────────
    def set_locked(self, locked, save=True):
        self._locked = locked
        # Qt-уровень: все события мыши игнорируются (атрибут наследуется детьми)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, locked)
        self.stop_picker()

        if sys.platform == "win32":
            # Нативный click-through: клики уходят приложению ПОД окном
            try:
                GWL_EXSTYLE = -20
                WS_EX_LAYERED = 0x00080000
                WS_EX_TRANSPARENT = 0x00000020
                user32 = ctypes.windll.user32
                hwnd = int(self.winId())
                style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                if locked:
                    style |= WS_EX_LAYERED | WS_EX_TRANSPARENT
                else:
                    style &= ~WS_EX_LAYERED & ~WS_EX_TRANSPARENT
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
            except Exception:
                pass
        else:
            vis = self.isVisible()
            self.setWindowFlag(Qt.WindowTransparentForInput, locked)
            if vis or locked is False:
                self.show()

        if locked:
            self.toolbar.hide()
            self.resize_handle.hide()

        self._sync_tray()
        if save:
            save_json(STATE_FILE, self._collect_state())

    def _collect_state(self):
        return {
            "pos": [self.x(), self.y()],
            "size": [self.width(), self.height()],
            "locked": self._locked,
            "url": self.page.url().toString(),
        }

    def save_state(self):
        save_json(STATE_FILE, self._collect_state())

    # ──────────────────────────────── Трей ────────────────────────────────
    def _build_tray_menu(self):
        self.tray_menu = QMenu()
        self.act_lock = self._act("🔒  Заблокировать (клики насквозь)", lambda: self.set_locked(True))
        self.act_unlock = self._act("🔓  Разблокировать окно", lambda: self.set_locked(False))
        self.act_reload = self._act("↻  Обновить страницу", self.reload)
        self.act_pick = self._act("", self.toggle_picker)
        self.restore_menu = QMenu("↩  Вернуть вырезанное", self.tray_menu)
        self.tray_menu.aboutToShow.connect(
            lambda: self._fill_restore_menu(self.restore_menu))
        self.act_html = self._act("</>  Редактировать HTML страницы…", self.edit_html)
        self.act_show = self._act("👁  Показать / скрыть окно", self.toggle_visible)
        self.act_cache = self._act("🧹  Очистить кеш", self.clear_cache)
        self.act_quit = self._act("🚪  Выход (освободить память)", self.quit_app)

        for a in (self.act_lock, self.act_unlock, self.act_reload, self.act_pick,
                  self.restore_menu.menuAction(), self.act_html, self.act_show,
                  self.act_cache, self.act_quit):
            self.tray_menu.addAction(a)
        self.tray_menu.insertSeparator(self.act_show)

        self.tray.setContextMenu(self.tray_menu)
        self._sync_tray()

    def _act(self, text, cb):
        a = QAction(text, self)
        a.triggered.connect(lambda *_, f=cb: f())
        return a

    def _sync_tray(self):
        self.act_lock.setVisible(not self._locked)
        self.act_unlock.setVisible(self._locked)
        self.act_pick.setEnabled(not self._locked)
        self.act_html.setEnabled(not self._locked)
        self.act_pick.setText("✂  Остановить вырезание" if self._picking
                              else "✂  Вырезать элемент (клик по блоку)")

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.toggle_visible()

    def toggle_visible(self):
        if self.isVisible():
            self.hide()
            self.save_state()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def clear_cache(self):
        self.profile.clearHttpCache()
        self.tray.showMessage("Popup Browser", "Кеш очищен",
                              QSystemTrayIcon.Information, 1500)

    def quit_app(self):
        self._quitting = True
        self.save_state()
        QApplication.instance().quit()

    def closeEvent(self, e):
        # Alt+F4 прячет окно в трей (сессия и кеш сохраняются); выход — через трей
        self.save_state()
        if self._quitting:
            e.accept()
        else:
            e.ignore()
            self.hide()

    # ───────────────────── Вырезание элементов (режим ✂) ──────────────────
    def toggle_picker(self):
        if self._locked:
            return
        self._picking = not self._picking
        js = "window.__picker && window.__picker.%s()" % (
            "activate" if self._picking else "deactivate")
        _dbg("toggle_picker ->", js)
        self.page.runJavaScript(js)
        _dbg("toggle_picker runJavaScript done")
        self.btn_pick.setChecked(self._picking)
        if self._picking:
            self._pick_timer.start()
        else:
            self._pick_timer.stop()
        self._sync_tray()

    def stop_picker(self):
        if self._picking:
            self._picking = False
            self._pick_timer.stop()
            self.btn_pick.setChecked(False)
            try:
                self.page.runJavaScript("window.__picker && window.__picker.deactivate()")
            except Exception:
                pass
        self._sync_tray()

    def _poll_picks(self):
        try:
            _dbg("_poll_picks call")
            self.page.runJavaScript(POLL_JS, self._on_poll)
            _dbg("_poll_picks sent")
        except Exception as e:
            _dbg("_poll_picks EXC", repr(e))
            self._pick_timer.stop()

    def _on_poll(self, res):
        _dbg("_on_poll got", type(res).__name__, str(res)[:80])
        try:
            data = json.loads(res) if isinstance(res, str) else (res or {})
        except Exception as e:
            _dbg("_on_poll parse EXC", repr(e))
            return
        host = self.page.url().host()
        for sel in (data.get("picks") or []):
            if sel and self.add_rule(host, sel):
                self.tray.showMessage("✂ Элемент вырезан (сохранено для сайта)",
                                      sel[:100], QSystemTrayIcon.Information, 1200)
        if not data.get("active") and self._picking:
            self._picking = False
            self._pick_timer.stop()
            self.btn_pick.setChecked(False)
            self._sync_tray()

    def add_rule(self, host, selector):
        host = host or "file"          # страницы file:// не имеют хоста
        if not selector:
            return False
        lst = self.rules.setdefault(host, [])
        if selector in lst:
            return False
        lst.append(selector)
        save_json(RULES_FILE, self.rules)
        return True

    def remove_rule(self, host, selector):
        """↩ Убрать правило ✂ — вырезанные элементы снова показываются."""
        host = host or "file"
        lst = self.rules.get(host) or []
        if selector not in lst:
            return False
        lst.remove(selector)
        if lst:
            self.rules[host] = lst
        else:
            self.rules.pop(host, None)
        save_json(RULES_FILE, self.rules)
        self.page.runJavaScript(set_rules_js([selector], lst))
        _dbg("remove_rule", host, selector)
        return True

    def restore_all_rules(self, host=None):
        """↩ Вернуть все вырезанные элементы сайта. Возвращает число правил."""
        host = host or self._current_host()
        old = self.rules.pop(host, None) or []
        if not old:
            return 0
        save_json(RULES_FILE, self.rules)
        self.page.runJavaScript(set_rules_js(old, []))
        _dbg("restore_all_rules", host, len(old))
        return len(old)

    def _current_host(self):
        return self.page.url().host() or "file"

    def _fill_restore_menu(self, menu):
        """Заполнить меню «↩ Вернуть вырезанное» правилами текущего сайта."""
        menu.clear()
        rules = list(self.rules.get(self._current_host()) or [])
        if not rules:
            a = menu.addAction("На этом сайте ничего не вырезано")
            a.setEnabled(False)
            return
        head = menu.addAction("Вырезано на этом сайте: %d" % len(rules))
        head.setEnabled(False)
        for sel in rules:
            act = menu.addAction("↩  " + (sel if len(sel) <= 70 else sel[:67] + "…"))
            act.setToolTip(sel)
            act.triggered.connect(lambda *_, s=sel: self._restore_one(s))
        menu.addSeparator()
        menu.addAction("↩  Вернуть всё (%d)" % len(rules), self._restore_all)

    def _restore_one(self, sel):
        if self.remove_rule(self._current_host(), sel):
            self.tray.showMessage("↩ Элемент возвращён", sel[:100],
                                  QSystemTrayIcon.Information, 1200)

    def _restore_all(self):
        n = self.restore_all_rules()
        if n:
            self.tray.showMessage("↩ Элементы возвращены",
                                  "сброшено правил сайта: %d" % n,
                                  QSystemTrayIcon.Information, 1500)

    def show_restore_menu(self):
        """Кнопка ↩ на панели — список вырезанного раскрывается под кнопкой."""
        if self._locked:
            return
        m = QMenu(self)
        self._fill_restore_menu(m)
        m.exec(self.btn_restore.mapToGlobal(self.btn_restore.rect().bottomLeft()))

    def apply_saved_rules(self):
        host = self.page.url().host() or "file"
        rules = self.rules.get(host) or []
        if rules:
            self.page.runJavaScript(apply_rules_js(rules))

    # ─────────────────────── Редактор HTML (</>) ──────────────────────────
    def edit_html(self):
        if self._locked:
            return
        self.page.toHtml(self._open_html_editor)

    def _open_html_editor(self, html):
        dlg = QDialog(self)
        dlg.setWindowTitle("Редактор HTML страницы")
        dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        dlg.resize(760, 560)
        self._html_dlg = dlg
        lay = QVBoxLayout(dlg)
        hint = QLabel("Правьте исходный код и нажмите «Применить» (изменение живёт до "
                      "перезагрузки страницы). Чтобы скрыть элементы навсегда — "
                      "используйте режим ✂: правила сохраняются для сайта.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        edit = QPlainTextEdit()
        f = QFont("Monospace")
        f.setStyleHint(QFont.Monospace)
        edit.setFont(f)
        edit.setPlainText(html)
        lay.addWidget(edit, 1)
        row = QHBoxLayout()
        b_apply = QPushButton("Применить")
        b_cancel = QPushButton("Отмена")
        b_apply.clicked.connect(lambda: self._apply_html(edit.toPlainText(), dlg))
        b_cancel.clicked.connect(dlg.reject)
        row.addStretch(1)
        row.addWidget(b_cancel)
        row.addWidget(b_apply)
        lay.addLayout(row)
        dlg.exec()

    def _apply_html(self, text, dlg):
        base = self.page.url()
        dlg.accept()
        self.page.setHtml(text, base)

    # ─────────────────── Контекстное меню страницы (ПКМ) ──────────────────
    def _view_context_menu(self, event):
        if self._locked:
            return
        gpos = event.globalPos()
        m = QMenu(self)
        m.addAction("↻  Обновить", self.reload)
        if self.page.history().canGoBack():
            m.addAction("←  Назад", lambda: self.page.triggerAction(QWebEnginePage.Back))
        if self.page.history().canGoForward():
            m.addAction("→  Вперёд", lambda: self.page.triggerAction(QWebEnginePage.Forward))
        m.addSeparator()
        m.addAction("✂  Вырезать элемент (клик по блоку, Esc — выход)", self.toggle_picker)
        rm = m.addMenu("↩  Вернуть вырезанное")
        self._fill_restore_menu(rm)
        m.addAction("</>  Редактировать HTML страницы…", self.edit_html)
        m.addSeparator()
        if self.page.selectedText():
            m.addAction("📋  Копировать выделенное",
                        lambda: self.page.triggerAction(QWebEnginePage.Copy))
        m.addAction("🔒  Заблокировать окно", lambda: self.set_locked(True))
        m.exec(gpos)

    # ─────────────────────────── События страницы ─────────────────────────
    def _on_url_changed(self, url):
        if not self.url_edit.hasFocus():
            self.url_edit.setText(url.toString())
        self.save_state()

    def _on_load_finished(self, ok):
        if not self.url_edit.hasFocus():
            self.url_edit.setText(self.page.url().toString())
        self.apply_saved_rules()
        self.filter.install()                          # на случай нового focusProxy
        QTimer.singleShot(1500, self.apply_saved_rules)   # для поздней разметки


# ───────────────────────────── Самопроверка билда ─────────────────────────────
# Используется автотестом: POPUPBROWSER_AUTOTEST=<файл-результат.json>
# POPUPBROWSER_AUTOTEST_PAGE=<тестовая html-страница>
def _autotest(win, out_path):
    res = {}

    def step1():
        win.page.runJavaScript("document.title", lambda t: res.update(title=t))
        win.toggle_picker()

    def step2():
        win.page.runJavaScript("!!window.__picker && window.__picker.active",
                               lambda r: res.update(picker=bool(r)))
        win.toggle_picker()
        win.add_rule(win.page.url().host(), "#a")
        win.apply_saved_rules()

    def step3():
        def got(r):
            res.update(rule=str(r))
            res["window_flags_topmost"] = bool(win.windowFlags() & Qt.WindowStaysOnTopHint)
            win.set_locked(True)
            res["locked"] = win._locked
            win.set_locked(False)
            host = win.page.url().host() or "file"
            res["rule_removed"] = bool(win.remove_rule(host, "#a"))
            win.page.runJavaScript(
                "getComputedStyle(document.querySelector('#a')).display", got2)

        def got2(r):
            # после ↩ элемент снова видим (display != none)
            res["restored_display"] = str(r)
            res["restored"] = str(r) != "none"
            save_json(out_path, res)
            QApplication.instance().quit()

        win.page.runJavaScript(
            "getComputedStyle(document.querySelector('#a')).display", got)

    win.page.load(QUrl("file://" + os.environ["POPUPBROWSER_AUTOTEST_PAGE"]))
    QTimer.singleShot(6000, step1)
    QTimer.singleShot(8000, step2)
    QTimer.singleShot(10500, step3)
    QTimer.singleShot(20000, QApplication.instance().quit)


def main():
    DATA_DIR.mkdir(exist_ok=True)
    # Общие GL-контексты — корректная работа WebEngine с аппаратным ускорением
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setApplicationName("Popup Browser")
    app.setQuitOnLastWindowClosed(False)

    start_url = None
    for a in sys.argv[1:]:
        if not a.startswith("-"):
            start_url = a
            break

    win = PopupBrowser(start_url)
    app.aboutToQuit.connect(win.save_state)

    autotest_out = os.environ.get("POPUPBROWSER_AUTOTEST")
    if autotest_out:
        _autotest(win, autotest_out)
    else:
        win.tray.showMessage(
            "Popup Browser запущен",
            "ПКМ по значку в трее — управление.\n"
            "Наведите мышь на окно — адресная строка.\n"
            "Ctrl+перетаскивание — переместить окно.",
            QSystemTrayIcon.Information, 5000)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
