# SMTPProxy

Windows-SMTP-Relay mit Qt6-Verwaltung und dauerhaftem Windows-Dienst.

Autor: **Marcel Braun**, **marcel.braun@ferchau.com**.

## Betrieb

- Lokaler Client: **localhost, Port 25, kein TLS, keine Authentifizierung**.
- Empfang ausschliesslich auf `127.0.0.1` und `::1`, sofern IPv6 verfuegbar ist.
- Zielserver: Benutzer/Kennwort, STARTTLS (Vorgabe), direktes TLS oder explizit unverschluesselt.
- TLS prueft Zertifikate und Hostnamen; kein automatischer Klartext-Fallback.
- Windows-Dienst unter `NT AUTHORITY\LocalService`; die GUI muss nicht geoeffnet bleiben.
- SQLite-Warteschlange mit persistenten Empfaengerzustaenden, Retry und Groessenlimit.

## Installation und erste Einrichtung

1. `SMTPProxy-Setup-1.0.0.exe` ausfuehren. Windows verlangt administrative Rechte.
2. SMTPProxy ueber das Startmenue oeffnen und **Als Administrator verwalten** waehlen.
3. Unter Einstellungen Zielserver, Port, Sicherheitsmodus, Benutzername und Kennwort eintragen.
4. Bei Bedarf lokalen Port und feste Absenderadresse setzen. Fuer unverschluesselten Versand
   werden auch die Zugangsdaten ohne Transportschutz uebertragen.
5. Einstellungen speichern und Verbindung testen. Danach den Dienst explizit starten.
6. Autostart einschalten, wenn der Dienst kuenftig bereits ohne Benutzeranmeldung laufen soll.
7. Mit der Testmail-Funktion oder dem eigenen SMTP-Client eine Nachricht senden.

Die Erstinstallation registriert den Dienst mit manuellem Start, ohne unausgefuellte
Konfiguration zu aktivieren. Autostart aus bedeutet manueller Start, nicht deaktivierter Dienst.
Bei laufendem Dienst werden gespeicherte Aenderungen erst nach explizitem Anwenden/Neustart
aktiv. Die GUI zeigt Abweichungen zwischen gespeichertem und tatsaechlichem SCM-Startmodus.
Die Funktion **Anwenden** startet einen zuvor laufenden Dienst neu; einen beendeten Dienst
anschliessend explizit starten. Die GUI startet selbst keinen SMTP-Listener.

Ein normaler Benutzer sieht Dienststatus und About. Einstellungen, Queue und Protokolle
sind nur im administrativen Verwaltungsmodus zugaenglich. Das Schliessen der GUI stoppt
den Windows-Dienst nicht. Portkonflikte mit anderer Software muessen durch Wahl eines
anderen lokalen Ports behoben werden.

## CLI und Windows-Aufgabenplaner

Zusaetzlich ist `app_cli.exe` enthalten. Sie fuehrt Empfang und Queue-Worker direkt als
Konsolenprozess aus, **ohne installierten Windows-Dienst**. `app_service.exe start` dagegen
startet ausschliesslich einen bereits installierten Windows-Dienst und beendet sich danach.
Beide EXEs bleiben zusammen mit `_internal` im Anwendungspaket; `app_cli.exe` ist keine
isoliert kopierbare Onefile-EXE.

In der festen Installation unter Program Files verwenden GUI und CLI standardmaessig die
geschuetzte ProgramData-Konfiguration (entsprechende Zugriffsrechte erforderlich):

```powershell
& 'C:\Program Files\SMTPProxy\app_cli.exe'
```

**Portabel im selben Ordner:** Den gesamten Ordner `dist\SMTPProxy` an einen beschreibbaren
lokalen Ort kopieren. `app_gui.exe`, `app_cli.exe` und `_internal` muessen zusammenbleiben.
Ausserhalb der festen Program-Files-Installation verwenden GUI und CLI standardmaessig
den Ordner ihrer EXE fuer `.env`, Warteschlange und Logs, unabhaengig vom Arbeitsverzeichnis.
GUI oeffnen, Einstellungen speichern und danach CLI starten; eine Dienstinstallation ist
nicht erforderlich. Im Quellbetrieb ist entsprechend der Projektordner der Standard.

```powershell
& 'C:\SMTPProxy\app_gui.exe'
& 'C:\SMTPProxy\app_cli.exe'
```

Alternativ verwenden beide Anwendungen ein explizites gemeinsames Datenverzeichnis:

```powershell
& 'C:\SMTPProxy\app_gui.exe' --data-dir 'C:\SMTPProxyData'
& 'C:\SMTPProxy\app_cli.exe' --data-dir 'C:\SMTPProxyData'
```

