# Prąd – prosument (Home Assistant)

Integracja Home Assistant dla prosumenta rozliczanego w systemie net-metering (opust). Na podstawie odczytów licznika liczy na bieżąco zużycie, bilans w strefach czasowych oraz szacowany koszt miesiąca, a przy zamknięciu miesiąca rozlicza magazyn energii zgodnie z zasadami opustu.

> Wersja robocza – projekt w trakcie budowy.

## Instalacja (HACS)

1. HACS → menu (trzy kropki) → **Repozytoria niestandardowe**.
2. Dodaj adres `https://github.com/drzejb/ha-prad-prosument`, kategoria **Integracja**.
3. Zainstaluj integrację „Prąd – prosument” i zrestartuj Home Assistant.
4. Ustawienia → Urządzenia i usługi → **Dodaj integrację** → „Prąd – prosument”.

## Konfiguracja

Konfiguracja odbywa się w interfejsie (config flow): wskazujesz encje liczników energii pobranej i oddanej oraz wpisujesz cennik i współczynniki opustu. Integracja nie zawiera żadnych danych użytkownika – wszystko podajesz sam.

## Usługi

Usługi integracji (m.in. inicjalizacja ze statystyk recordera) będą opisane tutaj w miarę powstawania kolejnych zadań.

## Ograniczenia

- Taryfa G12w (strefy czasowe i dni wolne od pracy w Polsce).
- Operator: Tauron.
- Opust: współczynniki 0,8 / 0,7 (zależnie od mocy instalacji).

## Prywatność

Integracja wysyła **położenie domu z ustawień Home Assistant** (szerokość i długość geograficzna) do usługi Open-Meteo, aby pobrać prognozę pogody. Żadne inne dane nie są wysyłane. Repozytorium nie zawiera danych właściciela.

## English summary

Home Assistant custom integration for Polish net-metering prosumers (Tauron, G12w tariff). It tracks meter increments per time zone, estimates the monthly cost and settles the energy store at month close. Install via HACS as a custom repository. The integration sends the home location configured in Home Assistant to Open-Meteo for weather data; no other data leaves your instance.
