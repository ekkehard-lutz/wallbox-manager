# Wallbox Manager – Entscheidungsverlauf und Ablöseregister (04.–12.10.2026)

**Zweck:** Historische Nachvollziehbarkeit für die konsolidierte Anforderungsbasis V3, kein zweites Lastenheft. Der Bericht bezieht sich auf den am 13.10.2026 hochgeladenen 619-seitigen PDF-Druck des verlinkten Chats. Seiten beziehen sich auf den Ausdruck. Die Reihenfolge unten orientiert sich an den **späteren ausdrücklichen Nutzerentscheidungen**, nicht bloß an zeitweiligen Vorschlägen oder Codex-Berichten.

## Entscheidungen in chronologischer Reihenfolge

| Datum / PDF-Seiten | Gegenstand | Endgültiger Stand nach dem Verlauf | Überholt / nicht übernehmen |
|---|---|---|---|
| 04.10., S. 1–19 | Einführung `PV_OPTIMUM`, drei SoC-/Verbrauchsparameter, Kapazität | Unterer/oberer SoC, geschätzter 24-h-Hausbedarf ohne EV, konfigurierbare maximale Batterieentladung; **separat** nominale Speicherkapazität aus Wh-Sensor für Prozentrechnung | SoC-% allein als Ersatz für Speicherenergie; SunSpec-Nameplate MaxDisChaRte als reale Entladeleistungsgrenze |
| 04.–05.10., S. 19–40 | Wiederverwendung; FAST-Regelung | Gemeinsame Leistungsbausteine; kontrollierte Erhöhung, unmittelbare echte Reduktion; rasche Beobachtung (FAST), 3-s-Importreaktion, Planungsperiode 300 s – soweit später nicht überschrieben | drei kopierte Regler; eigene Speicher-Modbus-Steuerung |
| 05.10., S. 44–52 | Solcast-Forecast-Datenfrische | Resttagesprognose als eigene 900-s-Frischeklasse; reguläre Quellen bleiben live; keine eigenen WBM-Solcast-API-Requests | 90-s-Livefrische für langsam aktualisierten Forecast |
| 05.10., S. 67–90 | Frühzeitige 0-A-Pausen und Phasensperre | FAST soll bei wirtschaftlich kleinem Zwischenbudget zunächst den technisch möglichen **positiven** Punkt halten; 0 A löst echte stationseigene Wiederanlaufsperre aus. Gewünschter und tatsächlich ausführbarer Betriebspunkt getrennt | Nullstrom nur aufgrund kurzer Anlauf-Messwertschwankung; unverbindliche Phasenposition statt bestätigtem Zustand |
| 06.10., S. 92–99 | Nachtbetrieb / SoC | FAST ist **nicht** an aktuelle PV-Produktion gebunden; SoC-Policy entscheidet. Bei ausreichend SoC sind FAST-Starts auch nachts zulässig | „nachts zwingend PV_BALANCE/STOP“ |
| 06.10., S. 98–119 | Tagesstart, Endbedingungen, Enable, Diagnose | PV-Tag beginnt erst bei **erstmaligem PV > Hausverbrauch ohne EV**; Restbedarf bis Mitternacht; `FINISHED` bei PV-Rest <= Haus-Rest, Tages-Latch; Enable-Reconciliation; Diagnose 0–3 | 100 W PV für 5 Minuten; Sonnenuntergang–1h als endgültiger Horizont; wiederholte Enable-Klicks als normales Verhalten |
| 07.10., S. 132–148 | Gemeinsame SoC-State-Machine | `R+H <= T <=100-H`, `L=T-H/2`, `M=T+H/2`, `U=T+H`, Zustands-/Startmatrix; `PV_MAXIMUM` mit `T=R+H`; `PV_SURPLUS` frei konfigurierbar; `PV_OPTIMUM` dynamisch. Auch PV_SURPLUS darf oberhalb U Speicherentladung nutzen | separater Pufferparameter, frühe STOP-Grenze bei SoC < T, drei SoC-Regler, Sonderverbot FAST für PV_SURPLUS |
| 08.–09.10., S. 209–246, 266–320, 401–449 | Pumpen, Messgenerationen, beta.9-Regler | Unterschiedliche echte Sensor-Akquisitionszeiten, ACK ≠ physische EV-Reaktion; konservatives Budget, keine Doppelverwendung einer Generation; normale Aufwärtsrampe mit Antwortkontrolle, schnelle Absenkung; geschlossener Simulations-Regelkreis | Aufwärtsrampe aus wiederverwendeten HA-Reports; nur `requested_w` als Basis; blindes Vertrauen in gleichzeitig berichtete PV-/EV-/Netzwerte |
| 09.10., S. 320–410 | Messwerterfassung / Wallbox-Stationary | OCPP-MeterValues testweise 10 s → 2 s; separate Analyse physischer Fronius-Zeitfenster. WBM darf asynchrone Quellen nicht bloß wegen schnellerer EV-Werte für synchron halten | Messwertgeschwindigkeit mit physischer Gleichzeitigkeit verwechseln |
| 09.10., S. 441–451 | Entwicklung/Deployment | Codex-Aufträge in Englisch; Releases als GitHub-**Prerelease** und Installation via HACS, **keine Direktinstallation**; `main`/Stable schützen | „einfach Dateien direkt in HA überschreiben“, schwere Pflicht-Backupzeremonie |
| 09.10., S. 460–476 | beta.9/10 Startproblem und Headroom | Start-/Antwortprüfung darf reale Mindeststartbedingungen nicht dauerhaft unmöglich machen. Unterausnutzung der konfigurierten 3.500 W bei stabiler Ladung wurde ausdrücklich toleriert | unbedingter Zwang, 100 % des wirtschaftlichen Leistungslimits auszunutzen |
| 10.10., S. 477–480 | Phasenlockout | 3p→1p `phase_switch_lockout` darf nicht selbst OFF verursachen; weiter mit aktuell erreichbarem Minimum, beabsichtigte Phase separat erhalten; 50%-Netzimport-Schwelle dynamisch aus M | STOP nur wegen gesperrtem Phasenwechsel; feste 1p-Annahme während 3p-Lockout |
| 11.10., S. 485–528 | beta.11/12 Befunde | D1: Strom-/Spannungsquantisierung stabil; D2: SoC-Schutz, wirtschaftlicher Stopp und OFF-Status sauber trennen; D3: Phasenprobe-Frist über Parameteränderung erhalten; D4: fehlende Authority im Status vorrangig | „stopped_battery_soc“ als generischer Grund für jeden Stopp; verzögert „Messwerte fehlen“ bei Local als vorrangige Diagnose |
| 11.10., S. 529–545 | pv5: Lastsprünge, Wirtschafts-STOP | Schnelle Leistungsreduzierung **beibehalten**. Wirtschaftliche **Nullstromabschaltung** erst nach **50 Wh tatsächlichem gesamten Netzbezug während Überschreitungsphasen über M/2**; streng >15 s unter/equal Schwelle zum Reset | Momentanwert allein als Wirtschaftsstopp; feste 30-s-Verzögerung; Summierung bloß der Leistung oberhalb M/2; zusätzliche Parallelregel |
| 11.–12.10., S. 566–575 | Architektur-Audit, fünf Freigaben | Wirtschaftliche Mindestpunkt-Ausnahme; einheitliche 50 Wh für alle PV-Profile; höchstens 10-s-Messabstand; Modell-basierter Restart; persistente wirtschaftliche STOP-Historie bei eindeutigem Scope | alte `hard_budget_pause`/10-s-/90-s wirtschaftliche Veto-Pfade behalten; 50 Wh nur als Nachsatz |
| 12.10., S. 575–583 | Profilverfügbarkeit und Gleichheit | Batterie-freies SURPLUS ausschließlich Grid-Import/Export plus EV; Batterie-Modi benutzen gemeinsamen Regler, verschiedene T; SURPLUS == MAXIMUM bei gleichem T und sonst gleicher Konfiguration | separate PV-/Hauslast-Entity als zusätzliche Pflicht ohne Akku; „PV_MAXIMUM hat gänzlich andere Regelung“ |
| 12.10., S. 595–600 | Wiederanlauf, Codex-Freigabe | Zustandsvergleich mit bestehendem H/SoC-/Batteriemodell, kein neuer Batterie-Pflichtsensor, 100-W-Deadband; erst nach nachgewiesenem Reset und **materieller** Verbesserung; bestehende Startup-/Safety-Fences bleiben | Restart durch verstrichene Zeit oder Budget-Reset allein; PV-only-Zwang auch mit Akku; unbegründeter Timer |
| 12.10., S. 605–613 | Implementierung/Review | Konkreter Restartvergleich, 50-Wh-Integrationsregel, Session-Persistenz und spätes ACK/OFF-Race; verifizierte Implementierungsdetails in `docs/pv-grid-import-policy-design.md`; finale Regressionen | vermeintliche Safety-Garantie für 50 Wh; alte UI-Texte und Abnahmetests für deaktivierte Wirtschaftstimer |
| 12.10., S. 615–619 | Beta.13 Veröffentlichung | Release `v0.3.2-beta.13` auf `develop` vorbereitet, `main`/v0.3.1 nicht verändert; 2.420 Python-Tests und 145 Frontend-Tests. Hardwarevalidierung **erst nach Release** | automatische Gleichsetzung von Test-Suite-Erfolg mit realer Regelqualität |