Die GUI kann `C:\SMTPProxyData\.env` anlegen und bearbeiten; alternativ `.env.example`
als Vorlage verwenden. Vor Start der CLI muss die Konfiguration gueltig ausgefuellt sein.
Queue und Logs entstehen in diesem Verzeichnis. Ein relativer Pfad
wird gegen das Arbeitsverzeichnis aufgeloest; fuer geplante Aufgaben immer einen absoluten
lokalen Pfad verwenden. Zugriffsrechte fuer ein eigenes Datenverzeichnis selbst auf das
ausfuehrende Konto und Administratoren beschraenken, da Kennwort und Nachrichten dort liegen.
Im portablen Modus benoetigt die GUI keine Administratorrechte und greift nicht auf SCM zu.
Sie zeigt Einstellungen, Queue, Protokolle und den Runtime-Status fuer dieses Verzeichnis.
Dienstinstallation, Dienststeuerung und Dienst-Autostart sind dort nicht verfuegbar;
Start/Stopp der CLI erfolgt in der Konsole oder durch den Aufgabenplaner. Nach Aenderungen
der Einstellungen eine laufende CLI explizit neu starten. Queue-Wiederholung und Loeschen
sind gegen eine laufende CLI gesperrt. Der Status basiert auf der Runtime-Sperre und ist
keine Aussage ueber die Erreichbarkeit des Zielservers.

Mit `--service-mode` laesst sich in beiden Anwendungen ausdruecklich der geschuetzte
ProgramData-Modus waehlen, auch wenn sie ausserhalb von Program Files gestartet werden.
Die Installation eines Windows-Dienstes bleibt auf die feste Installation beschraenkt.

Nur Konfiguration pruefen, ohne Listener, Queue oder Versand zu starten:

```powershell
& 'C:\SMTPProxy\app_cli.exe' --data-dir 'C:\SMTPProxyData' --check
```

Aufgabenplaner-Konfiguration fuer den direkten CLI-Betrieb:

| Feld | Wert |
| --- | --- |
| Programm | Vollstaendiger Pfad zu `app_cli.exe` |
| Argumente | `--data-dir "C:\SMTPProxyData"`, leer fuer den portablen EXE-Ordner, `--service-mode` fuer ProgramData |
| Starten in | Ordner der EXE, beispielsweise `C:\SMTPProxy` |
| Trigger | Beim Start des Computers oder bei Anmeldung |
| Konto | Konto mit Zugriff auf Programm und Datenverzeichnis |
| Anmeldung | Bei Bedarf unabhaengig von der Benutzeranmeldung ausfuehren |
| Mehrfachstart | Keine neue Instanz starten |
| Zeitlimit | Automatisches Beenden nach fester Laufzeit deaktivieren |

Mit eigenem Datenverzeichnis sind keine Administratorrechte allein fuer den CLI-Betrieb
erforderlich. Fuer die geschuetzten ProgramData-Daten ein berechtigtes Konto verwenden.
Dienst und CLI nicht gleichzeitig betreiben: dieselbe Queue ist durch eine Prozesssperre
geschuetzt; bei getrennten Datenverzeichnissen kann derselbe lokale Port trotzdem nur
einmal belegt werden. Fuer dauerhaften Scheduler-Betrieb den Dienst-Autostart ausschalten
und den Dienst beenden.

Strg+C oder Strg+Pause beendet die CLI geordnet. Exitcode `0` bedeutet geordnetes Ende
beziehungsweise erfolgreichen Konfigurationscheck, `1` einen Laufzeit-/Konfigurationsfehler,
`2` ungueltige Argumente. Laufzeitprotokolle erscheinen in der Konsole und in `logs\proxy.log`.
Das erzwungene Beenden einer Aufgabe durch Windows garantiert kein geordnetes Herunterfahren;
gespeicherte Nachrichten bleiben erhalten, das dokumentierte SMTP-Duplikatrisiko gilt weiterhin.

## Daten und Sicherheit

| Inhalt | Speicherort |
| --- | --- |
| Installierte Programme | `%ProgramFiles%\SMTPProxy` |
| Konfiguration | `%ProgramData%\SMTPProxy\.env` |
| Warteschlange | `%ProgramData%\SMTPProxy\queue.sqlite3` |
| Rotierende Protokolle | `%ProgramData%\SMTPProxy\logs` |

Die Dienstinstallation beschraenkt den Datenzugriff auf Administratoren, SYSTEM und
LocalService. Binaerdateien sind fuer normale Benutzer nicht schreibbar. Das Programm
ist lokal zu installieren: ein Netzlaufwerk wie das Entwicklungsverzeichnis ist kein
geeigneter Dienst-Installationsort.

