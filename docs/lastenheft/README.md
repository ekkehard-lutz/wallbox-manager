# Wallbox Manager – Lastenheft

Dieses Verzeichnis enthält die konsolidierten Anforderungen zur PV-Laderegelung und die dazugehörige Entscheidungshistorie.

| Dokument | Zweck | Status |
| --- | --- | --- |
| [PV-Regelung – Anforderungsbasis V3](pv-regelung.md) | Aktueller konsolidierter Soll-Entwurf mit stabilen Anforderungs-IDs, Abnahmekriterien, Quellen und offenen Punkten | **Entwurf – fachliche Freigabe ausstehend** |
| [Entscheidungsverlauf Oktober 2026](entscheidungsverlauf.md) | Nichtnormative Historie: Beschlüsse, ersetzte Zwischenlösungen und ausdrücklich verworfene Ansätze | Referenz / Historie |

## Verbindliche Arbeitsweise für Codex und weitere Iterationen

1. **Vor jedem PV-Regelungsauftrag** zunächst [pv-regelung.md](pv-regelung.md) vollständig lesen, insbesondere die geltende SoC-Matrix, die gemeinsame Wirtschafts-Stopp-/Restart-Policy und die ausdrücklich abgelösten Regeln.
2. [entscheidungsverlauf.md](entscheidungsverlauf.md) erklärt die Herkunft und Ablösung früherer Festlegungen; **alte oder verworfene Regeln nicht erneut implementieren**.
3. Diese V3 bleibt bis zur ausdrücklichen fachlichen Freigabe **Entwurf**. Widersprüche zwischen Entwurf, getaggter beta.13-Implementierung, Logs und älteren Berichten als offene Entscheidungen kennzeichnen – nicht eigenmächtig umdeuten.
4. Für jede Änderung die betroffenen **Anforderungs-IDs**, das beobachtete Ist, den behaupteten Soll-Verstoß, betroffene Nachbaranforderungen, Tests und Hardware-Nachweise dokumentieren. Änderungen am fachlichen Soll benötigen eine gesonderte Entscheidung.
5. Bestehende OCPP-, SoC-, Autoritäts-, Sicherheits-, Phasen- und Wirtschaftsstopp-Garantien bleiben bestehen, solange sie nicht ausdrücklich geändert wurden. Keine neue Parallelregel zur Umgehung bestehender Regeln einbauen.
6. Codex-Aufträge erfolgen in Englisch; Implementierung, Review, Merge, GitHub-Prerelease und Hardwaretest sind getrennte Schritte. Änderungen an `main`, der Wallbox-Firmware oder einer Home-Assistant-Installation folgen nicht automatisch aus der Dokumentation.

**Quellenstatus:** Die PDFs und historischen Hardwarelogs werden durch diese Markdown-Dateien nicht ins Repository importiert. Seitenangaben beziehen sich auf den extern aufbewahrten, 619-seitigen Gesprächsausdruck. Die Quellenlinks zu getaggten Repository-Dokumenten stehen in der Anforderungsbasis.
