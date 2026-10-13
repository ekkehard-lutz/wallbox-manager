# Wallbox Manager – PV-Regelung: konsolidierte Anforderungsbasis V3

**Status:** Entwurf zur fachlichen Freigabe, **keine neue Implementierungsvorgabe ohne Review**  
**Stand:** 13.10.2026  
**Bezug:** `wallbox-manager` v0.3.2-beta.13 (Release-Commit `aaef76d41295cf0b0255933f72b6d65448f6864a`), GitHub-Branch `develop` / Release-Tag `v0.3.2-beta.13`  
**Bearbeitungsart:** Chronologischer Quellenabgleich; keine Änderungen an Steuerungssoftware, HA oder Wallbox.  
**Primärquellen:** vom Benutzer bereitgestellter PDF-Ausdruck *Wallbox Modbus-Abendauswertung* (619 Seiten, Chat vom 04.–12.10.2026); insbesondere S. 1–19, 67–83, 92–119, 132–148, 209–246, 266–320, 401–449, 460–480, 501–528, 529–545 und 566–619; Repository-Dokumente, **insbesondere der jüngste** `docs/pv-grid-import-policy-design.md` aus `v0.3.2-beta.13` (vgl. Quellliste am Ende).  
**Beweisgrenze:** Der PDF-Druck enthält den sichtbaren Chattext, jedoch nicht automatisch den vollständigen Inhalt aller darin referenzierten lokalen Logs, Codex-Artefakte oder ausgeblendeten Anhänge. Die hier als „beschlossen“ bezeichneten Regeln sind anhand des sichtbaren Gesprächs beziehungsweise der mit beta.13 veröffentlichten Spezifikation belegt. Ob beta.13 ihnen zur Laufzeit genügt, ist eine eigenständige Prüfung.

## 0. Gültigkeit, Rangfolge und Änderungsregeln

- **BESCHLOSSEN:** ausdrückliche Zustimmung im Gespräch, zuletzt vom Benutzer autorisiert, oder eine nach dieser Zustimmung festgelegte und freigegebene technische Präzisierung. Gilt weiter, bis eine spätere ausdrückliche Entscheidung sie ersetzt.
- **TECHNISCH PRÄZISIERT:** konkrete Ausführungssemantik aus der finalen, mit beta.13 veröffentlichten Implementierungsdokumentation. Dies ist nicht automatisch eine universelle Produktvorgabe für spätere Neuentwürfe; Änderungen an ihr müssen begründet sein.
- **HISTORISCH / ABGELÖST:** nur zur Nachvollziehbarkeit, **nicht** als parallele Anforderung anwenden.
- **BEOBACHTET:** Befund aus Hardware-Log oder Gespräch. Daraus folgt ohne Soll-Abgleich kein Änderungsauftrag.
- **OFFEN:** weder verbindlicher Grenzwert noch Ursachenbeweis vorhanden. Keine neue willkürliche Regel einführen.

**Rangfolge bei Widersprüchen:** (1) letzte ausdrücklich bestätigte Entscheidung im Chat → (2) ihre detaillierte, freigegebene Umsetzung im beta.13-Design-/Review-Bericht → (3) ältere aktuelle, nicht überholte Spezifikationen → (4) ältere Entwurfs-/Implementierungsberichte als Historie. Diese Rangfolge gilt für fachlichen **Soll-Vertrag**; Ist-Verhalten und Code müssen gesondert geprüft werden.

**Bei jeder künftigen Änderung:** Anforderungs-ID, unverändertes/ersetztes Soll, beobachtetes Ist, Abweichung, Auswirkungen auf übrige IDs, konkrete Testfälle, erwartete Diagnosebelege sowie gegebenenfalls *explizite* Freigabe einer Soll-Änderung nennen. Keine Iteration darf eine bereits beschlossene Regel bloß aufgrund eines neuen Logs stillschweigend verwerfen.

---

## 1. Fachliche Architektur und Profilverfügbarkeit

### PV-ARCH-01 – Gemeinsamer Regler mit Hausbatterie [BESCHLOSSEN]

Alle drei PV-Profile mit Hausbatterie verwenden **eine gemeinsame** SoC-Zustandsmaschine, FAST-/BALANCE-Leistungsregelung, technische Betriebspunktauswahl, wirtschaftliche 50-Wh-Logik und OCPP-Ausführung. Die Profile unterscheiden sich vorrangig **in der Wahl des Batterie-Ziel-SoC T** – nicht in drei separat kopierten Leistungsreglern. **PV_MAXIMUM und PV_SURPLUS müssen bei identischem T und identischen übrigen Parametern dieselben Ladeentscheidungen und OCPP-Betriebspunkte liefern.**

Belege: PDF S. 19, 24, 134–145, 579–583; `docs/pv-soc-policy.md`, `docs/pv-grid-import-policy-design.md`.

### PV-ARCH-02 – PV_SURPLUS ohne Hausbatterie [BESCHLOSSEN]

`PV_SURPLUS` ist **das einzige PV-Profil, das auch ohne Hausbatterie funktionieren muss**. Der aktuelle verpflichtende Messvertrag für den batterielosen Modus besteht aus **gemessenem Netzbezug, gemessener Netzeinspeisung und gemessener Leistung der ausgewählten Wallbox**. Die Überschussvorgabe ergibt sich aus

`P_ziel = P_EV_ist + P_Netzeinspeisung - P_Netzbezug`.

Weder ein separater PV-Leistungssensor noch eine Hausverbrauchs-Entity noch eine Batterie oder PV-Prognose darf zur **zusätzlichen Pflicht** für diesen Sonderfall gemacht werden. Zulässige Messwerte und tatsächliche technische Betriebsgrenzen bleiben Voraussetzung. Eine unvollständige Netz-Mapping-Konfiguration darf PV_SURPLUS nicht fälschlich als ausführbar ausweisen.

Belege: PDF S. 575–580, 583, 608–609, 611–612; `docs/pv-grid-import-policy-design.md`, Schlussreview.

### PV-ARCH-03 – Profile mit Batterie und Forecast [BESCHLOSSEN]

- Batterie plus erforderliche Batteriemesswerte: `PV_SURPLUS` und `PV_MAXIMUM` möglich.
- `PV_OPTIMUM` zusätzlich **nur** mit gemappter gültiger Resttages-PV-Energieprognose (Energie, Wh/kWh/MWh; nicht bloße Momentanleistung).
- Fehlende Pflicht-Entities dürfen keine erfundenen Nullwerte oder zufällige Freigabe ergeben. Kurzzeitig nicht verfügbar ist vom dauerhaft nicht gemappten Profil zu unterscheiden.
- Eine echte Änderung der Profilverfügbarkeit ist Produktpolitik und benötigt gesonderte Zustimmung.

Belege: PDF S. 575–580, 609; `docs/pv-optimum-design.md`, `docs/pv-grid-import-policy-design.md`.

### PV-ARCH-04 – Zuständigkeitsgrenze [BESCHLOSSEN]

