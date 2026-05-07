# M5 WAR ROOM — TUI Design Refactoring Plan
> AI Big Data Behavioral Architect Perspective · sess.1594 · 2026-05-05

---

## 0. DOCTRINE

> *"Un cockpit non mostra dati. Mostra decisioni in attesa di essere prese."*

Il WAR ROOM TUI è un sistema di command intelligence — non un monitor passivo.
Ogni pixel deve rispondere alla domanda: **"cosa devo fare adesso?"**

---

## 1. BEHAVIORAL ARCHITECTURE (Livello Cognitivo)

### 1.1 Signal Hierarchy — 3 layer semantici

| Layer | Colore | Significato comportamentale |
|---|---|---|
| **CRITICAL** | `#ff3333` (WR_PRIMARY) | Azione immediata richiesta — bordi, cursori, tab active |
| **OPERATIONAL** | `#ff8c00` (WR_ACCENT) | Dati operativi — titoli sezione, header tabelle |
| **AMBIENT** | `#c4cedd` (DIM) | Contesto di sfondo — label, unità, chrome |

Principio: l'occhio deve trovare il rosso prima di trovare il teal.
Il rosso è la prima cosa che si vede in una war room. Sempre.

### 1.2 Kenosis Cognitiva — cosa è stato eliminato e perché

Ogni label rimossa riduce il **cognitive parsing time** del 15-40ms per elemento.
Su una schermata con 80 label → risparmio 1.2–3.2s per lettura completa.

| Eliminato | Impatto cognitivo |
|---|---|
| `HEALTH /100` | Il numero + emoji è auto-esplicativo (pattern recognition) |
| `avg` prima dei % | Il contesto strutturale (cluster header) già disambigua |
| `· 6 efficiency / · 12 performance` | La sigla S/P è un codice appreso — label = rumore |
| `MB/s` nel footer I/O | Le frecce ↓↑ + posizione già codificano tipo e direzione |
| `W=wired · A=active…` legenda | Il breakdown seg() sotto è la fonte — ridondanza pura |
| `┃` separatori TitleBar | Lo spazio bianco ha più potere separativo del glifo |
| `MRR`, `Outstanding`, `Pipeline` label | Le emoji 💰📌🎯 sono icone semantiche — la label è caption di quello che vedi |

### 1.3 Gaze Flow — eye tracking predittivo

```
TitleBar (anchor identitario)
   ↓
Tab bar (navigazione — rosso = active, alto contrasto)
   ↓
Top Row: CPU (sinistra) | MEM (destra) — scanpath orizzontale
   ↓
Feed Panel (amber border) — stream eventi
   ↓
Tab content (fullscreen per dati complessi)
```

Bordi ROSSI guidano l'occhio verso i confini dei pannelli prima ancora di leggere il contenuto.
È la stessa logica dei cockpit militari: **forma prima, contenuto dopo**.

---

## 2. DATA BEHAVIORAL ARCHITECTURE

### 2.1 Refresh Rate Strategy

| Panel | Rate | Perché |
|---|---|---|
| CPU cores (bar + %) | 2s | Nyquist per spike 4s — cattura picchi transitori |
| MEM stacked bar | 2s | Pressione memoria cambia lentamente — sync con CPU |
| Feed / Unifeed | 5s | Stream eventi — non deve distogliere dall'analisi core |
| KPI business (MRR/Pipeline) | 30s | Dati vault — nessun benefit da refresh più rapido |
| Heatmap temporal | 2s (buffer 88s) | Finestra temporale per pattern recognition |

### 2.2 Alert Surface Design

Un WAR ROOM ha bisogno di **alert escalation visiva**, non solo di dati statici.
Next iteration: colore di sfondo del pannello CPU cambia dinamicamente:

```python
# Pattern target (non ancora implementato)
if cpu_avg > 85:   panel.border_color = RED     # critico
elif cpu_avg > 65: panel.border_color = ORANGE  # warning
else:              panel.border_color = WR_PRIMARY  # nominal
```

