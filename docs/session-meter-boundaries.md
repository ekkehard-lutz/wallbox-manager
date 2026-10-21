# Session-Zähler und Ladezustandsprojektion

Basis: WBM `v0.4.0-beta.5` (`b1c663a`), Feature-Branch
`codex/session-meter-idle`. Keine Versionsänderung und kein Hardwareeingriff.

## Anzeige „Inaktiv“

`Reading` verwendet eine reine Anzeigeprojektion aus dem State: aktuelle akzeptierte
BootNotification-Registrierung, aktuelles `StatusNotification(Available)` am gebundenen
Connector und keine offene Session. Eine nach Neustart/Reconnect ungeklärte Session
verhindert die Projektion ebenfalls. Eine echte ChargingState-Beobachtung der aktuellen
Generation hat Vorrang; auch ein explizites Unknown oder ein Konflikt wird nicht
überschrieben. Disconnect und Generationswechsel entziehen die Grundlage sofort.

Die Projektion erzeugt weder Observation noch TransactionEvent noch Timer. Der
Ladezustandssensor wird beim Connectorstatus angelegt; Wert, Verfügbarkeit und
Discovery verwenden dieselbe Ableitung. Connector-/Session-Änderungen veröffentlichen
die davon betroffene Anzeige. Attribute kennzeichnen die Ableitung als
`wbm:registered_available` / `display_projection`, ohne physische CP-Messung zu behaupten.

## Grenzmessungen und Zeit

Für Started und Ended wird der zeitlich letzte eingebettete Energie-Messwert mit
`Messzeit <= Ereigniszeit` ausgewählt. Er muss gültig sein, zur aktuellen Generation
gehören und höchstens **15 Sekunden** vor dem Ereignis liegen (Grenze inklusive).
Ein ungültiger letzter Kandidat wird nicht durch einen älteren ersetzt. Ein bereits
bestätigter aktueller Messwert exakt zur Ended-Zeit darf ebenfalls finalisiert werden.
Ohne geeigneten Grenzbeleg bleiben Start bzw. Ende unbekannt. Insbesondere wird ein
fehlender Start nicht aus einem späteren Updated oder MeterValues rekonstruiert.

Begründung am Wallbox-Commit `222d3329a76a972e9ac7cec5030bee5b3071320e`:
`backends/sdm630.py` verwendet `ENERGY_INTERVAL = 5.0` und
`ENERGY_MAX_AGE = 15.0`; die tatsächliche Periode beträgt ungefähr 5–6 Sekunden.
15 Sekunden decken damit begrenzte Poll-/Transportverschiebungen ab, ohne Werte
jenseits der bestehenden Quellgültigkeit zu akzeptieren. Es gibt keine zusätzliche
Modbus-Abfrage und kein Warten auf den nächsten Poll.

**Ein älterer Grenzzähler ist eine zeitlich begrenzte Näherung, keine exakt zum
Transaktionsereignis erfasste Messung.** Der mögliche Energiefehler hängt von der
Leistung innerhalb dieses Zeitfensters ab; es findet keine Interpolation statt.
`started_at`/`ended_at` bleiben Ereigniszeiten; `start_meter_at`/`end_meter_at` sind
unveränderte Erfassungszeiten. Die Grenzzählersensoren zeigen `measured_at`, `event_at`
und `boundary_approximation`. Historische Daten ohne Erfassungszeit erhalten keine
erfundene Zeit. `energy_at` bleibt die Zeit des letzten gültigen Messwerts;
`energy_reported_at` hält die Reihenfolge einschließlich ungültiger Meldungen fest.
Werte vor dem Generationsepoch werden auch innerhalb der 15 Sekunden abgelehnt.
Werte nach dem Ereignis werden niemals als rückwirkende Grenzmessung verwendet.

## Laufende und abgeschlossene Session

Aktiv: `start_meter` nach gültigem Started-Beleg, `current_meter` als interner letzter
gültiger Zähler, `end_meter = None`, `ended_at = None` und
`energy = current_meter - start_meter`. Fehlende Werte bleiben `None`; eine gemessene
Null ist zulässig. Ended setzt den geeigneten Endbeleg und berechnet endgültig
`energy = end_meter - start_meter`. Rückwärtszähler, gleichzeitige widersprüchliche
Werte und ungültige Messungen sperren die Energieberechnung konservativ für diese
Session. Der letzte gültige interne Zähler bleibt zu Diagnosezwecken erhalten;
ein ungültiger Verlauf wird nicht als gültiger Endzähler präsentiert.

Abgeschlossene Werte einschließlich der internen Session-ID bleiben nach Disconnect,
Boot, WBM-Reload und HA-Neustart sichtbar. Erst eine akzeptierte neue Session ersetzt
die letzte Anzeige. Sequenz-, Zeit-, Generation-, Scope- und Closed-Transaction-Prüfungen
verhindern, dass Replays sie wieder öffnen oder überschreiben.

