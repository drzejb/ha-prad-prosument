# Prąd – prosument (Home Assistant)

Integracja Home Assistant dla prosumenta rozliczanego w starym systemie net-metering (opust). Z przyrostów liczników energii liczy godzina po godzinie bilans w strefach taryfy G12w, rozlicza magazyn energii (nadwyżki ważne 12 miesięcy), szacuje koszt bieżącego miesiąca i prognozę, a po zamknięciu miesiąca zapisuje jego rozliczenie do historii i plików CSV. Dodaje też dane do porównań: temperaturę, stopniodni i nasłonecznienie.

Wymaga Home Assistant 2026.10 lub nowszego oraz włączonego recordera.

## Instalacja (HACS)

1. HACS → menu (trzy kropki) → **Repozytoria niestandardowe**.
2. Dodaj adres `https://github.com/drzejb/ha-prad-prosument`, kategoria **Integracja**.
3. Zainstaluj „Prąd – prosument” i zrestartuj Home Assistant.
4. Ustawienia → Urządzenia i usługi → **Dodaj integrację** → „Prąd – prosument”.
5. Do pulpitu zainstaluj w HACS także kartę **ApexCharts Card**.

## Konfiguracja

Przy dodawaniu integracji wskazujesz cztery encje (wszystkie `sensor`):

| Pole | Co wskazać |
|---|---|
| Import | licznik energii pobranej z sieci (kWh lub Wh, narastający) |
| Eksport | licznik energii oddanej do sieci (kWh lub Wh, narastający) |
| PV | licznik produkcji falownika (kWh, narastający) |
| Temperatura | czujnik temperatury na zewnątrz |

Licznik, który chwilowo nie ma danych, powinien mieć stan `unavailable` (np. przez `availability` w szablonie), a nie `0`. Integracja rozkłada wtedy przerwę na brakujące godziny zamiast liczyć skok.

**Odbiorniki** (opcjonalnie): Ustawienia → Urządzenia i usługi → Prąd – prosument → **Konfiguruj** → „Dodaj odbiornik” (nazwa + licznik energii). Każdy odbiornik dostaje encję `sensor.prad_mc_odb_<nazwa>`. Nazwy `fox` i `pv` są zarezerwowane.

**Cennik i ustawienia** to encje `number.prad_*`, `text.prad_wersja_cennika` i `switch.prad_magazyn_przed_przeniesieniem` na urządzeniu „Prąd”. Domyślnie wpisany jest przykładowy cennik G12w – sprawdź go ze swoją fakturą.

## Jak to liczy

- Godziny rozliczane są o pełnej godzinie (+10 s). Bilans godzinowy netto (pobór − oddanie) trafia do strefy drogiej albo taniej G12w (weekendy, święta i 24 grudnia – tania).
- Przy zamknięciu miesiąca (1. dnia o 01:00:30, z ponowieniem co godzinę, gdy brakuje danych) kWh w strefach są zaokrąglane do pełnych. Pobór pokrywa najpierw nadwyżka tej samej strefy z tego miesiąca, potem drugiej strefy, potem magazyn od najstarszych wpływów. Opust = oddanie × współczynnik (domyślnie 0,8).
- VAT liczony jest od każdej pozycji, kwoty zaokrąglane do grosza.
- Pogoda: średnia dobowa temperatura ze statystyk HA, nasłonecznienie z Open-Meteo.

## Usługi

**`prad.inicjuj`** – przelicza historię ze statystyk godzinowych recordera (np. po instalacji albo po zmianie liczników).

```yaml
action: prad.inicjuj
data:
  od: "2026-09-01"             # pierwszy dzień do przeliczenia
  poczatek_magazynu: "2026-11" # od kiedy nadwyżki trafiają do magazynu
  potwierdz: true              # wymagane, gdy historia już istnieje
```

Usługa zwraca odpowiedź (liczba godzin, przerwy, zamknięte miesiące, ostrzeżenia) – uruchom ją w Narzędziach deweloperskich → Akcje z zaznaczonym „Zwróć odpowiedź”. Przed przeliczeniem zapisuje kopię stanu.

