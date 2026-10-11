# PV_OPTIMUM: Vorbereitung für beta.12

## Arbeitsstand und Umfang

Feature-Branch: `fix/pv-optimum-beta12-stability`, erstellt vom aktuellen
`origin/develop` bei `88ed5c457d7beeba499e5950399fa36b42fd0b20`
(`v0.3.2-beta.11`). Die beta.11-Implementierung und ihr Bericht sind enthalten.
Das Manifest bleibt auf `0.3.2-beta.11`.

Grundlage für D1–D3 ist die lokale vollständige Analyse
`.analysis/pv4-analysis.md` mit `.analysis/pv4.log`. Die dortigen sechs
Spannungsvektoren und das Soll von 4771.836 W wurden als kleine deterministische
Testdaten übernommen. Diagnoseprotokolle und andere bereits vorhandene
unversionierte Dateien gehören nicht zu diesem Commit.

## D1: Quantisierungsgrenze und bestätigter Strom

**Bestätigt:** Kleine Spannungsänderungen verschoben 3p/7 A um −0.900 bis
+3.388 W gegenüber dem unveränderten weichen Soll. Striktes DOWN wechselte
dadurch zwischen 3p/7 A und 1p/20 A. Bei gesperrtem Phasenwechsel wurde der
bestätigte 3p-Mindestpunkt mit 6 A ausführbar. Das erzeugte akzeptierte
Stromvorgaben im Abstand weniger Sekunden; physische Phasenoszillation wurde
im Log nicht nachgewiesen.

Der vorhandene Solver kann jetzt einen bereits bestätigten FAST-Strompunkt im
vorhandenen 100-W-Totband um das weiche Soll halten. Er berechnet dessen Leistung
mit aktuellen Spannungen neu und prüft das aktuelle Stromraster, alle Strom- und
Leistungsgrenzen, zulässige Phasen und die bestätigte physische Konfiguration.
Der Runtime-Kontext muss weiterhin gültig sein; unbestätigte Ziele sind ausgeschlossen.
`confirmed_current_retained` unterscheidet diese Entscheidung von normaler Auswahl.

Neue Punkte werden weiterhin mit DOWN gewählt. Die Ausnahme erhöht keinen Strom;
sie hält ausschließlich einen vorhandenen bestätigten Punkt nahe dem weichen Soll.
Mindestpunktabfragen, OFF, BALANCE, fehlende Spannungs-/Phasenbeweise und harte
Grenzverletzungen erhalten diese Ausnahme nicht. Es gibt keinen neuen Timer und
keinen zweiten Regler. Der bestehende Response- und Phasen-Stabilisierungsmechanismus
bleibt wirksam.

Der Test mit den sechs Originalvektoren bestätigt zunächst 7 A und erzeugt danach
keine weiteren 6/7-A-Befehle. Harte Budgetsenkungen wirken schon eine Sekunde später
auf 6 A beziehungsweise OFF; ein relevanter Import erlaubt weiterhin eine Senkung.

## D2: Pausengrund, Neustart und bestätigtes OFF

**Bestätigt:** Die OFFs bei 80.4 % und 80.7 % SoC waren wirtschaftliche BALANCE-Pausen
nach 90 Sekunden PV-Defizit. Das vorzeitige Löschen von `pv_ongoing` während Planung
und Dispatch-Validierung aktivierte bereits vor bestätigtem OFF die Neustartschwelle.
Spätere Diagnosen etikettierten diese Pause als SoC-Schutz.

Eine geplante wirtschaftliche Pause beendet die Fortsetzung jetzt erst nach
bestätigtem OFF. Der ursprüngliche Grund wird als `pv_pause_reason` gespeichert;
`pv_restart_reason` zeigt unabhängig die aktuelle Neustartberechtigung, etwa
`below_pv_start`. `stopped_battery_soc` bezeichnet echte Schutzbedingungen;
reine Startschwellen verwenden `waiting_battery_soc`. Das gemeinsame SoC-Policy-Modell
und seine Schwellen wurden nicht geändert.

PV-Erholung vor dem Senden widerruft das wirtschaftliche OFF. Ablehnung und
Supersession vor dem Senden erhalten einen gültigen bestätigten positiven Punkt.
Ein optionaler Dispatch-Beobachter am vorhandenen OCPP-2.1-Sendefence erlaubt diese
Unterscheidung auch bei Abbruch: vor dem ersten Schreiben bleibt die Bestätigung
gültig; nach dem Schreiben ist ein abgebrochener oder überholter akzeptierter
Ausgang unsicher und benötigt Reconciliation. Ein abgelehnter gesendeter Befehl
ändert den alten Punkt nicht. Unbekannte Adapter bleiben bei Abbruch konservativ.

