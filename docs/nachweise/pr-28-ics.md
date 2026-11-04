# PR #28: lokaler ICS-Nachweis vom 3. November 2026

[Aufnahme und Originalskript](https://gist.github.com/enving/89ff3814fe6e5e12188523c7c9906a54)
liegen außerhalb des Repository-Verlaufs. Die MP4 ist dort als Base64-Datei
abrufbar; die Anleitung im Gist erklärt das Dekodieren und enthält ihre Prüfsumme.

Geprüft wurde der kombinierte Dateibaum `b4344049df45d4aaec3d0f2f7ac1b1fba8d14121`
aus [PR #28](https://github.com/enving/Koprotokoll/pull/28) (`8dcdab3`)
und [PR #29](https://github.com/enving/Koprotokoll/pull/29) (`119043e`), nicht
#28 allein. Die lokale Arbeitskopie entsprach dem automatischen Merge beider Köpfe.

Das Skript erstellt eine synthetische ICS-Datei, legt die Quelle an, synchronisiert
über die API, entfernt den Termin aus der Datei und synchronisiert erneut. Es prüft
`scheduled` und anschließend `cancelled` samt Meldung. Die Aufnahme zeigt die Quelle
und die beiden Zustände, aber weder Dateibearbeitung noch API-Abgleiche. Ein echter
Kalenderanbieter und ein Meeting-Beitritt wurden nicht geprüft. Der Quellenfehler
ist ein automatisierter Test in #28, kein Teil der Aufnahme.