**Base64 ist keine Verschluesselung.** `UPSTREAM_PASSWORD_B64` enthaelt das UTF-8-Kennwort
lediglich kodiert. Wer die Datei lesen kann, kann das Kennwort rekonstruieren. Auch
Nachrichteninhalte in SQLite sind nicht verschluesselt. Bei vertraulichen Nachrichten
BitLocker und eine geeignete Sicherungs-/Aufbewahrungsrichtlinie einsetzen. SQLite kann
voruebergehend geloeschte Inhalte im WAL oder in Sicherungen enthalten.

Jeder lokale Prozess kann ohne Kennwort ueber den Proxy senden. Es wird kein offenes
LAN-Relay und keine Firewall-Freischaltung eingerichtet. Der Zielserver muss SMTP-
Kennwortauthentifizierung unterstuetzen; OAuth2, Microsoft Graph und SMTP-DSN sind nicht
implementiert. Nicht-ASCII-Envelope-Adressen/SMTPUTF8 werden nicht angeboten. UTF-8-Inhalte
und MIME-Anhaenge sind moeglich; fuer rohe 8-Bit-Inhalte muss der Zielserver 8BITMIME anbieten.

## Konfiguration

Die GUI schreibt `.env` atomar; eine Vorlage liegt in `.env.example`. Keine echten
Zugangsdaten ins Repository oder in das Buildpaket aufnehmen. Manuelle Aenderungen nur
bei beendetem Dienst und unter Beibehaltung der geschuetzten Dateiberechtigungen vornehmen.

| Einstellung | Vorgabe |
| --- | --- |
| `LOCAL_PORT` | `25` |
| `UPSTREAM_HOST`, `UPSTREAM_USERNAME`, `UPSTREAM_PASSWORD_B64` | erforderlich |
| `UPSTREAM_PORT` | `587` |
| `UPSTREAM_SECURITY` | `starttls`; alternativ `tls` oder `none` |
| `FORCE_FROM` | leer: Absender nicht umschreiben |
| `SERVICE_AUTOSTART` | `false` |
| `SMTP_TIMEOUT_SECONDS` | `30`, maximal `120` |
| `RETRY_INITIAL_SECONDS`, `RETRY_MAX_SECONDS` | `30`, `3600` |
| `MAX_QUEUE_AGE_HOURS` | `120` |
| `MAX_MESSAGE_BYTES` | `26214400` (25 MiB) |
| `MAX_QUEUE_BYTES` | `1073741824` (1 GiB Nutzdaten) |

Limits fuer die Annahme: 20 gleichzeitige Verbindungen, 100 Empfaenger je Nachricht,
10000 Nachrichten insgesamt. Das Queue-Byte-Limit umfasst auch fehlgeschlagene Nachrichten,
aber nicht SQLite-/Empfaenger-Metadaten oder WAL; ausreichend freien Datentraeger vorhalten.
Erweiterte numerische Limits koennen in der GUI gesetzt werden.

Eine feste Absenderadresse aendert den nichtleeren Envelope-Absender und den From-Header.
Leere Reverse-Paths fuer Ruecklaeufer bleiben leer, Reply-To bleibt unveraendert. Ohne
Umschreibung bleiben die urspruenglichen Nachrichtenbytes erhalten. Umschreibung kann
vorhandene DKIM-/S/MIME-Signaturen ungueltig machen und sollte nur bewusst aktiviert werden.
Die Umschreibung wird bei Annahme festgeschrieben, Zielserveraenderungen gelten hingegen
nach Dienstneustart auch fuer ausstehende Nachrichten.

## Versandfehler und Warteschlange

`250 Queued` bestaetigt die **dauerhafte lokale Speicherung**, nicht die Zustellung beim
Zielserver. Netzfehler und temporaere SMTP-Fehler werden mit exponentiellem Backoff erneut
versucht. Permanente 5xx-Empfaenger-/Datenfehler verbleiben als fehlgeschlagen in der Queue.
Auth-/TLS-Probleme lassen Nachrichten bis zur Altersgrenze ausstehend; nach Korrektur neu
starten und gegebenenfalls manuell wiederholen. Bereits zugestellte Empfaenger werden bei
Teilzustellung nicht erneut versendet. Erfolgreich vollstaendig versandte Nachrichten
werden aus der aktiven Datenbank entfernt.

Nach Ablauf der Altersgrenze wird nicht still geloescht. Fehlgeschlagene Nachrichten
muessen administrativ wiederholt oder explizit geloescht werden. Queue-Aenderungen sind
in der GUI ausschliesslich bei beendetem Dienst erlaubt. Wiederholen setzt die Altersfrist
neu; Loeschen entfernt die gesamte Nachricht einschliesslich aller Empfaengerzustaende.
Es werden keine automatischen Unzustellbarkeitsnachrichten erzeugt: Queue regelmaessig pruefen.

