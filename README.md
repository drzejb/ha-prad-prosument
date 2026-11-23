# Prąd – prosument (Home Assistant)

Integracja Home Assistant dla prosumenta rozliczanego w starym systemie net-metering (opust), miesięcznie, z licznikiem zdalnym. Obsługuje taryfy G11, G12 i G12w oraz cenniki obowiązujące od wybranej daty. Z przyrostów liczników energii liczy godzina po godzinie bilans w strefach taryfy, rozlicza magazyn energii (nadwyżki ważne 12 miesięcy), szacuje koszt bieżącego miesiąca i prognozę, a po zamknięciu miesiąca zapisuje jego rozliczenie do historii i plików CSV. Dodaje też dane do porównań: temperaturę, stopniodni i nasłonecznienie.

Wymaga Home Assistant 2026.10 lub nowszego oraz włączonego recordera.

## Instalacja (HACS)

1. HACS → menu (trzy kropki) → **Repozytoria niestandardowe**.
2. Dodaj adres `https://github.com/drzejb/ha-prad-prosument`, kategoria **Integracja**.
3. Zainstaluj „Prąd – prosument” i zrestartuj Home Assistant.
4. Ustawienia → Urządzenia i usługi → **Dodaj integrację** → „Prąd – prosument”.
5. Do pulpitu zainstaluj w HACS także kartę **ApexCharts Card**.

## Konfiguracja

Dodawanie integracji ma dwa kroki.

1. **Liczniki** (wszystkie to encje `sensor`):

| Pole | Co wskazać |
|---|---|
| Licznik sieci | dwa liczniki (pobrana i oddana energia) albo jeden licznik netto |
| Energia pobrana / oddana | liczniki narastające, kWh, Wh lub MWh |
| Licznik netto | zamiast dwóch powyższych; musi mieć `state_class: total` |
| PV (opcjonalnie) | licznik produkcji falownika |
| Temperatura (opcjonalnie) | czujnik temperatury na zewnątrz |

2. **Taryfa i opust**: wybierz taryfę (G12w Tauron, G11 albo własne godziny stref) i współczynnik opustu.

Potem uzupełnij ceny: Ustawienia → Urządzenia i usługi → Prąd – prosument → **Konfiguruj** → **Cenniki**. Przy G12w Tauron wpisany jest domyślny cennik, ale to **przykład – sprawdź go z fakturą**. Przy innych taryfach ceny są zerami do uzupełnienia.

Licznik, który chwilowo nie ma danych, powinien mieć stan `unavailable` (np. przez `availability` w szablonie), a nie `0`. Integracja rozkłada wtedy przerwę na brakujące godziny zamiast liczyć skok.

**Odbiorniki** (opcjonalnie): Konfiguruj → „Dodaj odbiornik” (nazwa + licznik energii). Każdy odbiornik dostaje encję `sensor.prad_mc_odb_<nazwa>`. Nazwy `fox` i `pv` są zarezerwowane.

**Cennik i ustawienia** to encje `number.prad_*`, `text.prad_wersja_cennika` i `switch.prad_magazyn_przed_przeniesieniem` na urządzeniu „Prąd”. Pola `number.prad_*` poprawiają cennik obowiązujący dziś (patrz „Cenniki od daty”).

## Cenniki od daty

Konfiguruj → **Cenniki**: dodaj cennik od daty, popraw go albo usuń. Cena zmienia się o północy wskazanego dnia. Zużycie każdego dnia liczone jest po cenie z tego dnia, a opłaty stałe – proporcjonalnie do liczby dni.

**Uwaga:** nowe ceny od przyszłej daty dodawaj w „Cenniki”, nie przez pola `number.prad_*`. Te pola poprawiają cennik obowiązujący dziś i przecenią bieżący miesiąc. Poprawka cennika zmienia tylko miesiąc bieżący i prognozę; miesiące zamknięte i oczekujące na zamknięcie mają własną kopię cennika.

## Współczynnik opustu

Konfiguruj → Instalacja → **Współczynnik opustu**. Współczynnik można podać z datą, np. po rozbudowie instalacji z 0,8 na 0,7. Uwaga: rozbudowa może zmienić także sposób rozliczenia części mocy. Integracja tego nie modeluje – sprawdź w umowie.

## Taryfy i strefy

