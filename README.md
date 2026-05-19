# Pipeline spektrofotometryczny — single Colab/Jupyter v5

To jest wersja pipeline’u przygotowana dla użytkowników wykonujących pomiary transmitancji i rozproszenia scyntylatorów przed i po napromienianiu.

Wersja v5 doprecyzowuje organizację plików i jest dostarczana jako czysty, nieuruchomiony notebook: GUI ma osobne pola dla każdego pliku, dark spectra są traktowane wyłącznie jako QC, a domyślny katalog danych po podłączeniu Google Drive to:

```text
/content/drive/Shareddrives/TN-Dose3D-Future-DAQ/Data/Spectrophotometry/Test_Measurements
```

Dane przykładowe dołączone do paczki są wyłącznie danymi testowymi pokazującymi format plików. Nie należy ich interpretować jako wyników fizycznych.

## Co użytkownik zmienia w notebooku

Wszystkie ustawienia są w pierwszych komórkach notebooka:

```text
notebooks/spectrophotometer_użytkownik_single_colab_pipeline.ipynb
```

Nie trzeba edytować żadnego pliku JSON. Użytkownik ustawia w GUI:

- `Data/root dir` — katalog z plikami TXT, zwykle katalog na Google Drive podany wyżej,
- `Output dir` — katalog, gdzie notebook zapisze CSV, figury i raport,
- `Direct blank` — powietrze / pusty holder w geometrii transmisji bezpośredniej,
- `Direct dark QC` — dark w geometrii bezpośredniej, tylko do kontroli jakości,
- `Direct quartz` — opcjonalne szkło kwarcowe / fused silica do kontroli stabilności setupu,
- `Direct samples` — tabela próbek scyntylatorów w transmisji bezpośredniej,
- `Scattering blank` — pusty holder / tło dla geometrii 90°,
- `Scattering dark QC` — dark dla geometrii 90°, tylko do kontroli jakości,
- `Scattering quartz` — kwarcowa próbka kontrolna dla rozproszenia,
- `Scattering samples` — tabela próbek scyntylatorów mierzonych w geometrii rozproszeniowej.

## Proponowane nazwy plików

Zalecany schemat:

```text
role__source__geometry__condition__sample-or-run.txt
```

Czyli np.:

```text
blank__whiteLED__direct-0deg__40uA__empty-holder__run01.txt
sample__whiteLED__direct-0deg__40uA__scint-A__dose-0Gy__run01.txt
sample__whiteLED__direct-0deg__40uA__scint-A__dose-10Gy__run01.txt
sample__LED472nm__direct-0deg__40uA__scint-A__dose-0Gy__run01.txt
dark__whiteLED__direct-0deg__LED-off__run01.txt
dark__whiteLED__scatter-90deg__LED-off__run01.txt
quartz__whiteLED__direct-0deg__40uA__fused-silica__run01.txt
quartz__whiteLED__scatter-90deg__40mA__fused-silica__run01.txt
```

Dla dwóch dosłanych darków proponuję zmianę nazw:

```text
air_attenuation_white_dark.txt
→ dark__whiteLED__direct-0deg__LED-off__run01.txt

air_scattering_white_dark_90deg.txt
→ dark__whiteLED__scatter-90deg__LED-off__run01.txt
```

`whiteLED` oznacza białą diodę / białe źródło. Dla pomiarów z diodą 472 nm używamy analogicznie `LED472nm`.

## Najważniejsza zasada porównań

Transmitancja i absorbancja są liczone tylko dla tego samego prądu diody, tej samej geometrii i tej samej konfiguracji pomiarowej:

```text
T(lambda) = I_sample(lambda) / I_blank(lambda)
A(lambda) = -log10(T(lambda))
```

`blank` oznacza powietrze / pusty holder. Próbka kwarcowa nie jest blankiem. Kwarc służy do kontroli, czy setup jest stabilny między seriami pomiarowymi.

Jeżeli próbka była mierzona przy innym prądzie diody niż blank, pipeline nie liczy dla niej transmitancji absolutnej. Taki pomiar trafia do analizy jako diagnostyczny kształt widma.

## Dark spectra

Zakładamy, że dane eksportowane ze spektrometru są już dark-corrected. Dlatego pipeline nie odejmuje darków.

Dark measurement jest mimo to bardzo ważny jako kontrola jakości. Jeżeli dark jest istotnie powyżej zera albo ma wyraźny kształt widmowy, może to oznaczać przeciekanie światła, źle zamknięty setup, stray light albo problem z akwizycją.

W v5 darki są domyślnie wyświetlane na osobnym wykresie `Dark QC spectra - display only, not subtracted`.

## Baseline i odcinanie szumu

Pipeline ma trzy główne parametry odcinania szumu:

- `Baseline ranges` — zakresy długości fali, gdzie oczekujemy braku użytecznego sygnału. Z nich liczony jest resztkowy offset i szum.
- `Blank min frac` — minimalny poziom blanku jako ułamek maksimum blanku. Tam, gdzie blank jest mniejszy, nie wolno liczyć `sample / blank`.
- `Signal floor frac` — minimalny poziom sygnału jako ułamek maksimum danego widma. Wartości poniżej tego progu są zerowane w widmach używanych do całek i diagnostyki.
- `Noise sigma x` — dodatkowy próg oparty na szumie w obszarze baseline. Efektywny próg to większa z wartości: próg procentowy albo `Noise sigma x * sigma_noise`.

Dzięki temu długie ogony widma, gdzie realnie jest tylko szum, nie psują transmitancji, absorbancji i całek.

## Główne wyniki

Pipeline zapisuje:

- `results/processed/analysis_ready_dataframe.csv` — główny dataframe do dalszej analizy,
- `results/processed/raw_spectra_long.csv` — wszystkie widma z metadanymi i sygnałami po bramkowaniu,
- `results/tables/qc_summary.csv` — kontrola jakości i progi szumu,
- `results/tables/integral_summary.csv` — całki pod krzywymi,
- `results/figures/` — wykresy diagnostyczne.

## Co porównywać w badaniach dawki

Dla wpływu promieniowania na scyntylatory najważniejsze będą:

- zmiana `T(lambda)` względem dawki,
- zmiana `A(lambda)` względem dawki,
- całka z `1 - T`,
- całka z absorbancji,
- zmiana kształtu i całki rozproszenia 90°,
- stabilność krzywej kwarcu między seriami.
