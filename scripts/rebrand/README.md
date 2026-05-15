# M5 Watcher Bundle Rebrand (sess.1988)

Artefatti per ricreare `~/Applications/M5 Watcher.app` come clone rebrandizzato di Ghostty.app con identità Polpo (`com.polpo.m5-watcher`).

## Quando ri-eseguire

- Dopo aggiornamento Ghostty.app (signature originale invalida il clone)
- Dopo reinstall macOS / nuova macchina (M1 setup)
- Se `M5 Watcher.app` viene cancellato per errore
- Se serve cambiare gli args passati a ghostty.bin (titolo, fullscreen, command path)

## File

| File | Ruolo |
|------|-------|
| `m5_watcher_rebrand.sh` | Script master 10-step idempotente. Lo lanci tu manualmente (auto-mode Claude blocca clone third-party signed app). |
| `m5_launcher.c` | Sorgente Mach-O del launcher che diventa `Contents/MacOS/ghostty`. Compila universal arm64+x86_64 con `clang -arch arm64 -arch x86_64`. |
| `entitlements_minimal.plist` | Entitlements ad-hoc compatibili: `allow-jit`, `allow-unsigned-executable-memory`, **`disable-library-validation`** (cruciale per caricare Sparkle.framework con Team ID originale). |

## Uso

```bash
bash ~/projects/m5-watcher/scripts/rebrand/m5_watcher_rebrand.sh
```

Lo script:
1. Pre-flight (verifica Ghostty.app, run.sh, clang, sorgenti)
2. Kill istanze running
3. Backup `M5 Watcher.app` esistente
4. Clone Ghostty.app → M5 Watcher.app
5. Inject icona M5 (da backup) + modifica Info.plist
6. Compile Mach-O launcher
7. Install come `CFBundleExecutable` (rinomina `ghostty` → `ghostty.bin`)
8. Re-sign bottom-up (XPC → app → plugin → framework → dylib)
9. Sign main bin + launcher con entitlements
10. Verify + xattr cleanup + LaunchServices refresh

## Rollback

```bash
rm -rf "$HOME/Applications/M5 Watcher.app"
mv "$HOME/Applications/M5 Watcher.app.applet.bak.sess1988" \
   "$HOME/Applications/M5 Watcher.app"
```

## Modificare gli args Ghostty

Edita le righe `char *new_argv[] = {...}` in `m5_launcher.c`, poi ri-esegui `m5_watcher_rebrand.sh`. Lo script ricompila + reinstall + re-sign.

Args correnti:
- `--title=M5 Watcher`
- `--fullscreen=true`
- `--class=com.polpo.m5-watcher`
- `--window-width=180`
- `--window-height=50`
- `--command=/Users/mattiacalastri/projects/m5-watcher/run.sh`

## Cicatrici catturate (vedi vault doctrine)

1. **Bash CFBundleExecutable** → `Launchd job spawn failed POSIX 162`. macOS richiede Mach-O.
2. **Sparkle.framework dyld load fail** → Team ID mismatch tra ad-hoc main e signed framework. Fix: bottom-up resign + `disable-library-validation`.
3. **Hardened runtime su bash** → spawn fail anche senza Sparkle.
4. **`-e <cmd>` flag** → Ghostty security dialog + apertura window separata. Fix: `--command=` config key.
5. **`com.apple.quarantine` xattr** ereditato da `cp -R` Ghostty.app → Gatekeeper rifiuta. Fix: `xattr -cr`.
6. **Claude auto-mode classifier** blocca clone third-party signed app → script user-launched dal terminale (NON bypass disciplina).

## Trade-off accettati

- Ad-hoc sign → Gatekeeper `spctl --assess` rifiuta. OK per app personale, NO redistribuzione.
- Sparkle auto-update disabilitato (Ghostty interno cerca aggiornamenti per `com.mitchellh.ghostty`, non per `com.polpo.m5-watcher`).
- Storage: ~62MB duplicati per il clone.
- Apple-restricted entitlements (camera, mic, contacts) NON grantabili ad-hoc — irrilevanti per TUI Polpo.

## Riferimenti

- Vault: `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Astra Digital Marketing/Patterns/macOS .app Rebrand — Mach-O Wrapper Canonical Sess.1988.md`
- Memory: `~/.claude/projects/-Users-mattiacalastri-projects/memory/reference_macos_app_rebrand_macho_wrapper_sess1988.md`
- Sister opposta (sess.1987): `reference_macos_system_app_neutralize.md` — neutralize system app vs rebrand third-party