- **G12w Tauron:** tania strefa 13–15 i 22–6, a także weekendy i święta.
- **G11:** jedna strefa całodobowa.
- **Własne godziny:** do 3 przedziałów taniej strefy, osobno decydujesz o weekendach i świętach.

Godziny G12 różnią się między operatorami i latami – wpisz je z umowy/taryfy jako własne.

Zmiana taryfy albo godzin stref jest możliwa tylko od pierwszego dnia miesiąca. Zmiana stref dni już policzonych wymaga zaznaczenia pola „Zmieniam mimo policzonych dni”, a potem uruchomienia `prad.inicjuj`.

## Liczniki (dwa / netto / z mocy)

- **Dwa liczniki** narastające (pobór i oddanie) albo **jeden licznik netto** (`state_class: total`). Jednostki: kWh, Wh, MWh.
- Gdy masz tylko czujnik mocy (W), zrób z niego licznik pomocnikiem Home Assistant „Całka (Riemann)”. Z mocy netto powstanie licznik netto.
- PV i temperatura są opcjonalne. Bez PV nie ma produkcji, autokonsumpcji, zużycia domu i „reszty”. Bez czujnika temperatury średnia dobowa pochodzi z Open-Meteo.

## Zmiana licznika a `prad.inicjuj`

Licznik wymienisz w Konfiguruj → Instalacja → **Liczniki**. Historia, magazyn i faktury zostają; godzina zmiany nie jest liczona. `prad.inicjuj` z datą wcześniejszą niż zmiana licznika jest odrzucane. Dodanie albo usunięcie PV liczy się jak zmiana licznika (też blokuje `prad.inicjuj` z wcześniejszą datą). Po zmianie licznika w trakcie miesiąca nie uruchamiaj `prad.inicjuj` do końca tego miesiąca – przeliczenie od dnia zmiany pominęłoby zużycie z wcześniejszych dni tego miesiąca.

## Jak to liczy

- Godziny rozliczane są o pełnej godzinie (+10 s). Bilans godzinowy netto (pobór − oddanie) trafia do strefy drogiej albo taniej wybranej taryfy (przy G12w weekendy, święta i 24 grudnia – tania; przy G11 jedna strefa całodobowa).
- Przy zamknięciu miesiąca (1. dnia o 01:00:30, z ponowieniem co godzinę, gdy brakuje danych) kWh w strefach są zaokrąglane do pełnych. Pobór pokrywa najpierw nadwyżka tej samej strefy z tego miesiąca, potem drugiej strefy, potem magazyn od najstarszych wpływów. Opust = oddanie × współczynnik (domyślnie 0,8).
- VAT liczony jest od każdej pozycji, kwoty zaokrąglane do grosza.
- Pogoda: średnia dobowa temperatura ze statystyk HA, nasłonecznienie z Open-Meteo.

## Usługi

**`prad.inicjuj`** – przelicza historię ze statystyk godzinowych recordera (np. po instalacji).

```yaml
action: prad.inicjuj
data:
  od: "2026-09-01"             # pierwszy dzień do przeliczenia
  poczatek_magazynu: "2026-11" # od kiedy nadwyżki trafiają do magazynu
  potwierdz: true              # wymagane, gdy historia już istnieje
```

Usługa zwraca odpowiedź (liczba godzin, przerwy, zamknięte miesiące, ostrzeżenia) – uruchom ją w Narzędziach deweloperskich → Akcje z zaznaczonym „Zwróć odpowiedź”. Przed przeliczeniem zapisuje kopię stanu.

**Faktury z pulpitu**: karta „Wpisz fakturę” (encje `select.prad_faktura_miesiac`, `number.prad_faktura_kwota`, `button.prad_faktura_zapisz`). Wybierz miesiąc (domyślnie najnowszy bez faktury), wpisz kwotę i kliknij „Zapisz fakturę”. Po wybraniu miesiąca z fakturą pole pokazuje zapisaną kwotę – nową kwotą można ją poprawić.

**`prad.wpisz_fakture`** – to samo jako usługa: kwota z faktury za miesiąc (do porównania z modelem, encja `sensor.prad_model_vs_faktura`). Wpisz kwotę za energię, bez opłat dodatkowych (np. za płatność online).

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