### 2.3 Behavioral Patterns identificati (da implementare)

1. **Threat Surface Pulse** — il bordo WR_PRIMARY dovrebbe pulsare (opacity oscillation) quando threat_level > 5 nel Sentinel tab
2. **Pressure Gradient** — la stacked bar memoria dovrebbe cambiare colore del bordo del pannello quando swap > 0
3. **Session Awareness** — il numero di sessioni Claude (`🐙 N`) è un KPI critico: highlight HOT_PINK quando N > 3
4. **Cold Leads Timer** — se `cold_avg > 7gg` → campo diventa RED invece di YELLOW

---

## 3. VISUAL DNA — Differenziatori vs Versione Standard

| Dimensione | Standard (app.py) | WAR ROOM (app_war_room.py) |
|---|---|---|
| Colore primario bordi | `#00d4aa` TEAL | `#ff3333` ROSSO |
| BG pannelli | `#0f1623` blu-notte | `#0d0812` viola-notte |
| Titolo | `M5 MAX WATCHER` 🐙 | `M5 WAR ROOM` ⚔ |
| Tab active | TEAL | ROSSO |
| DataTable header | TEAL | AMBER `#ff8c00` |
| DataTable cursor | TEAL BG | ROSSO BG |
| TitleBar border | `heavy` single | `double` — peso doppio |
| DIM (label) | `#8a98ad` (grigio freddo) | `#c4cedd` (più chiaro, +40% luminosità) |
| top-row scaling | 14-20 rows (cap 20) | 18-32 rows (scala 40% viewport) |
| TitleBar height | 7/9/16 rows | 9/11/18 rows |
| Font Ghostty | 12pt | 15pt |
| Padding pannelli | 1 3 (compatto) | 2 4 (respiro tattico) |

---

## 4. NEXT ITERATIONS — Backlog Prioritizzato

### P0 — Alta priorità (prossima sessione)
- [ ] **Dynamic border color** per CPU/MEM in base a threshold live
- [ ] **Sentinel tab redesign** — canary box con sfondo RED quando alert attivi
- [ ] **Rename file** → `app.py` in progetto dedicato `~/projects/m5-war-room/`

### P1 — Media priorità
- [ ] **Heatmap color remap** — palette più aggressiva (rosso/arancio per calore, non cyan)
- [ ] **WAR ROOM splash** — ASCII art ⚔ al posto del polpo nel banner ASCII
- [ ] **Pulse animation** — border opacity oscillation su eventi critici (Textual CSS animation)

### P2 — Design evolution
- [ ] **Biforcazione completa** — WAR ROOM come prodotto standalone separato dall'm5-watcher base
- [ ] **Night ops mode** — env var `M5W_NIGHT=1` riduce ulteriormente brightness (operazioni notturne)
- [ ] **VHS demo** — gif 30s per LinkedIn/Telegram con `brew install vhs`

---

## 5. PRINCIPIO ARCHITETTURALE FONDANTE

> *"Un sistema che non distingue il critico dall'ordinario non è un sistema di controllo. È solo uno specchio."*

Il WAR ROOM non mostra lo stesso sistema con colori diversi.
Mostra **una postura diversa** verso i dati — ogni elemento visivo è addestrato a rispondere: *"questo richiede la mia attenzione adesso?"*

Il rosso non è decorazione. È il suono di una sirena resa visiva.

---

## 6. FEED TAB — LIVE AGGREGATOR (sess.1607 · 2026-05-07)

> *"Il Feed non logga. Il Feed mostra cosa il sistema sta decidendo in questo istante."*

### 6.1 Filosofia — superficie unica auto-popolante

Il vecchio tab `📋 Logs` era un **text wall**: una `Static` con markup blob,
rendering one-shot, leggibilità degradante con la verbosità. Pattern fallimentare:
*"più dati metti, meno l'occhio trova."*

