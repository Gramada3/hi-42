# Hidroelectrica (personal) — integrare Home Assistant

Integrare proprie pentru contul iHidro (Hidroelectrica), domeniu `hidroelectrica_personal`.
Fără licență, fără telemetrie, fără apeluri către alte servere în afară de `ihidro.ro`.
Poate rula în paralel cu integrarea `hidroelectrica` a lui cnecrea (domenii diferite).

## Instalare

1. Copiază `custom_components/hidroelectrica_personal` în `config/custom_components/`
   (sau pune folderul într-un repo privat și adaugă-l în HACS ca *Custom repository → Integration*).
2. Repornește Home Assistant.
3. **Setări → Dispozitive și servicii → Adaugă integrare → Hidroelectrica (personal)**.
4. Email + parolă iHidro, apoi alegi locurile de consum, intervalul de actualizare și cât istoric să aducă.

Cerință: Home Assistant 2025.11 sau mai nou.

## Ce primești (per loc de consum / cod de încasare)

| Entitate | Tip | Detalii |
|---|---|---|
| Sold | RON | pozitiv = de plată, negativ = credit; atribute: status, scadență, ultima factură |
| Scadență | dată | data scadenței facturii curente |
| Ultima factură | RON | atribut `facturi` cu istoricul |
| Index consum | kWh, `total_increasing` | se poate adăuga în **Energy dashboard**; atribut `citiri` cu istoricul |
| Data ultimei citiri | dată | |
| Index producție | kWh | doar la prosumatori (registrul 1.8.0_P) |
| Autocitire de la / până la | dată | fereastra curentă sau următoarea (cu corecția bug-ului din `NextMonthClosingDate`) |
| Consum ultima lună / Cost ultima lună | kWh / RON | plus consum mediu zilnic, zile facturate |
| Consum anual | kWh | pe luni în atribute + atribut `istoric` |
| Ultima plată / Plăți anul curent | RON | doar plățile tale (fără compensațiile ANRE) |
| Compensații prosumator anul curent | RON | doar la prosumatori |
| Loc de consum (POD) | diagnostic | instalație, contor, distribuitor, furnizor, contract distribuție |
| Sold de plată | binar | |
| Factură restantă | binar (problem) | sumă restantă + zile de întârziere |
| Fereastră autocitire | binar | `on` când poți trimite indexul |
| Prosumator | binar, diagnostic | detectat automat |
| Index de trimis | number | câmp în care scrii indexul (păstrat la restart) |
| Trimite indexul | buton | validează la Hidroelectrica, apoi trimite |
| Actualizează datele | buton | refresh complet, inclusiv istoric |

Listele lungi (`istoric`, `facturi`, `citiri`, `plati`) apar în atribute dar nu sunt scrise în recorder.

## Servicii

```yaml
# Trimite indexul (verifică fereastra, că indexul nu e mai mic decât ultimul, apoi validează și trimite)
action: hidroelectrica_personal.submit_reading
data:
  account: "8000123456"   # opțional dacă ai un singur loc de consum
  index: 12345
  validate_only: false    # true = doar verifică la Hidroelectrica, nu trimite
response_variable: rezultat

# Contoare cu mai multe registre
action: hidroelectrica_personal.submit_reading
data:
  readings: {"001": 12345, "002": 6789}

# Refresh (full: true include istoricul)
action: hidroelectrica_personal.refresh
data:
  full: true

# Toate datele, pentru scripturi / șabloane
action: hidroelectrica_personal.get_data
data:
  sections: [invoices, payments, usage, reads]
response_variable: date
```

## Exemplu: notificare când se deschide fereastra de autocitire

```yaml
alias: Hidroelectrica — trimite indexul
triggers:
  - trigger: state
    entity_id: binary_sensor.hidroelectrica_8000123456_fereastra_autocitire
    to: "on"
actions:
  - action: notify.mobile_app_telefon
    data:
      title: Autocitire Hidroelectrica
      message: >-
        Poți trimite indexul până la
        {{ states('sensor.hidroelectrica_8000123456_autocitire_pana_la') }}.
```

ID-urile exacte ale entităților le vezi pe pagina dispozitivului.

## Diferențe față de integrarea originală

- fără sistem de licență, heartbeat sau trimitere de evenimente;
- valori numerice reale (RON, kWh, date) în loc de texte „Da/Nu” și „123,45 lei”;
- index compatibil cu Energy dashboard;
- câmpul de index face parte din integrare (nu mai trebuie creat manual un `input_number`);
- erorile de trimitere apar direct în interfață, nu doar în log;
- verificarea TLS e activă; trece pe conexiune neverificată doar dacă certificatul serverului chiar nu poate fi validat (cu avertisment în log);
- reautentificare din UI când se schimbă parola;
- diagnostice descărcabile, cu datele personale ascunse.

## Note

- Protocolul API (endpoint-uri, payload-uri, logica prosumator) provine din
  [cnecrea/hidroelectrica](https://github.com/cnecrea/hidroelectrica), licență MIT.
- La prosumatori butonul și câmpul de index nu se creează (contorul e citit de distribuitor);
  serviciul `submit_reading` rămâne disponibil.
- `GetPreviousMeterRead` răspunde doar cât timp fereastra de autocitire e deschisă,
  deci unele atribute (distribuitor, furnizor, index anterior) apar doar atunci.