**`prad.wpisz_fakture`** – kwota z faktury za miesiąc (do porównania z modelem, encja `sensor.prad_model_vs_faktura`). Wpisz kwotę za energię, bez opłat dodatkowych (np. za płatność online).

```yaml
action: prad.wpisz_fakture
data:
  miesiac: "2026-10"
  kwota: 123.45
```

**`prad.magazyn_korekta`** – ręczna korekta stanu magazynu (kWh, dodatnia albo ujemna) w danym miesiącu, np. według salda z faktury.

```yaml
action: prad.magazyn_korekta
data:
  miesiac: "2026-11"
  ilosc: -12.5
```

## Pulpit

Gotowy pulpit jest w [`dashboards/prad.yaml`](dashboards/prad.yaml): dwie zakładki – **Rachunek** i **Porównania**, jedna kolumna, czytelne na telefonie. Utwórz nowy pulpit → Edytuj → menu → **Edytor surowej konfiguracji** → wklej treść pliku. Wymaga karty ApexCharts Card.

## Jak porównać z fakturą

1. Po zamknięciu miesiąca wpisz kwotę z faktury usługą `prad.wpisz_fakture`.
2. `sensor.prad_model_vs_faktura` pokazuje różnicę w %, a tabela na pulpicie – kWh w strefach, koszt modelu i fakturę.
3. Różnica kilku groszy wynika zwykle z zaokrąglenia kWh przez operatora (±1 kWh na strefę).

## Ważne zasady

- **Nowe ceny wpisuj 1. dnia miesiąca albo później.** Zamykany miesiąc dostaje kopię cennika z chwili zamknięcia. Rzadki przypadek: gdy licznik nie odpowiada przez całą noc z 1. na 2., a cennik zmienisz w tym czasie, miesiąc zamknie się po nowych cenach.
- **Zamknięty miesiąc zachowuje swój cennik** i ustawienie przełącznika magazynu, także po ponownym `prad.inicjuj`. Świadomie przeliczyć go po nowych cenach się nie da.
- **Ponowne `prad.inicjuj` wywołuj zawsze z tym samym `od`.** Stan magazynu sprzed `od` jest zapamiętywany przy pierwszym przeliczeniu; jest dokładny, jeśli przed `od` nie było wpływów do magazynu.
- **Pliki CSV są tylko dopisywane** (`<config>/prad/`). Po ponownym `prad.inicjuj` stare wiersze zostają, nowe są dopisywane bez duplikatów miesięcy.
- Produkcja PV z godzin nocnych na przełomie miesiąca (≈ 0 kWh) dla już zamkniętego miesiąca jest pomijana.
- Stan jest zapisywany w `.storage/prad`, a przed każdym `prad.inicjuj` – kopie w `.storage/prad_backup` i `.storage/prad_backup_prev`.
- Prognoza w pierwszych dniach po instalacji (bez historii) może być zaniżona; nie wpływa na rozliczenie.

## Ograniczenia

- Taryfa G12w (strefy i dni wolne w Polsce), rozliczenie miesięczne, opust z jednym współczynnikiem.
- Jedna instalacja (jeden wpis integracji).

## Prywatność

Integracja wysyła **położenie domu z ustawień Home Assistant** (szerokość i długość geograficzna) do Open-Meteo (`api.open-meteo.com` – ostatnie dni, `archive-api.open-meteo.com` – dni starsze), aby pobrać nasłonecznienie. Żadne inne dane nie są wysyłane. Repozytorium nie zawiera danych właściciela.

## English summary

Home Assistant custom integration for Polish net-metering prosumers (G12w tariff, monthly settlement). It settles meter increments hourly per time zone, runs the 12-month energy store, estimates the monthly bill and keeps a monthly history with CSV export, temperature, degree days and solar radiation. Install via HACS as a custom repository; a ready dashboard (ApexCharts Card) is in `dashboards/prad.yaml`. The integration sends the home location configured in Home Assistant to Open-Meteo for radiation data; no other data leaves your instance.
