# SAT Autotune (Home Assistant add-on)

Meet doorlopend hoe de Intergas Xtreme 30 presteert en stelt vier SAT-instellingen begrensd bij om gas te
besparen. Elke wijziging wordt gelogd, gemeld, geëvalueerd en zo nodig automatisch teruggedraaid.

## Installeren

**Lokale add-on:** kopieer de map `sat_autotune/` naar `/addons/sat_autotune/` op je HA (Samba of SSH-add-on),
ga naar *Instellingen → Add-ons → Add-on store → ⋮ → Controleren op updates*, open "SAT Autotune" onder
*Lokale add-ons*, installeer en start.

**Via GitHub:** zet deze map (met `repository.yaml` in de root) in een repo en voeg die URL toe als
add-on-repository.

Zet *Toon in zijbalk* aan: het dashboard staat dan in het menu van HA.

## Eerste keer

1. Start de add-on. In het log zie je welke entiteiten ontbreken en of de SAT-versie (4.2.0) en veldnamen kloppen.
   Zo niet, dan draait hij in **alleen-lezen-modus** en krijg je een melding.
2. Er wordt een schakelaar `input_boolean.sat_autotune_actief` aangemaakt. Die staat **uit**: de add-on doet dan
   alleen analyses. Zet hem pas aan als je automatisch aanpassen wilt (ná de dry-run is het pas echt actief).
3. Zet `run_mode` op `test_7d`, start de add-on opnieuw en bekijk het log en het dashboard: de analyse van de
   afgelopen 7 dagen. Zet daarna `run_mode` terug op `normal`.
4. Zet `run_mode` op `selftest_options` om aanpassen en terugdraaien te testen op één onschuldig veld
   (`target_temperature_step`). Het log laat zien dat alle andere SAT-opties identiek zijn gebleven
   ("RESULTAAT: OK"). Zet daarna `run_mode` terug op `normal`.

## Dashboard

Open het paneel *SAT Autotune* in de zijbalk. Je ziet:
- live status (vlam, aanvoer, modulatie, kamertemperaturen) en de fase (dry-run/live, schakelaar aan/uit, lopend experiment);
- kerncijfers: gas per graaddag (met verschil t.o.v. vorige periode), totaal gas, starts per dag, aanvoer en condensatie;
- **Wat valt op**: korte conclusies, bijvoorbeeld weinig condensatie, veel korte cycli of een kamer die te koud zit;
- grafieken per dag (gas, graaddagen, starts, brandduur) en trends per week, met rode lijnen op het moment van een wijziging;
- comfort per kamer, het experimentlogboek (ook je eigen wijzigingen) en alle dagdetails.

Voor een eigen HA-dashboard staan de kerncijfers ook als sensoren: `sensor.sat_analyse_gas_per_graaddag`,
`..._starts_per_dag`, `..._gem_brandduur`, `..._aanvoer_gem`, `..._pct_onder_55`, `..._gas_totaal_week`,
`..._vergelijkbare_dagen`, `..._verschil_vorige_periode`, `..._laatste_wijziging`. Ze worden bij elk weekrapport
bijgewerkt en zijn na een HA-herstart leeg tot het volgende rapport (of draai `test_7d`).

## Gas, koken en douchen

De P1-gasmeter meet het hele huis. De add-on schat daarom een **basislast** (warm water + koken): de mediaan van het
gasverbruik op dagen zonder stookbehoefte (≤ 0,5 graaddag) in de laatste 90 dagen. Die wordt van het dagverbruik
afgetrokken voordat "gas per graaddag" wordt berekend. Is er geen zomerdata, dan staat er een waarschuwing en bevat
het getal ook douchen en koken. Je kunt de basislast ook zelf vastzetten met `base_gas_m3_per_day`. Omdat
douchen en koken per dag wisselen, wordt er altijd over minimaal 7 vergelijkbare dagen gemiddeld.

## Regels voor aanpassen

| Instelling | Min | Max | Stap |
|---|---|---|---|
| Stooklijncoëfficiënt | 1,0 | 3,0 | 0,1 (instelbaar tot 0,2) |
| Maximaal setpoint | 50 °C | 65 °C | 5 °C |
| Cycli per uur | 3 | 4 | 1 |
| OPV | 42 °C | 52 °C | 1 °C |

- Eén wijziging tegelijk, daarna `wait_days` (14) niets aanpassen.
- Alleen met minimaal 7 vergelijkbare dagen (≥ 5 graaddagen, weinig Away, geen handmatige stand).
- Niet tussen 22:00 en 07:00. Eerste 4 weken (`dry_run_weeks`) alleen voorstellen.
- Vangnetten: comfortbewaking (kamer > 1 °C te koud, > 2 uur, jij thuis → direct terug), evaluatie
  (minder dan 2% beter of starts > 20% gestegen → terug), pendelbewaking bij OPV (brand < 5 min → terug),
  noodstopschakelaar. De pushmelding heeft een knop **Terugdraaien**.
- De add-on schrijft alleen via de options flow van SAT en stuurt nooit MQTT naar de OTGW. De
  HA-client heeft een whitelist (alleen lezen, notify, eigen `sensor.sat_analyse_*`, SAT options flow).
- Comfortbewaking geldt voor `comfort_monitor_rooms` (standaard woonkamer en kantoor). Slaapkamer en hal zijn
  vaak bewust koeler dan het setpoint van SAT en zouden anders onterecht alarm slaan.

## Opmerkingen

- De veldnamen van de SAT-opties (`field_*` in de configuratie) zijn gebaseerd op SAT; het log toont bij het
  opstarten of ze bestaan. Staat een naam er niet, dan zie je alle beschikbare velden en past de add-on niets aan.
- De winstschatting bij een voorstel is een vuistregel; de echte beslissing valt bij de evaluatie na de wachttijd.
- Optioneel: `anthropic_api_key` voor een korte uitleg bij het rapport. Alleen berekende cijfers gaan mee; de AI
  beslist niets.
- Ontwikkelen/testen: `pip install pytest aiohttp && pytest tests`.