## Wichtigste Entscheidungsketten – wo ein zunächst plausibler Entwurf ausdrücklich ersetzt wurde

### A. PV-OPTIMUM-Tagesende

Der Chat begann mit PV-Erstproduktion (100 W) und Sonnenuntergang. Danach wurde `Sonnenuntergang−1h` kurz als Alternative diskutiert (um den oberen SoC zuverlässiger zu erreichen). Diese Alternative wurde **nicht** zur endgültigen Regel: Am Ende steht die restliche Solcast-Tagesenergie gegenüber dem linearen restlichen 24-h-Hausbedarf bis Mitternacht; `DYNAMIC` endet durch `FINISHED`-Latch. Die Abendreserve wird über den **dynamischen Ziel-SoC**, nicht über zusätzliche Sonnenuntergangsschaltzeiten, geschützt.

### B. SoC-Stoppgrenze

Anfangs wurde SoC < T als Stopp behandelt; angesichts kurzer Hauslastschwankungen diskutierten wir das Ende bei T−H/2. Das wurde mit gemeinsamen L/M/U-Schwellen für alle Batterieprofile verabschiedet. **Eine spätere Wiedereinführung von STOP bei SoC < T ohne Änderungsbeschluss wäre eine Regression.**

### C. OCPP-Nullstrom und Lockout

