# Nachweise für Pull Requests

Hier liegen kurze Beschreibungen und bei Bedarf kleine, bereinigte Skripte für
reproduzierbare Prüfungen. Große Aufnahmen nicht in Git einchecken: Ein Video kann
als Anhang zu einem öffentlichen GitHub-PR-Kommentar hochgeladen und dort verlinkt
werden. Vor dem Hochladen Bild, Ton und Testdaten auf vertrauliche Inhalte prüfen.
GitHub-Anhänge sind externe Ablage; Erreichbarkeit und Zugriff nach dem Hochladen
prüfen. Keine kurzlebigen CI-Artefakte als einzige dauerhafte Kopie verwenden.

Ein Nachweis nennt:

1. PR, genaue Commit-IDs und gegebenenfalls Kombinationen oder lokale Änderungen.
2. Umgebung, Vorbereitung, verwendete Testdaten und ausführbare Schritte.
3. Testbefehl mit Ergebnis und übersprungenen Tests; bei Video den direkten Link.
4. Beobachtung und Grenzen: Was ist sichtbar, was lief außerhalb der Aufnahme,
   was wurde simuliert und was wurde nicht getestet?

Im PR auf den Nachweis verweisen und offene Punkte ausdrücklich stehen lassen.
Nach Änderungen am PR-Kopf die Aussagekraft erneut prüfen und nötigenfalls erneuern.
