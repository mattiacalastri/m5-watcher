#!/bin/bash
# 🐙 M5 MAX WATCHER — launcher (sess.1568: Ghostty-only guard)
#
# Textual TUI rendering è buggy in Terminal.app: sul resize/fullscreen il footer
# viene replicato verticalmente perché Terminal.app non ripulisce il frame
# precedente correttamente. Ghostty (GPU) e iTerm2 gestiscono bene il
# refresh viewport. Self-dispatch a Ghostty se invocato da TERM incompatibile.

case "${TERM_PROGRAM:-}" in
  ghostty|iTerm.app)
    # Renderer compatibile, procedi.
    ;;
  *)
    echo "🐙 m5-watcher: TERM_PROGRAM='${TERM_PROGRAM:-unknown}' incompatibile con Textual."
    echo "    Auto-dispatch a Ghostty.app..."
    # sess.1988: Ghostty CLI args — finestra dedicata "M5 Watcher" sizing 180×50
    # così l'app non sembra una sessione Ghostty generica.
    if /usr/bin/open -na Ghostty.app --args \
         --title=M5\ Watcher \
         --class=com.polpo.m5-watcher \
         --fullscreen=true \
         --window-width=180 \
         --window-height=50 \
         -e "$0"; then
      exit 0
    else
      echo "    ⚠️  Ghostty.app non trovato. Apri manualmente:"
      echo "    open -na Ghostty.app --args -e $0"
      exit 1
    fi
    ;;
esac

# sess.1988: OSC title — finestra Ghostty mostra "🐙 M5 Watcher" anche per
# esecuzione manuale (non passata dal launcher .app).
printf '\033]0;🐙 M5 Watcher\007'

cd "$(dirname "$0")"
exec venv/bin/python app.py