0 A ist eine echte Unterbrechung und kann hardwareseitig 600 s Restart-Lockout auslösen; sie soll daher nicht durch kurzfristige Leistungsschätzung entstehen. Weiterladen auf einem **technisch nachgewiesenen Minimum** bei Phasensperre wurde ausdrücklich beschlossen. Die wirtschaftliche Beendigung dieses Mindestladens wurde später wiederum durch die 50-Wh-Regel normiert. Eine früher vereinbarte „50%-Momentanimporttoleranz mit 10-s-Stopp“ ist als eigene wirtschaftliche Stoppbedingung **abgelöst**.

### D. Batterie-Limit „hart“ oder wirtschaftlich?

Im beta.9-Kontext wurde der konservative EV-Hard-Cap zur Verhinderung unzulässiger hoher Sollpunkte entwickelt. Im späteren Audit wurde ein **wirtschaftlicher** Sofort-STOP am Minimum als unnötig erkannt (z. B. 4.033,94 W gegenüber 4.121,19 W technisch möglichem Punkt). Die **einzige ausdrücklich zugelassene Ausnahme** ist exakt der aktuelle technische Mindestpunkt; sie darf keine echten physischen Hardlimits verletzen. Oberhalb dieses Minimums gelten normale Budgetgrenzen unverändert.

### E. Wirtschaftsstopp