`wallbox-manager` ist für Profilpolitik, Ladebetriebspunkt und OCPP-Steuerung zuständig. Die Fronius-/BYD-Integration liefert Messwerte; der Wallbox Manager steuert den Hausakku **nicht** parallel über Modbus. Die Wallbox-Hardware verantwortet CP, Fahrzeugzustand und reale Phasenrelais. Ein späterer Energiemanager darf keinen zweiten konkurrierenden Regler erzeugen. Wiederverwendung bedeutet **gemeinsame Funktionen**, nicht duplizierte Algorithmen.

Belege: PDF S. 1–19, 209–246, 579–583.

---

## 2. Gemeinsame Batterie-SoC-Zustandsmaschine

### PV-SOC-01 – Ein Ziel-SoC T, ein Hystereseparameter H [BESCHLOSSEN]

`R` = gemeldete Mindestreserve der Hausbatterie, `H` = gemeinsamer Parameter `soc_hysterese`, standardmäßig **2 Prozentpunkte**, `T` = profilabhängiger Batterie-Ziel-SoC. Für gültige Einstellungen gilt:

`R + H <= T <= 100 - H`.

Drei Schwellen:

- `L = T - H/2` (untere Stoppschwelle)
- `M = T + H/2` (Bedingung für Start mit PV-Überschuss)
- `U = T + H` (FAST-Eintrittsschwelle)

**Nicht** zwei getrennte Konfigurationsparameter „Hysterese“ und „Puffer“ einführen.

Belege: PDF S. 133–145, 148; `docs/pv-soc-policy.md`.

### PV-SOC-02 – Vollständige, vom Vorzustand abhängige Matrix [BESCHLOSSEN]

| Batterie-SoC | bisher STOP/pausiert/neue Sitzung | bisher PV_BALANCE | bisher FAST_DISCHARGE |
|---|---|---|---|
| `SoC <= L` | STOP | STOP | STOP |
| `L < SoC < M` | STOP | PV_BALANCE fortsetzen | zu PV_BALANCE |
| `M <= SoC < U` | PV_BALANCE **nur** bei `PV > Hausverbrauch ohne ausgewähltes EV`; sonst STOP | PV_BALANCE fortsetzen | FAST_DISCHARGE fortsetzen |
| `SoC >= U` | FAST_DISCHARGE starten | zu FAST_DISCHARGE | FAST_DISCHARGE fortsetzen |

Die PV-Überschussbedingung ist **Startnachweis**, kein laufender Zwang zum STOP in BALANCE. Sie ist **keine** Voraussetzung für FAST_DISCHARGE (auch nachts möglich). Sinkt der SoC unter L, hat der SoC-STOP Vorrang vor wirtschaftlicher Wartezeit. Exakte Grenzwerte und Gleichheitsfälle folgen `docs/pv-soc-policy.md`.

Belege: PDF S. 92–99, 133–145; `docs/pv-soc-policy.md`.

### PV-SOC-03 – Zielwert je Profil [BESCHLOSSEN]

- **PV_MAXIMUM:** `T = R + H` (keine eigene Ziel-SoC-Einstellung).
- **PV_SURPLUS mit Batterie:** Benutzerwert `T` im zulässigen Intervall. **Auch hier ist oberhalb U absichtliche Batterieentladung für das Auto erlaubt**; PV_SURPLUS kann PV_MAXIMUM bei gleichem T nachbilden.
- **PV_OPTIMUM:** dynamisches T, durch eigene, geordnete untere/obere Benutzergrenze beschränkt; beide liegen zusätzlich im global zulässigen Intervall.
- **PV_SURPLUS ohne Batterie:** keine SoC-Zustandsmaschine und keine Batterie-SoC-Pflicht; eigene PV-/Netzüberschusspolitik, aber gemeinsame technische und wirtschaftliche Stopplogik.

Belege: PDF S. 140–148, 580–583.

---

## 3. PV_OPTIMUM und Tagesprognose

### PV-PLAN-01 – Zweck [BESCHLOSSEN]

Zu Beginn des PV-Tages darf die Batterie bis zur zulässigen dynamischen Zielgrenze entladen werden, damit wieder Kapazität zum Zwischenpuffern frei wird. Gegen Ende des möglichen Tagesertrags verschiebt sich T nach oben, sodass die obere Zielgrenze für Hausverbrauch am Abend/in der Nacht nach Möglichkeit wiederhergestellt wird. Der WBM **erzwingt keine nächtliche Speicherladung**. Kurze neue BALANCE-Ladephasen sind zulässig, solange die gemeinsam vereinbarten SoC-/Startregeln erfüllt sind.

Belege: PDF S. 1–19, 92–114; Bestätigung in der aktuellen Unterhaltung.

### PV-PLAN-02 – Drei Tagesphasen [BESCHLOSSEN; spätere Entscheidung ersetzt frühere]

1. `BEFORE_SURPLUS`: von lokalem Tagesbeginn bis zum **ersten nachgewiesenen strikten PV-Überschuss** `PV > Hausverbrauch ohne ausgewähltes EV`; Ziel = obere SoC-Grenze.
2. `DYNAMIC`: Ab diesem Erstereignis wird der Ziel-SoC regelmäßig berechnet. Eine Wolke oder vorübergehend negative Momentanbilanz nimmt den Beginn nicht zurück.
3. `FINISHED`: Sobald `remaining_PV_Wh <= remaining_house_Wh`, Ziel sofort auf obere Grenze setzen und diesen Tageszustand **bis Mitternacht** halten, auch wenn später eine neue Prognose günstiger würde.

Lokaler Tageswechsel setzt das Tages-Latch zurück. Der Tagesstatus wird versioniert persistiert; ein Reload darf keinen fiktiven erstmaligen PV-Überschuss erzeugen.

**Damit abgelöst:** „PV erstmals >=100 W“, „100 W für fünf Minuten“, Sonnenaufgang, starrer Sonnenuntergang oder `Sonnenuntergang – 1 h` als Planungsbeginn/-ende. Siehe Ablöseregister.

Belege: PDF S. 1, 98–114, 115–119; `docs/pv-optimum-design.md`.

### PV-PLAN-03 – Berechnung und Zeitmaß [BESCHLOSSEN]

Prognose ist **restliche PV-Energie für den restlichen lokalen Kalendertag**. `estimated_daily_house_consumption_kwh` bezeichnet einen angenommenen **24-h-Tagesverbrauch ohne EV**. Es wird linear nur der Rest dieses Tages angesetzt, mit korrekter Dauer bis zur nächsten lokalen Mitternacht (auch DST-Tage):

```
remaining_house_Wh = daily_house_kWh * 1000 * remaining_day_seconds / 86400
remaining_surplus_Wh = remaining_PV_Wh - remaining_house_Wh
T = clamp(upper_soc - 100 * remaining_surplus_Wh / nominal_storage_capacity_Wh,
          lower_soc, upper_soc)
```

Bei `remaining_surplus_Wh <= 0` greift stattdessen `FINISHED`. Nominale Batteriekapazität kommt aus einer gemappten realen Wh-Entity, **nicht** aus dem SoC allein. Konfigurierter Batterie-Entladeleistungsgrenzwert ist **nicht** der SunSpec-Nameplate-Wert `MaxDisChaRte`. Batterie-Kapazität und Entladeleistungsgrenze sind verschiedene Größen.