Die wirtschaftliche Pausenursache wird im bestehenden Ownership-Checkpoint und
beim Suspend gespeichert. Recovery übernimmt sie nur nach Live-OFF-Reconciliation
und für dieselbe aktive externe Transaktion. Ein neuer Ladevorgang, eine andere
Transaktion oder eine explizite Profilwahl entfernt den alten Grund. Die Karte
zeigt bei bestätigter wirtschaftlicher Pause zusätzlich eine wartende Neustartschwelle.

## D3: Technische Probe-Frist

**Bestätigt:** Gewöhnliche Parameteränderungen invalidierten die PV-Regelung und
löschten dabei die Probe-Frist, obwohl die bestätigte Phasensperre weiter galt.

Policy-Änderungen erhalten jetzt nur eine noch gültige technische
`PhaseSwitchLockout`-Probe-Frist samt Anfrage. Connection-/Authority-Kontext und
physische Phase bleiben über die bestehenden Runtime-Beweise maßgeblich. Wird
dieser Kontext ungültig, wird die zugehörige technische Frist entfernt.
Generisches BUSY und RestartLockout werden weiterhin nicht als Phasensperre gelernt.

84 → 83 → 82 → 81 → 80 % erzeugt im Regressionstest keine zusätzlichen bekannten
gesperrten Phasenanfragen. Die erste Probe-Frist bleibt bei 60 Sekunden; danach
kann eine erfolgreiche Probe die Sperre aufheben. Mindeststromfortsetzung und
gewöhnlicher BUSY-Backoff bleiben erhalten.

## D4: Quelle und nachgewiesener Statusfehler

**Ein Statusprioritätsfehler ist bestätigt.** Ein Regressionstest scheiterte vor
der Korrektur mit `measurements_unavailable` statt `no_authority`: Local authority,
weiter gültige OCPP-Spannungen, aber ein 91 Sekunden alter EV-Leistungswert.
Die konkreten Zeitpunkte und der ausgefallene Kanal der ursprünglichen
Hardware-Beobachtung bleiben offen.

Die genaue deutsche Meldung stammt aus dem Inline-Mapping der Karte für
`attrs.profile_status == "measurements_unavailable"`, nicht aus einer HA-JSON-
Übersetzungsdatei:

- Deutsch: „PV-Regelung pausiert: Messwerte fehlen, sind ungültig oder veraltet.“
- Englisch: “PV regulation paused: readings are missing, invalid or stale.”

Die Attribute kommen vom ChargingProfile-Select über `GridProfiles.attributes`.
Vorher wurde das interne `status` direkt als `profile_status` ausgegeben.
Die Warnung kann entstehen durch:

1. `optimum_refresh`: fehlende/ungültige/veraltete SoC-, Planungs- oder
   Energiedaten; in FAST zusätzlich erforderliche EV-Leistung.
2. `pv_request`/`pv_plan`: Fehler beim Lesen erforderlicher Ausführungsdaten.
3. `pv_continuation_plan`: fehlende EV-/Netzdaten für die Lockout-Fortsetzung.
4. SoC- und Messwert-Events: `pv_input_gap`; die Messwert-Events sind auf aktive
   PV-Tasks begrenzt, der passive Policy-Beobachter ist unabhängig von Authority.

Je nach Sicherheitspfad kann der Plan einen Eingabefehler auch zu
`hard_budget_unavailable`, `stop_delay_expired` oder No-Decision abbilden.
Diese internen Gründe sind nicht alle die Quelle derselben Kartenmeldung.

Bei konfiguriertem SoC installiert `load()` bereits einen passiven Beobachter mit
1-Sekunden-Intervall. Authority-Verlust stoppt aktive Regelaufgaben, aber nicht
diesen Beobachter. Die Live-Frischegrenze beträgt 90 Sekunden, für den explizit
klassifizierten Planungswert `remaining_pv_energy` 900 Sekunden. HA `last_reported`,
gegebenenfalls Quell-`observed_at` und explizites `valid_until` werden geprüft;
ein Wert kann deshalb früher ungültig werden. Ein bereits ungültiger Wert kann
sofort einen Fehler liefern, sonst entsteht er beim folgenden Frischeablauf und
der nächsten relevanten Auswertung. Daraus lässt sich kein mehrminütiger
Authority-Timeout der konkreten Beobachtung ableiten.