Zuerst gab es den 90-s-BALANCE-/SURPLUS-Defizitstop sowie einen eigenen 10-s-Importstop beim Minimum und budgetbedingte sofortige Off-Pfade. Im Gespräch wurde bewusst **kein weiteres 50-Wh-Gate angehängt**, sondern diese überlappenden wirtschaftlichen Stopps wurden **entfernt/ersetzt**. Der alte `pv_stop_delay` kann aus Migrationsgründen vorhanden sein, steuert aber wirtschaftlich keinen OFF mehr. Die exakt festgelegte Integrations-/Reset-Logik ist im betagten `docs/pv-grid-import-policy-design.md` dokumentiert.

### F. Neustart nach 50 Wh

Budget-Reset ist nicht gleich Erlaubnis zum Neustart. Neuere Entscheidung: nach >15 s gültiger Unterschreitung zusätzlich neue E-/Site-Evidenz, OFF-Antwort, profilabhängige Berechtigung, technische Ausführbarkeit und gegenüber dem alten STOP nachweisbar **mehr als 100 W** modellierte Verbesserung. Batteriebeteiligung ist ausdrücklich erlaubt. Ein reiner PV-Überschusstest würde PV_MAXIMUM und PV_OPTIMUM fälschlich einschränken.

### G. Zeitliche Regelung und Pumpen

Langsame Steigerung, rasche Reduktion und eine Reaktionsprüfung waren **bereits vereinbart**. Beta.9 regelte detailliert Messwertgeneration und EV-Antwort – vor allem im FAST-Pfad. Beta.12 korrigierte zusätzlich Strom-/Spannungsquantisierung. Das spätere Pumpen in beta.13 im `PV_BALANCE`-Pfad darf deshalb **nicht** dadurch „gelöst“ werden, dass sämtliche Zeit-/Messregeln neu erfunden werden. Nächster Schritt: tatsächlichen Pfad gegen die gültigen Anforderungen und `pv6` vergleichen.

## Offene Punkte, bei denen die Chronik **keine** abschließende neue Norm liefert

1. Ein quantitatives Abnahmekriterium für `PV_BALANCE`-Schwingungen (z. B. tolerierte Stromumkehrungen, Einschwingzeit nach Lastsprung) wurde bislang **nicht** ausdrücklich beschlossen.
2. Ob das stundenlange FAST-9-A-Verhalten in `pv6` tatsächlich eine unnötige Blockade ist oder eine bewusste/zulässige Headroom-Reserve, ist **offen**. Frühere Akzeptanz konservativer Reserven widerspricht einem pauschalen „bis exakt D ausfahren“.
3. Eine exakte Aussage über die physische Gleichzeitigkeit der Fronius-/BYD-/EV-Quellwerte lässt sich aus dem sichtbaren Chat, den Repository-Berichten und HA-Zeitstempeln **nicht garantieren**.
4. Der PDF-Druck enthält zahlreiche Verweise auf lokale Codex-Reports und historische Testlogs; diese müssen für eine spätere **Code-/Fehlerursachenprüfung** separat eingesehen werden, soweit nicht als getaggtes GitHub-Dokument vorhanden.

## Entwicklungsverfahren, das ebenfalls schon vereinbart war

Der Chat enthält mehrfach ausdrückliche Prozessentscheidungen: **Codex-Aufträge immer in Englisch** (S. 405), Codex als ausführendes Tool statt eigenständiger Architekt (S. 1), GitHub-Prerelease/HACS statt direkter HA-Dateiinstallation (S. 448–451) und keine unverhältnismäßige Backup- oder Release-Zeremonie bei einem Entwicklungsstand (S. 441–444). Auch diese Regeln müssen für nächste Iterationen sichtbar sein, gehören aber organisatorisch nicht in den mathematischen Reglerabschnitt.

## Empfohlene Verwendung

Dieser Verlauf gehört als **nichtnormativer Anhang / Entscheidungsjournal** zur konsolidierten V3. Soll-Dokument und Chronik bleiben getrennt, damit ein alter, ausdrücklich verworfener Vorschlag nicht über Copy/Paste wieder zur aktiven Regel wird. Weiterer Codeauftrag: erst nach Freigabe der V3 und einem dokumentierten Soll-Ist-Abgleich zu `pv6`.