Belege: PDF S. 12–17, 28–30, 44, 108–115; `docs/pv-optimum-design.md`.

### PV-PLAN-04 – Planung und Aktualität [BESCHLOSSEN]

Reguläre Neuplanung etwa alle **300 s**; festgelegte Ereignisse können sofort neu berechnen. Solcast-Updates dürfen T zwischen den Rechenzeitpunkten beeinflussen und in **beide Richtungen** verändern, solange `DYNAMIC` noch aktiv ist. Die externe Integration übernimmt API-Abrufe und interne Forecast-Aktualisierung; WBM startet **keine** eigenen Solcast-Requests (Limit der kostenlosen API nicht aushebeln). Restprognose hat eine eigene langsamere Frischeklasse von derzeit **900 s**, andere notwendige Live-Messungen weiterhin strengere Frische-/Gültigkeitsregeln. Ein ablaufender Forecast muss diagnostizierbar sein.

Belege: PDF S. 37–52, 101–119; `docs/pv-optimum-design.md`.

---

## 4. FAST_DISCHARGE – Batterie-Headroom nutzen

### PV-FAST-01 – Zweck und Ausgangsbasis [BESCHLOSSEN]

Ab U soll das Auto die erlaubte, **zusätzlich zur aktuellen Hauslast** verfügbare Batterieentladeleistung zügig, **aber kontrolliert** nutzen. Nicht pauschal auf 9 A begrenzen. Bei tatsächlich vorhandener Headroom-Evidenz soll über den Mindestpunkt hinaus hochgeregelt werden. Vollständige Ausnutzung bis auf das letzte Watt ist jedoch **kein** Qualitätsversprechen; eine begründete konservative Reserve ist ausdrücklich akzeptiert (Praxistest beta.10, PDF S. 476).

### PV-FAST-02 – Konfigurierte Entladegrenze ist wirtschaftlich [BESCHLOSSEN; wichtige Korrektur zu V2]

Die benutzerspezifische Batterieentladegrenze `D` (im Test z. B. 3.500 oder 4.500 W) ist **das wirtschaftliche reguläre Budget für die gesamte tatsächliche Speicherentladung**, einschließlich übriger Hauslast. Sie ist **keine vom WBM garantierbare physische Batterie-Schutzabschaltgrenze**. In regulären positiven Betriebspunkten oberhalb des Minimums ist dieses Budget einzuhalten. **Am exakt kleinsten technisch ausführbaren positiven Betriebspunkt** darf es als *eng begrenzte wirtschaftliche Ausnahme* vorübergehend überschritten werden; die gemeinsame 50-Wh-Entscheidung begrenzt dann über **tatsächlich gemessenen Netzbezug** allein die wirtschaftliche Fortsetzung. Tatsächliche Strom-, Phasen-, Geräte-/Inverter-Sicherheitsgrenzen dürfen niemals als „wirtschaftlich“ umetikettiert und übergangen werden.

Belege: PDF S. 568–571; `docs/pv-grid-import-policy-design.md`, Entscheidungs-Hierarchie. **Hinweis:** Bei 50 Wh geht es ausschließlich um **Netzbezug**, nicht um Batterieentladeenergie.

### PV-FAST-03 – Zeitliche Rückkopplung und Asymmetrie [BESCHLOSSEN]

- Gemessene EV-Leistung ist **nicht** gleich angebotenem oder quittiertem OCPP-Sollwert.
- Neue Erhöhung benötigt ausreichend **neue** relevante Quelleninformation und den definierten Nachweis der physikalischen Antwort; bloß wiederholte HA-Reports dürfen keine Aufwärtsrampe vortäuschen.
- Jede Messwertgeneration wird höchstens einmal als neuer Aufwärtsbeleg verwendet. Kein weiteres normales Hochregeln bei noch nicht beantworteter Stromänderung.
- **Langsam erhöhen, schnell und ausreichend stark reduzieren** bei nicht abgedecktem Netzbezug, echter Budgetüberschreitung oder Verbrauchs-/PV-Sprüngen. Eine Sicherheitsreduktion darf nicht auf einen Erhöhungstimer warten.
- Vorhandene Reaktions-/Einschwing- und Netzbezugs-Toleranzen der fachlichen FAST-Regelung bleiben erhalten, **soweit die neuere gemeinsame 50-Wh-STOP-Politik nicht ausdrücklich ersetzt hat**.
- Kein neues universelles fixes 20-/30-s-Erhöhungsintervall oder „einmal pro Messbericht +1 A“ ohne Nachweis, dass dies zu dieser Architektur passt.

Belege: PDF S. 31–40, 209–246, 266–320, 400–426; `docs/beta9-implementation-report.md`, `custom_components/wallbox_manager/pv_regulators.py`.

### PV-FAST-04 – Messwertdisziplin und harte Betriebspunkte [TECHNISCH PRÄZISIERT]

Die beta.9-Budgetberechnung verwendet frische Rohleistungen, wirtschaftliche Unsicherheitsreserve von 100 W, Quellengenerationen, bei asynchronen EV-/Site-Messwerten einen konservativen EV-Basiswert und eine getrennte Rückmeldung nach OCPP-Bestätigung. Eine für sich genommen positive Sollleistung darf weder diskrete Strom-/Phasengrenzen noch die gerade gültige **gewöhnliche** Leistungsobergrenze umgehen. Ein Minimum-Ausnahmezeugnis ist **genau auf den technischen Mindestpunkt** begrenzt, nicht auf andere Betriebspunkte und nicht auf unbestätigte Physik. Die neuere beta.13-Politik hat den wirtschaftlichen Mindest-STOP durch 50 Wh ersetzt; dies ist in der Anwendung auf die ursprünglichen beta.9-Formeln ausdrücklich zu berücksichtigen.

Belege: `docs/beta9-implementation-report.md`, `docs/pv-grid-import-policy-design.md`.

---

## 5. PV_BALANCE – Überschussnutzung und Stabilität

### PV-BAL-01 – Batterieausstattung [BESCHLOSSEN]

Mit Hausbatterie wird die gemeinsame Leistungsregelung verwendet. `PV_BALANCE` orientiert sich am verfügbaren Überschuss, berücksichtigt den gemessenen **Gesamtverbrauch inklusive ausgewählter Wallbox** und korrigiert für **deren tatsächliche Ladeleistung**, ohne andere EVs fälschlich abzuziehen:

`P_Balance = P_PV - P_Gesamtverbrauch_inkl_ausgewählter_EV + P_ausgewählte_EV_ist`.

Wenn die wirtschaftliche Batteriezuordnung BALANCE ohne absichtliche Batterieentladung vorsieht, ist in der neuerlichen Wirtschaftlichkeitsprüfung `D = 0`. Für `PV_SURPLUS` ohne Batterie gilt dagegen **PV-ARCH-02**, nicht dieser zusätzliche PV-/Verbraucher-Mappingvertrag.

