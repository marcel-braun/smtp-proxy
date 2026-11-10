import argparse
import os
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from smtp_proxy.gui.main_window import MainWindow
from smtp_proxy.paths import default_portable_dir


def main(argv=None):
    parser = argparse.ArgumentParser(description="SMTPProxy Qt6-Verwaltung")
    parser.add_argument("--smoke-test", action="store_true",
                        help="Fenster ohne Daten- oder Dienstzugriffe kurz pruefen")
    location = parser.add_mutually_exclusive_group()
    location.add_argument("--data-dir", type=Path, help="Gemeinsames Datenverzeichnis mit app_cli.exe")
    location.add_argument("--service-mode", action="store_true",
                          help="Windows-Dienst verwalten; App-Konfiguration neben der EXE, Laufzeitdaten in ProgramData")
    arguments = parser.parse_args(argv)
    if arguments.service_mode and sys.platform != "win32":
        parser.error("--service-mode ist nur unter Windows verfuegbar.")
    if arguments.smoke_test:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    application = QApplication.instance() or QApplication([sys.argv[0]])
    application.setApplicationName("SMTPProxy")
    application.setApplicationVersion("1.0.0")
    palette = application.palette()
    palette.setColor(QPalette.Window, QColor("#f5f6f5"))
    palette.setColor(QPalette.WindowText, QColor("#202420"))
    palette.setColor(QPalette.Base, QColor("#ffffff"))
    palette.setColor(QPalette.AlternateBase, QColor("#eef1ef"))
    palette.setColor(QPalette.Text, QColor("#202420"))
    palette.setColor(QPalette.Button, QColor("#eef1ef"))
    palette.setColor(QPalette.ButtonText, QColor("#202420"))
    palette.setColor(QPalette.Highlight, QColor("#287a50"))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    application.setPalette(palette)
    directory = None
    if not arguments.smoke_test and not arguments.service_mode:
        directory = arguments.data_dir if arguments.data_dir is not None else default_portable_dir()
    window = MainWindow(read_only=arguments.smoke_test, auto_refresh=not arguments.smoke_test,
                        directory=directory)
    window.show()
    if arguments.smoke_test:
        QTimer.singleShot(100, window.close)
    return application.exec()