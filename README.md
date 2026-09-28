# Create-a-DXF-file-from-PDF-or-Photo

`dxfconv` crea un file **DXF formato R12 (AC1009) in scala 1:1** partendo da un
**PDF** o da una **foto / scansione**. L'unità del disegno è il **millimetro**:
una quota di 100 mm sull'originale misura 100 unità nel DXF.

## Installazione

```bash
pip install -r requirements.txt      # oppure: pip install .
```

Richiede Python ≥ 3.9 (ezdxf, PyMuPDF, OpenCV, NumPy, Pillow).

## Uso rapido

```bash
python -m dxfconv disegno.pdf                    # -> disegno.dxf
python -m dxfconv scansione.png --dpi 300        # scansione a 300 dpi
python -m dxfconv foto.jpg --sheet A4            # foto di un foglio A4
python -m dxfconv foto.jpg --ref 120,340,980,352 --ref-length 200
```

Dopo l'installazione con `pip install .` è disponibile anche il comando `dxfconv`.

## Pagina web (senza installare nulla)

Il file [`dxfconv/static/index.html`](dxfconv/static/index.html) è
**autonomo**: basta aprirlo con doppio clic nel browser (Chrome, Edge, Firefox,
Safari) e convertire. Tutta l'elaborazione avviene nel browser, i file non
vengono inviati a nessun server. Per i PDF la pagina scarica la libreria
PDF.js da internet; le immagini funzionano anche offline.

La pagina permette di:

* trascinare un PDF o una foto e scegliere il metodo di scala;
* **cliccare sull'immagine** i due punti di riferimento o i 4 angoli del foglio;
* vedere l'**anteprima** del DXF con griglia da 10 mm, zoom e coordinate;
* **misurare** una distanza sull'anteprima (con aggancio ai vertici) e usarla
  per la calibrazione;
* scaricare il file DXF R12.

Il motore JavaScript è un porting del pacchetto Python e dà gli stessi
risultati (differenze di pochi decimi di millimetro nella vettorizzazione delle
immagini). Le immagini molto grandi vengono ridotte a 16 megapixel per
l'elaborazione, mantenendo la scala corretta; il formato TIFF non è supportato
dai browser (usare PNG o JPG).

In alternativa la stessa pagina può essere servita in locale con
`python -m dxfconv.web` (apre http://127.0.0.1:8000/).

## Come si ottiene la scala 1:1

### PDF vettoriale (esportato da CAD, Inkscape, Illustrator...)
Le coordinate PDF sono in punti tipografici (1 pt = 25,4/72 mm), quindi la
conversione è **esatta**: linee, polilinee, rettangoli e curve di Bézier
(approssimate con tolleranza `--tolerance`, default 0,05 mm). I cerchi vengono
riconosciuti ed esportati come entità `CIRCLE` (`--no-circles` per disattivare).
La rotazione della pagina viene rispettata; il rettangolo di sfondo grande
quanto la pagina viene ignorato.

Se il PDF è la stampa di un disegno in scala (es. 1:50), usare `--scale 50`.

### PDF scansionato
Se la pagina non contiene vettori (è solo un'immagine) viene renderizzata a
`--raster-dpi` (default 600) e vettorizzata; la scala deriva dalle dimensioni
della pagina PDF. `--raster` forza questa modalità.

### Immagine (scegliere **un** metodo)

| Opzione | Quando usarla |
|---|---|
| `--dpi N` | scansione piana; se omesso si usa il DPI salvato nel file (se presente) |
| `--px-per-mm N` | scala nota in pixel per millimetro |
| `--ref X1,Y1,X2,Y2 --ref-length MM` | due punti (in pixel) la cui distanza reale è nota, es. un righello nella foto |
| `--sheet A4` (o `A3`, `LETTER`, `210x297`...) | foto di un foglio di dimensioni note: la **prospettiva viene corretta** e la scala ricavata dal foglio. Gli angoli sono rilevati automaticamente o si indicano con `--corners "x1,y1;x2,y2;x3,y3;x4,y4"` |

Per le foto è consigliato `--sheet`: fotografare il foglio intero su uno sfondo
scuro e contrastato. I metodi `--dpi`, `--px-per-mm` e `--ref` presuppongono
una foto scattata perfettamente perpendicolare al disegno.

### Correzione finale
`--calibrate MISURATO,REALE` scala tutto il disegno in modo che una quota che
nel DXF misura `MISURATO` mm diventi `REALE` mm (es. `--calibrate 99.4,100`).

## Vettorizzazione delle immagini

* `--mode centerline` (default): estrae l'asse delle linee, ideale per disegni
  tecnici e schizzi a tratto. I tratti continui diventano polilinee uniche.
* `--mode contour`: estrae il bordo delle zone scure, ideale per sagome piene
  (es. la silhouette di un pezzo appoggiato sul foglio).
* `--threshold otsu|adaptive`: `adaptive` gestisce illuminazione non uniforme
  (default per `--sheet`).
* `--invert` per linee chiare su sfondo scuro.
* `--tolerance` (mm, default 0,2) semplificazione delle polilinee;
  `--min-length` (mm, default 2) scarta i tratti più corti (rumore);
  `--margin` (mm) bordo del foglio da ignorare dopo la correzione prospettica.

## Contenuto del DXF

* Versione `AC1009` (R12), leggibile da praticamente ogni CAD / CAM / laser.
* Entità: `LINE`, `POLYLINE` (2D, aperte o chiuse), `CIRCLE`.
* Layer `LINEE` (linee e polilinee) e `CERCHI`.
* Origine nell'angolo in basso a sinistra della pagina / immagine, asse Y verso l'alto.
* R12 non memorizza le unità di misura: all'apertura impostare **millimetri**
  se il CAD lo chiede.

Il testo del PDF non viene esportato (solo la geometria).

## Test

```bash
pip install pytest
python -m pytest
```

I test generano PDF e immagini sintetiche di dimensioni note (anche una foto in
prospettiva di un foglio A4) e verificano che il DXF ottenuto sia R12 e in
scala 1:1.
