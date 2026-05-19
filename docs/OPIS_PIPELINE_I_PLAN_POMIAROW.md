# Opis pipeline’u i plan pomiarów — wersja v5

## Cel

Pipeline ma służyć do powtarzalnej analizy pomiarów spektrofotometrycznych wykonywanych przez użytkowników. Głównym celem jest porównywanie zmian transmitancji, absorbancji i rozproszenia scyntylatorów po zmianie dawki promieniowania.

Pipeline działa w jednym notebooku Colab/Jupyter. Wszystkie ścieżki, pliki i parametry ustawia się w GUI w pierwszych komórkach notebooka. Nie ma obowiązkowego pliku JSON.

## Domyślna lokalizacja danych

Po zamontowaniu Google Drive w Colabie podstawowa ścieżka do danych to:

```text
/content/drive/Shareddrives/TN-Dose3D-Future-DAQ/Data/Spectrophotometry/Test_Measurements
```

W notebooku jest ona ustawiona jako domyślna. Jeżeli folder nie jest dostępny, notebook używa lokalnych plików testowych z `example_data/raw`.

## Nazewnictwo plików

Zalecany schemat nazw:

```text
role__source__geometry__condition__sample-or-run.txt
```

Przykłady:

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

Dla obecnie dosłanych darków proponowane zmiany nazw to:

```text
air_attenuation_white_dark.txt
→ dark__whiteLED__direct-0deg__LED-off__run01.txt

air_scattering_white_dark_90deg.txt
→ dark__whiteLED__scatter-90deg__LED-off__run01.txt
```

`whiteLED` oznacza białą diodę. Dla diody 472 nm należy używać `LED472nm`.

## Co oznaczają konkretne pliki

`blank` albo `empty-holder` oznacza pomiar powietrza / pustego holdera. To jest główna referencja do transmitancji.

`sample` oznacza właściwą próbkę scyntylatora.

`quartz` albo `fused-silica` oznacza próbkę referencyjną o dobrze znanych właściwościach optycznych. Nie zastępuje ona blanku. Służy głównie do sprawdzania stabilności setupu.

`dark` oznacza pomiar przy zamkniętym torze / wyłączonym źródle. W tym pipeline dark nie jest odejmowany, bo zakładamy, że spektrometr eksportuje dane już dark-corrected. Dark służy do kontroli, czy nie ma przeciekania światła albo problemu z tłem.

## Transmitancja i absorbancja

Transmitancja jest liczona względem powietrza / pustego holdera:

```text
T(lambda) = I_sample(lambda) / I_blank(lambda)
```

Absorbancja jest liczona jako:

```text
A(lambda) = -log10(T(lambda))
```

Porównania wykonujemy tylko dla tego samego prądu diody, tej samej geometrii i tej samej konfiguracji pomiarowej. To jest warunek konieczny, ponieważ zmiana prądu diody zmienia widmo i poziom sygnału źródła.

## Dlaczego nie dzielimy różnych prądów diody

Pomiar próbki przy większym prądzie diody może być użyteczny diagnostycznie, np. gdy chcemy lepiej zobaczyć kształt widma po przejściu przez silnie tłumiącą próbkę. Nie oznacza to jednak, że można taki pomiar podzielić przez blank wykonany przy innym prądzie.

Jeżeli prąd próbki i prąd blanku są różne, pipeline zapisuje taki pomiar jako `sample_only_shape_diagnostic`, a nie jako transmitancję absolutną.

## Baseline i odcinanie szumu

Ponieważ transmitancja jest ilorazem, regiony gdzie blank jest bardzo mały są niebezpieczne. Nawet mały szum może wtedy dawać sztucznie wysoką transmitancję.

Dlatego pipeline:

1. estymuje resztkowy baseline w zadanych zakresach długości fali,
2. odejmuje baseline,
3. ucina wartości ujemne,
4. zeruje sygnał poniżej progu szumu,
5. liczy transmitancję tylko tam, gdzie blank jest powyżej zadanego progu.

## Dark QC

Dark QC powinien być sprawdzany na osobnym wykresie. Idealnie powinien być bliski zeru i bez wyraźnej struktury widmowej. Jeżeli dark ma zauważalny pik albo strukturę podobną do widma LED, może to oznaczać przeciekanie światła.

## Pomiary, które warto wykonywać w każdej kampanii

Dla transmisji bezpośredniej:

1. direct dark QC,
2. direct blank / empty holder,
3. direct quartz / fused silica,
4. próbki scyntylatora przed napromienianiem,
5. próbki scyntylatora po kolejnych dawkach.

Dla rozproszenia 90°:

1. scattering dark QC,
2. scattering blank / empty holder,
3. scattering quartz / fused silica,
4. scattering samples dla tych samych próbek i dawek.

Dla pomiarów przy 472 nm używać tego samego schematu nazw, ale ze źródłem `LED472nm` zamiast `whiteLED`.