Belege: PDF S. 19–27, 579–583; `docs/pv-optimum-design.md`, `docs/pv-grid-import-policy-design.md`.

### PV-BAL-02 – Asymmetrische, stabile Regelung [BESCHLOSSEN]

Die Wallboxleistung soll sich nur kontrolliert erhöhen, aber bei echtem Leistungsdefizit, Wolken und plötzlichen Hauslasten **rasch und bedarfsgerecht absenken**. Nicht auf jeden scheinbaren Sensor-Sprung oder das von der Wallbox selbst erzeugte nachlaufende Messbild gegenregeln. Wiederholte Messwertkombinationen und nicht gemessene OCPP-Stellantworten dürfen weder unbegründete Erhöhung noch mehrfache hektische Richtungswechsel verursachen. Messwert-Frische, Quellengeneration, Stellantwort und zeitliche Asynchronität müssen **getrennt** behandelt werden.

**Technischer Ist-Prüfpunkt:** Die ausgefeilte beta.9-Quellen-/Stellantwortdisziplin ist im dokumentierten `FastDischargeRegulator` gut ausgearbeitet; ob `PV_BALANCE` auf allen heute relevanten Reglerpfaden gleich wirksam geschützt ist, ist anhand beta.13-Code und pv6-Log zu prüfen. Das ist **keine Ermächtigung**, die FAST-Logik zu kopieren oder die STOP-Policy zu ändern.

Belege: PDF S. 209–246, 401–426, 529–535, 540–545; aktueller Hardwaretest `pv6`.

### PV-BAL-03 – Frische und Glättung [BESCHLOSSENE EXISTIERENDE PARAMETER, keine Neueinführung]

Regelintervall (beispielsweise 5 s) und Zeitmittel (derzeit Default 5 s) sind **getrennte** Einstellungen. Das Zeitmittel gilt nach der bestehenden Dokumentation nur für **PV- und gesamten Verbraucherleistungswert**, nicht für SoC, elektrische Sicherheit, Spannung oder gemessene EV-Leistung. Ein 5-s-Zyklus beweist keine neuen physisch synchronen Fronius-/EV-Daten. Zusätzliche Glättung darf einen gefährlichen Lastsprung nicht unzulässig maskieren und ist gegen die vorhandene Messgeneration-/Stellantwortpolitik zu prüfen.

Belege: PDF S. 31–40, 209–246, 529–535; `README.md` (Regulation parameters).

### PV-BAL-04 – Nur gemeinsame Wirtschaftsstopp-Policy [BESCHLOSSEN]

Der alte BALANCE-/SURPLUS-„90 Sekunden dauerhaft zu wenig Überschuss“-STOP ist **durch die gemeinsame 50-Wh-Regel ersetzt**, wo deren gemessene Netzbezugsevidenz vorhanden ist. Der Konfigurationswert `pv_stop_delay` darf aus Kompatibilität gespeichert bleiben, ist aber **nicht mehr der wirtschaftliche Stopp-Timer**. Das ist von normalen SoC-Stopps, physischem Zero-Current-OFF und technischen Startsperren zu trennen.

Belege: PDF S. 542–545, 566–600, 606–613; `docs/pv-grid-import-policy-design.md`.

### PV-BAL-05 – Prüfkriterien für die nächste Hardwareiteration [OFFEN]

Noch **nicht** vereinbart sind belastbare quantitative Grenzen für Richtungswechsel pro Zeit, Nachregelzeit nach Lastsprung, minimal erforderliches Erhöhungspotenzial oder maximal akzeptable PV-Überschussverluste. Diese werden erst nach Messwert-/Kausalitätsanalyse festgelegt. Die bereits beschlossene qualitative Asymmetrie ist aber verbindlich.

---

## 6. Technischer Mindestpunkt, Phasensperren und OCPP

### PV-PHASE-01 – Nachweisbarer technisch ausführbarer Mindestpunkt [BESCHLOSSEN]

Die minimale Ladeleistung `M` wird aus tatsächlich **aktuell ausführbaren** Phasen, Strömen, Spannung, Geräte-/EVSE-Limits, nachgewiesener physischer Stellung und aktiver Phasenumschaltsperre abgeleitet. `M` ist nicht immer 1p/6 A. Bei bestätigter 3p-Sperre kann 3p/6 A das relevante Minimum sein. Eine nur geplante oder zeitlich vermutete 1p-Umschaltung zählt **nicht** als gegenwärtig ausführbar. Unbekannte physische Nachweise rechtfertigen kein erfundenes 1.380-W-Minimum.

Belege: PDF S. 72–83, 477–480, 569–571; `docs/phase-lockout-continuation-report.md`, `docs/pv-grid-import-policy-design.md`.

### PV-PHASE-02 – Weiterladen bei Phasenumschaltsperre [BESCHLOSSEN]

Ein abgelehnter 3p→1p-Wechsel aufgrund einer echten Phasensperre erzeugt **nicht automatisch einen STOP**. Der bestätigte ausführbare Phasenmodus bleibt aktiv; der Strom wird bei Bedarf bis zu dessen exaktem positiven Minimum reduziert. Der energetisch gewünschte Modus bleibt erhalten; spätere begrenzte Probes/Retry arbeiten mit **frischen** Eingangsdaten. Unmittelbare Gegenumschaltung nach bestätigtem Phasenwechsel muss durch die bestehende Einschwinglogik verhindert werden. Generisches OCPP BUSY ist kein Nachweis einer Phasensperre.

Belege: PDF S. 77–90, 477–480; `docs/phase-lockout-continuation-report.md`.

### PV-OCPP-01 – Befehls- und Istzustand [BESCHLOSSEN]

Ein bestätigter Strom-/Phasen-Sollwert beweist **keine** gemessene Leistung. Befehle werden serialisiert, an nachgewiesene Steuerautorität (`Remote`), zulässige Ladefreigabe, Transaktion und aktuelle Gerätedaten gebunden; bestehende Live-Fences gelten. Eine wirtschaftliche **0-A-Pause** benutzt das verifizierte Zero-Current-OCPP-Profil und lässt `ChargingEnabled` bestehen; stationsseitige Reenable- und Phasensperren sowie Retrybedingungen dürfen nicht umgangen werden. Benutzer-OFF und echte technische/SoC-Stoppgründe haben eigene Priorität.

Belege: PDF S. 67–83, 209–246, 477–480; `docs/pv-surplus-profile.md`, `docs/pv-grid-import-policy-design.md`.

### PV-OCPP-02 – Kommandosnapshot und Messwertänderung [BESCHLOSSEN]

Bei noch **nicht** gesendetem Befehl werden die definierten Pre-Dispatch-Nachweise erneut geprüft. Normale Änderungen von PV-, Haus-, EV-, Spannung- und SoC-Werten nach einem erfolgreichen Dispatch machen den gesendeten Punkt nicht rückwirkend zu einem „stale“-Fehler. Nach Dispatch bleiben wirkliche Autoritäts-, Permission-, Connection-, Transaction-, Capability-, Intent- und Safety-Fences aktiv. Neueste gültige Messwerte werden im folgenden Regeltakt verarbeitet. Ein abgebrochener oder nicht bestätigter OFF-Versuch darf keinen fälschlich endgültig gestoppten Zustand erzeugen; ein spät bestätigtes positives Kommando darf einen **neueren** nachgewiesenen 50-Wh-STOP nicht aufheben.