- **Zamknięty miesiąc zachowuje swoje cenniki, współczynnik opustu i strefy** oraz ustawienie przełącznika magazynu, także po ponownym `prad.inicjuj`. Świadomie przeliczyć go po nowych cenach się nie da.
- **`od` w `prad.inicjuj` ustawiaj na pierwszy dzień miesiąca.** Przeliczenie od innego dnia pomija dni tego miesiąca sprzed `od`.
- **Ponowne `prad.inicjuj` wywołuj zawsze z tym samym `od`.** Stan magazynu sprzed `od` jest zapamiętywany przy pierwszym przeliczeniu; jest dokładny, jeśli przed `od` nie było wpływów do magazynu.
- **Pliki CSV są tylko dopisywane** (`<config>/prad/`). Po ponownym `prad.inicjuj` stare wiersze zostają, nowe są dopisywane bez duplikatów miesięcy.
- Produkcja PV z godzin nocnych na przełomie miesiąca (≈ 0 kWh) dla już zamkniętego miesiąca jest pomijana.
- Stan jest zapisywany w `.storage/prad`, a przed każdym `prad.inicjuj` – kopie w `.storage/prad_backup` i `.storage/prad_backup_prev`.
- Prognoza w pierwszych dniach po instalacji (bez historii) może być zaniżona; nie wpływa na rozliczenie.

## Co sprawdzić przy pierwszej fakturze ze zmianą ceny

- **R1 – podział kWh do zapłaty między okresy** liczony jest proporcjonalnie do poboru. Faktura może rozliczać opust osobno w każdym okresie. Porównaj kWh z `segmenty` w atrybucie `miesiace` czujnika `sensor.prad_historia`.
- **R5 – opłaty stałe** liczone są proporcjonalnie do dni, a VAT od każdej pozycji. Faktura może zaokrąglać inaczej.

## Aktualizacja z v0.2 – brak powrotu

Stan jest przenoszony przy pierwszym uruchomieniu v0.3. Kopia sprzed migracji leży w `.storage/prad_backup_v02`. Powrotu do v0.2 nie ma. Encja `number.prad_bezpiecznik_fox` zmienia `entity_id` na `number.prad_bezpiecznik_siec` (chyba że zmieniono go ręcznie).

**Gdy migracja się nie powiedzie:** integracja pokazuje błąd uruchomienia („Migracja stanu prad nie powiodła się; plik stanu nie został zmieniony”) i się nie ładuje. Plik `.storage/prad` zostaje nietknięty (w formacie v0.2), a jego kopia leży w `.storage/prad_backup_v02`. Nic nie jest liczone ani nadpisywane, dopóki błąd nie zostanie usunięty. Żeby wrócić do v0.2: zatrzymaj Home Assistant, zainstaluj z HACS wersję v0.2, skopiuj `.storage/prad_backup_v02` na `.storage/prad` (w skopiowanym pliku zmień wartość `"key"` z `prad_backup_v02` na `prad`) i uruchom Home Assistant. Plik z błędem zgłoś razem z logiem.

## Ograniczenia

- Taryfy G11, G12 (jako własne godziny) i G12w; rozliczenie miesięczne w net-meteringu; licznik zdalny; opust ze współczynnikiem zmiennym w czasie.
- Nie obsługuje: net-billingu, rozliczenia 6/12-miesięcznego, taryf C, cen godzinowych, stref zmiennych w roku (lato/zima) ani zmiany ceny w środku doby.
- Jedna instalacja (jeden wpis integracji).

## Prywatność

Integracja wysyła **położenie domu z ustawień Home Assistant** (szerokość i długość geograficzna) do Open-Meteo (`api.open-meteo.com` – ostatnie dni, `archive-api.open-meteo.com` – dni starsze), po nasłonecznienie oraz – gdy nie wskażesz czujnika temperatury – średnią temperaturę. Żadne inne dane nie są wysyłane. Repozytorium nie zawiera danych właściciela.

## English summary

Home Assistant custom integration for Polish net-metering prosumers (G11/G12/G12w, dated price lists, single net meter, monthly settlement). It settles meter increments hourly per time zone, runs the 12-month energy store, estimates the monthly bill and keeps a monthly history with CSV export, temperature, degree days and solar radiation. Install via HACS as a custom repository; a ready dashboard (ApexCharts Card) is in `dashboards/prad.yaml`. The integration sends the home location configured in Home Assistant to Open-Meteo for solar radiation data and, when no temperature sensor is selected, for the mean temperature; no other data leaves your instance.