## Architekturentscheidung: begrenzte Live-Kanalzuordnung

Am angegebenen Wallbox-Commit verwenden `ocpp_runtime/transactions.py:send` und
`ocpp_runtime/station.py` für beide Sendepfade `build_meter_values` aus
`ocpp_runtime/metering.py`. Energie stammt aus `cumulative_energy_kwh` mit
`energy_timestamp` und wird dort einheitlich auf ganze Wh normalisiert.
WBM ändert seine Einheitenkonvertierung nicht; auch die bisher akzeptierte höhere
Auflösung anderer Protokollmeldungen bleibt erhalten.

Die Zuordnung wird ausschließlich unter diesen Bedingungen freigeschaltet:

- aktuelle akzeptierte Registrierung, Hersteller **und** Modell `wallbox-stationary`;
- Bindung und Session exakt EVSE 1 / Connector 1;
- Transaktionskanal als belegte Session-Basis;
- ein in beiden Kanälen gleicher gültiger normalisierter Zähler **mit identischer
  Erfassungszeit** in derselben Generation und Session.

Erst danach dürfen reguläre EVSE-1-Energiewerte den laufenden Sessionzähler fortführen.
Alle weiteren Werte unterliegen Zeitreihenfolge, Konflikt- und Monotonieprüfungen;
negative/ungültige Werte werden nicht zur Energie addiert. Der Nachweis gilt nicht für
andere EVSEs, Connectoren, Registerbasen oder Sessions und wird bei Generationswechsel
verworfen. Ohne Nachweis läuft die Session nur mit TransactionEvent-Messungen weiter.
Ein bereits isoliert verwendeter allgemeiner Kanal wird nicht nachträglich mit einem
Transaktionskanal verrechnet. Allgemeine Observationen werden durch Transaktionswerte
weiterhin nicht überschrieben.

Bewusste Begrenzung: Wenn Delta-Telemetrie keinen gemeinsamen Messzeitpunkt liefert
oder die konfigurierte Hersteller-/Modellkennung abweicht, bleibt die schnelle
Verknüpfung aus. Gleichheit allein ersetzt keinen gerätespezifischen Vertrag. Eine
allgemeine Multi-EVSE-/Meter-ID-Zuordnung wäre eine separate Architekturentscheidung;
für das unterstützte Gerät ist keine weitere Entscheidung offen.

## Speicherung und Migration

Der bestehende HA-Store und dessen Storage-Version 1 bleiben erhalten; das Dokument
trägt nun `schema: 2`. Schema 1 wird beim Laden additiv gelesen:

- aktive Session: bisheriger `end_meter` nach `current_meter`, öffentlicher
  `end_meter` auf unbekannt; ID, vorhandener Start und Energie bleiben erhalten;
- abgeschlossene Session: endgültige Werte und ID unverändert, `current_meter`
  zusätzlich aus dem gespeicherten Endwert;
- historische Lücken bleiben Lücken; fehlende Originalzeiten werden nicht ergänzt.

Auch die ältere Ledger-Projektion behandelt aktive Endwerte als laufend. Das originale
Ledger wird unverändert gelassen. Closed-Transaction-IDs bleiben erhalten; es gibt
keine Löschung bestehender Historie. Die nächste reguläre Speicherung schreibt Schema 2.
Live-Generation und Kanalnachweis werden nach einem Neustart neu verlangt.

## Validierung

Vor der Änderung: 119 Python-Tests bestanden. Drei neue Tests reproduzierten fehlende
Idle-Projektion, verlorenen Startzähler bei 5,5 Sekunden älterer Erfassung und den
vorzeitig sichtbaren Endzähler. Nach der Korrektur decken die zusätzlichen Tests
Grenzalter, zukünftige Zeitpunkte, Messreihenfolge, Invalidität, Replays, neue Sessions,
Generationsgrenzen, Kanalnachweise, Schema-1-Migration, WBM-Reload und einen frischen
HA-Kontext mit aus Datei gelesenen abgeschlossenen Sessiondaten ab.

Bestehende Assertions für aktive `end_meter` verwenden jetzt `current_meter`; die
Prüfungen für endgültige Endzähler bleiben erhalten. Direkte Live-Meter-Testaufrufe
verwenden keinen Grenzmessungsmarker mehr. Die Produktionsänderung betrifft nur
`store.py`, `sensor.py`, `session.py` und `ocpp.py`.

Abschließendes Ergebnis: `python -m pytest -q`: **152 bestanden** (5 bestehende
Deprecation-Warnungen aus HA/Abhängigkeiten). `ruff check .`,
`ruff format --check .`, JavaScript-Syntaxprüfung und alle **8 Frontend-Tests**
bestanden ebenfalls. Keine Hardwarevalidierung oder Produktivänderung durchgeführt.