Belege: PDF S. 103–119, 524–527, 605; `docs/pv-surplus-profile.md`, `docs/pv-grid-import-policy-design.md`.

### PV-OCPP-03 – Keine stillen Autoritätsübernahmen [BESCHLOSSEN]

Nur explizite Benutzeraktion erwirbt die Autorität. Bei `Local`/fehlender Autorität werden keine positiven Steuerbefehle versendet. Der Status meldet **fehlende Autorität vorrangig**, nicht irreführend eine verspätete Meldung über fehlende Messwerte; tatsächliche Sensordiagnose bleibt getrennt sichtbar. Nach Authority-Wechsel oder Reconnect muss die Anzeige zügig aktualisiert werden.

Belege: PDF S. 511–528; `docs/beta12-implementation-report.md`.

---

## 7. Einheitliche wirtschaftliche 50-Wh-Netzbezugspolitik

**Dieser Abschnitt ersetzt die historisch unterschiedlichen wirtschaftlichen STOP-Regeln.** Er folgt den ausdrücklichen fünf Architekturfreigaben (PDF S. 570–575) und der danach konkretisierten Profil-/Wiederanlaufregel (S. 575–600), zusammen mit `docs/pv-grid-import-policy-design.md` aus beta.13.

### PV-ECO-01 – Geltungsbereich und Priorität [BESCHLOSSEN]

Einheitlicher wirtschaftlicher STOP für **PV_MAXIMUM, PV_OPTIMUM, PV_SURPLUS mit Batterie und PV_SURPLUS ohne Batterie**, sofern notwendige **gültige gemessene Netzbezugsevidenz** vorhanden ist. Er **ersetzt** den bisherigen wirtschaftlichen Mindestbudget-Sofortstopp, den eigenen 10-s-Mindestbezugs-STOP und die BALANCE-/SURPLUS-Defizit-Timer. Kein zweiter paralleler wirtschaftlicher STOP. SoC-Schutz, technische Schutzgrenzen, Authority, Permission, OCPP-Lifecycle, Startberechtigung und bestehende technische Retrys werden **nicht** durch 50 Wh ersetzt.

### PV-ECO-02 – Dynamische Vergleichsschwelle [BESCHLOSSEN]

`T_import = M / 2`, wobei `M` der **gerade tatsächlich ausführbare, verifizierte positive Mindestladepunkt in Watt** ist. Bei 230 V und 1p/6 A ungefähr 690 W; bei 3p/6 A ungefähr 2.070 W. Eine geänderte tatsächliche Phasenfähigkeit oder Spannung ändert die Schwelle ab ihrem kausalen Beobachtungszeitpunkt, **nicht rückwirkend**. Die 50 % sind keine starre Leistungseinstellung.

### PV-ECO-03 – Genau welche Energie zählt [BESCHLOSSEN]

Wenn die **tatsächlich gemessene positive Netzbezugsleistung `P_import > T_import`** beträgt, zählt die **gesamte** Netzbezugsleistung über die nachgewiesene Zeitdauer in das Budget, nicht nur der Anteil über T_import. Bei `P_import <= T_import` wird nicht integriert, bereits gesammelte Energie bleibt zunächst bestehen. Budgeterschöpfung bei **50 Wh** löst den wirtschaftlichen OFF-Auftrag aus. Die 50 Wh sind **Befehlsauslöseschwelle**, keine garantierte physische Obergrenze des nachfolgenden Netzbezugs (Mess- und OCPP-Latenzen).

### PV-ECO-04 – Rücksetzung / Dynamische Frist [BESCHLOSSEN]

Nur **streng mehr als 15 Sekunden** ununterbrochen **nachgewiesener** Netzbezug `P_import <= T_import` setzt das Budget zurück. Genau 15 Sekunden genügen nicht. Kurzzeitige Unterschreitungen pausieren die Akkumulation, löschen aber das Budget nicht. Eine Phasen-/Spannungsänderung wechselt die Schwelle prospektiv und setzt die bereits angesammelte Energie nicht willkürlich zurück.

### PV-ECO-05 – Messlücken und Kausalität [BESCHLOSSEN + TECHNISCH PRÄZISIERT]

Für jedes gewertete Intervall müssen gültige unterschiedliche Messgenerationen, Quellen- und Annahmezeitbegrenzung **jeweils höchstens 10 Sekunden** vorliegen. Größere/unbekannte Abstände: **weder Energie integrieren noch die 15-s-Resetfrist fortschreiben**; bisherige nachgewiesene Energie erhalten; danach einen neuen Messanker setzen. Keine doppelte Integration von Wiederholungsmeldungen, alten oder widersprüchlichen Samples. Aktuelle Implementierung hält den letzten gültigen Wert zwischen zwei gültigen Quellenzeitpunkten konstant (Stufenintegration); exakte Wattsekunden statt fehleranfälliger Rundungen. Schwellenwechsel, OFF/Charging-Aktivitätsänderungen und Ursachezeitpunkte teilen Intervalle. Keine fiktive Energie durch lückenlose HA-Updates derselben physischen Quelle.

### PV-ECO-06 – Eng begrenzte technische Mindestpunkt-Ausnahme [BESCHLOSSEN]

Wenn das normale wirtschaftliche Budget keinen positiven Punkt zulässt, darf ein **bereits zulässiger** Ladevorgang mit **exakt M** fortgesetzt werden, selbst wenn dadurch `D` vorübergehend überschritten wird; **kein beliebig höherer Punkt** darf sich auf diese Ausnahme berufen. Startup-Response-Reserve, OFF-Antwort-Nachweis, SoC-/Profilschwellen, reale Strom-/Phasengrenzen, Authority/OCPP-Fences bleiben verbindlich. Die korrekte physische M-Bestimmung wird vor Senden nochmals geprüft. Die wirtschaftliche Fortsetzung endet erst durch die 50-Wh-Regel oder einen **unabhängig gültigen** anderen Stoppgrund.

### PV-ECO-07 – STOP-Provenienz und Transaktionsbindung [BESCHLOSSEN + TECHNISCH PRÄZISIERT]

Netzbezugsenergie, Aktivitäts-/Pending-/In-Flight-/Paused-Zustand, STOP-Ursache sowie Wiederanlaufreferenz gelten **sitzungs- und autoritätsgebunden**. Sie werden mit Home-Assistant-Store persistent gehalten, sofern dieselbe Entry, Wallboxidentität, Autorität, Connector, externe OCPP-Transaktion und feste Policy-Kontextdaten nachgewiesen werden können. Weder Reload noch Profilwechsel/erneutes PV-Profil-Anklicken darf ein verbrauchtes Budget blind zurücksetzen. Während Downtime **keine** Energieintegration oder Reset-Frist fortschreiben. Unsichere Wiederherstellung erlaubt keinen automatischen positiven Punkt. Explizites OFF, NETZ-Scope-Exit und belegtes echtes Transaktionsende schließen den Scope nach den implementierten Regeln. Späte Speichervorgänge oder verspätete ACKs dürfen einen jüngeren OFF nicht wieder rückgängig machen.