Zusätzlich konnte ein alter interner Eingabefehler ohne aktive Planung stehen
bleiben. Erfolgreiche erforderliche passive Prüfungen räumen ihn jetzt auf.
Das ist keine Vollinventur aller Sensoren: STOP benötigt weiterhin kein positives
Leistungsbudget, und BALANCE/FAST prüfen ihre bisherigen jeweiligen Eingaben.

Die primäre Anzeige klassifiziert jetzt zunächst Verbindung, Authority und
Ownership. Ohne Berechtigung erscheinen „PV-Regelung inaktiv: Wallbox Manager hat
keine Steuerungsberechtigung.“ beziehungsweise “PV regulation inactive: Wallbox
Manager has no control authority.” Unbekannte Authority, Disconnection und
inaktive Wallbox besitzen eigene Texte. Ein echter Eingabefehler bleibt separat
als `pv_measurement_status` und in den vorhandenen detaillierten Eingabediagnosen
sichtbar. Ein alter Befehlsfehler verdeckt diese primäre Klassifikation nicht.

Connection-/Authority-Revisionen veröffentlichen den Status im vorhandenen
Callback unmittelbar. Es gibt keine zusätzliche Abfrage oder Authority-Übernahme.
Der tatsächliche Enum ist LOCAL/REMOTE/UNKNOWN; OCPP 2.1 normalisiert nur `Local`
und `OCPP` zu bekannten Zuständen. Unbekannte Labels wie `EMS`/`External` bleiben
UNKNOWN. Reconnect und Reload erzeugen keine künstliche WBM-Berechtigung.

## Unveränderte Policies und überprüfte Grenzen

- FAST/BALANCE-Hysterese, 90-Sekunden-BALANCE-Pause und FAST-Budgetgleichungen.
- Konfigurierter Batterie-Entladedeckel und bestehende 100-W-Unsicherheitsmarge.
- Normales DOWN, 1-A-Raster, Mindeststrom, gültige 1p/3p-Punkte und OFF/Nullsemantik.
- beta.11-Lockout-Fortsetzung mit maximal 50 % Mindestleistungs-Netzimport und
  bestehender 10-Sekunden-Toleranz für Überschreitung; unabhängige harte Grenzen.
- Striktes Ziel-SoC im Phase-Lockout: Gleichheit erlaubt Fortsetzung, darunter STOP.
- Response-/Phasen-Stabilisierung, Station-Lockouts, explizite Übernahme,
  Authority-/Connection-/Permission-/Session-Fences und Transaktionsverfolgung.

`pv_budget.py`, `pv_regulators.py`, `pv_soc.py`, `session_ledger.py`, Manifest und
OCPP-1.6J-/2.0.1-Adapter wurden nicht geändert. Der OCPP-2.1-Adapter meldet lediglich
den bereits gefenceten Sendebeginn; Payload, Bestätigungsregeln und ControlAuthority-
Normalisierung bleiben gleich. Das Offer-/Messleistungs-Verhältnis wird nicht korrigiert.

## Geänderte Dateien

| Datei | Änderung |
| --- | --- |
| `control/commands.py` | Optionalen Dispatch-Beobachter dokumentieren |
| `control/runtime.py` | Bestätigten FAST-Punkt halten; wirtschaftliches OFF-Lifecycle |
| `solver/operating_point.py`, `solver/power.py` | Begrenzte bestätigte Retention mit harten Beweisen |
| `pv_surplus.py` | Bestätigte Pausenursache, Fortsetzung, technische Kontextbereinigung |
| `pv_optimum.py` | Schutz-/Neustartdiagnose, passive Eingabestatusbereinigung |
| `profiles.py` | Authority-Status, Ereignispublikation, Probe-Frist und Recovery |
| `ownership.py` | Transaktionsbezogene Pausenursache persistieren |
| `pv_diagnostics.py` | Unabhängige Statusdimensionen und Mindestpunkt-Policy |
| `www/wallbox-manager-card.js` | Deutsche/englische Authority- und Pausentexte |
| `tests/test_pv_beta12.py` | 62 neue D1–D4-Testfälle |
| `tests/test_pv_diagnostics.py`, `tests/test_pv_surplus.py` | Zwei Startschwellen-Erwartungen korrigieren |
| `tests/test_wallbox_card.cjs` | 16 zusätzliche Text-/Prioritätsprüfungen |
| `docs/beta12-implementation-report.md` | Ursachen, Änderungen, Validation und Grenzen |

Produktionspfade in dieser Tabelle sind relativ zu
`custom_components/wallbox_manager/`.

## Validation