SMTP bietet hier **At-least-once**, nicht Exactly-once: bei einem Absturz oder Verbindungs-
verlust nach Upstream-Annahme, aber vor lokaler Bestaetigung, sind Duplikate moeglich.
Zertifikats-/Verbindungsfehler werden ohne Kennwoerter, Nachrichtentexte oder unkontrollierte
Serverantworten protokolliert. Pro Logdatei maximal 5 MiB plus fuenf Rotationen.

## Entwicklung

Voraussetzungen: Windows x64, Python 3.12 x64. PowerShell im Projektordner:

```powershell
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e '.[dev,build]'
& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\python.exe -m ruff check src tests app_gui.py app_service.py app_cli.py packaging/SMTPProxy.spec
& .\.venv\Scripts\python.exe .\app_gui.py
```

Tests verwenden temporaere Datenverzeichnisse, lokale SMTP-Server und temporaere Test-CAs.
Dienststeuerung und GUI-Verwaltung sind in den Tests gemockt; es wird kein echter Dienst
installiert und keine produktive Konfiguration veraendert. Der schreibfreie GUI-Smokecheck:

```powershell
& .\.venv\Scripts\python.exe .\app_gui.py --smoke-test
```

## Einzelnes Setup bauen

Zusaetzlich **Inno Setup 6.3 oder neuer** installieren. Der Build benoetigt keine
Administratorrechte und registriert keinen Dienst. Netzwerkzugriff fuer pip erforderlich.

```powershell
.\build.ps1 -PythonExe 'C:\Pfad\zu\Python312\python.exe'
```

Falls der Compiler nicht automatisch gefunden wird:

```powershell
.\build.ps1 -ISCC 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
```

Das Skript erzeugt eine isolierte `.venv-build`, prueft Tests und Ruff, baut alle drei EXEs
mit gemeinsamer Laufzeit und erstellt **`dist\SMTPProxy-Setup-1.0.0.exe`** samt SHA256-Ausgabe.
Direkt abhaengige Pakete sind versionsgebunden; transitive Versionen werden von pip
aufgeloest. Fuer streng reproduzierbare Releases eine intern gepruefte vollstaendige
Lockdatei und Paketquelle festlegen.

Nur das Anwendungspaket ohne Setup erstellen:

```powershell
.\build.ps1 -SkipInstaller
```

Dieses Ergebnis ist `dist\SMTPProxy` und muss vollstaendig zusammenbleiben. Es ist bewusst
kein selbstentpackendes privilegiertes PyInstaller-Onefile: die einzelne Auslieferungsdatei
ist der Installer. Zielrechner brauchen weder Python noch Qt separat. Abhaengigkeitsmetadaten
und Lizenztexte liegen unter `_internal\licenses` bzw. den `.dist-info`-Verzeichnissen.
Qt-Lizenzbedingungen fuer die eigene Weitergabe pruefen. Fuer produktive Releases beide
Programm-EXEs vor dem Setup-Build und die Setup-EXE danach mit einem eigenen vertrauens-
wuerdigen Authenticode-Zertifikat signieren. Es wird kein Zertifikat mitgeliefert.

## Updates und Deinstallation

Vor einem Update Queue sichern und laufende GUI schliessen. Setup beendet einen vorhandenen
Dienst vor dem Dateiaustausch und erhaelt Einstellungen und Queue. Anschliessend Dienststatus
pruefen und den Dienst explizit starten; Setup startet ihn nicht automatisch. Autostart-
Einstellungen vorhandener Dienste bleiben erhalten. Bei Fehlern bricht Setup mit Meldung ab.

Deinstallation beendet und entfernt den Dienst. ProgramData bleibt standardmaessig erhalten;
das dauerhafte Loeschen von Kennwort, Nachrichten und Logs erfordert eine ausdrueckliche
Bestaetigung. Stille Deinstallation behaelt die Daten immer.

## Freigabepruefung auf Zielsystem

Automatisierte Tests ersetzen keinen realen Windows-Dienst-/Installer-Test. Vor produktivem
Einsatz auf sauberer Windows-VM ohne Python pruefen: Installation, ACLs als Standardbenutzer,
echter SMTP-Zielserver, Reboot ohne Anmeldung, Abmeldung bei laufendem Dienst, Autostart aus,
Queue-Wiederanlauf, Upgrade und Deinstallation mit und ohne Datenerhalt. Windows-Ereignisanzeige
und die geschuetzten Logs zeigen Dienststartfehler.