### PV-ECO-08 – Wirtschaftlicher automatischer Wiederanlauf [BESCHLOSSEN]

Ein alleiniger Rückgang des Netzbezugs, bloßer 15-s-Budget-Reset, neues Messereignis oder Ablauf einer Wartezeit bewirkt **keinen** Neustart nach wirtschaftlichem STOP. Erfordert zusätzlich **alle** Profil-/SoC-/Autoritäts-/Permission-/Transaktions-/Elektro-/Startup-Bedingungen, gültige frische Messungen und eine **materielle Verbesserung** der wirtschaftlichen Situation gegen den beim ersten nachgewiesenen STOP gespeicherten Referenzzustand. Batterieunterstützung darf nicht unterstellt als „null verfügbar“ abgewiesen werden; ein zusätzlicher PV-only-Zwangssensor oder neuer frei erfundener Neustarttimer ist unzulässig. Geprüft wird zunächst der normale bevorzugte Punkt, gegebenenfalls der genaue technisch erreichbare Mindestpunkt.

Die mit beta.13 **technisch festgelegte** und zu testende Bedingung lautet:

```
C_now - max(W_candidate, M_now) > C_stop - M_stop + 100 W
```

Dabei gilt mit `E` = gemessene EV-Leistung, `I` = Netzbezug, `X` = Einspeisung, `G=I-X`, `B` = gemessene Speicherentladung, `PV` = Erzeugung, `L` = gesamte Verbraucherleistung inkl. EV, `D` = konfigurierte wirtschaftliche Batterieallocation in FAST (in BALANCE `D=0`):

- Ohne Batterie: `C = E - G`.
- Mit Batterie: `C = min(E + D - B - G, E + D + PV - L) - 100 W`.

Der Vergleich verwendet tatsächliche EV-Leistung und einen physisch ausführbaren Mindestpunkt. Ein nur hypothetisch später möglicher Phasenwechsel ist kein billigerer aktueller Betriebspunkt. Vor dem Vergleich müssen EV- und Site-Messungen eindeutig **nach** dem bestätigten OFF liegen, einschließlich EV-Nullantwort (aktuelle Schwelle ≤100 W). Gleichheit oder Veränderung um nur 100 W genügt nicht. Dieser Vertrag ist ein **Modell**, keine Zusage, dass die Batterie beim nächsten Start tatsächlich sofort einspringt; deshalb bleibt die 50-Wh-Nachkontrolle nötig.

Belege für PV-ECO-01 bis -08: PDF S. 539–545, 566–583, 595–600, 605–613; `docs/pv-grid-import-policy-design.md`, Abschnitte *Decision hierarchy*, *Accounting convention*, *Model-based restart*, *Persistence and recovery*.

---

## 8. Technische Validierung, Diagnose und Qualitätsvertrag

### PV-QA-01 – Diagnose nicht mit Wirksamkeit verwechseln [BESCHLOSSEN]

Logeinträge unterscheiden geplanten, gesendeten, akzeptierten, bestätigten und **physisch wirksam gemessenen** Punkt. Aus einem OCPP `Accepted` folgt keine passende Netz-/Batteriebilanz. Diagnose mit Quellgeneration, Zeitbasis, Messwertalter, wirtschaftlichem `M`, `T_import`, Energie-Wh, Reset-Stand, vorheriger Stoppreferenz, Zustandsübergang, Request/Applied und Grund. Diagnoselevel 0–3 bleiben differenziert; Level 3 darf keine zweite Sensorabfrage auslösen und nicht unnötig Last verursachen.

Belege: PDF S. 99–119, 235–252, 529–535; `docs/pv-surplus-profile.md`, `docs/pv-grid-import-policy-design.md`.

### PV-QA-02 – Pflichtregressionen [BESCHLOSSENE INVARIANTEN + VORGESCHLAGENE TESTFÄLLE]

1. Gemeinsame SoC-Matrix mit exakten L/M/U-Grenzen, nachts FAST, START versus Fortsetzung, bei PV_MAXIMUM und PV_SURPLUS gleichem T identisches Ergebnis.
2. DYNAMIC/FINISHED-Latch, lokale Mitternacht/DST, prognostische Sprünge in beide Richtungen, 300-s-Replan, Forecast-Stale ohne unnötigen Solcast-Aufruf.
3. FAST: neuer Sensor-Satz nur einmal, EV-Stellantwort vor weiterer Steigerung, genug wirtschaftlicher Headroom, keine unbegrenzte Antwortsperre, schnelle echte Reduktion.
4. BALANCE: konstante Sonne und Last, bewusste PV-/Haushaltssprünge, asynchrone Berichte, konfigurierbare Glättung, keine 5-s-6A/14A-Pendelung durch eigene Stellkommandos.
5. Phasenwechsel 1p↔3p mit Lockout; innerhalb bestätigter 3p-Sperre 3p-Mindestpunkt halten; keine unmittelbare Gegenumschaltung, keine gefälschte Sperrablaufzeit.
6. Dynamischer technisch **erreichbarer** Mindestpunkt und 50%-Schwelle, einschließlich geänderter Spannungen/Phasennachweise; keine Mindestpunkt-Ausnahme für >M.
7. 50-Wh-Energie: nur gesamte gemessene Importleistung in P>T, >15 s zum Reset, 10-s-Quellen- **und** Annahmeabstand, Duplikat/Lücke/Stale/Gleichheit, OFF-Aktivität.
8. Wirtschaftliche STOP-Priorität: keinerlei alter 10-s-, 90-s-, H-minimum- oder weiterer unabhängiger wirtschaftlicher OFF-Pfad; echte SoC-/Sicherheitsstopps bleiben.
9. Persistence: HA-Neustart/Reload, aktiver Scope, genuine transaction end, Profilwechsel, Authority/Connection-Wechsel, späte ACK/Checkpoints und Fehlerszenarien.
10. Restart: Modellvergleich strenges `>`, 100-W-Abstand, Phasen-Mindestpunkt und Erhöhung von D, Nullantwort-/Quellenkausalität, unveränderte Situation blockiert, echte Verbesserung erlaubt.
11. Batterie-freies SURPLUS mit **nur** Grid Import/Export + EV-Mapping; unvollständige Konfiguration bleibt gesperrt.
12. Keine automatische Local→Remote-Takeover, keine unautorisierten Befehle, Statusmeldungen priorisieren Authority.

### PV-QA-03 – Hardware-Abnahmekriterien [OFFEN]

Verbindlich ist die **Absicht**, nicht eine unbelegte fixe Zahl: schnelle wirtschaftlich sinnvolle Entladung in FAST; ruhiger Regelkreis und bedarfsgerechte Störungen in BALANCE; keine unnötigen Nullstrompausen mit 600-s-Wiederanlaufsperre; nachvollziehbare Netzbezugsenergie statt Momentan-Spitzen. Vor der nächsten algorithmischen Änderung sind realistische Soll-Kennzahlen für Anzahl Stellwechsel, maximale Zeit bis zur Defizitkorrektur, Energieausnutzung und Neustartzuverlässigkeit festzulegen; vorzugsweise aus kontrollierten Lastsprung-/Wolken-Szenarien.