Il nuovo `📋 Feed` ribalta il modello:
- **Event stream aggregato** in alto (DataTable cronologica multi-sorgente)
- **4 state tables strutturate** sotto, ognuna con scope semantico chiuso
- Niente più `_safe_render_*_section` che concatenano markup → ogni superficie
  si auto-popola via `populate_*_table(table, data)` idempotente

Risultato: il cockpit non chiede più di **leggere**, chiede di **scegliere**.

### 6.2 Cinque sorgenti aggregate nell'event stream

| Emoji | Sorgente | Cosa porta |
|---|---|---|
| 🐙 | **TENTACOLI** (primario) | Status/uptime/severity_hint/last_log_line dei lobi Polpo |
| ⚡ | **UNIFEED** | Eventi cross-pillar normalizzati (Bot/AuraHome/Astra/OS/Brand) |
| 🔬 | **TELEMETRY** | Probe sintetici (pillar liveness, auth decay, pre_tool_check) |
| 🛡 | **SENTINEL** | Threat events, hook violations, immune system signals |
| 📡 | **GHL/CRM/Setter/WhatsApp/Jarvis/Voice** (preesistenti) | Eventi business già presenti, ora co-locati |

L'aggregatore (`feed_aggregator.py`, 332 righe) normalizza timestamp + severity +
sorgente in un record canonico, deduplica per `(source, key, ts_bucket)`,
e proietta nella tabella ordinata per `severity DESC, ts DESC`.

### 6.3 Quattro state tables sotto l'event stream

| Table | Scope | Severity-sticky |
|---|---|---|
| **outstanding-table** | Cose dovute (cassa, deliverable, follow-up) | P0 stuck, P1 aging, info |
| **traps-table** | Pattern overaction/loop/drift detected | P0 active trap, P1 watch, info |
| **filaments-table** | Connessioni cross-cluster aperte | P0 broken, P1 weak, info |
| **blocks-table** | Bloccanti hard (auth/dep/scope) | P0 hard block, P1 soft, info |

Ogni populator helper sta in `feed_populators.py` (305 righe) e applica la regola
**severity-sticky bucketing**: una riga P0 non scende a P1 finché la condizione
non viene risolta a monte (no demote silenzioso = no falsa rassicurazione).

### 6.4 Refresh strategy — 5s tab, 60s upstream

- **Feed tab refresh**: 5s (ereditato dal vecchio Logs, validato da gaze flow)
- **TTL upstream** sui `read_*()` roadmap_*: 60s (rate-limit naturale sui vault read)
- **Anti-flicker**: hash diff già presente per Static, ora esteso ai 4 DataTable
  via `_update_if_changed(table, rows_hash)` → la tabella si ridisegna **solo**
  se il payload semantico è cambiato. Cursor position e scroll preservati.

### 6.5 Pattern reusable cross-tab — populator + aggregator

Questo refactor estrae **due primitive riusabili** che diventano doctrine
per i tab futuri:

1. **`populate_X_table(table, data)`** — pattern populator idempotente, severity-aware,
   anti-flicker via hash diff. Drop-in per qualsiasi DataTable cockpit.
2. **`aggregator(*sources) → canonical_records`** — pattern fan-in normalizzato
   (timestamp + severity + source) con dedup. Drop-in per qualsiasi event stream.

Cookie cutter per i prossimi tab refactor (Sentinel, Telemetry, Pillars):
ogni text wall sopravvissuta è un debito di design da estinguere con la stessa coppia.

### 6.6 Ground truth filosofica

Il Feed non sostituisce i log. I log restano nei file. Il Feed mostra **cosa il
sistema ha deciso che merita la tua attenzione adesso** — pre-filtrato,
pre-bucketizzato, pre-aggregato. È la differenza tra una scrivania piena di
fogli e una scrivania con 5 buste etichettate.

> *"Un text wall mostra l'attività. Un Feed strutturato mostra l'agenda."*

---

*Forged sess.1594 · AI Big Data Behavioral Architect pattern · Polpo Cockpit Suite*