Die pytest-Läufe benutzen lokale simulierte HA-/OCPP-Peers und benötigen in dieser
Umgebung Ausführung außerhalb der Netzwerk-Sandbox. Es wurde keine echte Wallbox
angesprochen.

| Exakter Befehl | Ergebnis |
| --- | --- |
| `.venv/bin/pytest -q tests/test_pv_beta12.py tests/test_pv_phase_lockout_continuation.py tests/test_pv_optimum_hold.py tests/test_phase_lockout.py tests/test_voltage_command_fence.py tests/test_ownership_recovery.py tests/test_pv_input_diagnostics.py --tb=short --show-capture=no` | 265 bestanden, 1 Deprecation-Warnung, 42.92 s |
| `.venv/bin/pytest -q --tb=short --show-capture=no` | 2102 bestanden, 5 Deprecation-Warnungen, 123.22 s |
| `.venv/bin/ruff check .` | Alle Prüfungen bestanden |
| `.venv/bin/ruff format --check .` | 180 Dateien korrekt formatiert |
| `node --test tests/test_wallbox_card.cjs` | Testdatei bestanden, keine Fehler |
| `node tests/test_wallbox_card.cjs` | 145 Einzelprüfungen bestanden |
| `node --check custom_components/wallbox_manager/www/wallbox-manager-card.js` | Bestanden |
| `git diff --check` | Bestanden |

Der Node-Test-Runner fasst diese Testdatei im `--test`-Aufruf als einen Eintrag
zusammen; der direkte Aufruf liefert die 145 einzelnen Ergebnisse. Der letzte
Gesamtlauf enthält auch die abschließenden Dispatch-/Eingabestatus-Korrekturen.
Die Warnungen stammen aus Home Assistant und `backoff` unter Python 3.14, nicht
aus fehlgeschlagenen Prüfungen. Keine angeforderte Prüfung blieb unausgeführt.

Der erste Gesamtlauf fand zwei erwartete alte SoC-Diagnosebehauptungen und einen
zusätzlichen Reserve-Sensorzugriff durch die neue Statusprojektion. Die beiden
Erwartungen wurden auf den tatsächlichen Startschwellenstatus umgestellt; die
Statusprojektion liest nur gespeicherte Profileinstellungen. Der bestehende Test
für identische HA-Lesezugriffe und Reglerzustände bei Diagnosestufe 0/3 besteht.

## Grenzen und empfohlene Hardware-Prüfung

Die Tests beweisen die deterministische Entscheidungs- und Befehlslogik, keine
realen Relaiszeiten oder die Reaktion des Fahrzeugs. Der 100-W-Bereich hält nur
einen bestätigten FAST-Punkt; seine Wirkung bei echten Spannungsänderungen soll
mit angebotenem, bestätigtem und gemessenem Strom getrennt protokolliert werden.

Für eine später ausdrücklich autorisierte Hardware-Prüfung:

1. Die 3p/6–7-A-Schwelle bei aktivem Phase-Lockout beobachten; kleine Spannungsänderungen
   dürfen keine Befehlsserie auslösen. Harte Leistungs-/Stromgrenzen müssen sofort wirken.
2. BALANCE bei 80.4/80.7 % und Ziel 80 % mit anhaltendem PV-Defizit prüfen:
   90-Sekunden-Pause, wirtschaftlicher Grund und unabhängige Neustartschwelle.
   PV-Erholung vor OFF sowie echte Schutzgrenzen getrennt prüfen.
3. Unter Sperre 84 → 80 % rasch ändern; Probe-Cadence und anschließende legitime
   Erholung sowie Authority-/Reconnect-/Phasen-Kontextwechsel beobachten.
4. Local → OCPP → Local sowie UNKNOWN/Reconnect prüfen. Gleichzeitig alle
   erforderlichen Eingabekanäle samt Quellzeit, HA `last_reported`, `valid_until`,
   Authority-Revision und Select-/Kartenstatus erfassen. Für die bisher offene
   D4-Zeitleiste genügt ein zeitlich korrelierter diagnostischer Mitschnitt;
   frische Spannung allein beweist keine gültige PV-Eingabesituation.
5. Integration-Reload nach bestätigter wirtschaftlicher Pause und nach einem
   unklaren gesendeten OFF testen; Transaktionsbindung und Live-Reconciliation prüfen.

In diesem Task erfolgen ausschließlich Commit und Push des Feature-Branches.
Keine PR-Erstellung, Merge, Versionsänderung, Tag, Veröffentlichung, HA-Deployment,
Firmwareänderung oder Hardwareprüfung.