---

## 9. Beobachtungen aus beta.13 – ausdrücklich **keine** stillschweigenden Soll-Änderungen

- **OBS-13-01:** Im Test `pv6` blieb FAST für einen langen Zeitraum auf 1p/9 A. **Abweichungskandidat** gegen PV-FAST-01, aber erst nach Untersuchung von `D`, tatsächlichem `B`, EV-/Site-Evidenz, bestätigter Antwort, Regler-Hardbudget und Start-/Aufwärtsfences bewertbar. Eine begründete konservative Reserve war früher akzeptiert.
- **OBS-13-02:** `PV_BALANCE` pendelte 15:24–15:34 und 15:58–16:08 wiederholt zwischen niedrigen und höheren Amperewerten. Relevanter Soll-Vertrag PV-BAL-02 und PV-QA-02. Noch nicht bewiesen ist, welcher konkrete Teilpfad die mehrfachen Richtungswechsel verursacht. Erst Kausalanalyse, dann minimaler Fix.
- **OBS-13-03:** Dynamischer T-Anstieg, `FINISHED` und SoC-bedingte Ladepausen sind nach Benutzerklärung **beabsichtigte Strategie**. Nur konkrete Verletzungen von PV-PLAN/PV-SOC weiterverfolgen.
- **OBS-13-04:** Fronius-, EV- und Netzwerte laufen physisch und über HA asynchron; scheinbare negative/positive Leistungspeaks sind kein unmittelbarer Energieverbrauchsbeweis. Die WBM-Entscheidung muss anhand der **zum Zeitpunkt verfügbaren gültigen Daten** bewertet werden; Netzenergie anhand der getrennt beweisbaren Messung.
- **OBS-13-05:** Die beta.13-Quellenbeschreibung meldet 2.420 Python-Tests/145 Frontend-Tests ohne Hardwarefreigabe. Der spätere `pv6`-Test ist ein **neuer Hardwarebefund**, keine nachträgliche Bestätigung sämtlicher automatisierter Szenarien.

---

## 10. Explizit abgelöste Regeln – **nicht** in die Soll-Matrix zurückführen

| Historischer Ansatz | Späterer verbindlicher Ersatz | Quelle |
|---|---|---|
| Tagesbeginn ab PV >=100 W (teilweise 5 min) oder Sonnenaufgang | Erstmalig `PV > Hausverbrauch ohne ausgewähltes EV` | PDF S. 1, 98–114 |
| Sonnenuntergang bzw. Sonnenuntergang − 1 h als mathematischer Planungshorizont | Restlicher **lokaler Kalendertag bis Mitternacht**, mit `FINISHED`, sobald PV-Rest <= Rest-Hausbedarf | PDF S. 98–119 |
| Nacht: nie FAST ohne PV | FAST ab SoC >= U, auch nachts ohne PV | PDF S. 92–99, 133–145 |
| STOP bereits bei SoC < T | SoC-Zustandsmatrix mit `L = T-H/2`; Vorzustand relevant | PDF S. 133–145 |
| Separate SoC-Logik für Überschuss, Optimum, Maximum | Gemeinsame SoC-Policy und Regler; Profil liefert T | PDF S. 140–148, 579–583 |
| Unbegrenztes FAST-Mindestladen trotz wirtschaftlicher Budgetlage | Nur **exaktes technisch ausführbares M** als Ausnahme, einheitliche 50-Wh-STOP-Entscheidung | PDF S. 568–575, 599–600 |
| SoC-STOP allein bei Phasenlockout unter T / eigenständige 10-s-Netzimportfrist | Gemeinsame SoC-Matrix und gemeinsame dynamische M/2-/50-Wh-Policy | PDF S. 477–480, 539–545, 599–600 |
| `hard_budget_pause`/H-minimum als **wirtschaftlicher** Sofort-STOP | Exakt M darf weiterladen; ekonomischer STOP nur über 50 Wh | PDF S. 566–600 |
| 90-s-Defizit-STOP in BALANCE und SURPLUS | Einheitliche gemessene 50-Wh-Entscheidung; `pv_stop_delay` bleibt nur gespeicherte Legacy-Option | PDF S. 542–545, 599–600, 606–613 |
| Zeitbasierter 10-s-STOP bei über M/2 | 50 Wh tatsächliche Bezugsenergie, Rücksetzung streng >15 s; 10 s nur Messlückengrenze | PDF S. 539–545, 571–575 |
| Zusätzliches 50-Wh-Gate *hinter* alten STOPs | Alte wirtschaftliche STOPs **ersetzen**, nicht überlagern | PDF S. 566–571, 599–600 |
| Nur PV-Überschuss für Neustart (auch mit Hausakku) | Batteriefähige **modellbasierte** Bedingung, >100 W Verbesserung, normale Profilfreigabe | PDF S. 572–600, 606–609 |
| PV_SURPLUS braucht zwingend separate PV-/Hausverbrauchs-Entity | **Batteriefreies** SURPLUS braucht Grid Import/Export plus EV-Messwert | PDF S. 575–580, 608–612 |
| Jeder Sensor-/Sollwertdrift cancelt OCPP-ACK/Dispatch | Nach Dispatch normale Drift im Folgezyklus; echte Live-Fences bleiben | PDF S. 103–119, 524–527 |
| Neues 20–30-s-Intervall als einfache „Lösung“ für Pendeln | Nicht beschlossen; vorhandene Quellen-/Antwort-/Asymmetrie-Logik zuerst prüfen | Aktuelle Unterhaltung, im Altchat keine finale Freigabe |

*„Abgelöst“ betrifft jeweils nur die genannte fachliche Regel. Unabhängige echte Schutz-, Geräte- und Autoritätsbedingungen bleiben erhalten.*

---

## 11. Widersprüche/Veraltung in bestehender Dokumentation – Review-Aufgaben, **keine** Codeaufträge

1. `docs/pv-optimum-design.md` trägt selbst einen Hinweis, dass alte FAST-/Mindestpunkt-/Gap-Regeln durch beta.9 ersetzt wurden. Seine alten Mindestpunkt- und PV-Wartezeitabschnitte dürfen **nicht** gegen die finale beta.13-Wirtschaftsstoppregel verwendet werden.
2. `docs/beta9-implementation-report.md` behandelt eine konservative wirtschaftliche H-Grenze in damaligen Sofort-Stopp-Pfaden; die Mindestpunkt-Ausnahme und einheitliche 50 Wh aus beta.13 haben für den Wirtschaftsstopp Vorrang. Quellengeneration und Stellantwortdisziplin sind hingegen weiter zu schützen.
3. `README.md` und ältere Profil-/SoC-Dokumente enthalten ältere Texte zu PV_MAXIMUM („deferred“), Mindestpunkt und `pv_stop_delay`. Solche Stellen sollten **in einem eigenständigen Doku-Bereinigungsschritt** als historisch oder aktualisiert markiert werden – ohne Logikneufassung.
4. `docs/pv-surplus-profile.md` dokumentiert ältere 90-s-Stopptimer, die für den von beta.13 ersetzten wirtschaftlichen Pfad nicht mehr normativ sind.
5. Der historische Auditbericht `docs/pv-decision-logic-audit.md` war zunächst lokal/unversioniert; die finale implementierte Regel ist dagegen **im getaggten Release** unter `docs/pv-grid-import-policy-design.md` greifbar. Für Änderungen ist die zweite Quelle primär.

---

## 12. Entwicklungs- und Testverfahren (ausdrücklich im Altchat festgelegt)

**PV-PROC-01 – Codex ist ausführendes Werkzeug [BESCHLOSSEN].** Architektur- und Produktentscheidungen werden zuerst hier fachlich diskutiert; Codex erhält **vollständige, englischsprachige** Ausführungsaufträge und darf ungeklärte Grundsatzfragen nicht eigenmächtig per Code entscheiden. Der Benutzer hat „Codex-Aufträge immer in Englisch“ ausdrücklich eingefordert (PDF S. 1, 405).

**PV-PROC-02 – HACS-Prerelease statt Direktinstallation [BESCHLOSSEN].** Iterative Hardwaretests finden nach explizit autorisierter GitHub-**Prerelease** und Installation über **HACS** statt. **Keine** manuelle Direktinstallation/Überschreibung der HA-Integration als vermeintlich bequemer Testpfad. `main` und die neueste stabile Veröffentlichung bleiben unangetastet, bis eine eigene Stable-Freigabe erfolgt. Feature-Implementierung, Review, Merge, Tag/Release, HACS-Installation und Hardwaretest werden getrennt autorisiert (PDF S. 441–451, 615–619).

**PV-PROC-03 – Verhältnismäßige Tests und Quellnachweise [BESCHLOSSEN].** Realistische gezielte Hardwaretests sind erwünscht; keine unnötigen aufwendigen Snapshot-/Backup-Prozesse nur für eine Entwicklungsversion. Versionierte Git-Stände erlauben ein Code-Rollback. Testlogs aus `.analysis/` bleiben lokal und gehören ohne gesonderte Entscheidung **nicht** ins Repository. Bei jeder Analyse gesamte zeitliche Sequenz und Sensor-/Befehlskausalität bewerten, nicht nur einen STOP isoliert betrachten (PDF S. 221, 441–448, 529–531).

**PV-PROC-04 – Fronius-/Modbus-Zeitbezug [OFFENER EXTERNER DEPENDENCY-CHECK].** Die frühere Umstellung der Wallbox-OCPP-MeterValues von 10 s auf 2 s und die Fronius-Modbus-/Messwert-Zeituntersuchung waren eigenständige Instrumentierungsschritte. Ein 2-s-OCPP-Report macht Fronius-/Batterie-/Netzwert nicht automatisch zeitgleich. Die Untersuchung eines physischen `observed_at`-Vertrags gehört gegebenenfalls in `fronius-pv-manager`; **keine unautorisierten Änderungen an dessen Polling oder an `wallbox-stationary`** als Nebenprodukt eines WBM-Fixes (PDF S. 307–320, 360–410, 529–535).

---

## 13. Vorschlag für dauerhafte Pflege

- Diese Datei nach Review als **einzige aktuelle fachliche PV-Soll-Spezifikation** unter `docs/lastenheft/pv-regelung.md` versionieren, mit fortlaufender Versionshistorie und unveränderlichen IDs.
- Für jede Codeiteration eine kurze Datei `docs/changes/PV-YYYY-NN.md` oder Ticket: `Anforderungen`, `Ist-Befund`, `geänderte Norm?`, `betroffener Code`, `Tests`, `Hardwareverifikation`, `ausdrücklich abgelöste Aussagen`.
- Die Historie einschließlich der PDF-Seitenhinweise bleibt als Entscheidungsnachweis erhalten; technische Implementierungsberichte dürfen keine neue „gleichrangige“ Produktpolitik begründen.
- Review/Korrekturen erst auf dediziertem Branch, vollständige Regression, keine automatische Änderung von `main`, stabilen Tags oder Wallbox-Firmware. Hardwaretest und Release separat freigeben.

## Quellen

### Originalverlauf

- *Wallbox Modbus-Abendauswertung.pdf*, 619 Seiten, vom Benutzer am 13.10.2026 hochgeladen und **in dieser Revision tatsächlich ausgewertet**. Seitenreferenzen beziehen sich auf den PDF-Ausdruck, nicht auf Chat-Nachrichten-IDs. Einige ursprünglich verlinkte Codex-Logs/-Patches sind nicht im PDF selbst enthalten.

### Normative bzw. technische Repository-Quellen (Tag `v0.3.2-beta.13`)

- [`docs/pv-grid-import-policy-design.md`](https://github.com/ekkehard-lutz/wallbox-manager/blob/v0.3.2-beta.13/docs/pv-grid-import-policy-design.md) – neueste wirtschaftliche Entscheidung, Energiezählung, Neustart, Persistenz, Tests.
- [`docs/pv-soc-policy.md`](https://github.com/ekkehard-lutz/wallbox-manager/blob/v0.3.2-beta.13/docs/pv-soc-policy.md) – gemeinsame SoC-Grenzen und Übergangsmatrix.
- [`docs/pv-optimum-design.md`](https://github.com/ekkehard-lutz/wallbox-manager/blob/v0.3.2-beta.13/docs/pv-optimum-design.md) – Tagesprognose, Zustandsphasen und historische FAST-Fortentwicklung.
- [`docs/phase-lockout-continuation-report.md`](https://github.com/ekkehard-lutz/wallbox-manager/blob/v0.3.2-beta.13/docs/phase-lockout-continuation-report.md) – Phasensperre, Mindestpunkt, Stabilisierung.
- [`docs/beta12-implementation-report.md`](https://github.com/ekkehard-lutz/wallbox-manager/blob/v0.3.2-beta.13/docs/beta12-implementation-report.md) – bestätigt/geändert: D1–D4 und Status-Priorisierung.
- [`docs/beta9-implementation-report.md`](https://github.com/ekkehard-lutz/wallbox-manager/blob/v0.3.2-beta.13/docs/beta9-implementation-report.md) – physikalische Messgenerationen, Stellantwort und konservatives Budget (teilweise später überschrieben).
- [`README.md`](https://github.com/ekkehard-lutz/wallbox-manager/blob/v0.3.2-beta.13/README.md) – Integrationseinstellungen (ältere Aussagen gezielt gegen jüngeren Beschluss prüfen).

**Freigabevermerk:** Diese V3 ist ein im Repository versionierter **konsolidierter Entwurf**. Die Aufnahme in GitHub ist **keine fachliche Freigabe** und kein Code- oder Deploymentauftrag. Erst nach ausdrücklicher fachlicher Zustimmung erhält sie den Status einer normativen Anforderungsbasis.